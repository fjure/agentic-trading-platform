"""Execution-to-worker handoff has no provider handle and expires fail-closed."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from packages.brokers.alpaca_market_data import (
    AlpacaMarketDataProvider,
    MarketDataError,
    MarketSnapshot,
)
from packages.domain.models import BrokerCapabilities, Capability, SourceCapabilities
from services.execution.market_gateway import MarketGateway, WorkerMarketFeed
from services.worker.market_state import PaperMarketConfig
from tests.integration.test_replay import NOW, state
from tests.unit.test_decision import FixedClock, SeededIds


def _snapshot():
    bar = state().bar
    previous = replace(
        bar, id="previous", start_at=bar.start_at - timedelta(minutes=15),
        end_at=bar.start_at, event_time=bar.start_at,
    )
    source = SourceCapabilities(
        "alpaca-data.v1:iex", "SPY", ("PT15M",), 120,
        news=Capability.UNAVAILABLE, papers=Capability.UNAVAILABLE,
        history=Capability.CONFIRMED,
    )
    broker = BrokerCapabilities("alpaca-paper", "SPY", 1, paper=Capability.CONFIRMED)
    return MarketSnapshot(
        instrument=PaperMarketConfig.load().instruments["SPY"],
        source_capabilities=source, broker_capabilities=broker,
        bars=(previous, bar), as_of=NOW, valid_until=bar.end_at + timedelta(seconds=120),
        session_open=True, session_start=NOW.replace(hour=13, minute=30, second=0),
        session_end=NOW.replace(hour=20, minute=0, second=0), capability_version=1,
    )


def test_worker_handoff_never_contains_provider_or_credentials(monkeypatch):
    clock = FixedClock(NOW)
    provider = AlpacaMarketDataProvider(
        clock=clock, ids=SeededIds(), api_key="synthetic-only", api_secret="synthetic-only"
    )
    calls = []

    def capture(instrument_id, session_id):
        calls.append((instrument_id, session_id))
        return _snapshot()

    monkeypatch.setattr(provider, "snapshot", capture)
    feed = MarketGateway(provider=provider, clock=clock).read("SPY", "2026-10-12")
    assert type(feed) is WorkerMarketFeed
    assert feed.capabilities("SPY").history is Capability.CONFIRMED
    assert len(feed.closed_bars("SPY", "2026-10-12")) == 2
    assert calls == [("SPY", "2026-10-12")]
    assert not hasattr(feed, "_provider") and not hasattr(feed, "submit")
    payload = repr(asdict(feed.snapshot))
    assert all(value not in payload for value in ("synthetic-only", "api_key", "api_secret"))
    assert feed.snapshot.broker_capabilities.market_order is Capability.UNKNOWN
    assert not feed.snapshot.broker_capabilities.permits(
        order_type="MARKET", fractional=False, regular_session=True
    )
    with pytest.raises(FrozenInstanceError):
        feed.snapshot.as_of = NOW + timedelta(seconds=1)


@pytest.mark.parametrize("method", ["capabilities", "closed_bars"])
def test_worker_cannot_keep_using_expired_evidence(method):
    clock = FixedClock(NOW)
    feed = WorkerMarketFeed(_snapshot(), clock)
    clock.value = feed.snapshot.valid_until
    with pytest.raises(MarketDataError, match="STALE_DATA"):
        if method == "capabilities":
            feed.capabilities("SPY")
        else:
            feed.closed_bars("SPY", "2026-10-12")


@pytest.mark.parametrize("change", [
    {"as_of": NOW + timedelta(seconds=1)},
    {"valid_until": NOW + timedelta(seconds=121)},
    {"session_open": False},
    {"capability_version": 2},
    {"bars": ()},
])
def test_worker_rejects_invalid_or_forged_handoff(change):
    with pytest.raises(MarketDataError):
        WorkerMarketFeed(replace(_snapshot(), **change), FixedClock(NOW)).capabilities("SPY")


def test_worker_rejects_other_instrument_and_cursor():
    feed = WorkerMarketFeed(_snapshot(), FixedClock(NOW))
    with pytest.raises(MarketDataError, match="UNKNOWN_INSTRUMENT"):
        feed.capabilities("BTC/USD")
    with pytest.raises(MarketDataError, match="INVALID_SESSION"):
        feed.closed_bars("SPY", "2026-10-12", "opaque-provider-cursor")
    with pytest.raises(MarketDataError, match="INVALID_SESSION"):
        feed.closed_bars("SPY", "2026-10-13")


def test_gateway_rejects_arbitrary_provider():
    with pytest.raises(ValueError, match="execution-owned"):
        MarketGateway(provider=object(), clock=FixedClock(NOW))


def test_failed_refresh_revokes_old_worker_feed(monkeypatch):
    clock = FixedClock(NOW)
    provider = AlpacaMarketDataProvider(clock=clock, ids=SeededIds())
    monkeypatch.setattr(provider, "snapshot", lambda *_: _snapshot())
    gateway = MarketGateway(provider=provider, clock=clock)
    old = gateway.read("SPY", "2026-10-12")

    def failure(*_):
        # The former permission is already invalid during a new read-only capture.
        with pytest.raises(MarketDataError, match="STALE_DATA"):
            old.capabilities("SPY")
        raise MarketDataError("PERMISSION_DENIED")

    monkeypatch.setattr(provider, "snapshot", failure)
    with pytest.raises(MarketDataError, match="PERMISSION_DENIED"):
        gateway.read("SPY", "2026-10-12")
    with pytest.raises(MarketDataError, match="STALE_DATA"):
        old.closed_bars("SPY", "2026-10-12")
    monkeypatch.setattr(provider, "snapshot", lambda *_: _snapshot())
    fresh = gateway.read("SPY", "2026-10-12")
    assert fresh.capabilities("SPY").history is Capability.CONFIRMED
    with pytest.raises(MarketDataError, match="STALE_DATA"):
        old.capabilities("SPY")


def test_real_normalized_capture_can_be_consumed_by_worker():
    from tests.contracts.test_alpaca_market import make_provider

    provider, server, clock = make_provider()
    feed = MarketGateway(provider=provider, clock=clock).read("SPY", "2026-10-14")
    assert len(feed.closed_bars("SPY", "2026-10-14")) == 2
    count = len(server.calls)
    assert feed.capabilities("SPY").history is Capability.CONFIRMED
    assert len(server.calls) == count


@pytest.mark.parametrize("minute", [0, 15])
def test_crypto_worker_keeps_continuous_midnight_window(minute):
    from tests.contracts.test_alpaca_market import make_provider

    now = datetime(2026, 10, 14, 0, minute, 10, tzinfo=UTC)
    provider, _, clock = make_provider(now=now, enable_crypto=True)
    feed = MarketGateway(provider=provider, clock=clock).read("BTC/USD", "2026-10-14")
    bars = feed.closed_bars("BTC/USD", "2026-10-14")
    assert len(bars) == 2 and bars[0].start_at.date() < now.date()
    assert feed.capabilities("BTC/USD").history is Capability.CONFIRMED


def test_account_failure_revokes_feeds_of_all_instruments(monkeypatch):
    clock = FixedClock(NOW)
    provider = AlpacaMarketDataProvider(clock=clock, ids=SeededIds())

    def capture(instrument_id, _session):
        snapshot = _snapshot()
        return replace(
            snapshot,
            instrument=replace(snapshot.instrument, id=instrument_id, broker_symbol=instrument_id),
            source_capabilities=replace(snapshot.source_capabilities, instrument_id=instrument_id),
            broker_capabilities=replace(snapshot.broker_capabilities, instrument_id=instrument_id),
            bars=tuple(replace(bar, instrument_id=instrument_id) for bar in snapshot.bars),
        )

    monkeypatch.setattr(provider, "snapshot", capture)
    gateway = MarketGateway(provider=provider, clock=clock)
    spy = gateway.read("SPY", "2026-10-12")
    qqq = gateway.read("QQQ", "2026-10-12")
    assert spy.capabilities("SPY").history is Capability.CONFIRMED
    assert qqq.capabilities("QQQ").history is Capability.CONFIRMED

    def blocked(*_):
        raise MarketDataError("ACCOUNT_UNAVAILABLE")

    monkeypatch.setattr(provider, "snapshot", blocked)
    with pytest.raises(MarketDataError, match="ACCOUNT_UNAVAILABLE"):
        gateway.read("SPY", "2026-10-12")
    for feed, instrument_id in ((spy, "SPY"), (qqq, "QQQ")):
        with pytest.raises(MarketDataError, match="STALE_DATA"):
            feed.capabilities(instrument_id)
