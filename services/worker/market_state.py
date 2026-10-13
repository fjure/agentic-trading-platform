"""Fail-closed normalization of versioned, synthetic market fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from packages.decisions.neutral_evidence import neutral_evidence
from packages.domain.models import (
    Capability,
    Instrument,
    InstrumentUniverse,
    Market,
    MarketBar,
    MarketState,
    PortfolioSnapshot,
    Position,
    Quality,
    Reconciliation,
    SourceCapabilities,
    decimal_string,
    decimal_value,
    utc_time,
)

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config/markets/paper.yaml"


class MarketDataRejected(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class PaperMarketConfig:
    version: str
    fingerprint: str
    source_id: str
    interval: str
    max_age_seconds: int
    max_spread_fraction: Decimal
    required_bars: int
    instruments: Mapping[str, Instrument]
    calendar_start: date
    calendar_end: date
    holidays: frozenset[date]
    early_closes: Mapping[date, time]
    zone: ZoneInfo
    regular_open: time
    regular_close: time

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> PaperMarketConfig:
        # JSON is a YAML 1.2 subset. The runtime needs no YAML parser dependency.
        raw = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        calendar = raw["calendar"]
        if raw["interval"] != "PT15M" or calendar["timezone"] != "America/New_York":
            raise ValueError("unsupported interval or session timezone")
        instruments = {
            key: Instrument(
                id=key,
                market=Market(value["market"]),
                broker_symbol=key,
                asset_class=value["asset_class"],
                currency=value["currency"],
                version=1,
                session_name=value["session"],
            )
            for key, value in raw["instruments"].items()
        }
        InstrumentUniverse(tuple(instruments.values()))
        if any(
            item.market is not Market.US_EQUITY or item.session_name != "XNYS"
            for item in instruments.values()
        ):
            raise ValueError("only confirmed fixture equity sessions are configured")
        try:
            zone = ZoneInfo(calendar["timezone"])
        except ZoneInfoNotFoundError as exc:
            raise ValueError("IANA timezone data unavailable") from exc
        start, end = (
            date.fromisoformat(calendar["coverage_start"]),
            date.fromisoformat(calendar["coverage_end"]),
        )
        holidays = frozenset(date.fromisoformat(item) for item in calendar["holidays"])
        early_closes = MappingProxyType(
            {
                date.fromisoformat(key): time.fromisoformat(value)
                for key, value in calendar["early_closes"].items()
            }
        )
        regular_open = time.fromisoformat(calendar["open"])
        regular_close = time.fromisoformat(calendar["close"])
        if (
            not isinstance(raw["version"], str)
            or not raw["version"]
            or not isinstance(raw["source_id"], str)
            or not raw["source_id"]
            or not start <= end
            or not regular_open < regular_close
            or not all(start <= day <= end for day in holidays | early_closes.keys())
            or any(not regular_open < closing < regular_close for closing in early_closes.values())
            or type(raw["max_age_seconds"]) is not int
            or raw["max_age_seconds"] < 0
            or type(raw["required_bars"]) is not int
            or raw["required_bars"] < 1
        ):
            raise ValueError("invalid market fixture configuration")
        max_spread = decimal_value(raw["max_spread_fraction"])
        if not Decimal("0") < max_spread <= Decimal("1"):
            raise ValueError("invalid maximum spread")
        return cls(
            raw["version"],
            fingerprint,
            raw["source_id"],
            raw["interval"],
            raw["max_age_seconds"],
            max_spread,
            raw["required_bars"],
            MappingProxyType(instruments),
            start,
            end,
            holidays,
            early_closes,
            zone,
            regular_open,
            regular_close,
        )

    def capabilities(self, instrument_id: str) -> SourceCapabilities:
        if instrument_id not in self.instruments:
            raise MarketDataRejected("UNKNOWN_INSTRUMENT")
        return SourceCapabilities(
            source_id=self.source_id,
            instrument_id=instrument_id,
            intervals=(self.interval,),
            freshness_seconds=self.max_age_seconds,
            news=Capability.UNAVAILABLE,
            papers=Capability.UNAVAILABLE,
            history=Capability.UNAVAILABLE,
        )

    def session_bounds(self, moment: datetime) -> tuple[datetime, datetime]:
        """Return regular NYSE bounds in UTC; reject unknown and closed dates."""
        local = utc_time(moment).astimezone(self.zone)
        day = local.date()
        if not self.calendar_start <= day <= self.calendar_end:
            raise MarketDataRejected("CALENDAR_OUT_OF_COVERAGE")
        if day.weekday() >= 5 or day in self.holidays:
            raise MarketDataRejected("SESSION_CLOSED")
        opening = datetime.combine(day, self.regular_open, self.zone).astimezone(moment.tzinfo)
        closing = datetime.combine(
            day, self.early_closes.get(day, self.regular_close), self.zone
        ).astimezone(moment.tzinfo)
        return utc_time(opening), utc_time(closing)


def build_market_state(
    *,
    config: PaperMarketConfig,
    instrument_id: str,
    bars: tuple[MarketBar, ...],
    as_of: datetime,
    spread: Decimal | str,
    portfolio: PortfolioSnapshot,
    position: Position,
    trace_id: str,
) -> MarketState:
    """Admit a complete, ordered closed-bar window or reject the entire input."""
    as_of = utc_time(as_of)
    instrument = config.instruments.get(instrument_id)
    if instrument is None:
        raise MarketDataRejected("UNKNOWN_INSTRUMENT")
    if len(bars) != config.required_bars:
        raise MarketDataRejected("INCOMPLETE_WINDOW")
    if any(not isinstance(bar, MarketBar) or bar.instrument_id != instrument_id for bar in bars):
        raise MarketDataRejected("UNKNOWN_INSTRUMENT")
    if any(bar.interval != config.interval or not bar.closed for bar in bars):
        raise MarketDataRejected("UNSUPPORTED_BAR")
    if any(bar.quality is not Quality.VALID for bar in bars):
        raise MarketDataRejected("INVALID_BAR_QUALITY")
    if len({bar.id for bar in bars}) != len(bars) or len({bar.start_at for bar in bars}) != len(
        bars
    ):
        raise MarketDataRejected("DUPLICATE_BAR")
    if any(
        later.start_at != earlier.end_at
        for earlier, later in zip(bars, bars[1:], strict=False)
    ):
        raise MarketDataRejected("BAR_GAP_OR_ORDER")
    if any(
        bar.end_at > as_of
        or bar.event_time < bar.end_at
        or bar.event_time > bar.received_at
        or bar.received_at > bar.available_at
        or bar.available_at > as_of
        for bar in bars
    ):
        raise MarketDataRejected("FUTURE_OR_INCONSISTENT_TIME")
    opening, closing = config.session_bounds(as_of)
    if not opening <= as_of < closing or any(
        bar.start_at < opening or bar.end_at > closing for bar in bars
    ):
        raise MarketDataRejected("SESSION_CLOSED")
    if any(
        (bar.start_at - opening).total_seconds() % 900 != 0
        or (bar.end_at - opening).total_seconds() % 900 != 0
        for bar in bars
    ):
        raise MarketDataRejected("MISALIGNED_BAR")
    latest = bars[-1]
    if as_of - latest.available_at > timedelta(seconds=config.max_age_seconds):
        raise MarketDataRejected("STALE_DATA")
    spread = decimal_value(spread)
    # Exact cross multiplication avoids rounding a near-threshold ratio at
    # Decimal's default 28-digit precision.
    with localcontext() as context:
        context.prec = 80
        too_wide = spread > latest.close * config.max_spread_fraction
    if spread < 0 or too_wide:
        raise MarketDataRejected("SPREAD_TOO_WIDE")
    if (
        position.instrument_id != instrument_id
        or position.currency != instrument.currency
        or position.currency != portfolio.currency
        or portfolio.as_of > as_of
        or portfolio.quality is not Quality.VALID
        or portfolio.reconciliation is not Reconciliation.CURRENT
    ):
        raise MarketDataRejected("INCONSISTENT_PORTFOLIO")
    evidence, features = neutral_evidence(
        bars, source_id=config.source_id, config_version=f"{config.version}:{config.fingerprint}"
    )
    identity = "|".join(
        (
            config.version,
            config.fingerprint,
            instrument_id,
            evidence.id,
            as_of.isoformat(),
            decimal_string(spread),
            portfolio.id,
            str(portfolio.ledger_version),
            portfolio.as_of.isoformat(),
            decimal_string(portfolio.equity),
            decimal_string(portfolio.cash),
            decimal_string(portfolio.exposure),
            position.id,
            str(position.ledger_version),
            decimal_string(position.quantity),
            decimal_string(position.realized_pnl),
            (
                decimal_string(position.average_price)
                if position.average_price is not None
                else "null"
            ),
        )
    )
    state_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return MarketState(
        id=state_id,
        instrument_id=instrument_id,
        market=instrument.market,
        bar=latest,
        evidence=evidence,
        portfolio=portfolio,
        position=position,
        features=features,
        spread=spread,
        event_time=latest.event_time,
        received_at=latest.received_at,
        available_at=evidence.available_at,
        as_of=as_of,
        quality=Quality.VALID,
        trace_id=trace_id,
    )
