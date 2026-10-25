"""Offline double boundary tests; no SQL, real keys, HTTP sockets or gate mocks."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider, MarketDataError
from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.domain.models import OrderIntent, OrderStatus
from services.execution.paper_credentials import PrivatePaperCredentials
from services.execution.paper_daily_evidence import PaperPreparationCollector
from services.execution.paper_verification import PaperAccountVerifier
from services.worker.market_state import PaperMarketConfig
from tests.integration.paper_browser_provider import ACCOUNT_ID, PaperProvider

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
NOW = datetime(2026, 10, 22, 14, 0, 5, tzinfo=UTC)


@pytest.fixture
def double():
    clock = SimpleNamespace(value=NOW)
    clock.now = lambda: clock.value
    provider = PaperProvider(clock)
    with httpx.Client(transport=httpx.MockTransport(provider.handle)) as client:
        yield clock, provider, client


def submit(client, key="local-test-client-1", side="buy", quantity="1", price="15.00"):
    return client.post(
        PAPER + "/v2/orders",
        json=dict(
            client_order_id=key,
            symbol="SPY",
            qty=quantity,
            side=side,
            type="limit",
            time_in_force="day",
            limit_price=price,
            order_class="simple",
            extended_hours=False,
        ),
    )


def test_actual_verifier_and_twice_confirmed_first_day_collector(double):
    clock, provider, _ = double
    credentials = PrivatePaperCredentials(
        1, "synthetic-test-key", "synthetic-test-secret", ACCOUNT_ID
    )
    reader = SimpleNamespace(load=lambda generation: credentials)
    verifier = PaperAccountVerifier(
        credentials=reader,
        clock=clock,
        expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(provider.handle),
    )
    result = verifier.verify(1)
    assert result.public.provider_verification == "CONFIRMED"
    assert result.public.cash == Decimal("100000")
    collector = PaperPreparationCollector(
        credentials=reader,
        clock=clock,
        expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(provider.handle),
    )
    proof = collector.collect(1, result.private)
    assert proof.initial.cash == Decimal("100000")
    assert proof.initial.positions == ()
    assert proof.initial.order_anchors == ()
    assert proof.daily_reference.kind == "FIRST_DAY"
    assert provider.evidence()["posts"] == provider.evidence()["deletes"] == 0


def test_actual_market_adapter_obeys_real_config_and_regular_closed_bars(double):
    clock, provider, _ = double
    config = PaperMarketConfig.load()
    with_provider = AlpacaMarketDataProvider(
        clock,
        SimpleNamespace(new=lambda: "test"),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(provider.handle),
    )
    try:
        snapshot = with_provider.snapshot("SPY", "2026-10-22", require_spread=True)
        assert snapshot.bid == Decimal("14.99") and snapshot.ask == Decimal("15.00")
        assert len(snapshot.bars) == config.required_bars == 2
        assert all(bar.close == Decimal("15") for bar in snapshot.bars)
        assert snapshot.session_start == datetime(2026, 10, 22, 13, 30, tzinfo=UTC)
        clock.value += timedelta(seconds=121)
        with pytest.raises(MarketDataError, match="STALE"):
            with_provider.snapshot("SPY", "2026-10-22", require_spread=True)
    finally:
        with_provider.close()


def test_actual_broker_submit_partial_cancel_and_financial_evidence(double):
    clock, provider, _ = double
    adapter = AlpacaPaperBrokerAdapter(
        clock,
        SimpleNamespace(new_id=lambda namespace, seed=None: f"{namespace}:{seed}"),
        api_key="synthetic-test-key",
        api_secret="synthetic-secret",
        paper_enabled=True,
        expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(provider.handle),
    )
    try:
        capability = adapter.capabilities("SPY")
        draft = OrderIntent(
            id="intent:offline-1",
            proposal_id="proposal:offline-1",
            risk_decision_id="risk:offline-1",
            client_order_id="offline-actual-adapter-1",
            instrument_id="SPY",
            side="BUY",
            order_type="LIMIT",
            quantity=Decimal("1"),
            limit_price=Decimal("15.00"),
            capability_version=capability.version,
            created_at=clock.now(),
            trace_id="a" * 32,
        )
        order = adapter.submit(draft)
        assert order.status is OrderStatus.ACKNOWLEDGED
        provider.control("partial-fill-next")
        callbacks = []
        outcome = adapter.cancel_with_outcome(
            draft.client_order_id,
            expected_broker_order_id=order.broker_order_id,
            on_lookup=lambda proof: callbacks.append("LOOKUP"),
            before_delete=lambda: callbacks.append("DELETE"),
        )
        assert outcome.kind == "ACCEPTED" and callbacks == ["LOOKUP", "DELETE"]
        provider.control("cancel-terminal")
        evidence = adapter.reconciliation_evidence(after=clock.now() - timedelta(seconds=10))
        assert evidence.cash == Decimal("99992.500")
        assert evidence.equity == Decimal("100000")
        assert len(evidence.activities) == 1
        assert evidence.positions[0].quantity == Decimal("0.5")
        assert evidence.orders[0].status is OrderStatus.CANCELED
        assert provider.evidence()["posts"] == provider.evidence()["deletes"] == 1
    finally:
        adapter.close()


def test_no_auto_fill_buy_sell_exact_money_and_safe_evidence(double):
    _, provider, client = double
    assert submit(client, quantity="0.123456789").status_code == 200
    assert provider.evidence()["cash"] == "100000"
    assert client.get(PAPER + "/v2/positions").json() == []
    provider.control("fill-next")
    assert Decimal(provider.evidence()["cash"]) == Decimal("99998.14814816500")
    position = client.get(PAPER + "/v2/positions/SPY").json()
    assert Decimal(position["cost_basis"]) == Decimal("1.85185183500")
    assert submit(client, "local-sell-1", "sell", "0.123456789", "14.99").status_code == 200
    provider.control("fill-next")
    evidence = provider.evidence()
    assert Decimal(evidence["cash"]) == Decimal("99999.99876543211")
    assert evidence["positions"] == {}
    assert evidence["fills"] == evidence["filledOrders"] == 2
    serialized = json.dumps(evidence)
    assert ACCOUNT_ID not in serialized and "synthetic" not in serialized


def test_actual_adapter_mixed_buy_prices_partial_sell_and_exact_residual_basis(double):
    clock, provider, client = double
    adapter = AlpacaPaperBrokerAdapter(
        clock,
        SimpleNamespace(new_id=lambda namespace, seed=None: f"{namespace}:{seed}"),
        api_key="synthetic-test-key",
        api_secret="synthetic-secret",
        paper_enabled=True,
        expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(provider.handle),
    )
    baseline = clock.now() - timedelta(seconds=10)

    def trade(number, side, quantity, price):
        capability = adapter.capabilities("SPY")
        draft = OrderIntent(
            id=f"intent:mixed-{number}",
            proposal_id=f"proposal:mixed-{number}",
            risk_decision_id=f"risk:mixed-{number}",
            client_order_id=f"mixed-price-{number}",
            instrument_id="SPY",
            side=side,
            order_type="LIMIT",
            quantity=Decimal(quantity),
            limit_price=Decimal(price),
            capability_version=capability.version,
            created_at=clock.now(),
            trace_id="a" * 32,
        )
        assert adapter.submit(draft).status is OrderStatus.ACKNOWLEDGED
        clock.value += timedelta(seconds=1)
        provider.control("fill-next")
        return adapter.reconciliation_evidence(after=baseline)

    try:
        trade(1, "BUY", "1", "15.00")
        mixed = trade(2, "BUY", "2", "14.99")
        assert mixed.positions[0].quantity == Decimal("3")
        assert mixed.positions[0].cost_basis == Decimal("44.98")
        position = client.get(PAPER + "/v2/positions/SPY").json()
        assert position["avg_entry_price"] == "14.993333333333333333"
        remaining = trade(3, "SELL", "1", "14.99")
        assert remaining.positions[0].cost_basis == Decimal("29.986666666666666667")
        assert remaining.positions[0].quantity == Decimal("2")
        position = client.get(PAPER + "/v2/positions/SPY").json()
        assert position["avg_entry_price"] == "14.993333333333333334"
        closed = trade(4, "SELL", "2", "14.99")
        assert closed.positions == ()
        assert closed.cash == closed.equity == Decimal("99999.99")
        assert len(closed.activities) == 4
        assert provider.evidence()["posts"] == provider.evidence()["fills"] == 4
    finally:
        adapter.close()


def test_duplicate_parallel_posts_never_replace_immutable_terms(double):
    _, provider, client = double
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: submit(client), range(8)))
    assert sorted(r.status_code for r in responses) == [200] + [422] * 7
    assert submit(client, price="14.99").status_code == 422
    order = client.get(
        PAPER + "/v2/orders:by_client_order_id", params={"client_order_id": "local-test-client-1"}
    ).json()
    assert order["limit_price"] == "15.00"
    assert provider.evidence()["orders"] == 1


def test_cancel_ack_is_not_terminal_or_financial_release(double):
    _, provider, client = double
    order = submit(client).json()
    with pytest.raises(ValueError, match="NO_CANCEL_REQUEST"):
        provider.control("cancel-terminal")
    assert client.delete(PAPER + "/v2/orders/" + order["id"]).status_code == 204
    assert client.get(PAPER + "/v2/orders/" + order["id"]).json()["status"] == "pending_cancel"
    assert provider.evidence()["cash"] == "100000"
    assert provider.evidence()["openOrders"] == 1
    provider.control("cancel-terminal")
    assert provider.evidence()["canceledOrders"] == 1
    assert provider.evidence()["activities"] == 0


@pytest.mark.parametrize(
    "fault,expected", [("rate-limit-next-delete", 429), ("timeout-next-delete", None)]
)
def test_delete_faults_preserve_uncertain_obligation(double, fault, expected):
    _, provider, client = double
    order = submit(client).json()
    provider.control(fault)
    if expected:
        assert client.delete(PAPER + "/v2/orders/" + order["id"]).status_code == expected
        with pytest.raises(ValueError, match="NO_CANCEL_REQUEST"):
            provider.control("cancel-terminal")
    else:
        with pytest.raises(httpx.ReadTimeout):
            client.delete(PAPER + "/v2/orders/" + order["id"])
        provider.control("cancel-terminal")
    assert provider.evidence()["cash"] == "100000"
    assert provider.evidence()["fills"] == 0


def test_submit_timeout_retains_authoritative_lookup_without_second_post(double):
    _, provider, client = double
    provider.control("timeout-next-submit")
    with pytest.raises(httpx.ReadTimeout):
        submit(client)
    result = client.get(
        PAPER + "/v2/orders:by_client_order_id", params={"client_order_id": "local-test-client-1"}
    )
    assert result.status_code == 200 and result.json()["status"] == "new"
    assert provider.evidence()["posts"] == 1


def test_partial_fill_then_full_fill_wins_cancel_and_filters_paginate(double):
    clock, provider, client = double
    order = submit(client).json()
    clock.value += timedelta(seconds=1)
    provider.control("partial-fill-next")
    middle = clock.now()
    clock.value += timedelta(seconds=1)
    client.delete(PAPER + "/v2/orders/" + order["id"])
    provider.control("fill-next")
    assert provider.evidence()["filledOrders"] == 1
    assert provider.evidence()["canceledOrders"] == 0
    all_rows = client.get(
        PAPER + "/v2/account/activities", params={"direction": "asc", "page_size": "1"}
    ).json()
    assert len(all_rows) == 1 and all_rows[0]["cum_qty"] == "0.5"
    next_rows = client.get(
        PAPER + "/v2/account/activities",
        params={"direction": "asc", "page_token": all_rows[0]["id"]},
    ).json()
    assert len(next_rows) == 1 and next_rows[0]["cum_qty"] == "1.0"
    assert (
        client.get(PAPER + "/v2/account/activities", params={"until": middle.isoformat()}).json()
        == all_rows
    )
    assert (
        client.get(
            PAPER + "/v2/account/activities", params={"after": clock.now().isoformat()}
        ).json()
        == next_rows
    )
    assert client.get(PAPER + "/v2/orders", params={"status": "open"}).json() == []


def test_real_calendar_weekend_and_holiday_have_no_regular_session(double):
    clock, provider, client = double
    for day in (datetime(2026, 10, 24, 14, tzinfo=UTC), datetime(2026, 11, 26, 14, tzinfo=UTC)):
        clock.value = day
        assert client.get(PAPER + "/v2/clock").json()["is_open"] is False
        assert (
            client.get(PAPER + "/v2/calendar", params={"start": day.date().isoformat()}).json()
            == []
        )
    clock.value = datetime(2026, 11, 27, 17, tzinfo=UTC)
    assert (
        client.get(PAPER + "/v2/calendar", params={"start": "2026-11-27"}).json()[0]["close"]
        == "13:00"
    )


def test_no_external_requests_and_no_bypassing_before_io(double):
    clock, _, client = double
    calls = []
    provider = PaperProvider(clock, before_io=lambda request: calls.append(request.method))
    with pytest.raises(AssertionError, match="UNEXPECTED_REQUEST"):
        provider.handle(httpx.Request("GET", "https://example.invalid/v2/account"))
    assert calls == []
    provider.handle(httpx.Request("GET", PAPER + "/v2/account"))
    assert calls == ["GET"]
    assert submit(client, quantity="1.0000000001").status_code == 422
    assert submit(client, price="15.001").status_code == 422
