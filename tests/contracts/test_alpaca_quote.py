"""Fresh bid/ask evidence uses exact mocked provider endpoints; never real orders."""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider, MarketDataError
from services.execution.market_gateway import MarketGateway
from tests.contracts.test_alpaca_market import NOW, _Server, make_provider


class QuoteServer(_Server):
    def __init__(self, *, crypto=False):
        super().__init__(crypto=crypto)
        self.quote = {"bp": "100.10", "ap": "100.25", "t": NOW.isoformat()}
        self.quote_payload = None
        self.quote_status = 200

    def __call__(self, request):
        if "quotes" not in request.url.path:
            return super().__call__(request)
        self.calls.append(request)
        assert request.method == "GET"
        assert str(request.url).startswith("https://data.alpaca.markets/")
        assert request.headers["APCA-API-KEY-ID"] == "synthetic-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "synthetic-secret"
        if self.crypto:
            assert request.url.path == "/v1beta3/crypto/us/latest/quotes"
            assert dict(request.url.params) == {"symbols": "BTC/USD"}
        else:
            assert request.url.path == "/v2/stocks/quotes/latest"
            assert dict(request.url.params) == {"symbols": "SPY", "feed": "iex"}
        return httpx.Response(self.quote_status, json=(
            self.quote_payload if self.quote_payload is not None
            else {"quotes": {self.asset["symbol"]: self.quote}}
        ))


@pytest.mark.parametrize("crypto", [False, True])
def test_spread_is_actual_decimal_price_difference_with_fresh_provider_proof(crypto):
    server = QuoteServer(crypto=crypto)
    provider, _, clock = make_provider(server, enable_crypto=crypto)
    try:
        feed = MarketGateway(provider=provider, clock=clock).read(
            "BTC/USD" if crypto else "SPY", "2026-10-14", require_spread=True,
        )
        snapshot = feed.snapshot
        assert snapshot.spread == Decimal("0.15")
        assert snapshot.quote_provider_time == NOW
        assert snapshot.quote_observed_at == NOW
        assert snapshot.valid_until <= snapshot.quote_provider_time + timedelta(seconds=120)
        assert len([call for call in server.calls if "quotes" in call.url.path]) == 1
    finally:
        provider.close()


@pytest.mark.parametrize("patch,reason", [
    ({"bp": "0"}, "INVALID_QUOTE"),
    ({"bp": "-1"}, "INVALID_QUOTE"),
    ({"ap": "100.09"}, "INVALID_QUOTE"),
    ({"bp": "NaN"}, "INVALID_QUOTE"),
    ({"ap": "Infinity"}, "INVALID_QUOTE"),
    ({"bp": True}, "INVALID_QUOTE"),
    ({"ap": None}, "INVALID_QUOTE"),
    ({"t": "2026-10-14T14:01:00"}, "INVALID_QUOTE"),
    ({"t": (NOW - timedelta(seconds=120)).isoformat()}, "STALE_QUOTE"),
    ({"t": (NOW + timedelta(microseconds=1)).isoformat()}, "STALE_QUOTE"),
])
def test_invalid_or_stale_quote_blocks_snapshot_instead_of_inventing_zero(patch, reason):
    server = QuoteServer()
    server.quote.update(patch)
    provider, _, _ = make_provider(server)
    try:
        with pytest.raises(MarketDataError) as error:
            provider.snapshot("SPY", "2026-10-14", require_spread=True)
        assert error.value.reason == reason
    finally:
        provider.close()


@pytest.mark.parametrize("payload", [{}, {"quotes": {}}, {"quotes": {"QQQ": {}}},
                                        {"quotes": {"SPY": {}, "QQQ": {}}}])
def test_missing_or_wrong_asset_quote_is_unavailable(payload):
    server = QuoteServer()
    server.quote_payload = payload
    provider, _, _ = make_provider(server)
    try:
        with pytest.raises(MarketDataError, match="INVALID_QUOTE"):
            provider.snapshot("SPY", "2026-10-14", require_spread=True)
    finally:
        provider.close()


@pytest.mark.parametrize("status,reason", [(403, "PERMISSION_DENIED"), (429, "RATE_LIMIT"),
                                          (500, "UNAVAILABLE")])
def test_quote_provider_errors_remain_fail_closed(status, reason):
    server = QuoteServer()
    server.quote_status = status
    provider, _, _ = make_provider(server)
    try:
        with pytest.raises(MarketDataError, match=reason):
            provider.snapshot("SPY", "2026-10-14", require_spread=True)
    finally:
        provider.close()


def test_zero_spread_only_from_confirmed_equal_positive_bid_and_ask():
    server = QuoteServer()
    server.quote["ap"] = server.quote["bp"]
    provider, _, _ = make_provider(server)
    try:
        assert provider.snapshot("SPY", "2026-10-14", require_spread=True).spread == Decimal(0)
    finally:
        provider.close()


def test_optional_quote_does_not_promote_missing_quote_to_zero():
    provider, server, _ = make_provider()
    try:
        snapshot = provider.snapshot("SPY", "2026-10-14")
        assert snapshot.spread is snapshot.quote_provider_time is snapshot.quote_observed_at is None
        assert not any("quotes" in call.url.path for call in server.calls)
    finally:
        provider.close()


def test_quote_preserves_full_decimal_precision_despite_ambient_arithmetic_context():
    server = QuoteServer()
    server.quote.update({
        "bp": "10000000000000000000.100000000000000001",
        "ap": "10000000000000000000.250000000000000000",
    })
    provider, _, _ = make_provider(server)
    try:
        result = provider.snapshot("SPY", "2026-10-14", require_spread=True)
        assert result.spread == Decimal("0.149999999999999999")
    finally:
        provider.close()


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_proxy_flag_rejects_non_boolean_values(flag):
    with pytest.raises(ValueError, match="invalid enable flag"):
        AlpacaMarketDataProvider(object(), object(), egress_proxy=flag)


def test_proxy_cannot_be_combined_with_any_injected_transport():
    with pytest.raises(ValueError, match="proxy cannot"):
        AlpacaMarketDataProvider(
            object(), object(), egress_proxy=True,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )


def test_explicit_proxy_is_fixed_and_environment_is_ignored(monkeypatch):
    seen = {}

    def client(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setenv("HTTPS_PROXY", "http://synthetic-secret-proxy.invalid:1")
    AlpacaMarketDataProvider(object(), object(), egress_proxy=True)
    assert seen["proxy"] == "http://execution-egress:8080"
    assert seen["trust_env"] is False
    assert seen["follow_redirects"] is False
