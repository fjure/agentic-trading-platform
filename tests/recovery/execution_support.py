"""Private guest checks over real PostgreSQL and an exact process-local FakeBroker."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import sleep


class SimulatedCrash(BaseException):
    pass


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace, seed=None):
        return f"{namespace}:{seed}"


def checks(ledger, execution_ledger, engine, now):
    from sqlalchemy import create_engine, event, text

    from packages.brokers.fake import FakeBrokerAdapter, FakeFault
    from packages.domain import models as m
    from packages.persistence import PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.execution.intents import ExecutionProcess, SubmitDenied, submit_once

    scenario = sys.argv[2]
    proposal_id = "p-0"
    if scenario in ("expiry", "capability-expiry", "flat"):
        with ledger.transaction() as tx:
            original = tx.get(m.TradeProposal, "p-0")
            created = datetime.now(UTC)
            if scenario == "flat":
                position = tx.get(m.Position, "pos")
                tx.insert(
                    m.AuditEvent(
                        "position-flat",
                        created,
                        "fixture",
                        "fixture",
                        ("TEST",),
                        "pos",
                        2,
                        "a" * 32,
                    )
                )
                assert tx.replace(
                    replace(
                        position,
                        quantity=Decimal("1"),
                        average_price=Decimal("100"),
                        ledger_version=2,
                    ),
                    expected_version=1,
                )
                decision = tx.get(m.DecisionResult, original.decision_id)
                decision = replace(
                    decision,
                    id="flat-decision",
                    choice=m.Choice.FLAT,
                    distribution={
                        "LONG": Decimal("0.1"),
                        "FLAT": Decimal("0.8"),
                        "ABSTAIN": Decimal("0.1"),
                    },
                )
                tx.insert(
                    m.AuditEvent(
                        "decision-flat",
                        created,
                        "fixture",
                        "fixture",
                        ("TEST",),
                        decision.id,
                        1,
                        "a" * 32,
                    )
                )
                tx.insert(decision)
                proposal = replace(
                    original,
                    id="flat-proposal",
                    decision_id=decision.id,
                    target=m.Target.FLAT,
                    created_at=created,
                    valid_until=created + timedelta(seconds=30),
                )
            else:
                proposal = replace(
                    original,
                    id="short-lived",
                    created_at=created,
                    valid_until=created + timedelta(seconds=1),
                )
            tx.insert(
                m.AuditEvent(
                    "proposal-extra",
                    created,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    proposal.id,
                    1,
                    "a" * 32,
                )
            )
            tx.insert(proposal)
            proposal_id = proposal.id
    risk = reserve(
        ledger=execution_ledger, account_id="paper", proposal_id=proposal_id, limits=RiskLimits()
    )
    assert risk.approved
    with ledger.transaction() as tx:
        intent_id = tx.connection.execute(
            text("SELECT id FROM public.order_intents WHERE proposal_id=:proposal"),
            {"proposal": proposal_id},
        ).scalar_one()
    capabilities = m.BrokerCapabilities(
        "fake",
        "spy",
        1,
        paper=m.Capability.CONFIRMED,
        regular_session=m.Capability.CONFIRMED,
        market_order=m.Capability.CONFIRMED,
        limit_order=m.Capability.CONFIRMED,
        fractional=m.Capability.UNKNOWN,
        quantity_step=Decimal("1"),
        price_tick=Decimal("0.01"),
    )
    broker = FakeBrokerAdapter(
        clock=Clock(),
        ids=Ids(),
        capabilities={"spy": capabilities},
        session_open=lambda _i, _t: True,
        holdings={"spy": Decimal("1")} if scenario == "flat" else None,
    )
    original_submit = broker.submit
    calls = []
    capability_calls = []
    original_capabilities = broker.capabilities

    def capability_lookup(instrument):
        capability_calls.append(instrument)
        if scenario == "capability-expiry":
            sleep(1.1)
        return original_capabilities(instrument)

    broker.capabilities = capability_lookup
    pending_writer = []
    pool = ThreadPoolExecutor(max_workers=1)

    def update_context(**changes):
        with ledger.transaction() as tx:
            row = (
                tx.connection.execute(
                    text(
                        "SELECT * FROM public.risk_account_context "
                        "WHERE account_id='paper' FOR UPDATE"
                    )
                )
                .mappings()
                .one()
            )
            version = row["version"] + 1
            tx.insert(
                m.AuditEvent(
                    f"ctx-{version}",
                    datetime.now(UTC),
                    "fixture",
                    "fixture",
                    ("TEST",),
                    "paper",
                    version,
                    "a" * 32,
                )
            )
            changes.update(version=version, actor_id="fixture", trace_id="a" * 32)
            assignments = ",".join(f"{name}=:{name}" for name in changes)
            tx.connection.execute(
                text(
                    f"UPDATE public.risk_account_context SET {assignments} WHERE account_id='paper'"
                ),
                changes,
            )

    def submit(item):
        # Distinct connection proves SUBMITTING was committed before any I/O.
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT status FROM public.broker_orders WHERE intent_id=:id"),
                    {"id": item.id},
                ).scalar_one()
                == "SUBMITTING"
            )
        calls.append(item.client_order_id)
        if scenario == "pause-race":

            def pause_writer():
                with engine.connect() as connection:
                    pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
                    pending_writer.append(pid)
                    connection.commit()
                update_context(safety_state="PAUSE_NEW")

            future = pool.submit(pause_writer)
            for _ in range(100):
                if pending_writer:
                    with engine.connect() as observer:
                        blocked = observer.execute(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                                "WHERE cardinality(pg_blocking_pids(pid))>0 "
                                "AND datname=current_database())"
                            )
                        ).scalar_one()
                    if blocked:
                        break
                sleep(0.01)
            else:
                raise AssertionError("pause mutation was not blocked by submit gate")
            assert not future.done()
            pending_writer.append(future)
        if scenario == "secret-error":
            raise RuntimeError("SENTINEL_SECRET_DO_NOT_LEAK")
        if scenario == "crash-before":
            raise SimulatedCrash()
        result = original_submit(item)
        if scenario == "crash-after":
            raise SimulatedCrash()
        if scenario == "mismatch":
            return replace(result, client_order_id="wrong-client")
        return result

    broker.submit = submit

    def attempt(target_ledger=execution_ledger, enabled=True):
        return submit_once(
            ledger=target_ledger,
            account_id="paper",
            intent_id=intent_id,
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=enabled,
            owner_id="fixture",
            fencing_token=1,
        )

    if scenario == "startup":
        process = ExecutionProcess(
            ledger=execution_ledger,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
        )
        with engine.connect() as observer:
            context = observer.execute(
                text(
                    "SELECT safety_state,reconciliation_current,version "
                    "FROM public.risk_account_context WHERE account_id='paper'"
                )
            ).one()
            assert context.safety_state == "PAUSE_NEW" and not context.reconciliation_current
        try:
            process.submit(intent_id)
        except SubmitDenied:
            pass
        else:
            raise AssertionError("restart allowed automatic submit")
        assert not calls and not capability_calls
        ExecutionProcess(
            ledger=execution_ledger, account_id="paper", limits=RiskLimits(), broker=broker
        )
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT version FROM public.risk_account_context WHERE account_id='paper'")
                ).scalar_one()
                >= context.version
            )
        update_context(safety_state="HALTED", reconciliation_current=True)
        ExecutionProcess(
            ledger=execution_ledger, account_id="paper", limits=RiskLimits(), broker=broker
        )
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text(
                        "SELECT safety_state FROM public.risk_account_context "
                        "WHERE account_id='paper'"
                    )
                ).scalar_one()
                == "HALTED"
            )
        print("PASS startup: audited pause and reconciliation required before submit")
        return

    if scenario == "guards":

        def denied():
            try:
                attempt()
            except SubmitDenied:
                pass
            else:
                raise AssertionError("changed safety evidence allowed submit")
            assert not calls and not capability_calls

        with ledger.transaction() as tx:
            reservation = tx.get(m.RiskReservation, risk.reservation_id)
            tx.insert(
                m.AuditEvent(
                    "underfunded", now, "fixture", "fixture", ("TEST",), reservation.id, 2, "a" * 32
                )
            )
            assert tx.replace(
                replace(reservation, reserved_notional=Decimal("0"), version=2), expected_version=1
            )
        denied()
        with ledger.transaction() as tx:
            tx.insert(
                m.AuditEvent(
                    "funded", now, "fixture", "fixture", ("TEST",), reservation.id, 3, "a" * 32
                )
            )
            assert tx.replace(replace(reservation, version=3), expected_version=2)
        update_context(portfolio_version=2)
        denied()
        update_context(portfolio_version=1, reconciliation_current=False)
        denied()
        update_context(reconciliation_current=True, valid_until=now - timedelta(seconds=1))
        denied()
        update_context(valid_until=now + timedelta(minutes=5))
        for version, capability_version, session_open in (
            (2, 2, True),
            (3, 1, False),
            (4, 1, True),
        ):
            with ledger.transaction() as tx:
                tx.insert(
                    m.AuditEvent(
                        f"cap-{version}",
                        now,
                        "fixture",
                        "fixture",
                        ("TEST",),
                        "paper:spy",
                        version,
                        "a" * 32,
                    )
                )
                tx.connection.execute(
                    text(
                        "UPDATE public.risk_instrument_context SET version=:version,"
                        "capability_version=:cap,session_open=:session "
                        "WHERE account_id='paper' AND instrument_id='spy'"
                    ),
                    {"version": version, "cap": capability_version, "session": session_open},
                )
            if version < 4:
                denied()
        with ledger.transaction() as tx:
            policy = tx.get(m.RiskPolicy, "policy")
            updated = replace(policy, id="policy2", version=2, audit_event_id="policy2-audit")
            tx.insert(
                m.AuditEvent(
                    "policy2-audit", now, "operator", "policy", ("TEST",), updated.id, 2, "a" * 32
                )
            )
            tx.insert(updated)
        denied()
        print(
            "PASS guards: underreservation, portfolio, reconciliation, "
            "context expiry and policy version"
        )
        return

    if scenario == "pause-gap":
        paused_after_commit = []
        with execution_ledger.transaction() as tx:
            tx.connection.execute(
                text(
                    "UPDATE public.execution_owners SET heartbeat_at=clock_timestamp() "
                    "WHERE account_id='paper' AND owner_id='fixture' AND fencing_token=1"
                )
            )

        @event.listens_for(execution_ledger.engine, "begin")
        def pause_between_stages(_connection):
            # Observe the transaction boundary itself. The identity query now
            # includes database/login and must not control whether the race runs.
            if not paused_after_commit:
                with engine.connect() as observer:
                    persisted = observer.execute(
                        text("SELECT status FROM public.broker_orders WHERE intent_id=:id"),
                        {"id": intent_id},
                    ).scalar_one_or_none()
                if persisted == "SUBMITTING":
                    paused_after_commit.append(True)
                    update_context(safety_state="PAUSE_NEW")

        try:
            attempt()
        except SubmitDenied:
            pass
        else:
            raise AssertionError("pause between transactions allowed submit")
        assert paused_after_commit and not calls and not capability_calls
        with engine.connect() as observer:
            assert (
                observer.execute(text("SELECT status FROM public.broker_orders")).scalar_one()
                == "REJECTED"
            )
        print("PASS pause between commit and submit: second check rejected; zero I/O")
        return

    if scenario in ("expiry", "capability-expiry"):
        if scenario == "expiry":
            sleep(1.1)
        try:
            attempt()
        except SubmitDenied as exc:
            assert exc.reason_code == "EXPIRED"
        else:
            raise AssertionError("expired intent contacted broker")
        assert calls == []
        assert bool(capability_calls) == (scenario == "capability-expiry")
        print(f"PASS {scenario}: expiry before I/O")
        return

    if scenario == "denials":
        try:
            attempt(enabled=False)
        except SubmitDenied:
            pass
        else:
            raise AssertionError("disabled smoke allowed submit")
        worker_engine = create_engine(engine.url, hide_parameters=True)

        @event.listens_for(worker_engine, "connect")
        def set_worker(dbapi_connection, _record):
            dbapi_connection.execute("SET ROLE atl_worker")
            dbapi_connection.commit()

        try:
            attempt(PostgresLedger(engine=worker_engine))
        except SubmitDenied:
            pass
        else:
            raise AssertionError("worker DB role allowed submit")
        finally:
            worker_engine.dispose()
        with engine.connect() as observer:
            next_context_version = observer.execute(
                text("SELECT version+1 FROM public.risk_account_context WHERE account_id='paper'")
            ).scalar_one()
        for version, (mode, safety) in enumerate(
            (
                ("DISABLED", "RUNNING"),
                ("REPLAY", "RUNNING"),
                ("SHADOW", "RUNNING"),
                ("PAPER", "PAUSE_NEW"),
                ("PAPER", "HALTED"),
            ),
            start=next_context_version,
        ):
            with ledger.transaction() as tx:
                tx.insert(
                    m.AuditEvent(
                        f"context-{version}",
                        now,
                        "fixture",
                        "control.pause",
                        ("TEST",),
                        "paper",
                        version,
                        "a" * 32,
                    )
                )
                tx.connection.execute(
                    text(
                        "UPDATE public.risk_account_context SET version=:version,"
                        "mode=:mode,safety_state=:safety WHERE account_id='paper'"
                    ),
                    {"version": version, "mode": mode, "safety": safety},
                )
            try:
                attempt()
            except SubmitDenied:
                pass
            else:
                raise AssertionError("unsafe mode or safety allowed submit")
        assert calls == []
        print("PASS modes, pause, disabled smoke and actual worker role: zero submits")
        return

    if scenario == "timeout":
        broker.inject_once(FakeFault.TIMEOUT_AFTER_ACCEPT)
    if scenario.startswith("crash"):
        try:
            attempt()
        except SimulatedCrash:
            pass
        else:
            raise AssertionError("crash did not escape process boundary")
    else:
        order = attempt()
        assert order.status is (
            m.OrderStatus.UNKNOWN
            if scenario in ("timeout", "mismatch", "secret-error")
            else m.OrderStatus.ACKNOWLEDGED
        )
    assert len(calls) == 1
    if scenario == "pause-race":
        pending_writer[-1].result(timeout=5)
        pool.shutdown(wait=True)
    try:
        attempt()
    except SubmitDenied:
        pass
    assert len(calls) == 1, "recovery blindly submitted again"
    if scenario in ("timeout", "mismatch", "secret-error") or scenario.startswith("crash"):
        with engine.connect() as observer:
            reservation = observer.execute(
                text(
                    "SELECT state,reserved_notional FROM public.risk_reservations "
                    "WHERE proposal_id='p-0'"
                )
            ).one()
            assert reservation.state in ("ACTIVE", "HELD_UNKNOWN")
            assert reservation.reserved_notional == Decimal("100.01")
    if scenario == "secret-error":
        with engine.connect() as observer:
            for table in ("audit_events", "execution_reports", "outbox_events"):
                assert not observer.execute(
                    text(
                        f"SELECT EXISTS(SELECT 1 FROM public.{table} t "
                        "WHERE row_to_json(t)::text LIKE '%SENTINEL_SECRET%')"
                    )
                ).scalar_one()
    print(f"PASS {scenario}: durable intent; one request maximum; no blind recovery retry")


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
