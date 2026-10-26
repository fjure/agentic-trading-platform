"""Pure point-in-time MarketContext aggregation and Decimal metrics."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, DecimalException, localcontext
from uuid import UUID

from packages.domain.models import utc_time
from packages.research.models import (
    Adjustment,
    BarContent,
    BarInterval,
    BarRevision,
    ContextMetric,
    DerivedContent,
    EvidenceType,
    MarketContext,
    MetricStatus,
    PointInTimeQuery,
    QualityStatus,
    ResearchEvidence,
)
from packages.research.query import latest_visible

_CALCULATION_VERSION = "decimal-population-v1"


def _latest_evidence(items: Iterable[ResearchEvidence]) -> tuple[ResearchEvidence, ...]:
    latest: dict[tuple, ResearchEvidence] = {}
    for item in items:
        key = (item.evidence_type, item.source, item.source_reference, item.instrument_id)
        current = latest.get(key)
        if current is None or (
            item.data_revision,
            item.ingested_at,
            item.commit_version,
            item.fingerprint,
        ) > (
            current.data_revision,
            current.ingested_at,
            current.commit_version,
            current.fingerprint,
        ):
            latest[key] = item
    return tuple(
        sorted(
            latest.values(),
            key=lambda item: (
                item.event_time,
                item.evidence_type.value,
                item.source,
                item.source_reference,
                str(item.evidence_id),
            ),
        )
    )


def _metric_inputs_are_complete(
    bars: tuple[BarRevision, ...],
    expected_windows: tuple[tuple[datetime, datetime], ...],
    complete_window: bool,
) -> bool:
    if not complete_window or not expected_windows or len(bars) != len(expected_windows):
        return False
    observed = tuple((bar.identity.bar_start, bar.identity.bar_end) for bar in bars)
    return observed == expected_windows


def _metrics(bars: tuple[BarRevision, ...]) -> tuple[ContextMetric, ContextMetric]:
    if len(bars) < 3:
        missing = ContextMetric(MetricStatus.NOT_COMPUTABLE)
        return missing, missing
    with localcontext() as context:
        context.prec = 38
        context.rounding = ROUND_HALF_EVEN
        closes = tuple(bar.close for bar in bars)
        price_change = closes[-1] / closes[0] - Decimal(1)
        returns = tuple(
            closes[index] / closes[index - 1] - Decimal(1) for index in range(1, len(closes))
        )
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(
            len(returns)
        )
        volatility = variance.sqrt()
        output_scale = Decimal("0.000000000000000001")
        try:
            price_metric = ContextMetric(
                MetricStatus.COMPUTABLE,
                price_change.quantize(output_scale, rounding=ROUND_HALF_EVEN),
            )
        except (DecimalException, ValueError):
            price_metric = ContextMetric(MetricStatus.NOT_COMPUTABLE)
        try:
            volatility_metric = ContextMetric(
                MetricStatus.COMPUTABLE,
                volatility.quantize(output_scale, rounding=ROUND_HALF_EVEN),
            )
        except (DecimalException, ValueError):
            volatility_metric = ContextMetric(MetricStatus.NOT_COMPUTABLE)
    return price_metric, volatility_metric


def build_market_context(
    instrument_id: UUID,
    start: datetime,
    end: datetime,
    interval: BarInterval,
    query: PointInTimeQuery,
    bars: tuple[BarRevision, ...],
    evidence: tuple[ResearchEvidence, ...],
    *,
    complete_window: bool,
    expected_windows: tuple[tuple[datetime, datetime], ...],
) -> MarketContext:
    """Build context solely from typed, eligible evidence in one explicit PIT view.

    `complete_window` and `expected_windows` are caller-supplied coverage evidence.
    That permits valid calendar overnight gaps while preventing this function from
    inventing bars or claiming coverage from a timestamp span alone.
    """
    utc_time(start)
    utc_time(end)
    if not isinstance(instrument_id, UUID) or not isinstance(interval, BarInterval):
        raise ValueError("typed instrument and interval required")
    if not isinstance(query, PointInTimeQuery):
        raise ValueError("typed PIT query required")
    if not timedelta(0) < end - start <= timedelta(days=366):
        raise ValueError("context range must be nonempty and at most 366 days")
    if type(complete_window) is not bool:
        raise ValueError("explicit coverage result required")
    if not isinstance(bars, tuple) or any(not isinstance(item, BarRevision) for item in bars):
        raise ValueError("immutable typed bar revisions required")
    if not isinstance(evidence, tuple) or any(
        not isinstance(item, ResearchEvidence) for item in evidence
    ):
        raise ValueError("immutable typed research evidence required")
    if not isinstance(expected_windows, tuple):
        raise ValueError("immutable expected windows required")

    previous_end: datetime | None = None
    for window in expected_windows:
        if not isinstance(window, tuple) or len(window) != 2:
            raise ValueError("expected windows must be immutable start/end pairs")
        window_start, window_end = window
        utc_time(window_start)
        utc_time(window_end)
        if not start <= window_start < window_end <= end:
            raise ValueError("expected window falls outside the requested range")
        if previous_end is not None and window_start < previous_end:
            raise ValueError("expected windows must be ordered and nonoverlapping")
        if interval == BarInterval.FIFTEEN_MINUTES and window_end - window_start != timedelta(
            minutes=15
        ):
            raise ValueError("15Min expected window must span exactly 15 minutes")
        previous_end = window_end

    selected_bars = tuple(
        bar
        for bar in latest_visible(bars, query)
        if bar.identity.instrument_id == instrument_id
        and bar.identity.interval == interval
        and bar.identity.adjustment == Adjustment.RAW
        and bar.identity.feed == "iex"
        and start <= bar.identity.bar_start
        and bar.identity.bar_end <= end
    )

    eligible = tuple(
        item
        for item in evidence
        if query.includes(item)
        and start <= item.event_time <= end
        and item.instrument_id in (None, instrument_id)
    )
    bar_evidence: dict[UUID, ResearchEvidence] = {}
    other_evidence: list[ResearchEvidence] = []
    derived_evidence: list[ResearchEvidence] = []
    revisions = {bar.revision_id: bar for bar in selected_bars}
    for item in eligible:
        if item.evidence_type != EvidenceType.BAR:
            if item.evidence_type == EvidenceType.DERIVED:
                derived_evidence.append(item)
            else:
                other_evidence.append(item)
            continue
        content = item.content
        if not isinstance(content, BarContent):
            continue
        bar = revisions.get(content.revision_id)
        if (
            bar is None
            or content.bar_fingerprint != bar.fingerprint
            or item.instrument_id != instrument_id
            or item.source != bar.identity.provider
            or item.event_time != bar.identity.bar_end
        ):
            continue
        current = bar_evidence.get(bar.revision_id)
        if current is None or (
            item.data_revision,
            item.ingested_at,
            item.commit_version,
            str(item.evidence_id),
        ) > (
            current.data_revision,
            current.ingested_at,
            current.commit_version,
            str(current.evidence_id),
        ):
            bar_evidence[bar.revision_id] = item

    # Metrics and references only use bars with a matching, PIT-eligible BAR record.
    backed_bars = tuple(bar for bar in selected_bars if bar.revision_id in bar_evidence)
    backed_bars = tuple(
        sorted(
            backed_bars,
            key=lambda bar: (
                bar.identity.bar_start,
                bar.identity.bar_end,
                bar.identity.provider,
                str(bar.revision_id),
            ),
        )
    )
    coverage_matches = _metric_inputs_are_complete(backed_bars, expected_windows, complete_window)
    if coverage_matches and len(backed_bars) >= 3:
        price_change, volatility = _metrics(backed_bars)
    else:
        price_change = ContextMetric(MetricStatus.NOT_COMPUTABLE)
        volatility = ContextMetric(MetricStatus.NOT_COMPUTABLE)

    selected_other = _latest_evidence(other_evidence)
    base_evidence = tuple(bar_evidence[bar.revision_id] for bar in backed_bars) + selected_other
    base_ids = {item.evidence_id for item in base_evidence}
    selected_derived = tuple(
        item
        for item in _latest_evidence(derived_evidence)
        if isinstance(item.content, DerivedContent)
        and len(set(item.content.input_evidence_ids)) == len(item.content.input_evidence_ids)
        and set(item.content.input_evidence_ids) <= base_ids
    )
    selected_evidence = base_evidence + selected_derived
    selected_evidence = tuple(
        sorted(
            selected_evidence,
            key=lambda item: (item.event_time, item.evidence_type.value, str(item.evidence_id)),
        )
    )
    missing_sources = tuple(
        source
        for source, present in (
            (EvidenceType.BAR, bool(backed_bars) and coverage_matches),
            (EvidenceType.NEWS, any(e.evidence_type == EvidenceType.NEWS for e in selected_other)),
            (
                EvidenceType.FUNDAMENTAL,
                any(e.evidence_type == EvidenceType.FUNDAMENTAL for e in selected_other),
            ),
            (
                EvidenceType.MACRO_EVENT,
                any(e.evidence_type == EvidenceType.MACRO_EVENT for e in selected_other),
            ),
        )
        if not present
    )
    if not expected_windows:
        quality = QualityStatus.UNKNOWN_CALENDAR
    elif not backed_bars:
        quality = QualityStatus.NO_OBSERVATION
    elif not coverage_matches:
        quality = QualityStatus.MISSING_EXPECTED
    else:
        quality = QualityStatus.VALID
    data_version = max(
        max((item.commit_version for item in selected_evidence), default=0),
        max((bar.commit_version for bar in backed_bars), default=0),
    )
    return MarketContext(
        instrument_id=instrument_id,
        query=query,
        evidence_ids=tuple(item.evidence_id for item in selected_evidence),
        quality=quality,
        missing_sources=missing_sources,
        data_version=data_version,
        calculation_version=_CALCULATION_VERSION,
        price_change=price_change,
        volatility=volatility,
    )
