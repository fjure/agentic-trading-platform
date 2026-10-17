"""Execution-owned read-only gateway; workers receive normalized immutable evidence."""

from dataclasses import dataclass, field
from datetime import timedelta
from threading import Lock

from packages.brokers.alpaca_market_data import (
    AlpacaMarketDataProvider,
    MarketDataError,
    MarketSnapshot,
)
from packages.domain.models import (
    Capability,
    Market,
    MarketBar,
    Quality,
    SourceCapabilities,
    utc_time,
)
from packages.domain.ports import Clock


def _require_current(snapshot: MarketSnapshot, clock: Clock) -> None:
    now = utc_time(clock.now())
    if not (
        snapshot.as_of <= now < snapshot.valid_until
        and snapshot.valid_until <= snapshot.as_of + timedelta(seconds=120)
        and snapshot.session_open is True
        and snapshot.session_start <= now < snapshot.session_end
    ):
        raise MarketDataError("STALE_DATA")
    if (
        snapshot.source_capabilities.instrument_id != snapshot.instrument.id
        or snapshot.broker_capabilities.instrument_id != snapshot.instrument.id
        or snapshot.capability_version != snapshot.broker_capabilities.version
        or snapshot.source_capabilities.history is not Capability.CONFIRMED
        or snapshot.source_capabilities.intervals != ("PT15M",)
        or len(snapshot.bars) != 2
    ):
        raise MarketDataError("INVALID_RESPONSE")
    first, last = snapshot.bars
    if first.end_at != last.start_at or now - last.end_at > timedelta(seconds=120):
        raise MarketDataError("STALE_DATA")
    bar_window_start = snapshot.session_start - (
        timedelta(minutes=30) if snapshot.instrument.market is Market.CRYPTO else timedelta()
    )
    for bar in snapshot.bars:
        if not (
            bar.instrument_id == snapshot.instrument.id
            and bar.quality is Quality.VALID
            and bar.closed is True
            and bar.interval == "PT15M"
            and bar_window_start <= bar.start_at < bar.end_at <= snapshot.session_end
            and bar.end_at <= bar.event_time <= snapshot.as_of
            and bar.end_at <= bar.received_at <= snapshot.as_of
            and bar.end_at <= bar.available_at <= snapshot.as_of
        ):
            raise MarketDataError("INVALID_RESPONSE")


class _EvidenceLease:
    """Normalized in-process revocation state; no gateway/provider backreference."""

    def __init__(self) -> None:
        self._generation = 0
        self._lock = Lock()

    def invalidate(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def current(self, generation: int) -> bool:
        with self._lock:
            return self._generation == generation


@dataclass(frozen=True, slots=True)
class WorkerMarketFeed:
    """A worker-side port with no client, transport, credentials or network methods."""

    snapshot: MarketSnapshot
    clock: Clock
    _lease: _EvidenceLease | None = field(default=None, repr=False, compare=False)
    _generation: int = field(default=0, repr=False)

    def capabilities(self, instrument_id: str) -> SourceCapabilities:
        self._require(instrument_id)
        return self.snapshot.source_capabilities

    def closed_bars(
        self, instrument_id: str, session_id: str, cursor: str | None = None
    ) -> tuple[MarketBar, ...]:
        self._require(instrument_id)
        session_day = (
            self.snapshot.as_of.date()
            if self.snapshot.instrument.market is Market.CRYPTO
            else self.snapshot.session_start.date()
        )
        if cursor is not None or session_id != session_day.isoformat():
            raise MarketDataError("INVALID_SESSION")
        return self.snapshot.bars

    def _require(self, instrument_id: str) -> None:
        if instrument_id != self.snapshot.instrument.id:
            raise MarketDataError("UNKNOWN_INSTRUMENT")
        if self._lease is not None and not self._lease.current(self._generation):
            raise MarketDataError("STALE_DATA")
        _require_current(self.snapshot, self.clock)
        if self._lease is not None and not self._lease.current(self._generation):
            raise MarketDataError("STALE_DATA")


class MarketGateway:
    """Only Execution constructs this object; never inject it into a worker."""

    def __init__(self, *, provider: AlpacaMarketDataProvider, clock: Clock):
        if type(provider) is not AlpacaMarketDataProvider:
            raise ValueError("execution-owned Alpaca provider required")
        self._provider = provider
        self._clock = clock
        self._leases: dict[str, _EvidenceLease] = {}
        self._refresh_lock = Lock()

    def read(
        self, instrument_id: str, session_id: str, *, require_spread: bool = False,
    ) -> WorkerMarketFeed:
        with self._refresh_lock:
            if instrument_id not in {"SPY", "QQQ", "BTC/USD"}:
                raise MarketDataError("UNKNOWN_INSTRUMENT")
            lease = self._leases.setdefault(instrument_id, _EvidenceLease())
            # Invalidate before I/O: a failed refresh cannot revive prior permission.
            generation = lease.invalidate()
            try:
                if type(require_spread) is not bool:
                    raise MarketDataError("INVALID_REQUEST")
                snapshot = (
                    self._provider.snapshot(instrument_id, session_id, require_spread=True)
                    if require_spread is True
                    else self._provider.snapshot(instrument_id, session_id)
                )
                if require_spread and (
                    snapshot.spread is None or snapshot.quote_provider_time is None
                    or snapshot.quote_observed_at is None
                ):
                    raise MarketDataError("INVALID_QUOTE")
                if snapshot.instrument.id != instrument_id:
                    raise MarketDataError("INVALID_RESPONSE")
                _require_current(snapshot, self._clock)
            except Exception:
                # All feeds share one account; a failure may revoke account-wide access.
                for previous_lease in self._leases.values():
                    previous_lease.invalidate()
                raise
            return WorkerMarketFeed(
                snapshot=snapshot, clock=self._clock, _lease=lease, _generation=generation
            )
