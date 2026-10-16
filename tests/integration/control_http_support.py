"""Docker guest checks for durable HTTP control state and actual SQL roles."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from packages.persistence import PostgresLedger
from services.control_api.backend import ControlBackend

TRACE = "c" * 32


def missing_policy_guest():
    """Frisch migrierte, isolierte Datenbank vor der ersten Policyinitialisierung."""
    import json
    import sys
    from datetime import UTC, datetime
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import URL

    from packages.domain import models as m
    from packages.observability.outbox import _envelope

    payload = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=payload["role"],
            password=payload["password"],
            host="postgres",
            database=payload["database"],
            query={"connect_timeout": "5"},
        ),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "head")
    ledger = PostgresLedger(engine=engine)
    now = datetime.now(UTC)
    with ledger.transaction() as tx:
        tx.connection.execute(
            text("INSERT INTO public.accounts(id,currency,ledger_version) VALUES ('paper','USD',1)")
        )
        tx.insert(m.AuditEvent("fixture", now, "fixture", "fixture", ("TEST",), "paper", 1, TRACE))
        tx.insert(
            m.AuditEvent(
                "fixture-portfolio", now, "fixture", "fixture", ("TEST",), "portfolio", 1, TRACE
            )
        )
        tx.insert(
            m.PortfolioSnapshot(
                "portfolio",
                "USD",
                Decimal("1000"),
                Decimal("1000"),
                Decimal("0"),
                now,
                1,
                m.Quality.VALID,
                m.Reconciliation.PENDING,
            )
        )
        tx.connection.execute(
            text("""
            INSERT INTO public.risk_account_context
              (account_id,version,portfolio_snapshot_id,portfolio_version,risk_config_version,
               day_start_at,day_start_equity,day_start_realized_pnl,mode,safety_state,
               reconciliation_current,valid_until,actor_id,trace_id)
            VALUES ('paper',1,'portfolio',1,'config',clock_timestamp(),1000,0,'DISABLED',
                    'PAUSE_NEW',false,clock_timestamp(),'fixture',:trace)
        """),
            {"trace": TRACE},
        )
    role_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(role_engine, "connect")
    def role(connection, _record):
        connection.execute("SET ROLE atl_control_api")
        connection.commit()

    backend = ControlBackend(PostgresLedger(engine=role_engine), "paper")
    result = backend.mutate(
        "setMode", {"mode": "SHADOW", "reason": "fixture"}, "operator", "missing-policy-mode", TRACE
    )
    assert result[0] == 503
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM public.outbox_events")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one() == 2
        assert (
            c.execute(text("SELECT mode FROM public.risk_account_context")).scalar_one()
            == "DISABLED"
        )
    with role_engine.begin() as c:
        try:
            c.execute(
                text("SELECT public.control_set_safe_mode('paper','SHADOW','operator',:trace)"),
                {"trace": TRACE},
            )
        except DBAPIError:
            pass
        else:
            raise AssertionError("SQL mode bypass without policy")
    # Emergency latches remain available without inventing a policy version.
    for operation in ("pauseNewEntries", "activateKillSwitch"):
        assert (
            backend.mutate(operation, {"reason": "fixture"}, "operator", operation, TRACE)[0] == 200
        )
    with engine.connect() as c:
        rows = c.execute(text("SELECT * FROM public.outbox_events")).mappings().all()
        assert len(rows) == 2
        assert all(_envelope(row).payload.policy_version is None for row in rows)
    role_engine.dispose()
    engine.dispose()
    print("PASS absent policy blocks mode but not emergency latches; all outbox events decode")


def checks(_admin_ledger, _execution_ledger, admin_engine, _now):
    from packages.observability.outbox import _envelope

    role_engine = create_engine(admin_engine.url, hide_parameters=True, pool_size=8)

    @event.listens_for(role_engine, "connect")
    def role(dbapi_connection, _record):
        dbapi_connection.execute("SET ROLE atl_control_api")
        dbapi_connection.commit()

    backend = ControlBackend(PostgresLedger(engine=role_engine), "paper")
    initial = backend.read_mode(TRACE)
    assert initial["mode"] == "PAPER" and initial["health"] == "HEALTHY"
    policy, etag = backend.read_policy()
    assert policy["version"] == 1 and policy["simulationOnly"] and etag == '"risk-policy-v1"'

    # A stored success replays after process reconstruction without another audit/outbox.
    first = backend.mutate("pauseNewEntries", {"reason": "operator"}, "operator", "pause-1", TRACE)
    assert first[0] == 200 and first[1]["pauseNew"]
    recreated = ControlBackend(PostgresLedger(engine=role_engine), "paper")
    assert (
        recreated.mutate("pauseNewEntries", {"reason": "operator"}, "operator", "pause-1", TRACE)
        == first
    )
    conflict = recreated.mutate(
        "pauseNewEntries", {"reason": "changed"}, "operator", "pause-1", TRACE
    )
    assert conflict[0] == 409 and conflict[1]["code"] == "IDEMPOTENCY_CONFLICT"
    with admin_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM public.audit_events WHERE action='safety.latch'")
            ).scalar_one()
            == 1
        )

    # Exact policy retry wins over an obsolete If-Match; a new key gets 412.
    update = {
        "maxPositionsPerInstrument": 1,
        "maxOpenPositions": 3,
        "maxPositionEquityFraction": "0.20",
        "maxDailyLossFraction": "0.02",
    }
    changed = backend.mutate("updateRiskPolicy", update, "operator", "policy-1", TRACE, etag)
    assert changed[0] == 200 and changed[1]["version"] == 2
    assert changed[2]["ETag"] == '"risk-policy-v2"'
    assert (
        recreated.mutate("updateRiskPolicy", update, "operator", "policy-1", TRACE, etag) == changed
    )
    assert backend.mutate("updateRiskPolicy", update, "operator", "policy-2", TRACE, etag)[0] == 412
    assert backend.mutate("updateRiskPolicy", update, "operator", "policy-3", TRACE)[0] == 428
    with admin_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM public.risk_policies")).scalar_one() == 2
        )

    # A safe mode sets PAUSE_NEW and a later PAPER request records intent only.
    safe = backend.mutate(
        "setMode", {"mode": "SHADOW", "reason": "review"}, "operator", "mode-1", TRACE
    )
    assert safe[0] == 200 and safe[1]["mode"] == "SHADOW" and safe[1]["pauseNew"]
    paper = backend.mutate(
        "setMode", {"mode": "PAPER", "reason": "review"}, "operator", "mode-2", TRACE
    )
    assert paper[0] == 503 and paper[1]["code"] == "HEALTH_CHECK_FAILED"
    assert backend.read_mode(TRACE)["mode"] == "SHADOW"
    with admin_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM public.safety_resume_requests "
                    "WHERE operation='ACTIVATE_PAPER'"
                )
            ).scalar_one()
            == 1
        )

    # Independent counters must use independent aggregate identities.
    with admin_engine.connect() as connection:
        rows = (
            connection.execute(
                text("""
              SELECT * FROM public.outbox_events
            WHERE event_type IN ('control.mode.changed.v1','control.policy.changed.v1')
        """)
            )
            .mappings()
            .all()
        )
        assert {
            r["aggregate_id"] for r in rows if r["event_type"] == "control.mode.changed.v1"
        } == {"paper"}
        assert {
            r["aggregate_id"] for r in rows if r["event_type"] == "control.policy.changed.v1"
        } == {"risk-policy:paper"}
        assert all(_envelope(row).payload.policy_version == 2 for row in rows)

    # The SQL boundary also rejects Decimal scale loss before any effect.
    with role_engine.begin() as connection:
        try:
            connection.execute(
                text("""
                SELECT public.control_replace_risk_policy(
                  'paper','operator',:trace,1,2,0.0000000000000000001,0.01)
            """),
                {"trace": TRACE},
            )
        except DBAPIError:
            pass
        else:
            raise AssertionError("SQL accepted lossy decimal precision")
    assert backend.read_policy()[0]["version"] == 2

    # Account lock serializes competing commands; HALTED wins and cannot be cleared.
    def kill(key):
        return backend.mutate("activateKillSwitch", {"reason": "emergency"}, "operator", key, TRACE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(kill, ("kill-1", "kill-2")))
    assert all(result[0] == 200 and result[1]["killSwitch"] for result in results)
    assert backend.mutate(
        "pauseNewEntries", {"reason": "still safe"}, "operator", "pause-2", TRACE
    )[1]["killSwitch"]
    assert (
        backend.mutate("resumeNewEntries", {"reason": "retry"}, "operator", "resume-1", TRACE)[1][
            "code"
        ]
        == "KILL_SWITCH_ACTIVE"
    )

    # Exercise the real ASGI transport, local session, and this same SQL role.
    from fastapi.testclient import TestClient

    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth

    auth = LocalAuth()
    activation_code = auth.issue_activation_code()
    with TestClient(
        create_app(auth=auth, backend=backend),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 46000),
    ) as client:
        session = client.post(
            "/api/v1/session",
            json={"activationCode": activation_code},
            headers={"Origin": "http://127.0.0.1:8000", "Idempotency-Key": "session-activation-1"},
        )
        assert session.status_code == 204
        mode = client.get("/api/v1/mode")
        assert mode.status_code == 200 and mode.json()["killSwitch"]
        command = client.post(
            "/api/v1/control/kill",
            json={"reason": "operator emergency"},
            headers={"Origin": "http://127.0.0.1:8000", "Idempotency-Key": "http-kill-request-1"},
        )
        assert command.status_code == 200 and command.json()["killSwitch"]
        assert (
            client.post(
                "/api/v1/control/kill",
                json={"reason": "operator emergency"},
                headers={
                    "Origin": "http://127.0.0.1:8000",
                    "Idempotency-Key": "http-kill-request-1",
                },
            ).json()
            == command.json()
        )
    with role_engine.begin() as connection:
        try:
            connection.execute(
                text("UPDATE public.risk_account_context SET mode='PAPER',safety_state='RUNNING'")
            )
        except DBAPIError:
            pass
        else:
            raise AssertionError("API role unexpectedly has unrestricted context UPDATE")

    # Receipt failure rolls back the latch, audit, and outbox in one transaction.
    with admin_engine.begin() as connection:
        # A fresh mutable fixture ensures the attempted KILL really writes effects.
        connection.execute(
            text("""
            INSERT INTO public.audit_events
              (id,occurred_at,actor_id,action,reason_codes,entity_id,entity_version,trace_id)
            SELECT 'fixture-reset',clock_timestamp(),actor_id,'fixture.reset','["TEST"]'::jsonb,
                   account_id,version+1,trace_id
            FROM public.risk_account_context WHERE account_id='paper'
        """)
        )
        connection.execute(
            text(
                "UPDATE public.risk_account_context "
                "SET safety_state='PAUSE_NEW',version=version+1 "
                "WHERE account_id='paper'"
            )
        )
        connection.execute(
            text("""
            CREATE FUNCTION public.test_reject_control_receipt() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test receipt failure'; END $$
        """)
        )
        connection.execute(
            text("""
            CREATE TRIGGER test_reject_control_receipt BEFORE INSERT ON public.control_http_receipts
            FOR EACH ROW EXECUTE FUNCTION public.test_reject_control_receipt()
        """)
        )
    with admin_engine.connect() as connection:
        before = connection.execute(
            text("SELECT version FROM public.risk_account_context WHERE account_id='paper'")
        ).scalar_one()
        audit_before = connection.execute(
            text("SELECT count(*) FROM public.audit_events")
        ).scalar_one()
        outbox_before = connection.execute(
            text("SELECT count(*) FROM public.outbox_events")
        ).scalar_one()
    try:
        backend.mutate("activateKillSwitch", {"reason": "rollback"}, "operator", "rollback", TRACE)
    except DBAPIError:
        pass
    else:
        raise AssertionError("receipt failure did not abort mutation")
    with admin_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version FROM public.risk_account_context WHERE account_id='paper'")
            ).scalar_one()
            == before
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
            == audit_before
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.outbox_events")).scalar_one()
            == outbox_before
        )
    with admin_engine.begin() as connection:
        connection.execute(
            text("DROP TRIGGER test_reject_control_receipt ON public.control_http_receipts")
        )
        connection.execute(text("DROP FUNCTION public.test_reject_control_receipt()"))
    role_engine.dispose()
    print(
        "PASS durable control receipts, policy ETags, safety role grants, "
        "serialization and rollback"
    )


if __name__ == "__main__":
    import sys

    from risk_support import guest

    if len(sys.argv) > 2 and sys.argv[2] == "missing-policy":
        missing_policy_guest()
    else:
        guest(checks)
