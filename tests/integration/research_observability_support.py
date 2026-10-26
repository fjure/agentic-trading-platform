"""Actual PG audit atomicity, sanitization, immutable records and runtime ACL checks.

Call verify(engine, execution, control, instrument, start) after migrations and
instrument registration. Uses dedicated synthetic canaries, never provider data.
"""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.observability.research import observe_jobs


def verify(engine, execution, control, instrument, start):
    job_id = uuid4()
    canary = "synthetic-private-observability-canary"
    with execution.begin() as connection:
        connection.execute(
            text("""
          INSERT INTO research.ingestion_jobs(job_id,actor,idempotency_key,request_hash,
            instrument_id,start_at,end_at,interval,mode,status,trace_id,private_cursor)
          VALUES(:id,:canary,:canary,:hash,:instrument,:start,:end,'15Min','BACKFILL',
            'ACCEPTED',:trace,:canary)
        """),
            dict(
                id=job_id,
                canary=canary,
                hash="c" * 64,
                instrument=instrument.instrument_id,
                start=start,
                end=start + timedelta(hours=1),
                trace="d" * 32,
            ),
        )
    with control.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM research.job_audit WHERE job_id=:id ORDER BY audit_id"),
                dict(id=job_id),
            )
            .mappings()
            .one()
        )
        assert set(row) == {
            "audit_id",
            "job_id",
            "trace_id",
            "status",
            "error_code",
            "pages",
            "bars",
            "attempts",
            "fence",
            "accepted_at",
            "updated_at",
            "retry_at",
            "recorded_at",
        }
        assert row["status"] == "ACCEPTED" and row["trace_id"] == "d" * 32
        assert canary not in str(dict(row))
    with execution.begin() as connection:
        connection.execute(
            text("""
          UPDATE research.ingestion_jobs SET status='RETRY_WAIT',error_code=:canary,
            attempts=1,pages=2,bars=5,fence=3,updated_at=clock_timestamp(),
            retry_at=clock_timestamp()+interval '3 seconds' WHERE job_id=:id
        """),
            dict(id=job_id, canary=canary),
        )
    with control.connect() as connection:
        rows = (
            connection.execute(
                text("SELECT * FROM research.job_audit WHERE job_id=:id ORDER BY audit_id"),
                dict(id=job_id),
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        latest = rows[-1]
        assert latest["error_code"] == "UNAVAILABLE" and canary not in str(rows)
        assert (latest["pages"], latest["bars"], latest["attempts"], latest["fence"]) == (
            2,
            5,
            1,
            3,
        )
        assert latest["retry_at"] is not None
        numeric = observe_jobs(connection)
        assert numeric["research_jobs_retry_wait"] >= 1
        assert numeric["research_retry_events_count"] >= 1
        assert numeric["research_pages_count"] >= 2 and numeric["research_bars_count"] >= 5
        assert all(type(value) in (int, float) and value >= 0 for value in numeric.values())
        assert str(job_id) not in str(numeric) and "d" * 32 not in str(numeric)
    with execution.begin() as connection:
        connection.execute(
            text("""
          UPDATE research.ingestion_jobs SET private_cursor='synthetic-private-new-cursor'
          WHERE job_id=:id
        """),
            dict(id=job_id),
        )
    with execution.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("UPDATE research.ingestion_jobs SET pages=pages+1 WHERE job_id=:id"),
            dict(id=job_id),
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.job_audit WHERE job_id=:id"), dict(id=job_id)
            ).scalar_one()
            == 3
        )
        transaction.rollback()
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.job_audit WHERE job_id=:id"), dict(id=job_id)
            ).scalar_one()
            == 2
        )
        assert connection.execute(
            text("""
          SELECT prosecdef AND 'search_path=pg_catalog, research, pg_temp'=ANY(proconfig)
          FROM pg_proc WHERE oid='research.audit_job_transition()'::regprocedure
        """)
        ).scalar_one()
        assert (
            connection.execute(
                text("""
          SELECT count(*) FROM pg_proc p,
            LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
          WHERE p.oid='research.audit_job_transition()'::regprocedure
            AND a.grantee=0 AND a.privilege_type='EXECUTE'
        """)
            ).scalar_one()
            == 0
        )
    for role in ("atl_control_api", "atl_execution", "atl_worker", "atl_watchdog"):
        with engine.connect() as connection:
            assert not connection.execute(
                text(
                    "SELECT has_function_privilege(:role,"
                    "'research.audit_job_transition()','EXECUTE')"
                ),
                {"role": role},
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_sequence_privilege(:role,'research.job_audit_audit_id_seq','USAGE')"
                ),
                {"role": role},
            ).scalar_one()
        for statement in (
            "INSERT INTO research.job_audit SELECT * FROM research.job_audit",
            "UPDATE research.job_audit SET pages=0",
            "DELETE FROM research.job_audit",
            "TRUNCATE research.job_audit",
            "SELECT research.audit_job_transition()",
            "SELECT nextval('research.job_audit_audit_id_seq')",
        ):
            _denied(engine, statement, role)
    for statement in (
        "UPDATE research.job_audit SET pages=0",
        "DELETE FROM research.job_audit",
        "TRUNCATE research.job_audit",
    ):
        _denied(engine, statement)
    # Leave no claimable work that could interfere with later vertical tests.
    with execution.begin() as connection:
        connection.execute(
            text("""
          UPDATE research.ingestion_jobs SET status='BLOCKED',error_code='BLOCKED_LICENSE',
            updated_at=clock_timestamp() WHERE job_id=:id
        """),
            {"id": job_id},
        )


def _denied(engine, statement, role=None):
    try:
        with engine.begin() as connection:
            if role is not None:
                connection.execute(text("SET LOCAL ROLE " + role))
            connection.execute(text(statement))
    except DBAPIError:
        return
    raise AssertionError("research audit mutation or function privilege widened")
