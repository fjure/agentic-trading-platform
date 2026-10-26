"""PIT-safe context aggregation with independently calculated Decimal metrics."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID, uuid4

import pytest

from packages.research.context import build_market_context
from packages.research.models import (
    BarIdentity,
    BarInterval,
    BarRevision,
    DerivedContent,
    EvidenceType,
    MetricStatus,
    NewsContent,
    PointInTimeQuery,
    QualityStatus,
    ResearchEvidence,
)
from packages.research.storage import evidence_for_bar

INSTRUMENT = UUID("ce85d363-e845-4b4a-9b11-6de080bb43da")
START = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)
FUTURE = OBSERVED + timedelta(days=1)
INTERVAL = BarInterval.FIFTEEN_MINUTES


def bars_for(closes=("100", "200", "400", "600"), *, observed=OBSERVED):
    values = []
    for index, close in enumerate(closes):
        start = START + timedelta(minutes=15 * index)
        end = start + timedelta(minutes=15)
        price = Decimal(close)
        values.append(
            BarRevision(
                uuid4(),
                BarIdentity(INSTRUMENT, "alpaca", "iex", INTERVAL, start, end),
                price,
                price,
                price,
                price,
                Decimal("100"),
                observed,
                observed,
                commit_version=2,
            )
        )
    return tuple(values)


def windows(bars):
    return tuple((item.identity.bar_start, item.identity.bar_end) for item in bars)


def query(*, known=OBSERVED, version=4):
    return PointInTimeQuery(known, known, known, version)


def bar_evidence(bars):
    return tuple(evidence_for_bar(item) for item in bars)


def context_for(bars, evidence=(), *, view=None, complete=True, expected=None, **changes):
    expected_windows = windows(bars) if expected is None else expected
    requested_end = max((window[1] for window in expected_windows), default=None)
    if requested_end is None:
        requested_end = START + timedelta(minutes=15 * len(bars))
    return build_market_context(
        INSTRUMENT,
        START,
        requested_end,
        INTERVAL,
        view or query(),
        bars,
        evidence,
        complete_window=complete,
        expected_windows=expected_windows,
        **changes,
    )


def test_decimal_price_change_and_population_volatility_match_independent_example():
    bars = bars_for()
    result = context_for(bars, bar_evidence(bars))

    # Closes 100, 200, 400, 600 yield returns 1, 1, 0.5. Population
    # variance is 1/18, so the standard deviation is sqrt(1/18).
    with localcontext() as decimal_context:
        decimal_context.prec = 38
        decimal_context.rounding = ROUND_HALF_EVEN
        returns = (Decimal(1), Decimal(1), Decimal("0.5"))
        mean = sum(returns, Decimal(0)) / Decimal(3)
        expected_variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(3)
        expected_volatility = expected_variance.sqrt()

    assert result.price_change.status == MetricStatus.COMPUTABLE
    assert result.price_change.value == Decimal(5)
    assert result.volatility.value == expected_volatility.quantize(
        Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN
    )
    assert result.quality == QualityStatus.VALID
    assert result.data_version == 2
    assert len(result.evidence_ids) == len(bars)


def test_later_bar_revision_and_news_are_excluded_from_older_knowledge_view():
    bars = bars_for()
    corrected = replace(
        bars[-1],
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=bars[-1].revision_id,
        close=Decimal("900"),
        high=Decimal("900"),
        ingested_at=FUTURE,
        available_at=FUTURE,
        commit_version=8,
    )
    later_news = ResearchEvidence(
        uuid4(),
        EvidenceType.NEWS,
        "fixture",
        "fixture:news-1",
        INSTRUMENT,
        "US_EQUITY",
        bars[-1].identity.bar_end,
        None,
        FUTURE,
        FUTURE,
        # Headline content is typed; it is not a raw provider payload.
        NewsContent("Later headline", "en"),
        QualityStatus.VALID,
        commit_version=9,
    )
    result = context_for(
        (*bars, corrected),
        (*bar_evidence(bars), evidence_for_bar(corrected), later_news),
        view=query(known=OBSERVED, version=4),
        expected=windows(bars),
    )
    assert result.price_change.value == Decimal(5)
    assert result.data_version == 2
    assert later_news.evidence_id not in result.evidence_ids
    assert result.missing_sources == (
        EvidenceType.NEWS,
        EvidenceType.FUNDAMENTAL,
        EvidenceType.MACRO_EVENT,
    )


def test_invalid_high_confidence_evidence_never_repairs_quality_or_enters_context():
    bars = bars_for()
    invalid = ResearchEvidence(
        uuid4(),
        EvidenceType.NEWS,
        "fixture",
        "fixture:invalid-news",
        INSTRUMENT,
        "US_EQUITY",
        bars[0].identity.bar_end,
        None,
        OBSERVED,
        OBSERVED,
        NewsContent("Bad quality", "en"),
        QualityStatus.CONFLICT,
        model_confidence=Decimal("1"),
        commit_version=3,
    )
    result = context_for(bars, (*bar_evidence(bars), invalid))
    assert invalid.evidence_id not in result.evidence_ids
    assert EvidenceType.NEWS in result.missing_sources
    assert result.quality == QualityStatus.VALID


def test_only_matching_pit_bar_evidence_can_back_metrics_or_references():
    bars = bars_for()
    unrelated = replace(
        evidence_for_bar(bars[0]),
        evidence_id=uuid4(),
        instrument_id=uuid4(),
    )
    result = context_for(bars, (unrelated, *bar_evidence(bars[1:])))
    assert unrelated.evidence_id not in result.evidence_ids
    assert result.price_change.status == MetricStatus.NOT_COMPUTABLE
    assert result.volatility.status == MetricStatus.NOT_COMPUTABLE
    assert EvidenceType.BAR in result.missing_sources
    assert result.quality == QualityStatus.MISSING_EXPECTED


@pytest.mark.parametrize(
    "complete,expected,closes",
    [
        (False, "default", ("100", "200", "400", "600")),
        (True, "default", ("100", "200")),
        (True, (), ("100", "200", "400", "600")),
    ],
)
def test_incomplete_calendar_window_or_short_sample_has_no_metrics(complete, expected, closes):
    bars = bars_for(closes)
    selected_expected = None if expected == "default" else expected
    result = context_for(
        bars,
        bar_evidence(bars),
        complete=complete,
        expected=selected_expected,
    )
    assert result.price_change.status == MetricStatus.NOT_COMPUTABLE
    assert result.volatility.status == MetricStatus.NOT_COMPUTABLE
    if expected == ():
        assert result.quality == QualityStatus.UNKNOWN_CALENDAR
    elif complete and bars:
        assert result.quality == QualityStatus.VALID
    else:
        assert result.quality == QualityStatus.MISSING_EXPECTED


def test_expected_windows_allow_session_gaps_but_reject_missing_or_extra_bars():
    bars = bars_for(("100", "200", "400"))
    expected = (
        windows(bars)[0],
        (START + timedelta(hours=18), START + timedelta(hours=18, minutes=15)),
        (START + timedelta(hours=18, minutes=15), START + timedelta(hours=18, minutes=30)),
    )
    across_sessions = tuple(
        replace(
            bar,
            identity=replace(
                bar.identity,
                bar_start=expected[index][0],
                bar_end=expected[index][1],
            ),
        )
        for index, bar in enumerate(bars)
    )
    result = context_for(across_sessions, bar_evidence(across_sessions), expected=expected)
    assert result.volatility.status == MetricStatus.COMPUTABLE

    missing = context_for(
        across_sessions,
        bar_evidence(across_sessions[:2]),
        expected=expected,
    )
    assert missing.volatility.status == MetricStatus.NOT_COMPUTABLE
    assert missing.quality == QualityStatus.MISSING_EXPECTED


def test_mixed_bar_intervals_are_not_used_for_a_context_series():
    bars = bars_for()
    mixed = replace(
        bars[2],
        identity=replace(bars[2].identity, interval=BarInterval.ONE_DAY),
    )
    values = (*bars[:2], mixed, bars[3])
    result = context_for(values, bar_evidence(values), expected=windows(bars))
    assert result.volatility.status == MetricStatus.NOT_COMPUTABLE
    assert result.quality == QualityStatus.MISSING_EXPECTED


def test_derived_evidence_is_visible_only_when_its_inputs_are_referenced_here():
    bars = bars_for()
    bar_refs = bar_evidence(bars)
    valid_derived = ResearchEvidence(
        uuid4(),
        EvidenceType.DERIVED,
        "research",
        "derived:return-1",
        INSTRUMENT,
        "US_EQUITY",
        bars[-1].identity.bar_end,
        None,
        OBSERVED,
        OBSERVED,
        DerivedContent(
            "price_change",
            Decimal(5),
            "ratio",
            "calc-v1",
            (bar_refs[0].evidence_id, bar_refs[-1].evidence_id),
        ),
        QualityStatus.VALID,
        commit_version=3,
    )
    bypass = replace(
        valid_derived,
        evidence_id=uuid4(),
        source_reference="derived:bypass",
        content=DerivedContent("price_change", Decimal(99), "ratio", "calc-v1", (uuid4(),)),
    )
    result = context_for(bars, (*bar_refs, valid_derived, bypass))
    assert valid_derived.evidence_id in result.evidence_ids
    assert bypass.evidence_id not in result.evidence_ids
    assert result.price_change.value == Decimal(5)


def test_context_rejects_untyped_collections_and_mixed_interval_expectations():
    bars = bars_for()
    with pytest.raises(ValueError, match="typed research evidence"):
        context_for(bars, [*bar_evidence(bars)])
    bad_windows = ((START, START + timedelta(minutes=10)),)
    with pytest.raises(ValueError, match="15Min"):
        context_for(bars, bar_evidence(bars), expected=bad_windows)


def test_metrics_outside_domain_decimal_range_are_reported_not_computable():
    bars = bars_for(
        (
            "0.000000000000000001",
            "99999999999999999999.999999999999999999",
            "99999999999999999999.999999999999999999",
            "99999999999999999999.999999999999999999",
        )
    )
    result = context_for(bars, bar_evidence(bars))
    assert result.price_change.status == MetricStatus.NOT_COMPUTABLE
    assert result.volatility.status == MetricStatus.NOT_COMPUTABLE
