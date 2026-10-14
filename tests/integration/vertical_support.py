"""PTV-13 private guest: one synthetic market-to-Fake trade on isolated PostgreSQL.

The rolling session is explicitly test-only. It aligns two closed 15-minute
bars with the actual database clock, so the 120-second production freshness
gate is exercised regardless of the host's time of day.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal


class Crash(BaseException):
    """Simulate process termination without running submit exception handlers."""


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace, seed=None):
        return hashlib.sha256(f"ptv13:{namespace}:{seed}".encode()).hexdigest()


def _role(engine, name):
    from sqlalchemy import create_engine, event

    from packages.persistence import PostgresLedger

    role_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(role_engine, "connect")
    def assign(connection, _record):
        connection.execute(f"SET ROLE {name}")
        connection.commit()

    return PostgresLedger(engine=role_engine), role_engine


def _fixture_config(now, instrument):
    from services.worker.market_state import PaperMarketConfig

    class SyntheticSessionConfig(PaperMarketConfig):
        """Test clock session; the market builder still validates bar quality."""

        def session_bounds(self, moment):
            local = moment.astimezone(self.zone)
            return (
                datetime.combine(local.date(), self.regular_open, self.zone).astimezone(UTC),
                datetime.combine(local.date(), self.regular_close, self.zone).astimezone(UTC),
            )

    # A versioned local session whose opening precedes two complete bars.
    # It is deliberately distinct from config/markets/paper.yaml and XNYS.
    offset = timezone(timedelta(hours=12 - now.hour))
    local_now = now.astimezone(offset)
    opening = local_now - timedelta(minutes=30, seconds=10)
    closing = local_now + timedelta(hours=2)
    return SyntheticSessionConfig(
        version="ptv13-synthetic-session-v1",
        fingerprint=hashlib.sha256(b"ptv13-synthetic-session-v1").hexdigest(),
        source_id="ptv13-local-fixture-v1",
        interval="PT15M",
        max_age_seconds=120,
        max_spread_fraction=Decimal("0.01"),
        required_bars=2,
        instruments={instrument.id: instrument},
        calendar_start=local_now.date(),
        calendar_end=local_now.date(),
        holidays=frozenset(),
        early_closes={},
        zone=offset,
        regular_open=time(opening.hour, opening.minute, opening.second, opening.microsecond),
        regular_close=time(closing.hour, closing.minute, closing.second, closing.microsecond),
    )


def _market(admin, worker, instrument_id, trace, label, prices=("99", "100")):
    from packages.domain import models as m
    from services.worker.market_state import build_market_state

    with admin.transaction() as tx:
        now = tx.connection.exec_driver_sql("SELECT clock_timestamp()").scalar_one().astimezone(UTC)
        instrument = tx.get(m.Instrument, instrument_id)
        position = tx.get(m.Position, "pos" if instrument_id == "spy" else "pos2")
        context = tx.connection.exec_driver_sql(
            "SELECT portfolio_snapshot_id FROM public.risk_account_context WHERE account_id='paper'"
        ).scalar_one()
        portfolio = tx.get(m.PortfolioSnapshot, context)
        assert instrument is not None and position is not None and portfolio is not None
    config = _fixture_config(now, instrument)
    end = now - timedelta(seconds=10)
    bars = []
    for index, price in enumerate(prices):
        price = Decimal(price)
        close = end - timedelta(minutes=15 * (1 - index))
        bars.append(
            m.MarketBar(
                id=f"ptv13-{label}-{instrument_id}-bar-{index}",
                instrument_id=instrument_id,
                start_at=close - timedelta(minutes=15),
                end_at=close,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=Decimal("1000"),
                event_time=close,
                received_at=close + timedelta(seconds=1),
                available_at=close + timedelta(seconds=2),
                quality=m.Quality.VALID,
            )
        )
    state = build_market_state(
        config=config,
        instrument_id=instrument_id,
        bars=tuple(bars),
        as_of=now,
        spread=Decimal("0.01"),
        portfolio=portfolio,
        position=position,
        trace_id=trace,
    )
    if label == "trade":
        try:
            build_market_state(
                config=config,
                instrument_id=instrument_id,
                bars=(bars[0], replace(bars[1], closed=False)),
                as_of=now,
                spread=Decimal("0.01"),
                portfolio=portfolio,
                position=position,
                trace_id=trace,
            )
        except ValueError as error:
            assert str(error) == "only closed 15-minute bars are admissible"
        else:
            raise AssertionError("open or invalid bar admitted")
        corrected = build_market_state(
            config=config,
            instrument_id=instrument_id,
            bars=(
                bars[0],
                replace(bars[1], id=bars[1].id + "-corrected", version=2, close=Decimal("100.01")),
            ),
            as_of=now,
            spread=Decimal("0.01"),
            portfolio=portfolio,
            position=position,
            trace_id=trace,
        )
        assert corrected.id != state.id
        from packages.decisions.mock import MockDecisionEngine

        fixed_clock = type("SnapshotClock", (), {"now": lambda self: now})()
        model = MockDecisionEngine(clock=fixed_clock, ids=Ids())
        original = model.decide(state, "ptv13-mock-v1")
        assert original == model.decide(state, "ptv13-mock-v1")
        assert original.input_hash != model.decide(corrected, "ptv13-mock-v1").input_hash
    with worker.transaction() as tx:
        for bar in bars:
            tx.insert(bar)
        tx.insert(state.evidence)
        tx.insert(state)
        if label == "trade":
            import json

            from packages.persistence.ledger import _encode

            before = json.dumps(_encode(tx.get(m.MarketState, state.id)), sort_keys=True)
            tx.insert(corrected.bar)
            tx.insert(corrected.evidence)
            tx.insert(corrected)
            after = json.dumps(_encode(tx.get(m.MarketState, state.id)), sort_keys=True)
            assert before == after
            assert tx.get(m.MarketState, corrected.id).bar.close == Decimal("100.01")
    assert state.evidence.neutral and state.features["previous_close"] == Decimal(prices[0])
    assert state.features["last_close"] == Decimal(prices[1])
    return state


def _proposal(worker, state):
    from packages.decisions.mock import MockDecisionEngine
    from packages.domain import models as m
    from services.worker.planner import PlannerConfig, plan_proposal

    clock, ids = Clock(), Ids()
    decision = MockDecisionEngine(clock=clock, ids=ids).decide(state, "ptv13-mock-v1")
    assert decision.choice is m.Choice.LONG
    proposal = plan_proposal(
        decision=decision,
        market_state=state,
        config=PlannerConfig("ptv13-fixed-one-v1", Decimal("1")),
        clock=clock,
        ids=ids,
        ledger=worker,
    )
    assert proposal is not None and proposal.quantity == 1
    return decision, proposal


def _intent(ledger, proposal_id):
    from sqlalchemy import text

    from packages.domain import models as m

    with ledger.transaction() as tx:
        ident = tx.connection.execute(
            text("SELECT id FROM public.order_intents WHERE proposal_id=:p"),
            {"p": proposal_id},
        ).scalar_one()
        return tx.get(m.OrderIntent, ident)


def _broker():
    from packages.brokers.fake import FakeBrokerAdapter
    from packages.domain import models as m

    caps = {
        item: m.BrokerCapabilities(
            "fake",
            item,
            1,
            paper=m.Capability.CONFIRMED,
            regular_session=m.Capability.CONFIRMED,
            market_order=m.Capability.CONFIRMED,
            limit_order=m.Capability.CONFIRMED,
            fractional=m.Capability.UNKNOWN,
            quantity_step=Decimal("1"),
            price_tick=Decimal("0.01"),
        )
        for item in ("spy", "qqq")
    }
    return FakeBrokerAdapter(
        clock=Clock(),
        ids=Ids(),
        capabilities=caps,
        session_open=lambda _instrument, _now: True,
    )


def _trace_proof(ledger, trace, proposal, decision, risk, intent, order):
    from sqlalchemy import text

    with ledger.transaction() as tx:
        chain = tx.connection.execute(
            text("""
            SELECT ms.id, d.id, p.id, rd.id, rr.id, i.id, b.id
            FROM public.market_states ms
            JOIN public.decisions d ON d.market_state_id=ms.id
            JOIN public.trade_proposals p ON p.decision_id=d.id
            JOIN public.risk_decisions rd ON rd.proposal_id=p.id
            JOIN public.risk_reservations rr ON rr.id=rd.reservation_id
            JOIN public.order_intents i ON i.risk_decision_id=rd.id
            JOIN public.broker_orders b ON b.intent_id=i.id
            WHERE ms.trace_id=:trace AND d.trace_id=:trace
              AND p.trace_id=:trace AND rd.trace_id=:trace
              AND rr.trace_id=:trace AND i.trace_id=:trace AND b.trace_id=:trace
              AND p.id=:proposal
        """),
            {"trace": trace, "proposal": proposal.id},
        ).one()
        assert chain == (
            proposal.market_state_id,
            decision.id,
            proposal.id,
            risk.id,
            risk.reservation_id,
            intent.id,
            order.id,
        )
        audits = tx.connection.execute(
            text("""
            SELECT entity_id,action,actor_id,reason_codes,policy_version,occurred_at
            FROM public.audit_events WHERE trace_id=:trace
        """),
            {"trace": trace},
        ).all()
        for entity, action in (
            (decision.id, "decision.persist"),
            (proposal.id, "proposal.persist"),
            (risk.id, "risk.decide"),
            (risk.reservation_id, "risk.reserve"),
            (intent.id, "intent.create"),
            (order.id, "execution.submitting"),
        ):
            rows = [row for row in audits if row.entity_id == entity and row.action == action]
            assert len(rows) == 1, (entity, action)
            assert (
                rows[0].actor_id
                and rows[0].reason_codes
                and rows[0].occurred_at.utcoffset() == timedelta(0)
            )
            if action not in {"decision.persist", "proposal.persist"}:
                assert rows[0].policy_version == 1
        events = tx.connection.execute(
            text("""
            SELECT event_type,aggregate_id,aggregate_version,payload
            FROM public.outbox_events WHERE trace_id=:trace
        """),
            {"trace": trace},
        ).all()
        assert any(
            row.event_type == "execution.reported.v1" and row.payload["order_id"] == order.id
            for row in events
        )
        for row in events:
            if row.event_type == "execution.reported.v1":
                assert row.aggregate_id == row.payload["id"] and row.aggregate_version == 1


def _deliver_after_crash(execution, admin, trace):
    from sqlalchemy import text

    from packages.observability.outbox import Outbox

    delivered = []

    def fail(event):
        if event.trace_id == trace and event.event_type == "execution.reported.v1":
            delivered.append(event.event_id)
            raise Crash()

    try:
        while Outbox(execution.engine).publish_one(fail) is not None:
            pass
    except Crash:
        pass
    else:
        raise AssertionError("delivery crash was not injected")
    with admin.transaction() as tx:
        assert (
            tx.connection.execute(
                text("SELECT published_at FROM public.outbox_events WHERE event_id=:id"),
                {"id": delivered[0]},
            ).scalar_one()
            is None
        )
    restarted = Outbox(execution.engine)
    received = []

    def consume(event):
        delivered.append(event.event_id)
        restarted.consume(
            "ptv13-receiver", event.event_id, lambda _connection: received.append(event.event_id)
        )

    while restarted.publish_one(consume) is not None:
        pass
    assert delivered[0] == delivered[1]
    assert not restarted.consume(
        "ptv13-receiver", delivered[0], lambda _c: received.append("duplicate")
    )
    assert received.count(delivered[0]) == 1 and "duplicate" not in received
    with admin.transaction() as tx:
        assert (
            tx.connection.execute(
                text(
                    "SELECT count(*) FROM public.outbox_events "
                    "WHERE trace_id=:t AND published_at IS NULL"
                ),
                {"t": trace},
            ).scalar_one()
            == 0
        )


def checks(admin, execution, engine, _seed_now):
    from sqlalchemy import text

    from packages.brokers.fake import FakeFault
    from packages.domain import models as m
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.control_api.control import ControlDenied, request_resume
    from services.execution.intents import ExecutionProcess, SubmitDenied, submit_once
    from services.execution.reconciliation import reconcile_order

    scenario = sys.argv[2]
    trace = hashlib.md5(f"ptv13:{scenario}".encode()).hexdigest()
    worker, worker_engine = _role(engine, "atl_worker")
    control, control_engine = _role(engine, "atl_control_api")
    try:
        _market(admin, worker, "spy", trace, "initial")
        # Recovery verifies every configured instrument's latest bar.
        _market(admin, worker, "qqq", trace, "initial")
        broker = _broker()
        process = ExecutionProcess(
            ledger=execution,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
        )
        with admin.transaction() as tx:
            assert (
                tx.connection.execute(
                    text("""
                SELECT safety_state FROM public.risk_account_context WHERE account_id='paper'
            """)
                ).scalar_one()
                == "PAUSE_NEW"
            )
        activation = request_resume(
            ledger=control,
            account_id="paper",
            actor_id="ptv13-operator",
            trace_id=trace,
            expected_policy_version=1,
        )
        first_snapshot = process.recover(activation)
        with admin.transaction() as tx:
            assert tx.connection.execute(
                text("""
                SELECT portfolio_snapshot_id,safety_state FROM public.risk_account_context
                WHERE account_id='paper'
            """)
            ).one() == (first_snapshot, "RUNNING")
        state = _market(admin, worker, "spy", trace, "trade")
        if scenario == "budget-race":
            from concurrent.futures import ThreadPoolExecutor

            other = _market(admin, worker, "qqq", trace, "race")
            proposals = (_proposal(worker, state)[1], _proposal(worker, other)[1])

            def reserving(p):
                return reserve(
                    ledger=execution, account_id="paper", proposal_id=p.id, limits=RiskLimits()
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserving, proposals))
            assert sum(result.approved for result in results) == 1
            approved = next(result for result in results if result.approved)
            assert (
                reserving(next(p for p in proposals if p.id == approved.proposal_id)).id
                == approved.id
            )
            with admin.transaction() as tx:
                held = tx.connection.execute(
                    text("SELECT sum(reserved_notional) FROM public.risk_reservations")
                ).scalar_one()
                assert held == Decimal("100.01")
                assert (
                    tx.connection.execute(
                        text("SELECT count(*) FROM public.order_intents")
                    ).scalar_one()
                    == 1
                )
            assert broker.all_orders() == ()
            print("PASS budget-race: one approved pipeline proposal, bounded cash reservation")
            return
        if scenario == "decision-down":
            from packages.decisions.mock import MockDecisionEngine
            from services.control_api.control import pause
            from services.worker.planner import PlannerConfig, plan_proposal

            clock = Clock()
            abstain = MockDecisionEngine(clock=clock, ids=Ids(), failure="timeout").decide(
                state, "ptv13-down-v1"
            )
            assert abstain.choice is m.Choice.ABSTAIN
            assert (
                plan_proposal(
                    decision=abstain,
                    market_state=state,
                    config=PlannerConfig("ptv13-fixed-one-v1", Decimal("1")),
                    clock=clock,
                    ids=Ids(),
                    ledger=worker,
                )
                is None
            )
            pause(
                ledger=control,
                account_id="paper",
                actor_id="ptv13-operator",
                trace_id=trace,
                reason_code="PAUSED_NEW",
            )
            with admin.transaction() as tx:
                assert (
                    tx.connection.execute(
                        text(
                            "SELECT safety_state FROM public.risk_account_context "
                            "WHERE account_id='paper'"
                        )
                    ).scalar_one()
                    == "PAUSE_NEW"
                )
                assert (
                    tx.connection.execute(
                        text("SELECT count(*) FROM public.trade_proposals WHERE trace_id=:t"),
                        {"t": trace},
                    ).scalar_one()
                    == 0
                )
            assert broker.all_orders() == ()
            print("PASS decision-down: actual ABSTAIN, independent pause, no broker order")
            return
        decision, proposal = _proposal(worker, state)
        risk = reserve(
            ledger=execution,
            account_id="paper",
            proposal_id=proposal.id,
            limits=RiskLimits(),
        )
        assert risk.approved and risk.reservation_id is not None
        intent = _intent(admin, proposal.id)
        original_submit = broker.submit
        calls = []

        def counted(item):
            calls.append(item.client_order_id)
            if scenario == "crash-before" and len(calls) == 1:
                raise Crash()
            result = original_submit(item)
            if scenario == "crash-after" and len(calls) == 1:
                raise Crash()
            return result

        broker.submit = counted
        if scenario == "timeout":
            broker.inject_once(FakeFault.TIMEOUT_AFTER_ACCEPT)
        try:
            order = process.submit(intent.id)
        except Crash:
            with admin.transaction() as tx:
                order_id = tx.connection.execute(
                    text("SELECT id FROM public.broker_orders WHERE intent_id=:i"), {"i": intent.id}
                ).scalar_one()
                order = tx.get(m.BrokerOrder, order_id)
        assert calls == [intent.client_order_id]
        _trace_proof(admin, trace, proposal, decision, risk, intent, order)

        if scenario == "happy":
            assert order.status is m.OrderStatus.ACKNOWLEDGED
            broker.fill(
                intent.client_order_id,
                cumulative_quantity=Decimal("0.5"),
                price=Decimal("100"),
                event_id="ptv13-partial",
            )
            partial = reconcile_order(
                ledger=execution,
                account_id="paper",
                order_id=order.id,
                broker=broker,
            )
            assert partial.status is m.OrderStatus.PARTIALLY_FILLED
            with admin.transaction() as tx:
                position = tx.get(m.Position, "pos")
                reservation = tx.get(m.RiskReservation, risk.reservation_id)
                assert position.quantity == Decimal("0.5")
                assert reservation.reserved_notional == Decimal("50.005")
            broker.fill(
                intent.client_order_id,
                cumulative_quantity=Decimal("1"),
                price=Decimal("100"),
                event_id="ptv13-final",
            )
            filled = reconcile_order(
                ledger=execution,
                account_id="paper",
                order_id=order.id,
                broker=broker,
            )
            assert filled.status is m.OrderStatus.FILLED
            reconcile_order(ledger=execution, account_id="paper", order_id=order.id, broker=broker)
            with admin.transaction() as tx:
                position = tx.get(m.Position, "pos")
                reservation = tx.get(m.RiskReservation, risk.reservation_id)
                assert position.quantity == 1 and position.average_price == 100
                assert reservation.reserved_notional == 0
                context = tx.connection.execute(
                    text(
                        "SELECT portfolio_snapshot_id FROM public.risk_account_context "
                        "WHERE account_id='paper'"
                    )
                ).scalar_one()
                portfolio = tx.get(m.PortfolioSnapshot, context)
                assert (portfolio.cash, portfolio.exposure, portfolio.equity) == (
                    Decimal("0.01"),
                    Decimal("100"),
                    Decimal("2000"),
                )
                assert portfolio.reconciliation is m.Reconciliation.PENDING
                assert (
                    tx.connection.execute(
                        text("""
                    SELECT count(*) FROM public.execution_reports
                    WHERE order_id=:o AND broker_event_id IN ('ptv13-partial','ptv13-final')
                """),
                        {"o": order.id},
                    ).scalar_one()
                    == 2
                )
                assert (
                    tx.connection.execute(
                        text("""
                    SELECT count(*) FROM public.audit_events
                    WHERE trace_id=:t AND action='execution.reconciliation_report'
                """),
                        {"t": trace},
                    ).scalar_one()
                    >= 2
                )
            assert len(calls) == 1
            _deliver_after_crash(execution, admin, trace)
            print("PASS full fixture-market-mock-planner-risk-execution-reconciliation chain")
            return

        if scenario == "timeout":
            assert order.status is m.OrderStatus.UNKNOWN
            with admin.transaction() as tx:
                reservation = tx.get(m.RiskReservation, risk.reservation_id)
                assert reservation.state is m.ReservationState.HELD_UNKNOWN
                assert reservation.reserved_notional == Decimal("100.01")
                assert (
                    tx.connection.execute(
                        text("""
                    SELECT safety_state FROM public.risk_account_context
                    WHERE account_id='paper'
                """)
                    ).scalar_one()
                    == "PAUSE_NEW"
                )
        else:
            assert order.status is m.OrderStatus.SUBMITTING

        # Reuse the identical Fake instance: its accepted order, if any,
        # survives the simulated local Execution process crash.
        fresh = ExecutionProcess(
            ledger=execution,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
        )
        assert fresh.fencing_token > process.fencing_token
        with admin.transaction() as tx:
            assert tx.connection.execute(
                text("""
                SELECT safety_state,reconciliation_current FROM public.risk_account_context
                WHERE account_id='paper'
            """)
            ).one() == ("PAUSE_NEW", False)
        for denied in (
            lambda: fresh.submit(intent.id),
            lambda: submit_once(
                ledger=execution,
                account_id="paper",
                intent_id=intent.id,
                limits=RiskLimits(),
                broker=broker,
                paper_smoke_enabled=True,
                owner_id=process.owner_id,
                fencing_token=process.fencing_token,
            ),
        ):
            try:
                denied()
            except (SubmitDenied, ControlDenied):
                pass
            else:
                raise AssertionError("fresh or fenced process submitted unresolved intent")
        assert len(calls) == 1
        visible = reconcile_order(
            ledger=execution,
            account_id="paper",
            order_id=order.id,
            broker=broker,
        )
        if scenario == "crash-before":
            assert visible.status is m.OrderStatus.UNKNOWN
            with admin.transaction() as tx:
                assert tx.get(m.RiskReservation, risk.reservation_id).reserved_notional == Decimal(
                    "100.01"
                )
            assert broker.all_orders() == ()
            print("PASS crash-before: visible UNKNOWN and held budget; zero duplicate orders")
            return
        assert visible.status is m.OrderStatus.ACKNOWLEDGED
        broker.fill(
            intent.client_order_id,
            cumulative_quantity=Decimal("1"),
            price=Decimal("100"),
            event_id=f"ptv13-{scenario}-fill",
        )
        resolved = reconcile_order(
            ledger=execution,
            account_id="paper",
            order_id=order.id,
            broker=broker,
        )
        assert resolved.status is m.OrderStatus.FILLED
        request_id = request_resume(
            ledger=control,
            account_id="paper",
            actor_id="ptv13-operator",
            trace_id=trace,
            expected_policy_version=1,
        )
        # Health is computed from broker inventory, reports, positions and bars.
        recovered_snapshot = fresh.recover(request_id)
        with admin.transaction() as tx:
            assert tx.get(m.Position, "pos").quantity == 1
            assert tx.connection.execute(
                text("""
                SELECT portfolio_snapshot_id,safety_state FROM public.risk_account_context
                WHERE account_id='paper'
            """)
            ).one() == (recovered_snapshot, "RUNNING")
            assert (
                tx.connection.execute(
                    text("""
                SELECT count(*) FROM public.safety_health_evidence WHERE request_id=:r
            """),
                    {"r": request_id},
                ).scalar_one()
                == 1
            )
        assert len(calls) == 1
        print(f"PASS {scenario}: fenced takeover, authoritative recovery, one submit")
    finally:
        worker_engine.dispose()
        control_engine.dispose()


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
