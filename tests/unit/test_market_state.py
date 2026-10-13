"""T-MKT-001..008: the fixture ingress remains closed on uncertain evidence."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.domain.models import (
    Capability,
    MarketBar,
    PortfolioSnapshot,
    Position,
    Quality,
    Reconciliation,
)
from services.worker.market_state import MarketDataRejected, PaperMarketConfig, build_market_state

TRACE = "a" * 32


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def config() -> PaperMarketConfig:
    return PaperMarketConfig.load()


def bars(end: datetime | None = None) -> tuple[MarketBar, ...]:
    if end is None:
        end = at(2026, 10, 12, 14, 0)
    result = []
    for index in range(2):
        current_end = end - timedelta(minutes=15 * (1 - index))
        result.append(
            MarketBar(
                id=f"bar-{index}",
                instrument_id="SPY",
                start_at=current_end - timedelta(minutes=15),
                end_at=current_end,
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("1000"),
                event_time=current_end,
                received_at=current_end + timedelta(seconds=1),
                available_at=current_end + timedelta(seconds=2),
                quality=Quality.VALID,
            )
        )
    return tuple(result)


def portfolio(as_of: datetime) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        id="portfolio-1",
        currency="USD",
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        exposure=Decimal("0"),
        as_of=as_of,
        ledger_version=1,
        quality=Quality.VALID,
        reconciliation=Reconciliation.CURRENT,
    )


def position() -> Position:
    return Position(
        id="position-spy",
        instrument_id="SPY",
        currency="USD",
        quantity=Decimal("0"),
        average_price=None,
        realized_pnl=Decimal("0"),
        ledger_version=1,
    )


def make(
    config: PaperMarketConfig,
    *,
    window: tuple[MarketBar, ...] | None = None,
    as_of: datetime | None = None,
    instrument_id: str = "SPY",
    spread: str = "0.05",
    current_position: Position | None = None,
):
    if as_of is None:
        as_of = at(2026, 10, 12, 14, 0) + timedelta(seconds=10)
    return build_market_state(
        config=config,
        instrument_id=instrument_id,
        bars=bars() if window is None else window,
        as_of=as_of,
        spread=spread,
        portfolio=portfolio(as_of),
        position=position() if current_position is None else current_position,
        trace_id=TRACE,
    )


def test_valid_window_is_neutral_versioned_and_immutable(config: PaperMarketConfig) -> None:
    state = make(config)
    assert state.quality is Quality.VALID
    assert state.evidence.neutral is True
    assert state.evidence.sources == (config.source_id,)
    assert state.features["last_close"] == Decimal("100.5")
    assert state.features["window_volume"] == Decimal("2000")
    with pytest.raises(TypeError):
        state.features["order_quantity"] = Decimal("1")
    corrected = replace(bars()[-1], version=2, close=Decimal("100.6"))
    newer = make(config, window=(bars()[0], corrected))
    assert newer.id != state.id
    assert newer.evidence.id != state.evidence.id
    assert state.bar.close == Decimal("100.5")
    changed_without_version = make(
        config, window=(bars()[0], replace(bars()[1], high=Decimal("102")))
    )
    assert changed_without_version.id != state.id


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda b: (replace(b[0], instrument_id="OTHER"), b[1]), "UNKNOWN_INSTRUMENT"),
        (lambda b: (b[0],), "INCOMPLETE_WINDOW"),
        (lambda b: (b[0], b[0]), "DUPLICATE_BAR"),
        (lambda b: (b[1], b[0]), "BAR_GAP_OR_ORDER"),
        (
            lambda b: (
                b[0],
                replace(
                    b[1],
                    start_at=b[1].start_at + timedelta(minutes=15),
                    end_at=b[1].end_at + timedelta(minutes=15),
                ),
            ),
            "BAR_GAP_OR_ORDER",
        ),
        (lambda b: (b[0], replace(b[1], quality=Quality.CONFLICT)), "INVALID_BAR_QUALITY"),
        (
            lambda b: (b[0], replace(b[1], available_at=b[1].available_at + timedelta(minutes=1))),
            "FUTURE_OR_INCONSISTENT_TIME",
        ),
    ],
)
def test_rejected_windows(config: PaperMarketConfig, change, reason: str) -> None:
    with pytest.raises(MarketDataRejected, match=reason):
        make(config, window=change(bars()))


def test_unknown_instrument_interval_and_open_bar(config: PaperMarketConfig) -> None:
    with pytest.raises(MarketDataRejected, match="UNKNOWN_INSTRUMENT"):
        make(config, instrument_id="BTC/USD")
    assert config.capabilities("SPY").supports_interval("PT15M")
    assert not config.capabilities("SPY").supports_interval("PT1M")
    with pytest.raises(ValueError, match="closed 15-minute"):
        replace(bars()[0], closed=False)
    with pytest.raises(ValueError, match="closed 15-minute"):
        replace(bars()[0], interval="PT1M")


def test_invalid_decimal_and_timestamps() -> None:
    with pytest.raises(ValueError):
        replace(bars()[0], close=Decimal("NaN"))
    with pytest.raises(ValueError):
        replace(bars()[0], start_at=datetime(2026, 10, 12, 13, 30))


def test_future_and_stale_rejected(config: PaperMarketConfig) -> None:
    with pytest.raises(MarketDataRejected, match="FUTURE_OR_INCONSISTENT_TIME"):
        make(config, as_of=at(2026, 10, 12, 13, 59))
    with pytest.raises(MarketDataRejected, match="STALE_DATA"):
        make(config, as_of=at(2026, 10, 12, 14, 3))


def test_session_grid_and_instrument_currency(config: PaperMarketConfig) -> None:
    shifted = tuple(
        replace(
            bar,
            start_at=bar.start_at + timedelta(minutes=1),
            end_at=bar.end_at + timedelta(minutes=1),
            event_time=bar.event_time + timedelta(minutes=1),
            received_at=bar.received_at + timedelta(minutes=1),
            available_at=bar.available_at + timedelta(minutes=1),
        )
        for bar in bars()
    )
    with pytest.raises(MarketDataRejected, match="MISALIGNED_BAR"):
        make(config, window=shifted, as_of=at(2026, 10, 12, 14, 1) + timedelta(seconds=10))
    with pytest.raises(MarketDataRejected, match="INCONSISTENT_PORTFOLIO"):
        make(config, current_position=replace(position(), currency="EUR"))


def test_calendar_config_content_changes_identity(
    config: PaperMarketConfig, tmp_path: Path
) -> None:
    config_path = Path(__file__).resolve().parents[2] / "config/markets/paper.yaml"
    raw = json.loads(config_path.read_text())
    raw["max_spread_fraction"] = "0.02"
    alternative = tmp_path / "paper.yaml"
    alternative.write_text(json.dumps(raw), encoding="utf-8")
    changed_config = PaperMarketConfig.load(alternative)
    assert changed_config.version == config.version
    assert changed_config.fingerprint != config.fingerprint
    assert make(changed_config).id != make(config).id
    assert make(changed_config).evidence.id != make(config).evidence.id


def test_dst_holiday_halfday_and_coverage(config: PaperMarketConfig) -> None:
    assert config.session_bounds(at(2026, 3, 6, 15))[0] == at(2026, 3, 6, 14, 30)
    assert config.session_bounds(at(2026, 3, 9, 14))[0] == at(2026, 3, 9, 13, 30)
    assert config.session_bounds(at(2026, 10, 30, 14))[0] == at(2026, 10, 30, 13, 30)
    assert config.session_bounds(at(2026, 11, 2, 15))[0] == at(2026, 11, 2, 14, 30)
    assert config.session_bounds(at(2026, 11, 27, 17))[1] == at(2026, 11, 27, 18)
    with pytest.raises(MarketDataRejected, match="SESSION_CLOSED"):
        make(
            config,
            window=bars(at(2026, 11, 27, 18)),
            as_of=at(2026, 11, 27, 18) + timedelta(seconds=10),
        )
    with pytest.raises(MarketDataRejected, match="SESSION_CLOSED"):
        config.session_bounds(at(2026, 11, 26, 16))
    with pytest.raises(MarketDataRejected, match="SESSION_CLOSED"):
        make(config, as_of=at(2026, 10, 12, 20))
    with pytest.raises(MarketDataRejected, match="CALENDAR_OUT_OF_COVERAGE"):
        config.session_bounds(at(2027, 1, 4, 16))


def test_spread_and_explicit_missing_capabilities(config: PaperMarketConfig) -> None:
    with pytest.raises(MarketDataRejected, match="SPREAD_TOO_WIDE"):
        make(config, spread="2")
    caps = config.capabilities("SPY")
    assert caps.news is Capability.UNAVAILABLE
    assert caps.papers is Capability.UNAVAILABLE
    assert caps.history is Capability.UNAVAILABLE


def test_decimal_precision_near_numeric_limit(config: PaperMarketConfig) -> None:
    first, last = bars()
    high_volume = (
        replace(first, volume=Decimal("99999999999999999999.999999999999999998")),
        replace(last, volume=Decimal("0.000000000000000001")),
    )
    state = make(config, window=high_volume)
    assert state.features["window_volume"] == Decimal("99999999999999999999.999999999999999999")

    price = Decimal("10000000000000000000.000000000000000001")
    high_price = tuple(
        replace(bar, open=price, high=price, low=price, close=price) for bar in bars()
    )
    # This is above 1% by less than 10^-18; a 28-digit division rounds to 1%.
    with pytest.raises(MarketDataRejected, match="SPREAD_TOO_WIDE"):
        make(config, window=high_price, spread="100000000000000000.000000000000000001")

    with pytest.raises(ValueError, match="outside NUMERIC"):
        make(
            config,
            window=(
                replace(first, volume=Decimal("99999999999999999999.999999999999999999")),
                replace(last, volume=Decimal("0.000000000000000001")),
            ),
        )
