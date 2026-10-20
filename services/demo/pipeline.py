"""Real neutral decision → planner → risk → execution → Fake reconciliation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from packages.domain import models as m
from packages.persistence import PostgresLedger
from packages.risk.engine import RiskLimits
from services.demo.broker import Clock, Ids, journaled_broker
from services.demo.storage import financial_engine


def seed_run(engine, budget, trace):
    from services.runtime.provision import seed

    ledger = PostgresLedger(engine=engine)
    seed(ledger, budget)
    with ledger.transaction() as tx:
        now = tx.connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        for symbol in ("SPY", "QQQ"):
            tx.insert(
                m.AuditEvent(
                    Ids(trace).new_id("instrument-context", symbol),
                    now,
                    "demo-initializer",
                    "runtime.initialize",
                    ("SIMULATION_BOOTSTRAP",),
                    f"paper:{symbol}",
                    1,
                    trace,
                )
            )
            tx.connection.execute(
                text("""INSERT INTO public.risk_instrument_context
              (account_id,instrument_id,version,capability_version,broker_id,
               paper_confirmed,regular_session_confirmed,market_order_confirmed,
               limit_order_confirmed,fractional_confirmed,quantity_step,price_tick,
               session_open,session_version,session_valid_until,valid_until,actor_id,trace_id)
              VALUES ('paper',:symbol,1,1,'fake',true,true,true,true,false,1,0.01,
                true,'demo-synthetic-session.v1',:until,:until,'demo-initializer',:trace)"""),
                {"symbol": symbol, "until": now + timedelta(minutes=5), "trace": trace},
            )


def market_state(admin, worker, symbol, trace, label):
    from services.worker.market_state import PaperMarketConfig, build_market_state

    with admin.transaction() as tx:
        now = tx.connection.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)
        instrument = tx.get(m.Instrument, symbol)
        position = tx.get(m.Position, "runtime-position-" + symbol)
        portfolio_id = tx.connection.execute(
            text(
                "SELECT portfolio_snapshot_id FROM public.risk_account_context "
                "WHERE account_id='paper'"
            )
        ).scalar_one()
        portfolio = tx.get(m.PortfolioSnapshot, portfolio_id)

    # This bounded, versioned session is constructed only in the isolated demo.
    class SyntheticSession(PaperMarketConfig):
        def session_bounds(self, moment):
            local = moment.astimezone(self.zone)
            return (
                datetime.combine(local.date(), self.regular_open, self.zone).astimezone(UTC),
                datetime.combine(local.date(), self.regular_close, self.zone).astimezone(UTC),
            )

    offset = timezone(timedelta(hours=12 - now.hour))
    local = now.astimezone(offset)
    opening, closing = local - timedelta(minutes=30, seconds=10), local + timedelta(hours=2)
    config = SyntheticSession(
        version="demo-synthetic-session.v1",
        fingerprint=hashlib.sha256(b"demo-synthetic-session.v1").hexdigest(),
        source_id="demo-synthetic-bars.v1",
        interval="PT15M",
        max_age_seconds=120,
        max_spread_fraction=Decimal("0.01"),
        required_bars=2,
        instruments={symbol: instrument},
        calendar_start=local.date(),
        calendar_end=local.date(),
        holidays=frozenset(),
        early_closes={},
        zone=offset,
        regular_open=time(opening.hour, opening.minute, opening.second, opening.microsecond),
        regular_close=time(closing.hour, closing.minute, closing.second, closing.microsecond),
    )
    end = now - timedelta(seconds=10)
    bars = []
    for index, price in enumerate((Decimal("4.95"), Decimal("5"))):
        close = end - timedelta(minutes=15 * (1 - index))
        bars.append(
            m.MarketBar(
                id=Ids(trace).new_id("bar", f"{symbol}:{label}:{index}"),
                instrument_id=symbol,
                start_at=close - timedelta(minutes=15),
                end_at=close,
                open=price,
                high=price + Decimal("0.01"),
                low=price - Decimal("0.01"),
                close=price,
                volume=Decimal("1000"),
                event_time=close,
                received_at=close + timedelta(seconds=1),
                available_at=close + timedelta(seconds=2),
                quality=m.Quality.VALID,
            )
        )
    if label == "initial":
        # Initial cash is deliberately stale/pending until actual recovery.
        # Recovery needs admitted closed bars, not an invented valid portfolio.
        with worker.transaction() as tx:
            for bar in bars:
                tx.insert(bar)
        return None
    state = build_market_state(
        config=config,
        instrument_id=symbol,
        bars=tuple(bars),
        as_of=now,
        spread=Decimal("0.01"),
        portfolio=portfolio,
        position=position,
        trace_id=trace,
    )
    with worker.transaction() as tx:
        for bar in bars:
            tx.insert(bar)
        tx.insert(state.evidence)
        tx.insert(state)
    return state


def execute_run(
    metadata_engine,
    run,
    run_engine,
    *,
    fail_after_accept=False,
    activation_guard,
    running,
    transport_guard,
):
    from packages.decisions.baseline import DeterministicBaselineDecisionEngine
    from packages.decisions.mock import MockDecisionEngine
    from packages.risk.reservations import reserve
    from services.control_api.control import pause, request_paper_activation
    from services.execution.intents import ExecutionProcess
    from services.execution.reconciliation import reconcile_order
    from services.worker.planner import PlannerConfig, plan_proposal

    trace, run_id = run["trace_id"], str(run["run_id"])
    engines = [
        financial_engine(metadata_engine, run_id, role=role)
        for role in ("atl_worker", "atl_execution", "atl_control_api")
    ]
    worker, execution, control = [PostgresLedger(engine=item) for item in engines]
    admin = PostgresLedger(engine=run_engine)
    try:
        # Only initialization/recovery serializes with controls. Once it is
        # complete, pause/kill can latch while the worker plans and submits;
        # the unchanged Risk and Execution gates observe that durable latch.
        with activation_guard():
            if not running():
                return None
            seed_run(run_engine, run["initial_budget"], trace)
            for symbol in ("SPY", "QQQ"):
                market_state(admin, worker, symbol, trace, "initial")
            broker = journaled_broker(
                run_engine,
                run_id,
                fail_after_accept=fail_after_accept,
                transport_guard=transport_guard,
            )
            process = ExecutionProcess(
                ledger=execution,
                account_id="paper",
                limits=RiskLimits(),
                broker=broker,
                paper_smoke_enabled=True,
            )
            activation = request_paper_activation(
                ledger=control,
                account_id="paper",
                actor_id="demo-start-authority",
                trace_id=trace,
                expected_policy_version=1,
            )
            process.recover(activation)
        if not running():
            return None
        state = market_state(admin, worker, "SPY", trace, "trade")
        clock, ids = Clock(), Ids(run_id)
        strategy = (
            MockDecisionEngine
            if run["strategy"] == "mock-neutral"
            else DeterministicBaselineDecisionEngine
        )
        decision = strategy(clock=clock, ids=ids).decide(state, "demo-" + run["strategy"] + ".v1")
        proposal = plan_proposal(
            decision=decision,
            market_state=state,
            config=PlannerConfig("demo-fixed-one.v1", Decimal("1")),
            clock=clock,
            ids=ids,
            ledger=worker,
        )
        risk = None
        if proposal is not None:
            risk = reserve(
                ledger=execution, account_id="paper", proposal_id=proposal.id, limits=RiskLimits()
            )
            if risk.approved:
                if not running():
                    return None
                with admin.transaction() as tx:
                    ident = tx.connection.execute(
                        text("SELECT id FROM public.order_intents WHERE proposal_id=:p"),
                        {"p": proposal.id},
                    ).scalar_one()
                    intent = tx.get(m.OrderIntent, ident)
                order = process.submit(intent.id)
                if order.status is not m.OrderStatus.ACKNOWLEDGED:
                    raise RuntimeError("TEMPORARILY_UNAVAILABLE")
                for quantity, label in ((Decimal("0.5"), "partial"), (Decimal("1"), "final")):
                    if not running():
                        return None
                    broker.fill(
                        intent.client_order_id,
                        cumulative_quantity=quantity,
                        price=Decimal("5"),
                        event_id=ids.new_id("fill", label),
                    )
                    reconcile_order(
                        ledger=execution, account_id="paper", order_id=order.id, broker=broker
                    )
                    # Duplicate deliveries exercise durable deduplication without extra booking.
                    reconcile_order(
                        ledger=execution, account_id="paper", order_id=order.id, broker=broker
                    )
        pause(
            ledger=control,
            account_id="paper",
            actor_id="demo-completion",
            trace_id=trace,
            reason_code="PAUSED_NEW",
        )
        return "RISK_LIMIT_EXCEEDED" if risk is not None and not risk.approved else None
    finally:
        for engine in engines:
            engine.dispose()


def recover_interrupted(metadata_engine, run):
    """Reconcile journaled evidence, retain reservations and pause; never submit."""
    from services.control_api.control import pause
    from services.execution.intents import ExecutionProcess
    from services.execution.reconciliation import reconcile_order

    run_id, trace = str(run["run_id"]), run["trace_id"]
    owner = financial_engine(metadata_engine, run_id)
    execution_engine = financial_engine(metadata_engine, run_id, role="atl_execution")
    ledger = PostgresLedger(engine=execution_engine)
    try:
        broker = journaled_broker(owner, run_id)
        ExecutionProcess(
            ledger=ledger,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
        )
        with ledger.transaction() as tx:
            orders = (
                tx.connection.execute(text("SELECT id FROM public.broker_orders")).scalars().all()
            )
        for order in orders:
            reconcile_order(ledger=ledger, account_id="paper", order_id=order, broker=broker)
        pause(
            ledger=ledger,
            account_id="paper",
            actor_id="demo-restart",
            trace_id=trace,
            reason_code="PAUSED_NEW",
        )
    finally:
        owner.dispose()
        execution_engine.dispose()
