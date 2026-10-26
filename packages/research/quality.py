"""Immutable quality findings and conservative feed-specific coverage."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from packages.domain.models import decimal_value
from packages.research.calendar import NEW_YORK, CalendarCoverage, expected_windows
from packages.research.models import (
    Adjustment,
    BarInterval,
    BarRevision,
    QualityStatus,
    _label,
    _uuid,
)

QUALITY_RULE_VERSION = "research-quality-v1"


@dataclass(frozen=True)
class QualityFinding:
    code: str
    quality: QualityStatus
    bar_start: datetime | None = None


@dataclass(frozen=True)
class CoverageResult:
    instrument_id: UUID
    feed: str
    interval: BarInterval
    adjustment: Adjustment
    start: datetime
    end: datetime
    rule_version: str
    calendar_version: str
    expected: int | None
    observed: int
    missing: int | None
    unchecked: tuple[date, ...]
    quality: QualityStatus
    findings: tuple[QualityFinding, ...]
    missing_windows: tuple[tuple[datetime, datetime], ...] = ()


def validate_ohlcv(
    open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal
) -> tuple[QualityFinding, ...]:
    """Validate normalized candidate values before constructing a BarRevision."""
    try:
        values = tuple(decimal_value(value) for value in (open, high, low, close, volume))
    except (ValueError, TypeError):
        return (QualityFinding("INVALID_DECIMAL", QualityStatus.INVALID),)
    opening, highest, lowest, closing, size = values
    if min(values[:4]) <= 0 or size < 0:
        return (QualityFinding("INVALID_PRICE_OR_VOLUME", QualityStatus.INVALID),)
    if highest < max(opening, closing, lowest) or lowest > min(opening, closing):
        return (QualityFinding("IMPLAUSIBLE_OHLC", QualityStatus.INVALID),)
    return ()


def assess_coverage(
    *,
    instrument_id: UUID,
    feed: str,
    interval: BarInterval,
    adjustment: Adjustment,
    start: datetime,
    end: datetime,
    valuation_at: datetime,
    calendar: CalendarCoverage,
    bars: tuple[BarRevision, ...],
) -> CoverageResult:
    """Assess one series of already PIT-selected revisions, without filling gaps.

    Multiple identical observations deduplicate. Distinct contents declaring the
    same provider revision are conflicts. Different local revisions must be
    selected upstream; passing both makes the window ambiguous and invalid.
    """
    _uuid(instrument_id)
    _label(feed)
    if adjustment is not Adjustment.RAW:
        raise ValueError("only raw research coverage is supported")
    if not isinstance(bars, tuple):
        raise ValueError("immutable normalized bars required")
    calendar_windows = expected_windows(calendar, interval, start, end, valuation_at)
    expected = set(calendar_windows.windows)
    findings = []
    seen = {}
    observed = set()
    previous_start = None
    providers = {bar.identity.provider for bar in bars}
    if len(providers) > 1:
        raise ValueError("mixed research providers")
    for bar in bars:
        identity = bar.identity
        if (
            identity.instrument_id != instrument_id
            or identity.feed != feed
            or identity.interval != interval
            or identity.adjustment != adjustment
        ):
            raise ValueError("mixed research series")
        window = (identity.bar_start, identity.bar_end)
        if not start <= identity.bar_start < end or identity.bar_end > valuation_at:
            findings.append(QualityFinding("OUTSIDE_QUERY", QualityStatus.INVALID, window[0]))
            continue
        if previous_start is not None and identity.bar_start < previous_start:
            findings.append(QualityFinding("OUT_OF_ORDER", QualityStatus.INVALID, window[0]))
        previous_start = identity.bar_start
        key = (identity, bar.provider_revision)
        prior = seen.get(key)
        if prior is not None and prior != bar.fingerprint:
            findings.append(
                QualityFinding(
                    "PROVIDER_REVISION_CONFLICT"
                    if bar.provider_revision is not None
                    else "AMBIGUOUS_LOCAL_REVISION",
                    QualityStatus.CONFLICT,
                    window[0],
                )
            )
            observed.discard(window)
        elif window in observed and prior is None:
            findings.append(
                QualityFinding("AMBIGUOUS_LOCAL_REVISION", QualityStatus.CONFLICT, window[0])
            )
            observed.discard(window)
        elif bar.quality != QualityStatus.VALID:
            findings.append(QualityFinding("EXCLUDED_BAR_QUALITY", bar.quality, window[0]))
        elif window not in expected:
            unknown_date = window[0].astimezone(NEW_YORK).date() in (calendar_windows.unknown_dates)
            findings.append(
                QualityFinding(
                    "UNCHECKED_SESSION_WINDOW" if unknown_date else "OUTSIDE_SESSION_WINDOW",
                    QualityStatus.UNKNOWN_CALENDAR if unknown_date else QualityStatus.INVALID,
                    window[0],
                )
            )
        else:
            observed.add(window)
        seen[key] = bar.fingerprint
    # A later duplicate cannot rehabilitate a conflicting natural window.
    excluded = {
        finding.bar_start
        for finding in findings
        if finding.quality in (QualityStatus.CONFLICT, QualityStatus.INVALID)
    }
    observed = {window for window in observed if window[0] not in excluded}
    missing_windows = tuple(sorted(expected - observed))
    if missing_windows:
        gap_quality = (
            QualityStatus.NO_OBSERVATION
            if feed.lower() == "iex"
            else (QualityStatus.MISSING_EXPECTED)
        )
        findings.append(
            QualityFinding(
                "IEX_NO_OBSERVATION" if feed.lower() == "iex" else ("MISSING_EXPECTED_WINDOW"),
                gap_quality,
            )
        )
    if calendar_windows.unknown_dates:
        findings.append(QualityFinding("UNKNOWN_CALENDAR_RANGE", QualityStatus.UNKNOWN_CALENDAR))
    priority = (
        QualityStatus.CONFLICT,
        QualityStatus.INVALID,
        QualityStatus.UNKNOWN_CALENDAR,
        QualityStatus.MISSING_EXPECTED,
        QualityStatus.NO_OBSERVATION,
    )
    quality = next(
        (status for status in priority if any(item.quality == status for item in findings)),
        QualityStatus.VALID,
    )
    unknown = bool(calendar_windows.unknown_dates)
    return CoverageResult(
        instrument_id,
        feed,
        interval,
        adjustment,
        start,
        end,
        QUALITY_RULE_VERSION,
        calendar.version,
        None if unknown else len(expected),
        len(observed),
        None if unknown else len(missing_windows),
        calendar_windows.unknown_dates,
        quality,
        tuple(findings),
        missing_windows,
    )
