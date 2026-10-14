"""Isolated Docker guest; credentials travel only on stdin and are never logged."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


def host(*, guest_path: Path | None = None, guest_args: tuple[str, ...] = ()) -> str:
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    suffix = uuid.uuid4().hex[:20]
    database = f"atl_risk_{suffix}"
    role = f"atl_risk_{suffix}"
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
        (root / "config/agents/jev.yaml", "/opt/lab/config/agents/jev.yaml"),
        (root / "alembic.ini", "/opt/lab/alembic.ini"),
        (Path(__file__).resolve(), "/opt/lab/risk_support.py"),
    ):
        command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
    if guest_path is not None:
        guest_path = guest_path.resolve()
        if not guest_path.is_relative_to(root / "tests"):
            raise ValueError("guest test must be inside repository tests")
        command.extend(
            ("--mount", f"type=bind,source={guest_path},target=/opt/lab/test_guest.py,readonly")
        )
    command.extend(
        (
            "agentic-trading-lab-dev-build-smoke:latest",
            "test_guest.py" if guest_path else "risk_support.py",
            "guest",
        )
    )
    command.extend(guest_args)
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
            diagnostic = result.stderr.replace(password, "[redacted]").splitlines()
            detail = " | ".join(diagnostic[-7:])[:500]
            raise RuntimeError(f"isolated risk verification failed: {detail}")
        return result.stdout.strip()
    finally:
        if created:
            _admin(f"DROP DATABASE {database} WITH (FORCE);")
        _admin(f"DROP ROLE {role};")


def guest(run_checks=None) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.domain import models as m
    from packages.persistence import PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve

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
    engine = create_engine(url, hide_parameters=True, pool_size=20, max_overflow=0)
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    now = datetime.now(UTC).replace(microsecond=0)
    trace = "a" * 32
    instrument = m.Instrument("spy", m.Market.US_EQUITY, "SPY", "EQUITY", "USD", 1, "XNYS")
    bar = m.MarketBar(
        "bar",
        "spy",
        now - timedelta(minutes=15),
        now,
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("1"),
        now,
        now,
        now,
        m.Quality.VALID,
    )
    evidence = m.EvidenceBundle("ev", "spy", ("bar",), now, (), m.Quality.VALID)
    position = m.Position("pos", "spy", "USD", Decimal("0"), None, Decimal("0"), 1)
    portfolio = m.PortfolioSnapshot(
        "pf",
        "USD",
        Decimal("2000"),
        Decimal("100.01"),
        Decimal("0"),
        now,
        1,
        m.Quality.VALID,
        m.Reconciliation.CURRENT,
    )
    state = m.MarketState(
        "ms",
        "spy",
        m.Market.US_EQUITY,
        bar,
        evidence,
        portfolio,
        position,
        {},
        Decimal("0.01"),
        now,
        now,
        now,
        now,
        m.Quality.VALID,
        trace,
    )
    typed = m.TypedAnswers(
        regime=m.ChoiceAnswer(
            "q1",
            "TREND",
            Decimal("0.8"),
            {"TREND": Decimal("0.8"), "RANGE": Decimal("0.1"), "UNCERTAIN": Decimal("0.1")},
        ),
        evidence_quality=m.ScoreAnswer(
            "q2",
            Decimal("8"),
            {"0": "weak", "9": "strong"},
            {"0": Decimal("0.2"), "9": Decimal("0.8")},
            Decimal("0.8"),
        ),
        signal_conflict=m.NoulAnswer("q3", Decimal("0.1")),
    )
    decision = m.DecisionResult(
        "dec",
        "ms",
        m.Choice.LONG,
        "mock-v1",
        "prompt-v1",
        "questions-v1",
        "config-v1",
        "b" * 64,
        1,
        now + timedelta(minutes=5),
        trace,
        "OK",
        confidence=Decimal("0.8"),
        distribution={"LONG": Decimal("0.8"), "FLAT": Decimal("0.1"), "ABSTAIN": Decimal("0.1")},
        typed_answers=typed,
        regime="TREND",
        evidence_quality=Decimal("0.8"),
        signal_conflict=Decimal("0.1"),
    )
    policy = m.RiskPolicy(
        "policy",
        1,
        "USD",
        1,
        2,
        Decimal("0.10"),
        Decimal("0.01"),
        now,
        "operator",
        "audit-policy",
    )
    ledger = PostgresLedger(engine=engine)
    with ledger.transaction() as tx:
        tx.insert(instrument)
        tx.insert(bar)
        tx.insert(evidence)
        tx.insert(
            m.AuditEvent(
                "audit-paper",
                now,
                "fixture",
                "account.create",
                ("FIXTURE",),
                "paper",
                1,
                trace,
            )
        )
        tx.connection.execute(
            text("""
            INSERT INTO public.accounts(id,currency,ledger_version) VALUES ('paper','USD',1)
        """)
        )
        for item in (position, portfolio, decision, policy):
            tx.insert(
                m.AuditEvent(
                    id="audit-" + item.id,
                    occurred_at=now,
                    actor_id="fixture",
                    action="fixture",
                    reason_codes=("FIXTURE",),
                    entity_id=item.id,
                    entity_version=1,
                    trace_id=trace,
                )
            )
            if item is decision:
                tx.insert(state)
            if item is position:
                tx.connection.execute(
                    text("""
                    INSERT INTO public.positions
                      (id,instrument_id,currency,quantity,average_price,
                       realized_pnl,ledger_version,account_id)
                    VALUES ('pos','spy','USD',0,NULL,0,1,'paper')
                """)
                )
            else:
                tx.insert(item)
        instrument2 = replace(instrument, id="qqq", broker_symbol="QQQ")
        bar2 = replace(bar, id="bar2", instrument_id="qqq")
        evidence2 = replace(evidence, id="ev2", instrument_id="qqq", sources=("bar2",))
        position2 = replace(position, id="pos2", instrument_id="qqq")
        state2 = replace(
            state,
            id="ms2",
            instrument_id="qqq",
            bar=bar2,
            evidence=evidence2,
            position=position2,
        )
        decision2 = replace(decision, id="dec2", market_state_id="ms2")
        for item in (instrument2, bar2, evidence2):
            tx.insert(item)
        for item in (position2, decision2):
            tx.insert(
                m.AuditEvent(
                    id="audit-" + item.id,
                    occurred_at=now,
                    actor_id="fixture",
                    action="fixture",
                    reason_codes=("FIXTURE",),
                    entity_id=item.id,
                    entity_version=1,
                    trace_id=trace,
                )
            )
        tx.connection.execute(
            text("""
            INSERT INTO public.positions
              (id,instrument_id,currency,quantity,average_price,
               realized_pnl,ledger_version,account_id)
            VALUES ('pos2','qqq','USD',0,NULL,0,1,'paper')
        """)
        )
        tx.insert(state2)
        tx.insert(decision2)
        for item in ("paper", "paper:spy", "paper:qqq"):
            tx.insert(
                m.AuditEvent(
                    id="audit-context-" + item,
                    occurred_at=now,
                    actor_id="fixture",
                    action="risk.context",
                    reason_codes=("FIXTURE",),
                    entity_id=item,
                    entity_version=1,
                    trace_id=trace,
                )
            )
        tx.connection.execute(
            text("""
            INSERT INTO public.risk_account_context
              (account_id,version,portfolio_snapshot_id,portfolio_version,
               risk_config_version,day_start_at,day_start_equity,day_start_realized_pnl,mode,
               safety_state,reconciliation_current,valid_until,actor_id,trace_id)
            VALUES ('paper',1,'pf',1,'paper-risk-v1',:day,2000,0,
                    'PAPER','RUNNING',true,:until,'fixture',:trace)
        """),
            {
                "day": now.replace(hour=0, minute=0, second=0),
                "until": now + timedelta(minutes=5),
                "trace": trace,
            },
        )
        tx.connection.execute(
            text("""
            INSERT INTO public.risk_instrument_context
              (account_id,instrument_id,version,capability_version,broker_id,
               paper_confirmed,regular_session_confirmed,market_order_confirmed,
               limit_order_confirmed,fractional_confirmed,quantity_step,price_tick,
               session_open,session_version,session_valid_until,valid_until,actor_id,trace_id)
            VALUES ('paper','spy',1,1,'fake',true,true,true,true,false,1,0.01,
                    true,'fixture-session',:until,:until,'fixture',:trace)
        """),
            {"until": now + timedelta(minutes=5), "trace": trace},
        )
        tx.connection.execute(
            text("""
            INSERT INTO public.risk_instrument_context
              (account_id,instrument_id,version,capability_version,broker_id,
               paper_confirmed,regular_session_confirmed,market_order_confirmed,
               limit_order_confirmed,fractional_confirmed,quantity_step,price_tick,
               session_open,session_version,session_valid_until,valid_until,actor_id,trace_id)
            VALUES ('paper','qqq',1,1,'fake',true,true,true,true,false,1,0.01,
                    true,'fixture-session',:until,:until,'fixture',:trace)
        """),
            {"until": now + timedelta(minutes=5), "trace": trace},
        )
        for index in range(100):
            second = index % 2 == 1
            proposal = m.TradeProposal(
                f"p-{index}",
                "dec2" if second else "dec",
                "ms2" if second else "ms",
                "qqq" if second else "spy",
                m.Target.LONG,
                Decimal("1"),
                now,
                now + timedelta(seconds=30),
                trace,
            )
            tx.insert(
                m.AuditEvent(
                    f"audit-p-{index}",
                    now,
                    "fixture",
                    "proposal",
                    ("FIXTURE",),
                    proposal.id,
                    1,
                    trace,
                )
            )
            tx.insert(proposal)

    role_engine = create_engine(url, hide_parameters=True, pool_size=20, max_overflow=0)

    @event.listens_for(role_engine, "connect")
    def set_execution_role(dbapi_connection, _connection_record):
        dbapi_connection.execute("SET ROLE atl_execution")
        dbapi_connection.commit()

    execution_ledger = PostgresLedger(engine=role_engine)

    # Explicit synthetic bootstrap evidence for the mandatory Runtime gate.
    # Runtime itself claims/fences a process and pauses; fixtures start RUNNING.
    with execution_ledger.transaction() as tx:
        tx.connection.execute(text("""
            INSERT INTO public.execution_owners
              (account_id,owner_id,fencing_token,claimed_at,heartbeat_at,trace_id)
            VALUES ('paper','fixture',1,clock_timestamp(),clock_timestamp(),:trace)
        """), {"trace": trace})

    if run_checks is not None:
        run_checks(ledger, execution_ledger, engine, now)
        role_engine.dispose()
        engine.dispose()
        return

    def run(index):
        return reserve(
            ledger=execution_ledger,
            account_id="paper",
            proposal_id=f"p-{index}",
            limits=RiskLimits(),
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        decisions = list(pool.map(run, range(100)))
    assert len(decisions) == 100
    assert sum(item.approved for item in decisions) == 1
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM public.risk_decisions")).scalar_one()
            == 100
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.risk_reservations")).scalar_one()
            == 1
        )
        assert connection.execute(
            text("""
            SELECT reserved_notional FROM public.risk_reservations
        """)
        ).scalar_one() == Decimal("100.01")
        assert (
            connection.execute(
                text("""
            SELECT count(*) FROM public.risk_decisions WHERE reason_code='CASH_LIMIT'
        """)
            ).scalar_one()
            > 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.order_intents")).scalar_one() == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.outbox_events")).scalar_one()
            == 101
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM public.audit_events WHERE action='risk.decide'")
            ).scalar_one()
            == 100
        )
    first = next(item for item in decisions if item.approved)
    assert run(int(first.proposal_id.split("-")[1])) == first
    for table in ("positions", "risk_reservations", "order_intents"):
        try:
            with engine.begin() as connection:
                connection.execute(text(f"UPDATE public.{table} SET account_id=NULL"))
        except DBAPIError:
            pass
        else:
            raise AssertionError("economic ownership removal committed")
    for group in ("atl_worker", "atl_control_api", "atl_watchdog"):
        try:
            with engine.begin() as connection:
                connection.execute(text(f"SET LOCAL ROLE {group}"))
                connection.execute(text("UPDATE public.risk_account_context SET version=2"))
        except DBAPIError:
            pass
        else:
            raise AssertionError("non-execution role changed risk context")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE public.risk_account_context SET version=2 WHERE account_id='paper'
            """)
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("risk context update without audit committed")
    with engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO public.audit_events
              (id,occurred_at,actor_id,action,reason_codes,entity_id,
               entity_version,trace_id,policy_version,outcome)
            VALUES ('audit-context-paper-v2',:now,'fixture','risk.context',
                    '["REFRESH"]'::jsonb,'paper',2,:trace,1,'CURRENT')
        """),
            {"now": now, "trace": trace},
        )
        connection.execute(
            text("""
            UPDATE public.risk_account_context SET version=2 WHERE account_id='paper'
        """)
        )
    try:
        with engine.begin() as connection:
            connection.execute(
                text("""
                UPDATE public.risk_account_context
                SET version=3,account_id='other' WHERE account_id='paper'
            """)
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("risk context identity change committed")
    # A reserved SELL consumes inventory even before a broker order exists.
    with ledger.transaction() as tx:
        tx.insert(
            m.AuditEvent(
                "audit-pos-v2", now, "fixture", "position.fill", ("FIXTURE",), "pos", 2, trace
            )
        )
        assert tx.replace(
            replace(
                position, quantity=Decimal("1"), average_price=Decimal("100"), ledger_version=2
            ),
            expected_version=1,
        )
        flat_decision = replace(
            decision,
            id="dec-flat",
            choice=m.Choice.FLAT,
            distribution={
                "LONG": Decimal("0.1"),
                "FLAT": Decimal("0.8"),
                "ABSTAIN": Decimal("0.1"),
            },
        )
        tx.insert(
            m.AuditEvent(
                "audit-dec-flat",
                now,
                "fixture",
                "decision",
                ("FIXTURE",),
                flat_decision.id,
                1,
                trace,
            )
        )
        tx.insert(flat_decision)
        for index in range(2):
            proposal = m.TradeProposal(
                f"flat-{index}",
                flat_decision.id,
                state.id,
                instrument.id,
                m.Target.FLAT,
                Decimal("1"),
                now,
                now + timedelta(seconds=30),
                trace,
            )
            tx.insert(
                m.AuditEvent(
                    f"audit-flat-{index}",
                    now,
                    "fixture",
                    "proposal",
                    ("FIXTURE",),
                    proposal.id,
                    1,
                    trace,
                )
            )
            tx.insert(proposal)
    first_exit = reserve(
        ledger=execution_ledger, account_id="paper", proposal_id="flat-0", limits=RiskLimits()
    )
    second_exit = reserve(
        ledger=execution_ledger, account_id="paper", proposal_id="flat-1", limits=RiskLimits()
    )
    assert first_exit.approved
    assert not second_exit.approved and "EXIT_QUANTITY" in second_exit.reason_codes
    print("PASS 100 serialized decisions; 1 reservation; 100 audits; 101 outbox events")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "guest":
        guest()
    else:
        print(host())
