"""Synthetic, exact-host contract tests. No credentials or network access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider, MarketDataError
from packages.domain.models import Capability, Market

NOW = datetime(2026, 10, 14, 14, 1, tzinfo=UTC)


@dataclass
class _Clock:
    value: datetime

    def now(self) -> datetime:
        return self.value


def _bar(start: datetime, *, close: str = "101.123456789012345678") -> dict[str, str]:
    return {
        "t": start.isoformat().replace("+00:00", "Z"),
        "o": "100.0",
        "h": "102.0",
        "l": "99.0",
        "c": close,
        "v": "123.5",
    }


class _Server:
    def __init__(self, now: datetime = NOW, *, crypto: bool = False, feed: str = "iex") -> None:
        self.now = now
        self.crypto = crypto
        self.feed = feed
        self.calls: list[httpx.Request] = []
        self.account: dict[str, object] = {
            "id": "11111111-1111-4111-8111-111111111111",
            "status": "ACTIVE",
            "currency": "USD",
            "account_blocked": False,
            "trading_blocked": False,
            "trade_suspended_by_user": False,
            "crypto_status": "ACTIVE",
        }
        self.asset: dict[str, object] = {
            "id": "22222222-2222-4222-8222-222222222222",
            "symbol": "BTC/USD" if crypto else "SPY",
            "class": "crypto" if crypto else "us_equity",
            "status": "active",
            "tradable": True,
            "fractionable": True,
        }
        self.broker_clock: dict[str, object] = {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "is_open": not crypto,
        }
        local_day = now.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date()
        self.calendar: list[dict[str, str]] = [
            {
                "date": local_day.isoformat(),
                "open": "09:30",
                "close": "16:00",
            }
        ]
        floor = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        symbol = "BTC/USD" if crypto else "SPY"
        self.bars: dict[str, object] = {
            "bars": {
                symbol: [_bar(floor - timedelta(minutes=30)), _bar(floor - timedelta(minutes=15))]
            },
            "next_page_token": None,
        }
        self.override: dict[str, tuple[int, object]] = {}
        self.pages: dict[str, dict[str, object]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host in {"paper-api.alpaca.markets", "data.alpaca.markets"}
        assert request.headers["APCA-API-KEY-ID"] == "synthetic-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "synthetic-secret"
        path = request.url.path
        if path in self.override:
            status, data = self.override[path]
            return httpx.Response(status, json=data)
        if path == "/v2/account":
            data = self.account
        elif path == "/v2/assets":
            assert self.crypto
            assert request.url.params["status"] == "active"
            assert request.url.params["asset_class"] == "crypto"
            data = [self.asset]
        elif path in {"/v2/assets/SPY", "/v2/assets/QQQ"}:
            assert not self.crypto
            assert path.rsplit("/", 1)[-1] == self.asset["symbol"]
            assert not request.url.params
            data = self.asset
        elif path == "/v2/clock":
            data = self.broker_clock
        elif path == "/v2/calendar":
            assert request.url.params["start"] == request.url.params["end"]
            data = self.calendar
        elif path in {"/v2/stocks/bars", "/v1beta3/crypto/us/bars"}:
            assert request.url.params["timeframe"] == "15Min"
            assert request.url.params["symbols"] == self.asset["symbol"]
            if not self.crypto:
                assert request.url.params["feed"] == self.feed
            data = self.pages.get(request.url.params.get("page_token", ""), self.bars)
        else:
            raise AssertionError(f"unexpected endpoint {path}")
        return httpx.Response(200, json=data)


def make_provider(
    server: _Server | None = None,
    *,
    now: datetime = NOW,
    enabled: bool = True,
    enable_crypto: bool = False,
    api_key: str | None = "synthetic-key",
    api_secret: str | None = "synthetic-secret",
    feed: str = "iex",
) -> tuple[AlpacaMarketDataProvider, _Server, _Clock]:
    """Fixture helper for gateway tests; calls remain inside exact MockTransport."""
    server = server or _Server(now, crypto=enable_crypto, feed=feed)
    clock = _Clock(now)
    provider = AlpacaMarketDataProvider(
        clock,
        object(),
        api_key=api_key,
        api_secret=api_secret,
        enabled=enabled,
        enable_crypto=enable_crypto,
        feed=feed,
        transport=httpx.MockTransport(server),
    )
    return provider, server, clock


def _reason(
    provider: AlpacaMarketDataProvider, symbol: str = "SPY", day: str = "2026-10-14"
) -> str:
    with pytest.raises(MarketDataError) as error:
        provider.snapshot(symbol, day)
    return error.value.reason


@pytest.mark.parametrize("symbol", ["SPY", "QQQ"])
def test_equity_snapshot_requires_fresh_normalized_proofs(symbol: str) -> None:
    provider, server, _ = make_provider()
    server.asset["symbol"] = symbol
    server.bars["bars"] = {symbol: server.bars["bars"]["SPY"]}
    snapshot = provider.snapshot(symbol, "2026-10-14")
    assert snapshot.instrument.id == symbol
    assert snapshot.instrument.market is Market.US_EQUITY
    assert snapshot.session_start == datetime(2026, 10, 14, 13, 30, tzinfo=UTC)
    assert snapshot.session_end == datetime(2026, 10, 14, 20, tzinfo=UTC)
    assert snapshot.as_of == NOW
    assert snapshot.valid_until == datetime(2026, 10, 14, 14, 2, tzinfo=UTC)
    assert snapshot.source_capabilities.history is Capability.CONFIRMED
    assert snapshot.source_capabilities.news is Capability.UNAVAILABLE
    assert snapshot.source_capabilities.papers is Capability.UNAVAILABLE
    assert snapshot.broker_capabilities.market_order is Capability.UNKNOWN
    assert snapshot.broker_capabilities.limit_order is Capability.UNKNOWN
    assert snapshot.broker_capabilities.quantity_step is None
    assert snapshot.broker_capabilities.price_tick is None
    assert snapshot.capability_version == snapshot.broker_capabilities.version
    assert all(
        bar.received_at <= snapshot.as_of and bar.available_at <= snapshot.as_of
        for bar in snapshot.bars
    )
    assert str(snapshot.bars[-1].close) == "101.123456789012345678"
    assert provider.closed_bars(symbol, "2026-10-14") == snapshot.bars
    assert all(request.method == "GET" for request in server.calls)
    assert any(request.url.path == f"/v2/assets/{symbol}" for request in server.calls)
    assert not any(request.url.path == "/v2/assets" for request in server.calls)
    assert "synthetic-secret" not in repr(snapshot)
    assert "synthetic-secret" not in repr(provider)


def test_crypto_requires_opt_in_account_and_asset_but_not_equity_clock() -> None:
    provider, server, _ = make_provider(enable_crypto=True)
    snapshot = provider.snapshot("BTC/USD", "2026-10-14")
    assert snapshot.instrument.market is Market.CRYPTO
    assert snapshot.session_start == datetime(2026, 10, 14, tzinfo=UTC)
    assert snapshot.session_end == datetime(2026, 10, 15, tzinfo=UTC)
    assert snapshot.broker_capabilities.continuous_session is Capability.CONFIRMED
    assert not any(request.url.path == "/v2/calendar" for request in server.calls)
    server.account["crypto_status"] = "INACTIVE"
    assert _reason(provider, "BTC/USD") == "CRYPTO_UNAVAILABLE"


def test_explicit_crypto_increments_map_without_certifying_order_types() -> None:
    provider, server, _ = make_provider(enable_crypto=True)
    before = provider.snapshot("BTC/USD", "2026-10-14")
    assert before.instrument.quantity_step is None
    assert before.broker_capabilities.price_tick is None
    server.asset["min_trade_increment"] = "0.00000001"
    server.asset["price_increment"] = "0.01"
    after = provider.snapshot("BTC/USD", "2026-10-14")
    assert after.instrument.quantity_step == Decimal("0.00000001")
    assert after.instrument.price_tick == Decimal("0.01")
    assert after.broker_capabilities.quantity_step == Decimal("0.00000001")
    assert after.broker_capabilities.price_tick == Decimal("0.01")
    assert after.broker_capabilities.market_order is Capability.UNKNOWN
    assert after.broker_capabilities.limit_order is Capability.UNKNOWN
    assert after.capability_version != before.capability_version
    assert after.capability_version == after.broker_capabilities.version


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_trade_increment", "0"),
        ("min_trade_increment", "NaN"),
        ("min_trade_increment", "0.0000000000000000001"),
        ("price_increment", "0"),
        ("price_increment", "Infinity"),
        ("price_increment", None),
    ],
)
def test_malformed_explicit_asset_increment_rejected(field: str, value: object) -> None:
    provider, server, _ = make_provider(enable_crypto=True)
    server.asset[field] = value
    assert _reason(provider, "BTC/USD") == "INVALID_RESPONSE"


@pytest.mark.parametrize("minute", [0, 15])
def test_crypto_window_crosses_utc_midnight(minute: int) -> None:
    now = datetime(2026, 10, 15, 0, minute, 10, tzinfo=UTC)
    provider, _, _ = make_provider(now=now, enable_crypto=True)
    snapshot = provider.snapshot("BTC/USD", "2026-10-15")
    assert snapshot.session_start == datetime(2026, 10, 15, tzinfo=UTC)
    assert snapshot.bars[-1].end_at <= now
    assert snapshot.bars[0].end_at == snapshot.bars[1].start_at


def test_disabled_unknown_and_no_credentials_issue_no_requests() -> None:
    provider, server, _ = make_provider(enabled=False)
    assert _reason(provider) == "DISABLED"
    assert server.calls == []
    provider, server, _ = make_provider()
    assert _reason(provider, "BTC/USD") == "UNKNOWN_INSTRUMENT"
    assert _reason(provider, "AAPL") == "UNKNOWN_INSTRUMENT"
    assert server.calls == []
    provider, server, _ = make_provider(api_key=None)
    assert _reason(provider) == "AUTH_UNAVAILABLE"
    assert server.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "INACTIVE"),
        ("currency", "EUR"),
        ("account_blocked", True),
        ("trading_blocked", True),
        ("trade_suspended_by_user", True),
        ("account_blocked", "false"),
    ],
)
def test_account_guard_fails_closed(field: str, value: object) -> None:
    provider, server, _ = make_provider()
    server.account[field] = value
    assert _reason(provider) == "ACCOUNT_UNAVAILABLE"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "inactive"),
        ("tradable", False),
        ("tradable", "true"),
        ("class", "crypto"),
    ],
)
def test_asset_guard_fails_closed(field: str, value: object) -> None:
    provider, server, _ = make_provider()
    server.asset[field] = value
    assert _reason(provider) == "ASSET_UNAVAILABLE"


@pytest.mark.parametrize(
    "bad_id", [None, "", 7, "not-a-uuid", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"]
)
@pytest.mark.parametrize("kind", ["account", "asset"])
def test_permission_identity_requires_canonical_uuid(kind: str, bad_id: object) -> None:
    provider, server, _ = make_provider()
    getattr(server, kind)["id"] = bad_id
    assert _reason(provider) == "INVALID_RESPONSE"


def test_permission_identity_changes_capability_version_without_exposure() -> None:
    provider, server, _ = make_provider()
    original = provider.snapshot("SPY", "2026-10-14")
    server.account["id"] = "33333333-3333-4333-8333-333333333333"
    changed_account = provider.snapshot("SPY", "2026-10-14")
    server.asset["id"] = "44444444-4444-4444-8444-444444444444"
    changed_asset = provider.snapshot("SPY", "2026-10-14")
    assert (
        len(
            {
                original.capability_version,
                changed_account.capability_version,
                changed_asset.capability_version,
            }
        )
        == 3
    )
    assert "33333333-3333-4333-8333-333333333333" not in repr(changed_account)
    assert "44444444-4444-4444-8444-444444444444" not in repr(changed_asset)


def test_feed_permission_error_never_falls_back() -> None:
    provider, server, _ = make_provider()
    server.override["/v2/stocks/bars"] = (403, {"message": "secret"})
    assert _reason(provider) == "PERMISSION_DENIED"
    assert len([request for request in server.calls if request.url.path == "/v2/stocks/bars"]) == 1


@pytest.mark.parametrize("status,reason", [(429, "RATE_LIMIT"), (500, "UNAVAILABLE")])
def test_redacted_http_errors(status: int, reason: str) -> None:
    provider, server, _ = make_provider()
    server.override["/v2/account"] = (status, {"message": "synthetic-secret"})
    assert _reason(provider) == reason


def test_stale_and_future_clock_rejected() -> None:
    provider, server, _ = make_provider()
    server.broker_clock["timestamp"] = (NOW - timedelta(seconds=121)).isoformat()
    assert _reason(provider) == "STALE_CLOCK"
    server.broker_clock["timestamp"] = (NOW + timedelta(seconds=6)).isoformat()
    assert _reason(provider) == "STALE_CLOCK"


def test_dst_and_early_close_from_calendar() -> None:
    now = datetime(2026, 11, 27, 17, 1, tzinfo=UTC)
    provider, server, _ = make_provider(now=now)
    server.calendar = [{"date": "2026-11-27", "open": "09:30", "close": "13:00"}]
    snapshot = provider.snapshot("SPY", "2026-11-27")
    assert snapshot.session_start == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert snapshot.session_end == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    server.calendar = []
    assert _reason(provider, day="2026-11-27") == "SESSION_CLOSED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("c", "NaN"),
        ("c", "Infinity"),
        ("c", "1e2"),
        ("v", "123456789012345678901"),
        ("o", "0"),
    ],
)
def test_invalid_precision_and_nonfinite_bar(field: str, value: str) -> None:
    provider, server, _ = make_provider()
    server.bars["bars"]["SPY"][1][field] = value
    assert _reason(provider) == "INVALID_BAR"


@pytest.mark.parametrize(
    "raw,valid",
    [
        (b"101.123456789012345678", True),
        (b"101.1234567890123456789", False),
    ],
)
def test_json_number_token_uses_exact_decimal_parser(raw: bytes, valid: bool) -> None:
    server = _Server()

    def numeric_wire(request: httpx.Request) -> httpx.Response:
        response = server(request)
        if request.url.path != "/v2/stocks/bars":
            return response
        original = b'"101.123456789012345678"'
        assert original in response.content
        return httpx.Response(
            200,
            content=response.content.replace(original, raw),
            headers={"content-type": "application/json"},
        )

    provider = AlpacaMarketDataProvider(
        _Clock(NOW),
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(numeric_wire),
    )
    if valid:
        snapshot = provider.snapshot("SPY", "2026-10-14")
        assert snapshot.bars[-1].close == Decimal("101.123456789012345678")
    else:
        assert _reason(provider) == "INVALID_BAR"


def test_duplicate_gap_out_of_order_and_open_bar_rejected() -> None:
    provider, server, _ = make_provider()
    bars = server.bars["bars"]["SPY"]
    bars[1]["t"] = bars[0]["t"]
    assert _reason(provider) == "DUPLICATE_BAR"
    bars[0]["t"], bars[1]["t"] = (
        (NOW - timedelta(minutes=16)).replace(minute=45, second=0).isoformat(),
        (NOW - timedelta(minutes=31)).replace(minute=30, second=0).isoformat(),
    )
    assert _reason(provider) == "BAR_GAP_OR_ORDER"
    bars[1]["t"] = NOW.replace(minute=0).isoformat()
    assert _reason(provider) == "OPEN_OR_OUTSIDE_SESSION"


def test_stale_last_bar_and_changed_bar_identity() -> None:
    provider, server, _ = make_provider()
    original = provider.snapshot("SPY", "2026-10-14").bars[-1].id
    server.bars["bars"]["SPY"][1]["c"] = "101.123456789012345679"
    corrected = provider.snapshot("SPY", "2026-10-14").bars[-1].id
    assert corrected != original
    provider, _, clock = make_provider()
    clock.value += timedelta(minutes=5)
    assert _reason(provider) == "STALE_CLOCK"


def test_incomplete_and_extra_window_rejected() -> None:
    provider, server, _ = make_provider()
    server.bars["bars"]["SPY"].pop()
    assert _reason(provider) == "INCOMPLETE_WINDOW"
    server.bars["bars"]["SPY"].append(_bar(NOW.replace(minute=0)))
    server.bars["bars"]["SPY"].append(_bar(NOW.replace(minute=0) - timedelta(minutes=15)))
    assert _reason(provider) == "INCOMPLETE_WINDOW"


def test_cursor_and_invalid_session_rejected_without_network() -> None:
    provider, server, _ = make_provider()
    with pytest.raises(MarketDataError, match="UNSUPPORTED_CURSOR"):
        provider.closed_bars("SPY", "2026-10-14", "opaque")
    assert _reason(provider, day="2026-10-14junk") == "INVALID_SESSION"
    assert server.calls == []


def test_pagination_collects_complete_window_and_rejects_repeated_token() -> None:
    provider, server, _ = make_provider()
    second = server.bars["bars"]["SPY"].pop()
    server.bars["next_page_token"] = "next"
    server.pages["next"] = {"bars": {"SPY": [second]}, "next_page_token": None}
    assert len(provider.snapshot("SPY", "2026-10-14").bars) == 2
    assert any(request.url.params.get("page_token") == "next" for request in server.calls)
    server.pages["next"]["next_page_token"] = "next"
    assert _reason(provider) == "INVALID_RESPONSE"


def test_timeout_and_malformed_json_fail_closed() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic-secret")

    provider = AlpacaMarketDataProvider(
        _Clock(NOW),
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(timeout),
    )
    assert _reason(provider) == "UNAVAILABLE"

    def duplicate(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"status":"ACTIVE","status":"ACTIVE"}')

    provider = AlpacaMarketDataProvider(
        _Clock(NOW),
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(duplicate),
    )
    assert _reason(provider) == "INVALID_RESPONSE"


def test_deeply_nested_json_has_stable_redacted_error() -> None:
    def deeply_nested(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"[" * 1500 + b"0" + b"]" * 1500,
            headers={"content-type": "application/json"},
        )

    provider = AlpacaMarketDataProvider(
        _Clock(NOW),
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(deeply_nested),
    )
    assert _reason(provider) == "INVALID_RESPONSE"


def test_inflight_aging_invalidates_capture() -> None:
    provider, server, clock = make_provider()
    original = server.__call__

    def delayed(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path == "/v2/stocks/bars":
            clock.value += timedelta(seconds=121)
        return response

    provider.close()
    provider = AlpacaMarketDataProvider(
        clock,
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(delayed),
    )
    assert _reason(provider) == "STALE_CLOCK"


def test_receipt_time_is_after_io_and_clock_reply_may_be_later_than_capture() -> None:
    provider, server, clock = make_provider()
    original = server.__call__

    def advancing(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/v2/account", "/v2/assets/SPY"}:
            clock.value += timedelta(seconds=10)
        if request.url.path == "/v2/clock":
            server.broker_clock["timestamp"] = clock.value.isoformat()
        response = original(request)
        if request.url.path == "/v2/stocks/bars":
            clock.value += timedelta(seconds=1)
        return response

    provider.close()
    provider = AlpacaMarketDataProvider(
        clock,
        object(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(advancing),
    )
    snapshot = provider.snapshot("SPY", "2026-10-14")
    assert snapshot.as_of == NOW + timedelta(seconds=21)
    assert all(bar.received_at == snapshot.as_of for bar in snapshot.bars)
    assert all(bar.available_at == snapshot.as_of for bar in snapshot.bars)


def test_feed_is_explicit_in_provenance_and_bar_identity() -> None:
    iex, _, _ = make_provider()
    sip, _, _ = make_provider(feed="sip")
    iex_snapshot = iex.snapshot("SPY", "2026-10-14")
    sip_snapshot = sip.snapshot("SPY", "2026-10-14")
    assert iex_snapshot.source_capabilities.source_id == "alpaca-market-data:iex"
    assert sip_snapshot.source_capabilities.source_id == "alpaca-market-data:sip"
    assert iex_snapshot.bars[-1].id != sip_snapshot.bars[-1].id
    assert iex_snapshot.capability_version != sip_snapshot.capability_version
    assert iex_snapshot.capability_version <= 2**31


def test_only_exact_mock_transport_can_be_injected() -> None:
    with pytest.raises(ValueError, match="exact MockTransport"):
        AlpacaMarketDataProvider(_Clock(NOW), object(), transport=httpx.HTTPTransport())
