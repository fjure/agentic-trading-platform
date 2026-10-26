"""Isolated Docker-network verification when Desktop exposes no host PostgreSQL port.

Run ``python tests/integration/ledger_support.py``. No credential is persisted,
passed on a command line, or printed. Only UUID-named database/login are changed.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

PSQL = [
    "docker",
    "compose",
    "-f",
    "infra/compose/compose.yaml",
    "--profile",
    "fake",
    "exec",
    "-T",
    "--user",
    "postgres",
    "postgres",
    "psql",
    "-X",
    "-q",
    "-v",
    "ON_ERROR_STOP=1",
    "-U",
    "lab_migration",
    "-d",
    "lab",
]
GROUPS = ("atl_worker", "atl_execution", "atl_control_api", "atl_watchdog", "atl_backtest_worker")


def _admin(sql: str, *, database: str = "lab", restore_diagnostic: bool = False) -> str:
    __tracebackhide__ = True
    command_line = [*PSQL[:-1], database]
    completed = subprocess.run(command_line, input=sql, text=True, capture_output=True, check=False)
    if completed.returncode:
        if restore_diagnostic:
            errors = [line for line in completed.stderr.splitlines() if "ERROR:" in line]
            detail = errors[0][:180] if errors else "no SQL error line"
            raise RuntimeError(f"isolated PostgreSQL restore failed: {detail}") from None
        raise RuntimeError("isolated PostgreSQL setup failed; output redacted") from None
    return completed.stdout


def _fingerprint(database: str) -> str:
    from services.control_api.migrations.ledger_schema_v1 import TABLE_NAMES

    queries = [
        f"SELECT '{name}', md5(COALESCE(jsonb_agg(to_jsonb(row) "
        f"ORDER BY to_jsonb(row)::text)::text, '[]')) FROM public.\"{name}\" row"
        for name in (*TABLE_NAMES, "alembic_version")
    ]
    return _admin(" UNION ALL ".join(queries) + " ORDER BY 1;", database=database)


def _runtime_privileges(database: str) -> str:
    from services.control_api.migrations.ledger_schema_v1 import TABLE_NAMES

    roles = ",".join(f"('{role}')" for role in GROUPS)
    tables = ",".join(f"('{name}')" for name in (*TABLE_NAMES, "alembic_version"))
    permissions = ",".join(
        f"('{permission}')"
        for permission in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        )
    )
    return _admin(
        "SELECT role_name, table_name, permission, "
        "has_table_privilege(role_name, 'public.' || quote_ident(table_name), permission) "
        f"FROM (VALUES {roles}) roles(role_name) CROSS JOIN (VALUES {tables}) tables(table_name) "
        f"CROSS JOIN (VALUES {permissions}) permissions(permission) ORDER BY 1,2,3;",
        database=database,
    )


def host() -> str:
    __tracebackhide__ = True
    root = Path(__file__).resolve().parents[2]
    suffix = uuid.uuid4().hex[:20]
    database = f"atl_probe_{suffix}"
    restored = f"atl_restore_{suffix}"
    role = f"atl_probe_{suffix}"
    password = secrets.token_urlsafe(32)
    setup = "\n".join(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{group}') "
        f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; "
        "END IF; END $$;"
        for group in GROUPS
    )
    _admin(setup)
    _admin(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOINHERIT;")
    created_database = False
    created_restore = False
    mounts = [
        (root / "packages", "/opt/lab/packages"),
        (root / "services", "/opt/lab/services"),
        (root / "alembic.ini", "/opt/lab/alembic.ini"),
        (Path(__file__).resolve(), "/opt/lab/ledger_support.py"),
    ]
    command_line = [
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
    for source, target in mounts:
        command_line.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
    command_line.extend(
        ("agentic-trading-lab-dev-build-smoke:latest", "ledger_support.py", "guest")
    )
    try:
        _admin("\n".join(f"GRANT {group} TO {role} WITH ADMIN OPTION;" for group in GROUPS))
        _admin(f"CREATE DATABASE {database} OWNER {role};")
        created_database = True
        result = subprocess.run(
            command_line,
            input=json.dumps({"database": database, "role": role, "password": password}),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            # Guest stdout/stderr are deliberately not emitted; SQLAlchemy and
            # psycopg failures can include connection strings in tracebacks.
            raise RuntimeError(
                "isolated ledger verification failed; " + result.stdout.strip()[:200]
            )
        # Compose exec options precede the executable. Keep the dump entirely in
        # memory and never include global role state or credential files.
        dump_command = [
            *PSQL[:11],
            "pg_dump",
            "-U",
            "lab_migration",
            "-d",
            database,
            "--no-owner",
            "--schema=public",
        ]
        dumped = subprocess.run(dump_command, capture_output=True, text=True, check=False)
        if dumped.returncode or not dumped.stdout:
            raise RuntimeError("isolated ledger backup failed; output redacted")
        _admin(f"CREATE DATABASE {restored} OWNER {role};")
        created_restore = True
        _admin("DROP SCHEMA public CASCADE;", database=restored)
        _admin(dumped.stdout, database=restored, restore_diagnostic=True)
        if _fingerprint(database) != _fingerprint(restored):
            raise RuntimeError("full ledger backup/restore content mismatch")
        if _runtime_privileges(restored) != _runtime_privileges(database):
            raise RuntimeError("restored runtime privileges mismatch")
        count = _admin("SELECT count(*) FROM broker_orders;", database=restored)
        if "1" not in count.split():
            raise RuntimeError("isolated ledger restore verification failed")
        message = result.stdout.strip() + "; backup/restore: PASS"
        print(message)
        return message
    finally:
        cleanup = ""
        if created_restore:
            cleanup += f"DROP DATABASE {restored} WITH (FORCE);\n"
        if created_database:
            cleanup += f"DROP DATABASE {database} WITH (FORCE);\n"
        cleanup += f"DROP ROLE {role};"
        _admin(cleanup)


def guest() -> None:
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError, IntegrityError

    from packages.domain import models as m
    from packages.persistence import PostgresLedger

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
        # Verify the frozen first migration before testing today's runtime/restore.
        command.upgrade(config, "0001_ledger")
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0001_ledger"
        )
        assert (
            connection.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='decisions' "
                "AND column_name IN ('input_tokens','output_tokens')"
            )).scalar_one()
            == 0
        )
        command.upgrade(config, "head")
    now = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
    trace = "a" * 32
    instrument = m.Instrument("spy", m.Market.US_EQUITY, "SPY", "EQUITY", "USD", 1, "NYSE")
    bar = m.MarketBar(
        "bar-1",
        "spy",
        now - timedelta(minutes=15),
        now,
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100.5"),
        Decimal("30"),
        now,
        now,
        now,
        m.Quality.VALID,
    )
    evidence = m.EvidenceBundle("ev-1", "spy", ("bar-1",), now, (), m.Quality.VALID)
    position = m.Position("pos-1", "spy", "USD", Decimal("1"), Decimal("100"), Decimal("0"), 1)
    portfolio = m.PortfolioSnapshot(
        "pf-1",
        "USD",
        Decimal("1000"),
        Decimal("900"),
        Decimal("100"),
        now,
        1,
        m.Quality.VALID,
        m.Reconciliation.CURRENT,
    )
    state = m.MarketState(
        "ms-1",
        "spy",
        m.Market.US_EQUITY,
        bar,
        evidence,
        portfolio,
        position,
        {"return": Decimal("0.01")},
        Decimal("0.02"),
        now,
        now,
        now,
        now,
        m.Quality.VALID,
        trace,
    )
    decision = m.DecisionResult(
        "dec-1",
        "ms-1",
        m.Choice.ABSTAIN,
        None,
        "prompt-v1",
        "question-v1",
        "config-v1",
        "b" * 64,
        0,
        now + timedelta(seconds=30),
        trace,
        "ABSTAIN_UNAVAILABLE",
    )
    proposal = m.TradeProposal(
        "proposal-1",
        "dec-1",
        "ms-1",
        "spy",
        m.Target.FLAT,
        Decimal("1"),
        now,
        now + timedelta(seconds=30),
        trace,
    )
    risk = m.RiskDecision(
        "risk-1",
        "proposal-1",
        True,
        "OK",
        ("OK",),
        1,
        m.ExposureSnapshot("USD", Decimal("1000"), Decimal("100"), Decimal("100")),
        now,
        now + timedelta(seconds=30),
        trace,
        "reservation-1",
    )
    reservation = m.RiskReservation(
        "reservation-1",
        "proposal-1",
        "risk-1",
        "USD",
        Decimal("100"),
        m.ReservationState.HELD_UNKNOWN,
        1,
        now,
        trace,
    )
    intent = m.OrderIntent(
        "intent-1",
        "proposal-1",
        "risk-1",
        "client-1",
        "spy",
        "SELL",
        "MARKET",
        Decimal("1"),
        1,
        now,
        trace,
    )
    order = m.BrokerOrder(
        "order-1",
        "intent-1",
        "client-1",
        "broker-1",
        m.OrderStatus.UNKNOWN,
        Decimal("1"),
        Decimal("0"),
        1,
        now,
        trace,
    )
    report = m.ExecutionReport(
        "report-1",
        "order-1",
        m.OrderStatus.UNKNOWN,
        Decimal("1"),
        Decimal("0"),
        m.Reconciliation.PENDING,
        now,
        now,
        trace,
        broker_event_id="event-1",
    )
    ledger = PostgresLedger(engine=engine)
    policy = m.RiskPolicy(
        "policy-1",
        1,
        "USD",
        1,
        2,
        Decimal("0.10"),
        Decimal("0.01"),
        now,
        "test",
        "audit-policy-1",
    )
    chain = (
        instrument,
        bar,
        evidence,
        position,
        portfolio,
        state,
        decision,
        proposal,
        policy,
        risk,
        reservation,
        intent,
        order,
        report,
        m.AgentRun("run-1", "d" * 64, "config-v1", None, "SUCCEEDED", now, proposal.id, trace),
    )
    audited = (
        m.Position,
        m.PortfolioSnapshot,
        m.DecisionResult,
        m.TradeProposal,
        m.RiskPolicy,
        m.RiskDecision,
        m.RiskReservation,
        m.OrderIntent,
        m.BrokerOrder,
        m.ExecutionReport,
    )
    with ledger.transaction() as tx:
        for entity in chain:
            if isinstance(entity, audited):
                tx.insert(
                    m.AuditEvent(
                        f"audit-{entity.id}",
                        now,
                        "test",
                        "persist",
                        ("TEST",),
                        entity.id,
                        1,
                        trace,
                    )
                )
            tx.insert(
                entity,
                adapter="fake" if isinstance(entity, (m.BrokerOrder, m.ExecutionReport)) else None,
            )
    with ledger.transaction() as tx:
        assert tx.get(m.MarketState, "ms-1") == state
        assert tx.get(m.RiskReservation, "reservation-1") == reservation
        assert tx.get(m.BrokerOrder, "order-1") == order
    typed = m.TypedAnswers(
        regime=m.ChoiceAnswer(
            "q-regime-v1",
            "TREND",
            Decimal("0.8"),
            {"TREND": Decimal("0.8"), "RANGE": Decimal("0.1"), "UNCERTAIN": Decimal("0.1")},
        ),
        evidence_quality=m.ScoreAnswer(
            "q-quality-v1",
            Decimal("8"),
            {"0": "weak", "9": "strong"},
            {"0": Decimal("0.2"), "9": Decimal("0.8")},
            Decimal("0.8"),
        ),
        signal_conflict=m.NoulAnswer("q-conflict-v1", Decimal("0.1")),
    )
    typed_decision = m.DecisionResult(
        "dec-typed",
        "ms-1",
        m.Choice.LONG,
        "mock-v1",
        "prompt-v1",
        "questions-v1",
        "config-v1",
        "c" * 64,
        1,
        now + timedelta(seconds=30),
        trace,
        "OK",
        confidence=Decimal("0.7"),
        distribution={"LONG": Decimal("0.7"), "FLAT": Decimal("0.2"), "ABSTAIN": Decimal("0.1")},
        typed_answers=typed,
        regime="TREND",
        evidence_quality=Decimal("0.8"),
        signal_conflict=Decimal("0.1"),
    )
    with ledger.transaction() as tx:
        tx.insert(
            m.AuditEvent(
                "audit-dec-typed",
                now,
                "test",
                "decision",
                ("OK",),
                typed_decision.id,
                1,
                trace,
            )
        )
        tx.insert(typed_decision)
    with ledger.transaction() as tx:
        assert tx.get(m.DecisionResult, "dec-typed") == typed_decision
    newer_position = m.Position(
        "pos-1",
        "spy",
        "USD",
        Decimal("2"),
        Decimal("101"),
        Decimal("1"),
        2,
    )
    with ledger.transaction() as tx:
        tx.insert(
            m.AuditEvent(
                "audit-pos-version-2",
                now,
                "test",
                "position-update",
                ("FILL",),
                "pos-1",
                2,
                trace,
            )
        )
        assert tx.replace(newer_position, expected_version=1)
    with ledger.transaction() as tx:
        assert tx.get(m.Position, "pos-1") == newer_position
        assert tx.get(m.MarketState, "ms-1") == state
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM position_versions")).scalar_one() == 2
        assert not connection.execute(
            text("SELECT has_table_privilege('atl_worker','positions','INSERT')")
        ).scalar_one()
        assert connection.execute(
            text("SELECT has_table_privilege('atl_execution','positions','INSERT')")
        ).scalar_one()
        assert (
            connection.execute(
                text("SELECT exposure_snapshot->>'equity' FROM risk_decisions WHERE id='risk-1'")
            ).scalar_one()
            == "1000"
        )
        assert (
            connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='positions' AND column_name='quantity'"
                )
            ).scalar_one()
            == "numeric"
        )
        assert (
            connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name='market_bars' AND column_name='event_time'"
                )
            ).scalar_one()
            == "timestamp with time zone"
        )
        unsafe_roles = connection.execute(
            text(
                "SELECT count(*) FROM pg_roles WHERE rolname IN "
                "('atl_worker','atl_execution','atl_control_api','atl_watchdog') "
                "AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole "
                "OR rolreplication OR rolbypassrls OR rolinherit)"
            )
        ).scalar_one()
        assert unsafe_roles == 0
    for statement in (
        "UPDATE audit_events SET action='changed' WHERE id='audit-order-1'",
        "DELETE FROM trade_proposals WHERE id='proposal-1'",
        "TRUNCATE audit_events",
        "UPDATE market_states SET spread=0 WHERE id='ms-1'",
    ):
        try:
            with engine.begin() as connection:
                connection.execute(text(statement))
        except DBAPIError:
            continue
        raise AssertionError("immutable ledger mutation succeeded")
    try:
        with ledger.transaction() as tx:
            tx.insert(
                m.OrderIntent(
                    "intent-2",
                    "proposal-1",
                    "risk-1",
                    "client-1",
                    "spy",
                    "SELL",
                    "MARKET",
                    Decimal("1"),
                    1,
                    now,
                    trace,
                )
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("duplicate client order identity succeeded")
    # Different transport IDs and decisions must not bypass proposal deduplication.
    for entity, constraint_name in (
        (
            replace(intent, id="duplicate-proposal-intent", client_order_id="client-new"),
            "uq_intent_proposal",
        ),
        (
            replace(reservation, id="duplicate-proposal-reservation", risk_decision_id="risk-new"),
            "uq_reservation_proposal",
        ),
    ):
        try:
            with ledger.transaction() as tx:
                if isinstance(entity, m.RiskReservation):
                    tx.insert(replace(risk, id="risk-new", reservation_id=None))
                tx.insert(entity)
        except IntegrityError as error:
            assert error.orig.sqlstate == "23505"
            assert error.orig.diag.constraint_name == constraint_name
        else:
            raise AssertionError("duplicate proposal succeeded")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO risk_decisions SELECT 'risk-no-audit', proposal_id, "
                    "approved, reason_code, reason_codes, policy_version, "
                    "exposure_snapshot, decided_at, valid_until, trace_id, NULL "
                    "FROM risk_decisions WHERE id='risk-1'"
                )
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("unaudited risk decision succeeded")
    try:
        with ledger.transaction() as tx:
            tx.insert(
                m.ExecutionReport(
                    "report-2",
                    "order-1",
                    m.OrderStatus.UNKNOWN,
                    Decimal("1"),
                    Decimal("0"),
                    m.Reconciliation.PENDING,
                    now,
                    now,
                    trace,
                    broker_event_id="event-1",
                ),
                adapter="fake",
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("duplicate broker event succeeded")
    for role_name, statement in (
        ("atl_worker", "INSERT INTO positions SELECT * FROM positions WHERE id='pos-1'"),
        (
            "atl_control_api",
            "INSERT INTO broker_orders SELECT * FROM broker_orders WHERE id='order-1'",
        ),
        ("atl_watchdog", "UPDATE accounts SET ledger_version=1"),
        ("atl_execution", "UPDATE audit_events SET action='changed' WHERE id='audit-order-1'"),
    ):
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"SET ROLE {role_name}")
                connection.execute(text(statement))
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501"
        else:
            raise AssertionError(f"runtime role {role_name} gained forbidden write")
    with ledger.transaction() as tx:
        tx.insert(
            m.AuditEvent(
                "audit-account-1",
                now,
                "test",
                "account-open",
                ("TEST",),
                "paper-account",
                1,
                trace,
            )
        )
        tx.connection.execute(
            text(
                "INSERT INTO accounts (id, currency, ledger_version) "
                "VALUES ('paper-account', 'USD', 0)"
            )
        )
    with engine.connect() as first, engine.connect() as second:
        first_transaction = first.begin()
        second_transaction = second.begin()
        try:
            assert (
                first.execute(
                    text("SELECT id FROM accounts WHERE id='paper-account' FOR UPDATE")
                ).scalar_one()
                == "paper-account"
            )
            second.exec_driver_sql("SET LOCAL lock_timeout = '300ms'")
            try:
                second.execute(text("SELECT id FROM accounts WHERE id='paper-account' FOR UPDATE"))
            except DBAPIError as error:
                assert error.orig.sqlstate == "55P03"
            else:
                raise AssertionError("parallel account lock did not block")
        finally:
            second_transaction.rollback()
            first_transaction.commit()
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT id FROM accounts WHERE id='paper-account' FOR UPDATE")
            ).scalar_one()
            == "paper-account"
        )
    # Positive writes run with actual runtime privileges, not the migration owner.
    worker_decision = replace(typed_decision, id="worker-dec")
    worker_proposal = replace(proposal, id="worker-proposal", decision_id=worker_decision.id)
    with ledger.transaction() as tx:
        tx.connection.exec_driver_sql("SET LOCAL ROLE atl_worker")
        for entity in (worker_decision, worker_proposal):
            tx.insert(
                m.AuditEvent(
                    f"audit-{entity.id}",
                    now,
                    "worker",
                    "persist",
                    ("TEST",),
                    entity.id,
                    1,
                    trace,
                )
            )
            tx.insert(entity)
    with ledger.transaction() as tx:
        assert tx.get(m.DecisionResult, worker_decision.id) == worker_decision
        assert tx.get(m.RiskPolicy, policy.id) == policy
        assert tx.get(m.AgentRun, "run-1").output_id == proposal.id

    # A temporary audit cannot stand in for the canonical durable audit.
    try:
        with ledger.transaction() as tx:
            tx.connection.exec_driver_sql("SET LOCAL ROLE atl_worker")
            tx.connection.exec_driver_sql(
                "CREATE TEMP TABLE audit_events (entity_id TEXT, transaction_id BIGINT) "
                "ON COMMIT DROP"
            )
            tx.connection.exec_driver_sql(
                "INSERT INTO pg_temp.audit_events VALUES ('shadow-dec', txid_current())"
            )
            tx.insert(replace(typed_decision, id="shadow-dec"))
    except DBAPIError as error:
        assert error.orig.sqlstate == "P0001"
    else:
        raise AssertionError("temporary audit bypassed durable audit requirement")

    # The definer trigger must write a real position version even with a shadow table.
    latest_position = replace(newer_position, quantity=Decimal("3"), ledger_version=3)
    with ledger.transaction() as tx:
        tx.connection.exec_driver_sql("SET LOCAL ROLE atl_execution")
        tx.connection.exec_driver_sql(
            "CREATE TEMP TABLE position_versions (LIKE public.position_versions INCLUDING ALL) "
            "ON COMMIT DROP"
        )
        tx.insert(
            m.AuditEvent(
                "audit-position-3",
                now,
                "execution",
                "fill",
                ("TEST",),
                "pos-1",
                3,
                trace,
            )
        )
        assert tx.replace(latest_position, expected_version=2)
        assert (
            tx.connection.execute(
                text("SELECT count(*) FROM pg_temp.position_versions")
            ).scalar_one()
            == 0
        )
    with engine.begin() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM public.position_versions")).scalar_one()
            == 3
        )

    for entity in (
        replace(order, status=m.OrderStatus.ACKNOWLEDGED, version=2),
        replace(reservation, state=m.ReservationState.ACTIVE, version=2),
    ):
        with ledger.transaction() as tx:
            tx.connection.exec_driver_sql("SET LOCAL ROLE atl_execution")
            tx.insert(
                m.AuditEvent(
                    f"audit-update-{entity.id}",
                    now,
                    "execution",
                    "update",
                    ("TEST",),
                    entity.id,
                    2,
                    trace,
                )
            )
            assert tx.replace(entity, expected_version=1)

    try:
        with ledger.transaction() as tx:
            tx.insert(
                m.AuditEvent(
                    "audit-orphan-policy",
                    now,
                    "test",
                    "risk",
                    ("TEST",),
                    "orphan-policy",
                    1,
                    trace,
                )
            )
            tx.insert(replace(risk, id="orphan-policy", policy_version=999, reservation_id=None))
    except IntegrityError as error:
        assert error.orig.sqlstate == "23503"
    else:
        raise AssertionError("unknown risk policy version persisted")
    # No runtime role may attach an owner-rights trigger to an attacker-owned table.
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE atl_worker")
            connection.exec_driver_sql(
                "CREATE TEMP TABLE spoof_positions (LIKE public.positions INCLUDING ALL) "
                "ON COMMIT DROP"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER spoof_snapshot AFTER INSERT ON pg_temp.spoof_positions "
                "FOR EACH ROW EXECUTE FUNCTION public.ledger_snapshot_position()"
            )
    except DBAPIError as error:
        assert error.orig.sqlstate == "42501"
    else:
        raise AssertionError("worker could attach privileged snapshot trigger")
    # Even an owner-attached trigger rejects a noncanonical source relation.
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TEMP TABLE spoof_positions (LIKE public.positions INCLUDING ALL) "
                "ON COMMIT DROP"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER spoof_snapshot AFTER INSERT ON pg_temp.spoof_positions "
                "FOR EACH ROW EXECUTE FUNCTION public.ledger_snapshot_position()"
            )
            connection.exec_driver_sql(
                "INSERT INTO pg_temp.spoof_positions SELECT * FROM public.positions"
            )
    except DBAPIError as error:
        assert error.orig.sqlstate == "P0001"
    else:
        raise AssertionError("snapshot trigger accepted a noncanonical source")
    engine.dispose()
    print(
        "PG18 migration, typed roundtrip, replay snapshot, account lock, roles, "
        "immutability, deduplication: PASS"
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
        host()
    else:
        raise SystemExit("usage: ledger_support.py [guest]")
