"""Offline ADR-APW-004 equity fractional DAY-LIMIT certification only."""

import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from packages.brokers.alpaca_paper import AlpacaPaperError
from packages.brokers.fake import CapabilityError
from packages.domain.models import Capability, OrderStatus
from tests.contracts.test_alpaca_paper import (
    NOW,
    MutableClock,
    PaperServer,
    make_adapter,
    prepared,
)


@pytest.mark.parametrize("symbol", ["SPY", "QQQ"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("quantity", ["0.000000001", "0.123456789", "1.5"])
def test_certified_fractional_day_limit_exact_wire_and_replay(symbol, side, quantity):
    class CanonicalQuantityServer(PaperServer):
        def __call__(self, request):
            response = super().__call__(request)
            if request.method == "POST":
                body = json.loads(request.content)
                # The old fixture stringifies Decimal(1e-9) as an exponent;
                # the provider contract is a plain decimal quantity string.
                self.orders[body["client_order_id"]]["qty"] = body["qty"]
                return httpx.Response(200, json=self.orders[body["client_order_id"]])
            return response

    server = CanonicalQuantityServer()
    broker = make_adapter(server)
    order_intent = prepared(broker, instrument_id=symbol, side=side, quantity=quantity)
    capability = broker.capabilities(symbol)
    assert capability.fractional is Capability.CONFIRMED
    assert capability.limit_order is Capability.CONFIRMED
    assert capability.regular_session is Capability.CONFIRMED
    assert capability.quantity_step == Decimal("0.000000001")
    result = broker.submit(order_intent)
    assert result.status is OrderStatus.ACKNOWLEDGED
    assert result.requested_quantity == Decimal(quantity)
    assert broker.submit(order_intent) == result
    posts = [request for request in server.requests if request.method == "POST"]
    assert len(posts) == 1
    body = json.loads(posts[0].content)
    assert body == {
        "symbol": symbol,
        "qty": quantity,
        "side": side.lower(),
        "type": "limit",
        "time_in_force": "day",
        "order_class": "simple",
        "extended_hours": False,
        "client_order_id": "client-1",
        "limit_price": "100.01",
    }
    assert "notional" not in body
    assert server.count("GET", f"/v2/assets/{symbol}") >= 5


@pytest.mark.parametrize("fractionable", [False, None, "true", 1])
def test_fractional_requires_explicit_boolean_fractionable(fractionable):
    server = PaperServer()
    server.asset["fractionable"] = fractionable
    broker = make_adapter(server)
    order_intent = prepared(broker, quantity="0.5")
    assert broker.capabilities("SPY").fractional is Capability.UNAVAILABLE
    with pytest.raises(CapabilityError):
        broker.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 0


@pytest.mark.parametrize(
    "field,value",
    [("class", "crypto"), ("status", "inactive"), ("tradable", False), ("id", "invalid")],
)
def test_wrong_asset_rejected_before_submit(field, value):
    server = PaperServer()
    broker = make_adapter(server)
    order_intent = prepared(broker, quantity="0.5")
    server.asset[field] = value
    with pytest.raises((CapabilityError, AlpacaPaperError)):
        broker.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 0


def test_fractional_asset_change_during_lookup_cannot_reuse_old_confirmation():
    server = PaperServer()
    broker = make_adapter(server)
    order_intent = prepared(broker, quantity="0.5")
    server.after_first_lookup = lambda: server.asset.update(fractionable=False)
    with pytest.raises(CapabilityError, match="capability version changed"):
        broker.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 0


@pytest.mark.parametrize("quantity", ["0.0000000001", "0.1234567891"])
def test_overprecise_quantity_never_rounds_or_falls_back(quantity):
    server = PaperServer()
    broker = make_adapter(server)
    with pytest.raises(CapabilityError, match="fractional equity precision"):
        broker.submit(prepared(broker, quantity=quantity))
    assert server.count("POST", "/v2/orders") == 0


@pytest.mark.parametrize("price", ["100.001", "0.12345"])
def test_fractional_limit_keeps_existing_price_tick_constraints(price):
    server = PaperServer()
    broker = make_adapter(server)
    with pytest.raises(CapabilityError, match="equity price increment"):
        broker.submit(prepared(broker, quantity="0.5", limit_price=price))
    assert server.count("POST", "/v2/orders") == 0


def test_fractional_after_hours_and_stale_clock_never_submit():
    for mode in ("after-hours", "stale"):
        server = PaperServer()
        clock = MutableClock()
        broker = make_adapter(server, clock=clock)
        order_intent = prepared(broker, quantity="0.5")
        if mode == "after-hours":
            clock.value = NOW + timedelta(hours=6)
            server.clock_timestamp = clock.value.isoformat()
        else:
            server.clock_timestamp = (NOW - timedelta(seconds=121)).isoformat()
        with pytest.raises((CapabilityError, AlpacaPaperError)):
            broker.submit(order_intent)
        assert server.count("POST", "/v2/orders") == 0


@pytest.mark.parametrize(
    "field,value", [("time_in_force", "gtc"), ("extended_hours", True), ("type", "market")]
)
def test_provider_response_cannot_change_tif_session_or_limit_type(field, value):
    class ChangedResponse(PaperServer):
        def __call__(self, request):
            response = super().__call__(request)
            if request.method == "POST":
                return httpx.Response(200, json=dict(response.json(), **{field: value}))
            return response

    server = ChangedResponse()
    broker = make_adapter(server)
    order_intent = prepared(broker, quantity="0.5")
    assert broker.submit(order_intent).status is OrderStatus.UNKNOWN
    assert server.count("POST", "/v2/orders") == 1
    # Reconciliation may later prove the provider's actual order. It may only GET.
    broker.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 1
    assert (
        json.loads(next(r for r in server.requests if r.method == "POST").content)["type"]
        == "limit"
    )


@pytest.mark.parametrize("failure", ["422", "timeout"])
def test_uncertain_outcome_retains_unknown_and_never_resubmits_or_changes_type(failure):
    server = PaperServer()
    server.post_status = 422 if failure == "422" else None
    server.post_timeout_after_accept = failure == "timeout"
    broker = make_adapter(server)
    order_intent = prepared(broker, quantity="0.5")
    result = broker.submit(order_intent)
    assert result.status is OrderStatus.UNKNOWN and result.filled_quantity == 0
    if failure == "timeout":
        server.hide_client_lookup = True
    assert broker.submit(order_intent).status is OrderStatus.UNKNOWN
    assert server.count("POST", "/v2/orders") == 1
    restored = make_adapter(server, restored_intents=(order_intent,))
    assert restored.submit(order_intent).status is OrderStatus.UNKNOWN
    assert server.count("POST", "/v2/orders") == 1
    body = json.loads(next(r for r in server.requests if r.method == "POST").content)
    assert body["type"] == "limit" and "limit_price" in body and "notional" not in body


def test_whole_equity_does_not_require_fractionable_and_market_buy_still_forbidden():
    server = PaperServer()
    server.asset["fractionable"] = False
    broker = make_adapter(server)
    assert broker.submit(prepared(broker)).status is OrderStatus.ACKNOWLEDGED
    with pytest.raises(CapabilityError, match="bounded cost"):
        broker.submit(prepared(broker, client_id="no-market", quantity="2", order_type="MARKET"))
