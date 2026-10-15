"""Offline Alpaca Paper order contract; no real request leaves MockTransport."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext

import httpx
import pytest

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter, AlpacaPaperError
from packages.brokers.fake import CapabilityError, IdempotencyConflict
from packages.domain.models import OrderIntent, OrderStatus, Reconciliation
from packages.domain.ports import BrokerAdapter

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
ACCOUNT_ID = "10000000-0000-4000-8000-000000000001"
ASSET_ID = "20000000-0000-4000-8000-000000000001"
BROKER_ID = "30000000-0000-4000-8000-000000000001"


class FrozenClock:
    def now(self) -> datetime:
        return NOW


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class SeededIds:
    def new_id(self, namespace: str, seed: str | None = None) -> str:
        return f"{namespace}:{seed}"


def intent(
    client_id: str = "client-1",
    *,
    side: str = "BUY",
    order_type: str = "LIMIT",
    quantity: str = "2",
    instrument_id: str = "SPY",
    limit_price: str = "100.01",
    capability_version: int = 1,
) -> OrderIntent:
    return OrderIntent(
        id=f"intent:{client_id}",
        proposal_id="proposal-1",
        risk_decision_id="risk-1",
        client_order_id=client_id,
        instrument_id=instrument_id,
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        capability_version=capability_version,
        created_at=NOW,
        trace_id="a" * 32,
        limit_price=Decimal(limit_price) if order_type == "LIMIT" else None,
    )


def payload(
    order_intent: OrderIntent,
    *,
    status: str = "new",
    filled_qty: str = "0",
    filled_avg_price: str | None = None,
    broker_id: str = BROKER_ID,
) -> dict[str, object]:
    return {
        "id": broker_id,
        "client_order_id": order_intent.client_order_id,
        "symbol": order_intent.instrument_id,
        "side": order_intent.side.lower(),
        "type": order_intent.order_type.lower(),
        "time_in_force": "gtc" if order_intent.instrument_id == "BTC/USD" else "day",
        "order_class": "simple",
        "qty": str(order_intent.quantity),
        "limit_price": str(order_intent.limit_price)
        if order_intent.limit_price is not None
        else None,
        "status": status,
        "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price,
        "created_at": "2026-10-14T14:59:00Z",
        "updated_at": "2026-10-14T15:00:00Z",
    }


class PaperServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.orders: dict[str, dict[str, object]] = {}
        self.post_status: int | None = None
        self.post_timeout_after_accept = False
        self.after_first_lookup: object | None = None
        self.hide_client_lookup = False
        self.after_second_position: object | None = None
        self.account_id = ACCOUNT_ID
        self.account_blocked = False
        self.cash = "1000.00"
        self.position = "5"
        self.clock_timestamp = "2026-10-14T15:00:00Z"
        self.calendar_close = "16:00"
        self.asset = {
            "id": ASSET_ID,
            "symbol": "SPY",
            "class": "us_equity",
            "status": "active",
            "tradable": True,
            "fractionable": True,
        }
        self.crypto_asset = {
            "id": "20000000-0000-4000-8000-000000000002",
            "symbol": "BTC/USD",
            "class": "crypto",
            "status": "active",
            "tradable": True,
            "min_trade_increment": "0.00000001",
            "price_increment": "0.01",
            "min_order_size": "0.00001",
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "paper-api.alpaca.markets"
        assert request.headers["APCA-API-KEY-ID"] == "test-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "test-secret"
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(
                200,
                json={
                    "id": self.account_id,
                    "status": "ACTIVE",
                    "currency": "USD",
                    "account_blocked": self.account_blocked,
                    "trading_blocked": False,
                    "trade_suspended_by_user": False,
                    "crypto_status": "ACTIVE",
                    "cash": self.cash,
                },
            )
        if path == "/v2/clock":
            return httpx.Response(200, json={"timestamp": self.clock_timestamp, "is_open": True})
        if path == "/v2/calendar":
            assert request.url.params["start"] == "2026-10-14"
            assert request.url.params["end"] == "2026-10-14"
            return httpx.Response(200, json=[{"date": "2026-10-14", "open": "09:30",
                                              "close": self.calendar_close}])
        if path in {"/v2/assets/SPY", "/v2/assets/QQQ"}:
            return httpx.Response(200, json={**self.asset, "symbol": path.rsplit("/", 1)[-1]})
        if path == "/v2/assets":
            return httpx.Response(200, json=[self.crypto_asset])
        if path.startswith("/v2/positions/"):
            if self.count("GET", path) == 2 and self.after_second_position is not None:
                callback = self.after_second_position
                self.after_second_position = None
                callback()
            return httpx.Response(
                200,
                json={
                    "symbol": path.rsplit("/", 1)[-1],
                    "qty": self.position,
                    "qty_available": self.position,
                },
            )
        if path == "/v2/orders:by_client_order_id":
            found = (
                None
                if self.hide_client_lookup
                else self.orders.get(request.url.params["client_order_id"])
            )
            callback = self.after_first_lookup
            if callback is not None:
                self.after_first_lookup = None
                callback()
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        if path == "/v2/orders" and request.method == "POST":
            if self.post_status is not None:
                return httpx.Response(self.post_status, json={"code": "synthetic"})
            body = json.loads(request.content)
            order_intent = intent(
                body["client_order_id"],
                side=body["side"].upper(),
                order_type=body["type"].upper(),
                quantity=body["qty"],
                instrument_id=body["symbol"],
                limit_price=body.get("limit_price") or "1",
            )
            self.orders[order_intent.client_order_id] = payload(order_intent)
            if self.post_timeout_after_accept:
                raise httpx.ReadTimeout("synthetic post-accept timeout", request=request)
            return httpx.Response(200, json=self.orders[order_intent.client_order_id])
        if path.startswith("/v2/orders/"):
            found = next(
                (order for order in self.orders.values() if order["id"] == path.rsplit("/", 1)[-1]),
                None,
            )
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        raise AssertionError(f"unexpected route: {request.method} {path}")

    def count(self, method: str, path: str) -> int:
        return sum(req.method == method and req.url.path == path for req in self.requests)


def make_adapter(
    server: PaperServer | None = None,
    *,
    enabled: bool = True,
    restored_intents: tuple[OrderIntent, ...] = (),
    attempted_client_ids: frozenset[str] = frozenset(),
    clock: FrozenClock | MutableClock | None = None,
    cash_cushion_fraction: Decimal = Decimal("0.01"),
) -> AlpacaPaperBrokerAdapter:
    server = server or PaperServer()
    return AlpacaPaperBrokerAdapter(
        clock or FrozenClock(),
        SeededIds(),
        api_key="test-key",
        api_secret="test-secret",
        paper_enabled=enabled,
        expected_account_id=ACCOUNT_ID,
        enable_crypto=True,
        transport=httpx.MockTransport(server),
        restored_intents=restored_intents,
        attempted_client_ids=attempted_client_ids,
        cash_cushion_fraction=cash_cushion_fraction,
    )


def prepared(adapter: AlpacaPaperBrokerAdapter, **changes: object) -> OrderIntent:
    draft = intent(**changes)
    cap = adapter.capabilities(draft.instrument_id)
    return replace(draft, capability_version=cap.version)


def test_disabled_and_fixed_transport_guard() -> None:
    server = PaperServer()
    adapter: BrokerAdapter = make_adapter(server, enabled=False)
    with pytest.raises(AlpacaPaperError, match="PAPER_DISABLED"):
        adapter.capabilities("SPY")
    assert server.requests == []
    with pytest.raises(ValueError, match="MockTransport"):
        AlpacaPaperBrokerAdapter(
            FrozenClock(), SeededIds(), paper_enabled=True, transport=httpx.HTTPTransport()
        )
    with pytest.raises(AlpacaPaperError, match="INVALID_RESPONSE"):
        AlpacaPaperBrokerAdapter(FrozenClock(), SeededIds(), expected_account_id="not-a-uuid")


def test_limit_mapping_exact_decimal_and_idempotency() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter, limit_price="100.01")
    first = adapter.submit(order_intent)
    assert first.status is OrderStatus.ACKNOWLEDGED
    assert first.broker_order_id == BROKER_ID
    assert first.id != first.broker_order_id
    request = next(req for req in server.requests if req.method == "POST")
    body = json.loads(request.content)
    assert body == {
        "symbol": "SPY",
        "qty": "2",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "order_class": "simple",
        "extended_hours": False,
        "client_order_id": "client-1",
        "limit_price": "100.01",
    }
    assert adapter.submit(order_intent) == first
    assert server.count("POST", "/v2/orders") == 1
    with pytest.raises(IdempotencyConflict, match="payload mismatch"):
        adapter.submit(replace(order_intent, quantity=Decimal("3")))
    report = adapter.reports("client-1")[0]
    assert report.fee is None and report.reconciliation is Reconciliation.PENDING


def test_preexisting_and_restored_attempt_never_post() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    server.orders["client-1"] = payload(order_intent)
    assert adapter.submit(order_intent).broker_order_id == BROKER_ID
    assert server.count("POST", "/v2/orders") == 0
    restored = make_adapter(
        server, restored_intents=(order_intent,), attempted_client_ids=frozenset({"client-1"})
    )
    assert restored.submit(order_intent).broker_order_id == BROKER_ID
    assert server.count("POST", "/v2/orders") == 0
    del server.orders["client-1"]
    assert restored.submit(order_intent).status is OrderStatus.UNKNOWN
    assert server.count("POST", "/v2/orders") == 0


@pytest.mark.parametrize("status", [429, 422, 500])
def test_ambiguous_post_status_reserves_id_without_retry(status: int) -> None:
    server = PaperServer()
    server.post_status = status
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    assert adapter.submit(order_intent).status is OrderStatus.UNKNOWN
    assert adapter.submit(order_intent).status is OrderStatus.UNKNOWN
    assert server.count("POST", "/v2/orders") == 1


def test_timeout_after_accept_queries_client_without_second_post() -> None:
    server = PaperServer()
    server.post_timeout_after_accept = True
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    assert adapter.submit(order_intent).status is OrderStatus.UNKNOWN
    assert adapter.submit(order_intent).status is OrderStatus.ACKNOWLEDGED
    assert server.count("POST", "/v2/orders") == 1


def test_capability_cash_fraction_and_no_short_guard() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    whole = prepared(adapter, quantity="2")
    with pytest.raises(CapabilityError, match="capability version"):
        adapter.submit(replace(whole, capability_version=1))
    fractional = prepared(adapter, client_id="fraction", quantity="0.5", order_type="MARKET")
    with pytest.raises(CapabilityError, match="bounded cost"):
        adapter.submit(fractional)
    with pytest.raises(CapabilityError, match="fractional equities"):
        adapter.submit(replace(fractional, order_type="LIMIT", limit_price=Decimal("100.01")))
    with pytest.raises(CapabilityError, match="insufficient cash"):
        adapter.submit(replace(whole, quantity=Decimal("11")))
    seller = prepared(adapter, client_id="sell", side="SELL", quantity="6")
    with pytest.raises(CapabilityError, match="short sale"):
        adapter.submit(seller)
    assert server.count("POST", "/v2/orders") == 0


def test_partial_cancel_race_and_unknown_status_fee_pending() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter, side="SELL")
    adapter.submit(order_intent)
    server.orders["client-1"] = payload(
        order_intent, status="partially_filled", filled_qty="1", filled_avg_price="100.01"
    )
    assert adapter.order_status("client-1").filled_quantity == Decimal("1")
    assert adapter.cancel("client-1").status is OrderStatus.CANCEL_PENDING
    assert server.count("DELETE", f"/v2/orders/{BROKER_ID}") == 1
    server.orders["client-1"] = payload(
        order_intent, status="filled", filled_qty="2", filled_avg_price="100.02"
    )
    assert adapter.reconcile("client-1", BROKER_ID).status is OrderStatus.FILLED
    assert adapter.reports("client-1")[-1].fee is None
    server.orders["client-1"] = payload(
        order_intent, status="some_new_vendor_state", filled_qty="2", filled_avg_price="100.02"
    )
    with pytest.raises(IdempotencyConflict, match="terminal order regressed"):
        adapter.order_status("client-1")
    server.orders["client-1"] = payload(
        order_intent, status="partially_filled", filled_qty="0.5", filled_avg_price="100.02"
    )
    with pytest.raises(IdempotencyConflict, match="regressed"):
        adapter.order_status("client-1")


def test_foreign_ids_and_malformed_response_rejected() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    adapter.bind_intent(order_intent)
    server.orders["client-1"] = payload(order_intent)
    server.orders["client-1"]["client_order_id"] = "foreign"
    with pytest.raises(IdempotencyConflict, match="disagree"):
        adapter.order_status("client-1")
    server.orders["client-1"] = payload(order_intent, filled_qty="3")
    with pytest.raises(IdempotencyConflict, match="quantity"):
        adapter.order_status("client-1")
    assert server.count("POST", "/v2/orders") == 0


def test_wrong_account_and_crypto_requires_increments() -> None:
    server = PaperServer()
    server.account_id = "10000000-0000-4000-8000-000000000002"
    adapter = make_adapter(server)
    with pytest.raises(AlpacaPaperError, match="ACCOUNT_MISMATCH"):
        adapter.capabilities("SPY")
    assert server.count("POST", "/v2/orders") == 0
    server.account_id = ACCOUNT_ID
    cap = adapter.capabilities("BTC/USD")
    assert cap.continuous_session.name == "CONFIRMED"
    assert cap.quantity_step == Decimal("0.00000001")
    del server.crypto_asset["min_order_size"]
    with pytest.raises(AlpacaPaperError, match="INVALID_RESPONSE"):
        adapter.capabilities("BTC/USD")


def test_final_cash_and_position_recheck_block_race() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    buy = prepared(adapter)
    server.after_first_lookup = lambda: setattr(server, "cash", "1")
    with pytest.raises(CapabilityError, match="insufficient cash"):
        adapter.submit(buy)
    assert server.count("POST", "/v2/orders") == 0

    seller = prepared(adapter, client_id="sell", side="SELL")
    server.after_first_lookup = lambda: setattr(server, "position", "0")
    with pytest.raises(CapabilityError, match="short sale"):
        adapter.submit(seller)
    assert server.count("POST", "/v2/orders") == 0


def test_reconcile_broker_id_after_restart_when_client_query_missing() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    server.orders["client-1"] = payload(order_intent)
    server.hide_client_lookup = True
    restored = make_adapter(server, restored_intents=(order_intent,),
                            attempted_client_ids=frozenset({"client-1"}))
    assert restored.reconcile("client-1", BROKER_ID).status is OrderStatus.ACKNOWLEDGED
    assert server.count("POST", "/v2/orders") == 0
    server.orders["client-1"]["client_order_id"] = "foreign"
    with pytest.raises(IdempotencyConflict, match="disagree"):
        restored.reconcile("client-1", BROKER_ID)


def test_concurrent_same_client_id_has_one_post() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adapter.submit, (order_intent, order_intent)))
    assert results[0] == results[1]
    assert server.count("POST", "/v2/orders") == 1


def test_crypto_limit_quantity_minimum_and_price_increment() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter, instrument_id="BTC/USD", quantity="0.00010",
                            limit_price="100.01")
    assert adapter.submit(order_intent).status is OrderStatus.ACKNOWLEDGED
    body = json.loads(next(req.content for req in server.requests if req.method == "POST"))
    assert body["time_in_force"] == "gtc"
    assert body["qty"] == "0.0001"
    assert body["limit_price"] == "100.01"
    with pytest.raises(CapabilityError, match="crypto quantity"):
        adapter.submit(replace(order_intent, client_order_id="too-small",
                               quantity=Decimal("0.000001")))
    with pytest.raises(CapabilityError, match="crypto price increment"):
        adapter.submit(replace(order_intent, client_order_id="bad-tick",
                               limit_price=Decimal("100.001")))
    with pytest.raises(CapabilityError, match="crypto notional ceiling"):
        adapter.submit(replace(order_intent, client_order_id="too-large",
                               quantity=Decimal("2000")))
    assert server.count("POST", "/v2/orders") == 1


def test_fee_cash_buffer_rejects_near_cash_buy() -> None:
    server = PaperServer()
    server.cash = "200.01"
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    with pytest.raises(CapabilityError, match="insufficient cash"):
        adapter.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 0


def test_duplicate_json_keys_and_body_bound_fail_closed() -> None:
    server = PaperServer()

    def malformed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/account":
            return httpx.Response(200, content=b'{"id":"a","id":"b"}',
                                  headers={"content-type": "application/json"})
        return server(request)

    adapter = AlpacaPaperBrokerAdapter(
        FrozenClock(), SeededIds(), api_key="test-key", api_secret="test-secret",
        paper_enabled=True, expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(malformed),
    )
    with pytest.raises(AlpacaPaperError, match="INVALID_RESPONSE"):
        adapter.capabilities("SPY")

    def oversized(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/account":
            return httpx.Response(200, content=b"{" + b" " * 1_000_001 + b"}",
                                  headers={"content-type": "application/json"})
        return server(request)

    large_adapter = AlpacaPaperBrokerAdapter(
        FrozenClock(), SeededIds(), api_key="test-key", api_secret="test-secret",
        paper_enabled=True, expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(oversized),
    )
    with pytest.raises(AlpacaPaperError, match="INVALID_RESPONSE"):
        large_adapter.capabilities("SPY")


def test_report_identity_stable_across_restart_and_distinct_after_fill() -> None:
    server = PaperServer()
    first = make_adapter(server)
    order_intent = prepared(first, side="SELL")
    server.orders["client-1"] = payload(order_intent, status="partially_filled",
                                         filled_qty="1", filled_avg_price="100.01")
    first.bind_intent(order_intent)
    first.order_status("client-1")
    partial_report = first.reports("client-1")[-1]
    replay = make_adapter(server, restored_intents=(order_intent,),
                          attempted_client_ids=frozenset({"client-1"}))
    replay.order_status("client-1")
    assert replay.reports("client-1")[-1].broker_event_id == partial_report.broker_event_id
    server.orders["client-1"] = payload(order_intent, status="filled", filled_qty="2",
                                         filled_avg_price="100.02")
    recovered = make_adapter(server, restored_intents=(order_intent,),
                             attempted_client_ids=frozenset({"client-1"}))
    recovered.order_status("client-1")
    final_report = recovered.reports("client-1")[-1]
    assert final_report.broker_event_id != partial_report.broker_event_id
    assert final_report.id != partial_report.id
    assert final_report.reconciliation is Reconciliation.PENDING
    assert final_report.fee is None


def test_rotated_account_blocks_all_order_routes_but_blocked_account_can_cancel() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    server.orders["client-1"] = payload(order_intent)
    adapter.bind_intent(order_intent)
    assert adapter.order_status("client-1").broker_order_id == BROKER_ID
    server.account_blocked = True
    assert adapter.order_status("client-1").status is OrderStatus.ACKNOWLEDGED
    assert adapter.cancel("client-1").status is OrderStatus.CANCEL_PENDING

    server.account_id = "10000000-0000-4000-8000-000000000002"
    before = [req for req in server.requests if req.url.path.startswith("/v2/orders")]
    for operation in (
        lambda: adapter.order_status("client-1"),
        lambda: adapter.by_broker_id(BROKER_ID),
        lambda: adapter.cancel("client-1"),
        lambda: adapter.reconcile("client-1", BROKER_ID),
        lambda: adapter.submit(order_intent),
    ):
        with pytest.raises(AlpacaPaperError, match="ACCOUNT_MISMATCH"):
            operation()
    after = [req for req in server.requests if req.url.path.startswith("/v2/orders")]
    assert len(after) == len(before)


def test_delayed_final_position_read_cannot_cross_early_close() -> None:
    server = PaperServer()
    clock = MutableClock(datetime(2026, 10, 14, 16, 59, tzinfo=UTC))
    server.clock_timestamp = "2026-10-14T16:59:00Z"
    server.calendar_close = "13:00"
    adapter = make_adapter(server, clock=clock)
    order_intent = prepared(adapter, side="SELL")
    server.after_second_position = lambda: setattr(
        clock, "value", datetime(2026, 10, 14, 17, 0, tzinfo=UTC)
    )
    with pytest.raises(AlpacaPaperError, match="STALE_CLOCK"):
        adapter.submit(order_intent)
    assert server.count("POST", "/v2/orders") == 0


def test_cash_cushion_is_explicit_capability_policy() -> None:
    server = PaperServer()
    default = make_adapter(server).capabilities("SPY")
    strict = make_adapter(server, cash_cushion_fraction=Decimal("0.02")).capabilities("SPY")
    assert default.version != strict.version
    with localcontext() as context:
        context.prec = 2
        tiny = make_adapter(
            server, cash_cushion_fraction=Decimal("0.00000000000000001")
        )
        assert tiny._cash_buffer == Decimal("1.00000000000000001")
        assert tiny.capabilities("SPY").version != make_adapter(
            server, cash_cushion_fraction=Decimal("0.00000000000000002")
        ).capabilities("SPY").version
    with pytest.raises(ValueError, match="cash cushion"):
        make_adapter(server, cash_cushion_fraction=Decimal("1.01"))
    with pytest.raises(ValueError, match="cash cushion"):
        make_adapter(server, cash_cushion_fraction=Decimal("0"))


def test_crypto_capability_binds_minimum_and_ignores_equity_clock_phase() -> None:
    server = PaperServer()
    adapter = make_adapter(server)
    original = adapter.capabilities("BTC/USD")
    server.crypto_asset["min_order_size"] = "0.001"
    changed = adapter.capabilities("BTC/USD")
    assert changed.version != original.version
    original_call = server.__call__

    def closed_equity_clock(request):
        if request.url.path == "/v2/clock":
            server.requests.append(request)
            return httpx.Response(200, json={"timestamp": NOW.isoformat(), "is_open": False})
        return original_call(request)

    closed = AlpacaPaperBrokerAdapter(
        FrozenClock(), SeededIds(), api_key="test-key", api_secret="test-secret",
        paper_enabled=True, expected_account_id=ACCOUNT_ID, enable_crypto=True,
        transport=httpx.MockTransport(closed_equity_clock),
    )
    assert closed.capabilities("BTC/USD").version == changed.version


@pytest.mark.parametrize("order_type,qty,price,expected_qty,expected_price", [
    ("MARKET", "0.500000000000000000", None, "0.5", None),
    ("LIMIT", "2.000000000000000000", "100.010000000000000000", "2", "100.01"),
])
def test_database_scale_is_losslessly_normalized_on_wire(
    order_type, qty, price, expected_qty, expected_price
):
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(
        adapter, side="SELL", order_type=order_type, quantity=qty,
        limit_price=price or "100.01",
    )
    assert adapter.submit(order_intent).status is OrderStatus.ACKNOWLEDGED
    body = json.loads(next(req.content for req in server.requests if req.method == "POST"))
    assert body["qty"] == expected_qty
    assert body.get("limit_price") == expected_price
