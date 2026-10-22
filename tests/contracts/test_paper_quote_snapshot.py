"""Actual GET-only quote capture retains prices without leaking raw payloads."""

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.brokers.alpaca_market_data import MarketDataError
from tests.contracts.test_alpaca_market import NOW, _Server, make_provider

CANARY = "synthetic-quote-extra-secret-canary"


def capture(**quote_changes):
    server = _Server()
    quote = {
        "bp": "100.001",
        "ap": "100.009",
        "t": NOW.isoformat(),
        "api_key": CANARY,
        "raw_credentials": {"secret": CANARY},
    }
    quote.update(quote_changes)
    server.override["/v2/stocks/quotes/latest"] = (200, {"quotes": {"SPY": quote}, "extra": CANARY})
    provider, _, _ = make_provider(server)
    return provider, server


def test_required_quote_preserves_exact_bid_ask_separate_from_bar_and_spread():
    provider, server = capture()
    snapshot = provider.snapshot("SPY", "2026-10-14", require_spread=True)
    assert snapshot.bid == Decimal("100.001")
    assert snapshot.ask == Decimal("100.009")
    assert snapshot.spread == Decimal("0.008")
    assert snapshot.bars[-1].close == Decimal("101.123456789012345678")
    assert snapshot.bars[-1].close not in {snapshot.bid, snapshot.ask}
    assert snapshot.quote_provider_time == snapshot.quote_observed_at == NOW
    assert snapshot.valid_until <= snapshot.quote_provider_time + timedelta(seconds=120)
    public = asdict(snapshot)
    assert CANARY not in repr(public) and "synthetic-secret" not in repr(public)
    assert "api_key" not in public and "raw_credentials" not in public and "extra" not in public
    quotes = [r for r in server.calls if r.url.path == "/v2/stocks/quotes/latest"]
    assert len(quotes) == 1 and dict(quotes[0].url.params) == {"symbols": "SPY", "feed": "iex"}
    assert all(r.method == "GET" for r in server.calls)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"bp": "0"}, "INVALID_QUOTE"),
        ({"ap": "100"}, "INVALID_QUOTE"),
        ({"bp": True}, "INVALID_QUOTE"),
        ({"ap": "NaN"}, "INVALID_QUOTE"),
        ({"bp": "1e2"}, "INVALID_QUOTE"),
        ({"t": "not-a-time"}, "INVALID_QUOTE"),
        ({"t": NOW.replace(tzinfo=None).isoformat()}, "INVALID_QUOTE"),
        ({"t": (NOW - timedelta(seconds=120)).isoformat()}, "STALE_QUOTE"),
        ({"t": (NOW + timedelta(microseconds=1)).isoformat()}, "STALE_QUOTE"),
    ],
)
def test_required_quote_rejects_invalid_or_stale_proof(changes, reason):
    provider, _ = capture(**changes)
    with pytest.raises(MarketDataError) as error:
        provider.snapshot("SPY", "2026-10-14", require_spread=True)
    assert error.value.reason == reason
    assert CANARY not in str(error.value)


def test_quote_is_never_synthesized_from_bar_close_or_optional_capture():
    provider, server, _ = make_provider()
    snapshot = provider.snapshot("SPY", "2026-10-14")
    assert snapshot.bid is snapshot.ask is snapshot.spread is None
    assert not any(r.url.path == "/v2/stocks/quotes/latest" for r in server.calls)
    server.override["/v2/stocks/quotes/latest"] = (200, {"quotes": {}})
    with pytest.raises(MarketDataError, match="INVALID_QUOTE"):
        provider.snapshot("SPY", "2026-10-14", require_spread=True)
