"""UUID-isolated SQL tests; synthetic DTOs/MockTransport never leave this guest.

The publish test isolates Runtime's storage/recovery sequencing. Its normalized
gateway DTOs and venue calendar are explicit fixtures, not real venue evidence.
Broker/account binding and human recovery still execute their actual gates.
"""

from __future__ import annotations

import hashlib
import runpy
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from packages.domain import models as m
from packages.persistence import PostgresLedger

FIXTURE = runpy.run_path("tests/integration/alpaca_reconciliation_support.py")
Clock, Ids, Server = (FIXTURE[name] for name in ("Clock", "Ids", "Server"))
TRACE = "a" * 32


def role_ledger(engine, role):
    assert role in {"atl_worker", "atl_control_api"}
    restricted = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(restricted, "connect")
    def assign(connection, _record):
        connection.execute("SET ROLE " + role)
        connection.commit()

    return PostgresLedger(engine=restricted)


def scalar(engine, query, **params):
    with engine.connect() as connection:
        return connection.execute(text(query), params).scalar_one()


def begin_run_checks(admin, execution, engine, now):
    from services.execution.paper_smoke import OperatorOptions, SmokeDenied, begin_run

    def options():
        return OperatorOptions(str(uuid.uuid4()), "f" * 64, "SPY", Decimal("100"), Decimal("102"))

    def context(mode, safety):
        with execution.transaction() as tx:
            version = tx.connection.execute(
                text("SELECT version+1 FROM risk_account_context WHERE account_id='paper'")
            ).scalar_one()
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    datetime.now(UTC),
                    "fixture",
                    "fixture",
                    ("SYNTHETIC_CONTEXT",),
                    "paper",
                    version,
                    TRACE,
                )
            )
            tx.connection.execute(
                text(
                    "UPDATE risk_account_context SET version=:version,mode=:mode,"
                    "safety_state=:safety,actor_id='fixture',trace_id=:trace "
                    "WHERE account_id='paper'"
                ),
                {"version": version, "mode": mode, "safety": safety, "trace": TRACE},
            )

    def denied(item):
        before = scalar(
            engine, "SELECT count(*) FROM audit_events WHERE action='paper_smoke.started'"
        )
        try:
            begin_run(execution, item)
        except SmokeDenied:
            pass
        else:
            raise AssertionError("unsafe/used smoke run admitted")
        assert (
            scalar(engine, "SELECT count(*) FROM audit_events WHERE action='paper_smoke.started'")
            == before
        )

    denied(options())  # Original fixture is PAPER/RUNNING.
    context("DISABLED", "RUNNING")
    denied(options())
    context("PAPER", "PAUSE_NEW")
    denied(options())
    context("DISABLED", "PAUSE_NEW")
    first = options()
    begin_run(execution, first)
    denied(first)

    # Explicit standalone synthetic risk rows isolate the reservation predicate:
    # no intent, order, position or unfinished job can mask a missing hold gate.
    reservation = m.RiskReservation(
        "synthetic-smoke-reservation",
        "p-0",
        "synthetic-smoke-risk",
        "USD",
        Decimal("101"),
        m.ReservationState.ACTIVE,
        1,
        now,
        TRACE,
    )
    risk = m.RiskDecision(
        "synthetic-smoke-risk",
        "p-0",
        True,
        "SYNTHETIC_TEST",
        ("SYNTHETIC_TEST",),
        1,
        m.ExposureSnapshot("USD", Decimal("2000"), Decimal("0"), Decimal("101")),
        now,
        now + timedelta(seconds=30),
        TRACE,
        reservation.id,
    )
    with execution.transaction() as tx:
        for item in (risk, reservation):
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    now,
                    "fixture",
                    "fixture",
                    ("SYNTHETIC_HOLD_ONLY",),
                    item.id,
                    1,
                    TRACE,
                )
            )
            tx.insert(item)
        tx.connection.execute(
            text("UPDATE risk_reservations SET account_id='paper' WHERE id=:id"),
            {"id": reservation.id},
        )
    assert scalar(engine, "SELECT count(*) FROM order_intents") == 0
    assert scalar(engine, "SELECT count(*) FROM broker_orders") == 0
    next_run = options()
    denied(next_run)
    for state in (m.ReservationState.HELD_UNKNOWN, m.ReservationState.RELEASED):
        with execution.transaction() as tx:
            current = tx.get(m.RiskReservation, reservation.id)
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    datetime.now(UTC),
                    "fixture",
                    "fixture",
                    ("SYNTHETIC_HOLD_ONLY",),
                    current.id,
                    current.version + 1,
                    TRACE,
                )
            )
            assert tx.replace(
                replace(current, state=state, version=current.version + 1),
                expected_version=current.version,
            )
        if state is m.ReservationState.HELD_UNKNOWN:
            denied(next_run)
        else:
            begin_run(execution, next_run)
    assert (
        scalar(engine, "SELECT count(*) FROM audit_events WHERE action='paper_smoke.started'") == 2
    )
    print("PASS begin-run: actual role/context/replay and isolated ACTIVE/HELD_UNKNOWN holds")


def worker_checks(admin, execution, engine, now):
    from packages.domain.paper_smoke import SmokeRequest
    from services.worker.paper_smoke_plan import plan_smoke

    worker = role_ledger(engine, "atl_worker")
    # These observations are deliberately synthetic and fresh in real UTC.
    # The existing baseline must independently derive LONG from rising closes.
    with admin.transaction() as tx:
        original = tx.get(m.MarketState, "ms")
        bar = replace(
            original.bar, id="smoke-rising-bar", close=Decimal("101"), high=Decimal("101")
        )
        tx.insert(bar)
        state = replace(
            original,
            id="smoke-rising-state",
            bar=bar,
            features={
                "previous_close": Decimal("100"),
                "last_close": Decimal("101"),
                "window_volume": Decimal("2"),
            },
        )
        flat = replace(
            state,
            id="smoke-flat-state",
            features={
                "previous_close": Decimal("101"),
                "last_close": Decimal("101"),
                "window_volume": Decimal("2"),
            },
        )
        tx.insert(state)
        tx.insert(flat)

    def request(**changes):
        values = dict(
            run_id=str(uuid.uuid4()),
            account_id="paper",
            account_fingerprint="f" * 64,
            instrument_id="SPY",
            limit_price=Decimal("101"),
            max_notional=Decimal("103"),
            market_state_id=state.id,
        )
        values.update(changes)
        return SmokeRequest(**values)

    def authorize(item, *, age=timedelta(), actor="paper-smoke-execution", ledger=execution):
        with ledger.transaction() as tx:
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    datetime.now(UTC) - age,
                    actor,
                    "paper_smoke.authorize",
                    ("REQUEST_SHA256:" + item.digest(),),
                    item.entity_id,
                    1,
                    item.trace_id,
                    outcome="AUTHORIZED",
                )
            )

    def plan(item, ledger=worker):
        return plan_smoke(request=item, ledger=ledger, clock=Clock(), ids=Ids())

    def reject(item):
        before = scalar(engine, "SELECT count(*) FROM trade_proposals")
        try:
            plan(item)
        except ValueError:
            pass
        else:
            raise AssertionError("unauthorized smoke plan admitted")
        assert scalar(engine, "SELECT count(*) FROM trade_proposals") == before
        assert (
            scalar(engine, "SELECT count(*) FROM decisions WHERE id=:id", id=item.decision_id) == 0
        )

    reject(request())
    expired = request()
    authorize(expired, age=timedelta(seconds=121))
    reject(expired)
    future = request()
    authorize(future, age=timedelta(seconds=-10))
    reject(future)
    wrong_actor = request()
    authorize(wrong_actor, actor="synthetic-untrusted-actor")
    reject(wrong_actor)
    tampered = request()
    authorize(tampered)
    reject(replace(tampered, limit_price=Decimal("101.01")))
    mismatch = request(instrument_id="QQQ")
    authorize(mismatch)
    reject(mismatch)

    admitted = request()
    authorize(admitted)
    before = scalar(engine, "SELECT count(*) FROM outbox_events")
    with ThreadPoolExecutor(max_workers=2) as callers:
        simultaneous = list(callers.map(lambda _index: plan(admitted), range(2)))
    proposal = simultaneous[0]
    assert simultaneous[1] == proposal
    assert proposal is not None
    assert proposal.id == admitted.proposal_id
    assert proposal.quantity == Decimal("1") and proposal.limit_price == Decimal("101")
    assert proposal.target is m.Target.LONG
    assert proposal.market_state_id == state.id
    assert proposal.quantity * proposal.limit_price * Decimal("1.01") <= admitted.max_notional
    with admin.transaction() as tx:
        decision = tx.get(m.DecisionResult, admitted.decision_id)
        assert decision.choice is m.Choice.LONG
        assert decision.trace_id == state.trace_id == proposal.trace_id
    assert scalar(engine, "SELECT count(*) FROM outbox_events") == before + 2
    assert plan(admitted) == proposal
    try:
        plan(replace(admitted, max_notional=Decimal("104")))
    except ValueError:
        pass
    else:
        raise AssertionError("same run accepted changed immutable authorization")
    assert (
        scalar(engine, "SELECT count(*) FROM trade_proposals WHERE id=:id", id=admitted.proposal_id)
        == 1
    )
    assert scalar(engine, "SELECT count(*) FROM outbox_events") == before + 2

    abstain = request(market_state_id=flat.id)
    authorize(abstain)
    assert plan(abstain) is None
    assert plan(abstain) is None
    with admin.transaction() as tx:
        assert tx.get(m.DecisionResult, abstain.decision_id).choice is m.Choice.ABSTAIN
        assert tx.get(m.TradeProposal, abstain.proposal_id) is None

    # Runtime role separation is checked against real PostgreSQL privileges.
    for query in (
        "SELECT * FROM broker_account_bindings",
        "INSERT INTO order_intents DEFAULT VALUES",
    ):
        try:
            with worker.transaction() as tx:
                tx.connection.execute(text(query))
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "42501"
        else:
            raise AssertionError("Worker gained Execution privileges")
    try:
        authorize(request(), ledger=worker)
    except DBAPIError as exc:
        assert exc.orig.sqlstate == "P0001"
    else:
        raise AssertionError("Worker forged Execution smoke authorization")
    assert scalar(engine, "SELECT count(*) FROM order_intents") == 0
    worker.engine.dispose()
    print("PASS worker: authorization/TTL/tamper/abstain/replay and actual role isolation")


def raw_bootstrap_checks(admin, execution, engine, now):
    from packages.brokers import alpaca_market_data, alpaca_paper
    from packages.risk.engine import RiskLimits
    from services.control_api.control import pause, request_paper_activation, request_resume
    from services.execution.broker_binding import AlpacaExecutionBinding
    from services.execution.intents import ExecutionProcess
    from services.runtime.config import RuntimeConfig
    from services.runtime.execution import ExecutionRuntime
    from services.worker.market_state import PaperMarketConfig

    zone = timezone(timedelta(hours=12 - datetime.now(UTC).hour))
    alpaca_market_data._EASTERN = zone
    alpaca_paper._EASTERN = zone

    class CancelServer(Server):
        def __call__(self, request):
            if request.method == "DELETE":
                assert request.url.host == "paper-api.alpaca.markets"
                assert request.url.path == "/v2/orders/" + FIXTURE["ORDER"]
                # A different SQL connection must see the durable cancel before I/O.
                assert scalar(engine, "SELECT status FROM broker_orders") == "CANCEL_PENDING"
                assert (
                    scalar(
                        engine,
                        "SELECT count(*) FROM audit_events "
                        "WHERE action='paper_smoke.cancel_requested'",
                    )
                    == 1
                )
                self.calls.append(("DELETE", request.url.path))
                if sys.argv[2] == "execution-canceled":
                    # DELETE itself still only returns 204. The next complete
                    # read-only account/order reconciliation observes terminality.
                    next(iter(self.orders.values())).update(
                        status="canceled", updated_at=datetime.now(UTC).isoformat()
                    )
                return httpx.Response(204)
            return super().__call__(request)

    server = CancelServer(zone)
    market = alpaca_market_data.AlpacaMarketDataProvider(
        Clock(),
        Ids(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(server),
    )
    broker = alpaca_paper.AlpacaPaperBrokerAdapter(
        Clock(),
        Ids(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        paper_enabled=True,
        expected_account_id=FIXTURE["ACCOUNT"],
        reconciliation_fee_profile=FIXTURE["PROFILE"],
        transport=httpx.MockTransport(server),
    )
    binding = AlpacaExecutionBinding(execution, "paper", broker, market)
    for instrument in ("SPY", "QQQ"):
        binding.publish(instrument, TRACE)
    process = ExecutionProcess(
        ledger=execution,
        account_id="paper",
        limits=RiskLimits(),
        broker=broker,
        binding=binding,
        paper_smoke_enabled=True,
    )
    control = role_ledger(engine, "atl_control_api")
    pause(
        ledger=control,
        account_id="paper",
        actor_id="synthetic-human",
        trace_id=TRACE,
        reason_code="PAUSED_NEW",
    )
    with execution.transaction() as tx:
        version = tx.connection.execute(
            text("SELECT version+1 FROM risk_account_context WHERE account_id='paper'")
        ).scalar_one()
        tx.insert(
            m.AuditEvent(
                str(uuid.uuid4()),
                datetime.now(UTC),
                "fixture",
                "fixture",
                ("SYNTHETIC_BOOTSTRAP",),
                "paper",
                version,
                TRACE,
            )
        )
        tx.connection.execute(
            text(
                "UPDATE risk_account_context SET version=:v,reconciliation_current=false,"
                "mode=:mode,actor_id='fixture',trace_id=:trace "
                "WHERE account_id='paper'"
            ),
            {
                "v": version,
                "mode": "DISABLED" if sys.argv[2].startswith("execution") else "PAPER",
                "trace": TRACE,
            },
        )

    # Explicit gateway DTO fixtures, with no claim to certify a venue calendar.
    config = PaperMarketConfig.load()
    anchor = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)
    opening = anchor - timedelta(minutes=150)
    closing = anchor + timedelta(minutes=150)
    feeds = {}

    def fresh_feed(instrument):
        with admin.transaction() as tx:
            template = tx.get(m.MarketBar, "bar" if instrument == "SPY" else "bar2")
        captured = datetime.now(UTC)
        bars = tuple(
            replace(
                template,
                id="synthetic-smoke-" + instrument + str(index),
                start_at=anchor - timedelta(minutes=30 - index * 15),
                end_at=anchor - timedelta(minutes=15 - index * 15),
                event_time=anchor - timedelta(minutes=15 - index * 15),
                received_at=captured,
                available_at=captured,
            )
            for index in range(2)
        )
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                bars=bars,
                source_capabilities=SimpleNamespace(source_id="synthetic-smoke"),
                as_of=captured,
                spread=Decimal("0.01"),
                quote_provider_time=captured,
            )
        )

    for instrument in ("SPY", "QQQ"):
        feeds[instrument] = fresh_feed(instrument)
    runtime = ExecutionRuntime.__new__(ExecutionRuntime)
    runtime.config, runtime.ledger = RuntimeConfig(), execution
    runtime.clock, runtime.ids, runtime.binding, runtime.process = Clock(), Ids(), binding, process
    runtime.limits = RiskLimits()
    runtime.gateway = SimpleNamespace(read=lambda instrument, *_a, **_kw: feeds[instrument])
    if sys.argv[2].startswith("execution"):
        from packages.domain.paper_smoke import SmokeRequest
        from services.execution import paper_smoke
        from services.worker.paper_smoke_plan import plan_smoke

        # The real entry point owns control flow, finally-pause and settlement.
        # Its human prompt callbacks represent separate Control/Worker terminals.
        options = paper_smoke.OperatorOptions(
            str(uuid.uuid4()),
            hashlib.sha256(FIXTURE["ACCOUNT"].encode()).hexdigest(),
            "SPY",
            Decimal("100.01"),
            Decimal("102"),
        )
        started = paper_smoke.begin_run(execution, options)
        worker = role_ledger(engine, "atl_worker")
        operator_aborted = False

        def ask(prompt, _process, **_kwargs):
            nonlocal operator_aborted
            if "activate-paper" in prompt:
                return request_paper_activation(
                    ledger=control,
                    account_id="paper",
                    actor_id="synthetic-human",
                    trace_id=TRACE,
                    expected_policy_version=1,
                )
            if sys.argv[2] == "execution-abort":
                operator_aborted = True
                raise RuntimeError("synthetic operator abort before submit")
            state_id = scalar(
                engine,
                "SELECT id FROM market_states WHERE instrument_id='SPY' "
                "ORDER BY available_at DESC,id DESC LIMIT 1",
            )
            request = options.request(state_id)
            assert isinstance(request, SmokeRequest)
            proposal = plan_smoke(request=request, ledger=worker, clock=Clock(), ids=Ids())
            assert proposal is not None, "fixture must produce actual baseline LONG"
            return proposal.id

        def rising_feed(instrument, *_args, **_kwargs):
            feed = fresh_feed(instrument)
            bars = tuple(
                replace(bar, high=Decimal("101"), close=Decimal(str(100 + index)))
                for index, bar in enumerate(feed.snapshot.bars)
            )
            return SimpleNamespace(snapshot=replace_snapshot(feed.snapshot, bars=bars))

        def replace_snapshot(snapshot, **changes):
            return SimpleNamespace(**(vars(snapshot) | changes))

        runtime.gateway = SimpleNamespace(read=rising_feed)
        original_settle = paper_smoke.settle

        def settle_without_wait(runtime, request, intent):
            return original_settle(runtime, request, intent, wait=lambda _seconds: None, polls=2)

        with (
            patch.object(PaperMarketConfig, "load", return_value=config),
            patch.object(PaperMarketConfig, "session_bounds", return_value=(opening, closing)),
            patch.object(paper_smoke, "settle", side_effect=settle_without_wait),
        ):
            try:
                paper_smoke.run(runtime, options, started, ask=ask, output=lambda _line: None)
            except (paper_smoke.SmokeDenied, RuntimeError):
                if sys.argv[2] == "execution-canceled":
                    raise
                pass
            else:
                assert sys.argv[2] == "execution-canceled", (
                    "unconfirmed cancel/aborted operator should not finish"
                )
        assert scalar(engine, "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
        if sys.argv[2] == "execution-abort":
            assert operator_aborted
            assert scalar(engine, "SELECT mode FROM risk_account_context") == "PAPER"
            assert not any(method == "POST" for method, _ in server.calls)
            assert scalar(engine, "SELECT count(*) FROM order_intents") == 0
        elif sys.argv[2] == "execution":
            assert server.calls.count(("POST", "/v2/orders")) == 1
            assert server.calls.count(("DELETE", "/v2/orders/" + FIXTURE["ORDER"])) == 1
            assert scalar(engine, "SELECT status FROM broker_orders") == "UNKNOWN"
            assert scalar(engine, "SELECT state FROM risk_reservations") == "HELD_UNKNOWN"
            assert (
                scalar(
                    engine, "SELECT count(*) FROM execution_reports WHERE status='CANCEL_PENDING'"
                )
                >= 1
            )
            assert (
                scalar(
                    engine,
                    "SELECT count(*) FROM audit_events "
                    "WHERE action='paper_smoke.completed' AND outcome='UNKNOWN'",
                )
                == 1
            )
        else:
            assert server.calls.count(("POST", "/v2/orders")) == 1
            assert server.calls.count(("DELETE", "/v2/orders/" + FIXTURE["ORDER"])) == 1
            assert scalar(engine, "SELECT status FROM broker_orders") == "CANCELED"
            assert scalar(engine, "SELECT state FROM risk_reservations") == "RELEASED"
            assert scalar(engine, "SELECT NOT reconciliation_current FROM risk_account_context")
            assert (
                scalar(
                    engine,
                    "SELECT count(*) FROM execution_reports WHERE status='CANCELED' "
                    "AND reconciliation='CURRENT'",
                )
                >= 1
            )
            assert scalar(
                engine,
                "SELECT ready FROM paper_reconciliation_receipts "
                "ORDER BY observed_at DESC,id DESC LIMIT 1",
            )
            assert (
                scalar(
                    engine,
                    "SELECT count(*) FROM audit_events "
                    "WHERE action='paper_smoke.completed' AND outcome='CANCELED'",
                )
                == 1
            )
            # The operator execute() wrapper runs this exact cleanup helper
            # while ordinary queues remain stopped. Exercise its actual SQL
            # publication destination used by the public SSE transport.
            paper_smoke.publish_outbox(execution)
            assert (
                scalar(engine, "SELECT count(*) FROM outbox_events WHERE published_at IS NULL") == 0
            )
            assert (
                scalar(
                    engine,
                    "SELECT count(*) FROM publish_log l "
                    "JOIN outbox_events e USING(event_id) "
                    "WHERE event_type='execution.reported.v1' "
                    "AND payload->>'status'='CANCELED'",
                )
                >= 1
            )
        worker.engine.dispose()
        control.engine.dispose()
        print("PASS " + sys.argv[2] + ": entrypoint finally PAUSE; at most one submit/cancel")
        return
    before = scalar(engine, "SELECT count(*) FROM market_states")
    with (
        patch.object(PaperMarketConfig, "load", return_value=config),
        patch.object(PaperMarketConfig, "session_bounds", return_value=(opening, closing)),
    ):
        assert runtime.publish_market() is False
        assert scalar(engine, "SELECT count(*) FROM market_states") == before
        assert (
            scalar(engine, "SELECT count(*) FROM market_bars WHERE id LIKE 'synthetic-smoke-%'")
            == 4
        )
        assert scalar(engine, "SELECT NOT reconciliation_current FROM risk_account_context")
        assert scalar(engine, "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
        request_id = request_resume(
            ledger=control,
            account_id="paper",
            actor_id="synthetic-human",
            trace_id=TRACE,
            expected_policy_version=1,
        )
        assert scalar(engine, "SELECT NOT reconciliation_current FROM risk_account_context")
        assert scalar(engine, "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
        process.recover(request_id)
        assert scalar(engine, "SELECT reconciliation_current FROM risk_account_context")
        assert scalar(engine, "SELECT safety_state FROM risk_account_context") == "RUNNING"
        # Re-read the normalized DTO after recovery, preserving time authority.
        for instrument in feeds:
            feeds[instrument] = fresh_feed(instrument)
        assert runtime.publish_market() is True
    assert scalar(engine, "SELECT count(*) FROM market_states") == before + 2
    assert (
        scalar(
            engine,
            "SELECT count(*) FROM safety_health_evidence WHERE request_id=:id",
            id=request_id,
        )
        == 1
    )
    assert not any(method in {"POST", "DELETE"} for method, _ in server.calls)
    control.engine.dispose()
    print("PASS raw-bootstrap: raw bars before authority; real fresh human recovery admits state")


if __name__ == "__main__":
    from risk_support import guest

    scenario = sys.argv[2]
    checks = {"worker": worker_checks, "begin-run": begin_run_checks}
    guest(checks.get(scenario, raw_bootstrap_checks), uppercase_instruments=True)
