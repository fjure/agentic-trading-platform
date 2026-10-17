"""Runtime failure injection over disposable PostgreSQL and local provider transports."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


def test_role_runtime_failures(scenario):
    from tests.integration.risk_support import host

    assert "PASS runtime failure" in host(guest_path=Path(__file__), guest_args=(scenario,))


def checks(admin, execution, engine, now):
    import httpx
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import OperationalError

    from packages.brokers.fake import FakeBrokerAdapter
    from packages.decisions.jev import JevDecisionEngine, load_config
    from packages.domain import models as m
    from packages.persistence import PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.control_api.control import pause, request_resume
    from services.execution.intents import SubmitDenied
    from services.runtime.config import RuntimeConfig
    from services.runtime.execution import ExecutionRuntime
    from services.runtime.worker import WorkerRuntime
    from services.watchdog.main import poll_once

    scenario = sys.argv[2]
    engines = []

    def role(name):
        item = create_engine(engine.url, hide_parameters=True)
        engines.append(item)

        @event.listens_for(item, "connect")
        def assign(connection, _record):
            connection.execute(f"SET ROLE {name}")
            connection.commit()

        return PostgresLedger(engine=item)

    control = role("atl_control_api")
    watchdog = role("atl_watchdog")
    worker_ledger = role("atl_worker")
    common = {"account_id": "paper", "actor_id": "failure-fixture", "trace_id": "a" * 32}
    config = RuntimeConfig(mode="PAPER", paper_enabled=True)
    runtime = ExecutionRuntime(config, execution)
    caps = {
        instrument: m.BrokerCapabilities(
            "fake",
            instrument,
            1,
            paper=m.Capability.CONFIRMED,
            regular_session=m.Capability.CONFIRMED,
            market_order=m.Capability.CONFIRMED,
            limit_order=m.Capability.CONFIRMED,
            fractional=m.Capability.UNKNOWN,
            quantity_step=Decimal("1"),
            price_tick=Decimal("0.01"),
        )
        for instrument in config.instruments
    }
    broker = FakeBrokerAdapter(
        clock=runtime.clock,
        ids=runtime.ids,
        capabilities=caps,
        session_open=lambda _instrument, _moment: True,
    )
    runtime.process.broker = broker

    def context():
        with engine.connect() as connection:
            return (
                connection.execute(
                    text("SELECT * FROM public.risk_account_context WHERE account_id='paper'")
                )
                .mappings()
                .one()
            )

    def resume():
        request_id = request_resume(ledger=control, expected_policy_version=1, **common)
        runtime.process.recover(request_id)
        assert context()["safety_state"] == "RUNNING"

    def count(table):
        assert table in {"decisions", "trade_proposals", "broker_orders", "risk_decisions"}
        with engine.connect() as connection:
            return connection.execute(text(f"SELECT count(*) FROM public.{table}")).scalar_one()

    @contextmanager
    def unavailable_database(target):
        # PostgreSQL itself refuses new sessions to this UUID-named disposable
        # database. The keeper is established first and only this test's pool is
        # disposed; shared PostgreSQL and unrelated databases stay available.
        database = engine.dialect.identifier_preparer.quote(engine.url.database)
        keeper_engine = create_engine(engine.url.set(database="lab"), hide_parameters=True)
        with keeper_engine.connect() as keeper:
            keeper.exec_driver_sql(f"ALTER DATABASE {database} ALLOW_CONNECTIONS false")
            keeper.commit()
            target.engine.dispose()
            try:
                yield
            finally:
                keeper.exec_driver_sql(f"ALTER DATABASE {database} ALLOW_CONNECTIONS true")
                keeper.commit()
        keeper_engine.dispose()

    def current_intent():
        moment = datetime.now(UTC)
        with admin.transaction() as tx:
            portfolio = tx.get(m.PortfolioSnapshot, context()["portfolio_snapshot_id"])
            source = tx.get(m.MarketState, "ms")
            state = replace(source, id="failure-submit-market", portfolio=portfolio)
            decision = replace(
                tx.get(m.DecisionResult, "dec"),
                id="failure-submit-decision",
                market_state_id=state.id,
            )
            proposal = replace(
                tx.get(m.TradeProposal, "p-0"),
                id="failure-submit-proposal",
                decision_id=decision.id,
                market_state_id=state.id,
                created_at=moment,
                valid_until=moment + timedelta(seconds=30),
            )
            tx.insert(state)
            for entity in (decision, proposal):
                tx.insert(
                    m.AuditEvent(
                        "audit-" + entity.id,
                        moment,
                        "fixture",
                        "fixture",
                        ("FIXTURE",),
                        entity.id,
                        1,
                        source.trace_id,
                    )
                )
                tx.insert(entity)
        risk = reserve(
            ledger=execution, account_id="paper", proposal_id=proposal.id, limits=RiskLimits()
        )
        assert risk.approved, "fresh synthetic proposal was not approved"
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT id FROM public.order_intents WHERE proposal_id=:proposal"),
                {"proposal": proposal.id},
            ).scalar_one()

    try:
        # Positive control: actual role, current owner and authoritative Fake inventory
        # can recover. Every failure below starts from this proven RUNNING state.
        resume()
        assert poll_once(ledger=watchdog, account_id="paper").healthy

        if scenario == "api-concurrency":
            import json
            from concurrent.futures import ThreadPoolExecutor

            from services.runtime.common import write_status

            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(lambda _: write_status("api", "READY"), range(60)))
            status = json.loads(Path("/tmp/runtime-status.json").read_text("utf-8"))
            assert status["role"] == "api" and status["code"] == "READY"

        elif scenario == "runtime-metrics":
            from packages.observability.runtime import RuntimeMetrics
            from packages.observability.telemetry import Telemetry

            for role_name, ledger in (("execution", execution), ("api", control)):
                telemetry = Telemetry(role_name)
                sampler = RuntimeMetrics(ledger, telemetry, "paper")
                assert sampler.sample(), "actual role cannot read bounded metric snapshot"
                points = telemetry.snapshot()["metrics"]
                assert {point["name"] for point in points} >= {
                    "data_age_seconds", "lease_age_seconds", "outbox_lag_seconds",
                    "reconciliation_mismatch_count", "unknown_count", "reserved_risk",
                }
                assert all(point["value"] >= 0 for point in points)
                with unavailable_database(ledger):
                    assert sampler.sample() is False
                telemetry.close()

        elif scenario in {"worker-jev-down", "worker-lease-loss"}:
            worker = WorkerRuntime(RuntimeConfig(instruments=("SPY",)), worker_ledger)
            with admin.transaction() as tx:
                state = replace(
                    tx.get(m.MarketState, "ms"),
                    id="runtime-failure-market",
                    as_of=now + timedelta(microseconds=1),
                    features={
                        "previous_close": Decimal("99"),
                        "last_close": Decimal("100"),
                        "window_volume": Decimal("1000"),
                    },
                )
                tx.insert(state)
            calls = []

            def unavailable(_request):
                calls.append(True)
                if scenario == "worker-lease-loss":
                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                "UPDATE public.jobs SET "
                                "lease_until=clock_timestamp()-interval '1 second' "
                                "WHERE id='decision:runtime-failure-market'"
                            )
                        )
                raise httpx.ReadTimeout("synthetic local transport failure")

            worker.decision = JevDecisionEngine(
                clock=worker.clock,
                ids=worker.ids,
                api_key="synthetic-test-only",
                config=replace(load_config(), enabled=True),
                transport=httpx.MockTransport(unavailable),
            )
            before_decisions, before_proposals = count("decisions"), count("trade_proposals")
            assert worker.tick() == "DECISION_UNAVAILABLE"
            assert calls == [True], "test must reach the local Jev transport"
            assert count("trade_proposals") == before_proposals
            assert count("broker_orders") == 0 and broker.accepted_count == 0
            if scenario == "worker-jev-down":
                assert count("decisions") == before_decisions + 1
                with engine.connect() as connection:
                    assert (
                        connection.execute(
                            text(
                                "SELECT reason_code FROM public.decisions "
                                "WHERE market_state_id='runtime-failure-market'"
                            )
                        ).scalar_one()
                        == "ABSTAIN_UNAVAILABLE"
                    )
            else:
                assert count("decisions") == before_decisions
            # Real independent role can latch pause while Jev/worker has failed.
            pause(ledger=control, reason_code="PAUSED_NEW", **common)
            runtime.reconcile()
            assert poll_once(ledger=watchdog, account_id="paper").healthy
            assert context()["safety_state"] == "PAUSE_NEW"

        elif scenario == "execution-data-down":
            intent = current_intent()

            def data_down():
                raise TimeoutError("synthetic market data unavailable")

            runtime.publish_market = data_down
            assert runtime.tick() == "DATA_UNAVAILABLE"
            assert context()["safety_state"] == "PAUSE_NEW"
            assert broker.accepted_count == 0 and count("broker_orders") == 0
            try:
                runtime.process.submit(intent)
            except SubmitDenied:
                pass
            else:
                raise AssertionError("market outage allowed new submit")

        elif scenario == "execution-broker-down":
            intent = current_intent()
            accepted = runtime.process.submit(intent)
            assert accepted.status is m.OrderStatus.ACKNOWLEDGED and broker.accepted_count == 1
            broker.disconnect()
            runtime.publish_market = lambda: True  # Only the market source remains healthy.
            runtime.tick()
            assert context()["safety_state"] == "PAUSE_NEW"
            assert not context()["reconciliation_current"]
            with engine.connect() as connection:
                order = connection.execute(
                    text("SELECT status FROM public.broker_orders WHERE intent_id=:intent"),
                    {"intent": intent},
                ).scalar_one()
                assert order == "UNKNOWN"
                assert (
                    connection.execute(
                        text(
                            "SELECT state FROM public.risk_reservations r "
                            "JOIN public.order_intents i "
                            "ON i.risk_decision_id=r.risk_decision_id WHERE i.id=:intent"
                        ),
                        {"intent": intent},
                    ).scalar_one()
                    == "HELD_UNKNOWN"
                )
            assert broker.accepted_count == 1, "outage must not resubmit an accepted order"
            assert poll_once(ledger=watchdog, account_id="paper").healthy

        elif scenario == "execution-owner-loss":
            successor = ExecutionRuntime(config, execution)
            try:
                runtime.tick()
            except Exception:
                pass
            else:
                raise AssertionError("superseded process continued its tick")
            assert runtime.process._faulted
            assert broker.accepted_count == 0 and count("broker_orders") == 0
            successor.process.heartbeat()
            assert poll_once(ledger=watchdog, account_id="paper").healthy

        elif scenario == "execution-db-sticky":
            with unavailable_database(execution):
                try:
                    runtime.tick()
                except OperationalError:
                    pass
                else:
                    raise AssertionError("database outage was treated as successful tick")
            assert runtime.process._faulted
            runtime.publish_market = lambda: True
            runtime.tick()
            assert context()["safety_state"] == "PAUSE_NEW" and runtime.process._faulted
            assert broker.accepted_count == 0
            # A second tick must retain the pause generation so a fresh operator
            # recovery request can be accepted after storage returns.
            request_resume(ledger=control, expected_policy_version=1, **common)
            runtime.tick()
            assert not runtime.process._faulted
            assert context()["safety_state"] == "RUNNING"

        elif scenario == "watchdog-owner-stale":
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE public.execution_owners SET "
                        "heartbeat_at=clock_timestamp()-interval '31 seconds' "
                        "WHERE account_id='paper'"
                    )
                )
            result = poll_once(ledger=watchdog, account_id="paper")
            assert not result.healthy and result.paused
            assert context()["safety_state"] == "PAUSE_NEW"
            # DB failure is never reported as a healthy independent watchdog.
            with unavailable_database(watchdog):
                try:
                    poll_once(ledger=watchdog, account_id="paper")
                except OperationalError:
                    pass
                else:
                    raise AssertionError("watchdog fabricated health without a database")
            assert broker.accepted_count == 0
        else:
            raise AssertionError("unknown synthetic scenario")
        print("PASS runtime failure: actual roles and durable fail-closed state")
    finally:
        for item in engines:
            item.dispose()


if __name__ == "__main__":
    from risk_support import guest

    guest(checks, uppercase_instruments=True)
else:
    import pytest

    test_role_runtime_failures = pytest.mark.parametrize(
        "scenario",
        [
            "worker-jev-down",
            "worker-lease-loss",
            "execution-data-down",
            "execution-broker-down",
            "execution-owner-loss",
            "execution-db-sticky",
            "watchdog-owner-stale",
            "runtime-metrics",
            "api-concurrency",
        ],
    )(test_role_runtime_failures)
