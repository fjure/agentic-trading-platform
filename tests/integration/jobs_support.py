"""Isolated PostgreSQL guest for PTV-05. No credential is stored or printed."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread


def host() -> str:
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    suffix = uuid.uuid4().hex[:20]
    database = f"atl_job_{suffix}"
    role = f"atl_job_{suffix}"
    password = secrets.token_urlsafe(32)
    setup = "\n".join(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{group}') "
        f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; "
        "END IF; END $$;"
        for group in GROUPS
    )
    _admin(setup)
    _admin(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOINHERIT;")
    created = False
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "agentic-trading-lab-dev_local",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--tmpfs",
        "/tmp",
        "--workdir",
        "/opt/lab",
        "--entrypoint",
        "python",
    ]
    for source, target in (
        (root / "packages", "/opt/lab/packages"),
        (root / "services", "/opt/lab/services"),
        (root / "alembic.ini", "/opt/lab/alembic.ini"),
        (Path(__file__).resolve(), "/opt/lab/jobs_support.py"),
    ):
        command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
    command.extend(("agentic-trading-lab-dev-build-smoke:latest", "jobs_support.py", "guest"))
    try:
        _admin("\n".join(f"GRANT {group} TO {role} WITH ADMIN OPTION;" for group in GROUPS))
        _admin(f"CREATE DATABASE {database} OWNER {role};")
        created = True
        result = subprocess.run(
            command,
            input=json.dumps({"database": database, "role": role, "password": password}),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("isolated jobs verification failed: " + result.stdout.strip()[:200])
        return result.stdout.strip()
    finally:
        if created:
            _admin(f"DROP DATABASE {database} WITH (FORCE);")
        _admin(f"DROP ROLE {role};")


def guest() -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.domain import models as m
    from packages.observability.outbox import Outbox, persist
    from services.worker.jobs import JobStore, LeaseLost, enqueue

    payload = json.loads(sys.stdin.read())
    url = URL.create(
        "postgresql+psycopg",
        username=payload["role"],
        password=payload["password"],
        host="postgres",
        port=5432,
        database=payload["database"],
        query={"connect_timeout": "5"},
    )
    engine = create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.execute(text("CREATE TABLE public.test_effects (id text PRIMARY KEY)"))
        connection.execute(text("GRANT SELECT, INSERT ON public.test_effects TO atl_worker"))
    role_engine = create_engine(url, hide_parameters=True)

    @event.listens_for(role_engine, "connect")
    def set_worker_role(dbapi_connection, _connection_record):
        dbapi_connection.execute("SET ROLE atl_worker")
        dbapi_connection.commit()

    store = JobStore(role_engine)
    outbox = Outbox(role_engine)
    now = datetime.now(UTC)
    proposal = m.TradeProposal(
        "proposal-1",
        "decision-1",
        "state-1",
        "spy",
        m.Target.FLAT,
        Decimal("1"),
        now,
        now + timedelta(seconds=30),
        "a" * 32,
    )
    event = m.EventEnvelope("event-1", "trade.proposed.v1", now, proposal.id, 1, "a" * 32, proposal)

    with role_engine.begin() as connection:
        enqueue(
            connection, job_id="job-1", job_type="decision", input_ref="input-1", trace_id="a" * 32
        )
    first = store.claim("worker-a")
    assert first and first.attempt == 1 and first.token == 1
    assert store.heartbeat(first)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE public.jobs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id='job-1'"
            )
        )
    assert not store.heartbeat(first)
    assert store.claim("worker-b") is None

    def verify(connection, job_id, input_ref):
        # The takeover decision is based on persisted input and effect state.
        assert not hasattr(connection, "commit")
        try:
            connection.execute(text("UPDATE public.jobs SET error_code='unsafe'"))
        except ValueError:
            pass
        else:
            raise AssertionError("recovery verifier performed a mutation")
        assert (
            connection.execute(
                text("SELECT input_ref FROM public.jobs WHERE id=:id"), {"id": job_id}
            ).scalar_one()
            == input_ref
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.test_effects")).scalar_one() == 0
        )
        return "db:input-1:no-effect"

    second = store.claim("worker-b", verify_recovery=verify)
    assert second and second.token == 2 and second.attempt == 2
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT evidence_ref FROM public.job_recovery_checks WHERE job_id='job-1'")
            ).scalar_one()
            == "db:input-1:no-effect"
        )
    try:
        with store.transaction(first) as connection:
            connection.execute(text("INSERT INTO public.test_effects VALUES ('stale')"))
    except LeaseLost:
        pass
    else:
        raise AssertionError("stale worker committed")
    with store.transaction(second) as connection:
        assert not hasattr(connection, "commit")
        for forbidden in ("COMMIT", "ROLLBACK", "-- hidden\nCOMMIT", "/* hidden */ COMMIT"):
            try:
                connection.execute(text(forbidden))
            except ValueError:
                pass
            else:
                raise AssertionError("job body controlled its transaction")
        connection.execute(text("INSERT INTO public.test_effects VALUES ('winner')"))
        persist(connection, event)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM public.test_effects")).scalars().all() == [
            "winner"
        ]
        assert (
            connection.execute(text("SELECT status FROM public.jobs WHERE id='job-1'")).scalar_one()
            == "DONE"
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.outbox_events")).scalar_one() == 1
        )

    with role_engine.begin() as connection:
        persist(connection, event)
    try:
        with engine.begin() as connection:
            persist(connection, replace(event, aggregate_version=2))
    except ValueError:
        pass
    else:
        raise AssertionError("outbox accepted payload mismatch")

    # An unacknowledged delivery survives a publisher crash; consumer effect
    # and receipt commit atomically, so redelivery has no second effect.
    def crash_delivery(delivered):
        assert delivered == event
        assert outbox.consume(
            "projection",
            delivered.event_id,
            lambda c: c.execute(text("INSERT INTO public.test_effects VALUES ('consumer')")),
        )
        raise RuntimeError("crash after delivery")

    try:
        outbox.publish_one(crash_delivery)
    except RuntimeError:
        pass
    else:
        raise AssertionError("crash injection failed")
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT published_at IS NULL FROM public.outbox_events")
        ).scalar_one()
    delivered = []
    cursor = outbox.publish_one(delivered.append)
    assert cursor == 1 and delivered == [event]
    assert not outbox.consume(
        "projection",
        event.event_id,
        lambda c: c.execute(text("INSERT INTO public.test_effects VALUES ('duplicate')")),
    )
    state, high_water, entries = outbox.snapshot_and_events(
        lambda c: c.execute(text("SELECT count(*) FROM public.test_effects")).scalar_one()
    )
    assert state == 2 and high_water == 1 and entries == [(1, event)]

    def failed_effect(connection):
        assert not hasattr(connection, "commit")
        connection.execute(text("INSERT INTO public.test_effects VALUES ('rolled-back')"))
        raise RuntimeError("consumer crash")

    try:
        outbox.consume("crashing-consumer", event.event_id, failed_effect)
    except RuntimeError:
        pass
    else:
        raise AssertionError("consumer crash injection failed")
    with role_engine.connect() as connection:
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM public.consumer_receipts
            WHERE consumer='crashing-consumer'
        """)
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM public.test_effects WHERE id='rolled-back'
        """)
            ).scalar_one()
            == 0
        )

    with role_engine.begin() as connection:
        persist(connection, replace(event, event_id="event-2"))
    for statement in (
        "UPDATE public.publish_cursor SET next_cursor=next_cursor+1",
        "UPDATE public.outbox_events SET published_at=clock_timestamp() WHERE event_id='event-2'",
        "INSERT INTO public.publish_log(cursor,event_id) VALUES (2,'event-2')",
    ):
        try:
            with role_engine.begin() as connection:
                connection.execute(text(statement))
        except DBAPIError as error:
            assert error.orig.sqlstate == "P0001"
        else:
            raise AssertionError("publish state committed without its matching records")

    for statement, expected_state in (
        ("UPDATE public.outbox_events SET payload='{}'::jsonb WHERE event_id='event-1'", "42501"),
        ("DELETE FROM public.consumer_receipts WHERE consumer='projection'", "42501"),
        ("DELETE FROM public.publish_log WHERE cursor=1", "42501"),
        ("UPDATE public.job_recovery_checks SET evidence_ref='changed'", "42501"),
        ("UPDATE public.publish_cursor SET next_cursor=next_cursor+2", "P0001"),
    ):
        try:
            with role_engine.begin() as connection:
                connection.execute(text(statement))
        except DBAPIError as error:
            assert error.orig.sqlstate == expected_state
        else:
            raise AssertionError("runtime role bypassed outbox boundary")

    # The second backend must be observed waiting on the singleton row before
    # the first transaction is released. Test both rollback reuse and commit order.
    def allocate(connection, event_id):
        cursor = connection.execute(
            text("""
            UPDATE public.publish_cursor SET next_cursor=next_cursor+1
            RETURNING next_cursor-1
        """)
        ).scalar_one()
        connection.execute(
            text("""
            INSERT INTO public.publish_log(cursor,event_id) VALUES (:cursor,:event_id)
        """),
            {"cursor": cursor, "event_id": event_id},
        )
        connection.execute(
            text("""
            UPDATE public.outbox_events SET published_at=clock_timestamp()
            WHERE event_id=:event_id
        """),
            {"event_id": event_id},
        )
        return cursor

    def cursor_race(first_event, second_event, *, commit_first, expected_first):
        started = Event()
        contender_pid = []
        contender_result = []

        def contender():
            try:
                with role_engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL lock_timeout = '4s'")
                    contender_pid.append(
                        connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    )
                    started.set()
                    contender_result.append(allocate(connection, second_event))
            except Exception as error:
                contender_result.append(error)
                started.set()

        with role_engine.connect() as first:
            transaction = first.begin()
            first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
            if first_event is None:
                first_cursor = first.execute(
                    text("""
                    UPDATE public.publish_cursor SET next_cursor=next_cursor+1
                    RETURNING next_cursor-1
                """)
                ).scalar_one()
            else:
                first_cursor = allocate(first, first_event)
            assert first_cursor == expected_first
            thread = Thread(target=contender)
            thread.start()
            try:
                assert started.wait(3)
                deadline = time.monotonic() + 3
                blocked = False
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        blockers = observer.execute(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": contender_pid[0]},
                        ).scalar_one()
                    if first_pid in blockers:
                        blocked = True
                        break
                    time.sleep(0.02)
                assert blocked, "second allocator never blocked on singleton row"
                if commit_first:
                    transaction.commit()
                else:
                    transaction.rollback()
            finally:
                if transaction.is_active:
                    transaction.rollback()
                thread.join(timeout=5)
            assert not thread.is_alive()
            assert contender_result == [expected_first + (1 if commit_first else 0)]

    cursor_race(None, "event-2", commit_first=False, expected_first=2)
    with role_engine.begin() as connection:
        persist(connection, replace(event, event_id="event-3"))
        persist(connection, replace(event, event_id="event-4"))
    cursor_race("event-3", "event-4", commit_first=True, expected_first=3)
    _, high_water, entries = outbox.snapshot_and_events(lambda _: "snapshot", 1)
    assert high_water == 4
    assert [(cursor, envelope.event_id) for cursor, envelope in entries] == [
        (2, "event-2"),
        (3, "event-3"),
        (4, "event-4"),
    ]

    with engine.begin() as connection:
        enqueue(
            connection,
            job_id="job-expiry",
            job_type="decision",
            input_ref="input-expiry",
            trace_id="b" * 32,
        )
    expiring = store.claim("worker-c")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE public.jobs SET lease_until=clock_timestamp()+interval '0.2 seconds' "
                "WHERE id='job-expiry'"
            )
        )
    try:
        with store.transaction(expiring) as connection:
            connection.execute(text("INSERT INTO public.test_effects VALUES ('expired')"))
            connection.execute(text("SELECT pg_sleep(0.3)"))
    except LeaseLost:
        pass
    else:
        raise AssertionError("worker committed after lease expiry")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM public.test_effects WHERE id='expired'")
            ).scalar_one()
            == 0
        )

    # A SELECT projection may be evaluated before waiting for a row lock.
    # Fresh database time must be checked after the lock is actually acquired.
    for operation in ("heartbeat", "fail", "transaction"):
        job_id = f"wait-{operation}"
        with role_engine.begin() as connection:
            enqueue(
                connection, job_id=job_id, job_type="decision", input_ref=job_id, trace_id="e" * 32
            )
        waiting_claim = store.claim(f"owner-{operation}")
        assert waiting_claim and waiting_claim.job_id == job_id
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE public.jobs SET lease_until=clock_timestamp()+interval '0.15 seconds'
                WHERE id=:id
            """),
                {"id": job_id},
            )
        results = []

        def call_after_lock(op=operation, result=results, claim=waiting_claim):
            if op == "heartbeat":
                result.append(store.heartbeat(claim))
            elif op == "fail":
                result.append(store.fail(claim, "TEST_FAILURE"))
            else:
                try:
                    with store.transaction(claim) as scoped:
                        scoped.execute(
                            text("INSERT INTO public.test_effects VALUES ('wait-effect')")
                        )
                except LeaseLost:
                    result.append(False)
                else:
                    result.append(True)

        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM public.jobs WHERE id=:id FOR UPDATE"), {"id": job_id}
            )
            contender = Thread(target=call_after_lock)
            contender.start()
            time.sleep(0.25)
        contender.join(timeout=3)
        assert not contender.is_alive() and results == [False]
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM public.test_effects WHERE id='wait-effect'
        """)
            ).scalar_one()
            == 0
        )

    with engine.begin() as connection:
        enqueue(
            connection,
            job_id="job-retry",
            job_type="decision",
            input_ref="input-retry",
            trace_id="c" * 32,
        )
    for attempt in range(1, 4):
        claim = (
            store.claim("worker-retry", verify_recovery=lambda *_: None)
            if attempt == 1
            else store.claim("worker-retry")
        )
        assert claim and claim.attempt == attempt
        assert store.fail(claim, "TEST_FAILURE")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.jobs SET not_before=clock_timestamp()-interval '1 second' "
                    "WHERE id='job-retry'"
                )
            )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM public.jobs WHERE id='job-retry'")
            ).scalar_one()
            == "DEAD"
        )
    assert store.blocks_new_risk("input-retry")
    for job_type in ("submit", "order_submit"):
        job_id = f"job-{job_type}"
        with engine.begin() as connection:
            enqueue(
                connection, job_id=job_id, job_type=job_type, input_ref=job_id, trace_id="d" * 32
            )
        submit = store.claim("worker-submit")
        assert submit and submit.job_id == job_id and store.fail(submit, "UNKNOWN_RESULT")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("""
                SELECT status FROM public.jobs WHERE id=:id
            """),
                    {"id": job_id},
                ).scalar_one()
                == "RECONCILE"
            )
        assert store.claim("worker-submit") is None
        assert store.blocks_new_risk(job_id)
    role_engine.dispose()
    engine.dispose()
    print(
        "PG18 jobs: lease takeover/fencing/expiry, bounded retry/dead letter, "
        "submit quarantine; outbox crash/dedup/cursor: PASS"
    )


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "guest":
        try:
            guest()
        except Exception as error:
            import traceback

            location = traceback.extract_tb(error.__traceback__)[-1]
            print(f"guest failure: {type(error).__name__} at {location.name}:{location.lineno}")
            raise SystemExit(1) from None
    elif len(sys.argv) == 1:
        print(host())
    else:
        raise SystemExit("usage: jobs_support.py [guest]")
