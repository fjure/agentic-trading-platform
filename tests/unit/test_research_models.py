"""Research invariants are independent of provider transport and trading state."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest

from packages.research.models import (
    Adjustment,
    AssetClass,
    AvailabilityBasis,
    BarContent,
    BarIdentity,
    BarInterval,
    BarRevision,
    ContextMetric,
    DerivedContent,
    EvidenceType,
    FundamentalContent,
    Instrument,
    MacroEventContent,
    MarketContext,
    MetricStatus,
    NewsContent,
    PointInTimeQuery,
    QualityStatus,
    ResearchEvidence,
    SymbolMapping,
    validate_symbol_mappings,
)
from packages.research.ports import (
    FundamentalsRequest,
    HistoricalBarsPage,
    HistoricalBarsRequest,
    MacroEventsRequest,
    NewsPage,
    NewsRequest,
    SourceError,
    SourceErrorCode,
    SourceStatus,
    UnavailableFundamentalsSource,
    UnavailableMacroEventsSource,
    UnavailableNewsSource,
)

ID = UUID("00000000-0000-0000-0000-000000000001")
START = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=15)
SEEN = END + timedelta(days=1)


def bar(**changes):
    value = BarRevision(
        revision_id=uuid4(),
        identity=BarIdentity(ID, "alpaca", "iex", BarInterval.FIFTEEN_MINUTES, START, END),
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal("11"),
        volume=Decimal("100"),
        ingested_at=SEEN,
        available_at=SEEN,
        commit_version=2,
    )
    return replace(value, **changes)


def evidence(content=None, evidence_type=EvidenceType.BAR, **changes):
    item = bar()
    value = ResearchEvidence(
        evidence_id=uuid4(),
        evidence_type=evidence_type,
        source="alpaca",
        source_reference="SPY:2026-10-23:14-00",
        instrument_id=ID,
        market="US_EQUITY",
        event_time=END,
        published_at=None,
        available_at=SEEN,
        ingested_at=SEEN,
        content=content or BarContent(item.revision_id, item.fingerprint),
        quality=QualityStatus.VALID,
        commit_version=2,
    )
    return replace(value, **changes)


def test_instrument_and_frozen_bar():
    assert Instrument(ID, AssetClass.ETF, "ARCX", "USD", "share", "USD").instrument_id == ID
    with pytest.raises(FrozenInstanceError):
        bar().volume = Decimal("0")


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": ""},
        {"mic": "NYSE-long"},
        {"asset_class": "ETF"},
        {"instrument_id": "SPY"},
        {"base_unit": ""},
    ],
)
def test_instrument_rejects_ambiguous_identity(changes):
    with pytest.raises(ValueError):
        replace(Instrument(ID, AssetClass.ETF, "ARCX", "USD", "share", "USD"), **changes)


def test_symbol_changes_preserve_identity_and_allow_adjacent_validity():
    old = SymbolMapping(ID, "alpaca", "OLD", START, END)
    new = SymbolMapping(ID, "alpaca", "NEW", END)
    validate_symbol_mappings((old, new))
    with pytest.raises(ValueError, match="overlapping"):
        validate_symbol_mappings((old, replace(new, valid_from=START)))
    with pytest.raises(ValueError, match="overlapping"):
        validate_symbol_mappings((old, replace(old, instrument_id=uuid4())))


@pytest.mark.parametrize(
    "changes",
    [
        {"open": 10.0},
        {"volume": Decimal("NaN")},
        {"close": Decimal("Infinity")},
        {"low": Decimal("-1")},
        {"volume": Decimal("-1")},
        {"high": Decimal("8")},
        {"low": Decimal("11")},
        {"ingested_at": START},
        {"available_at": END},
        {"published_at": END},
        {"local_revision": 2},
        {"commit_version": True},
        {"ingested_at": SEEN.replace(tzinfo=None)},
        {"ingested_at": SEEN.astimezone(timezone(timedelta(hours=2)))},
    ],
)
def test_bar_rejects_invalid_exact_values_and_time(changes):
    with pytest.raises(ValueError):
        bar(**changes)


def test_interval_and_adjustment_are_explicit():
    with pytest.raises(ValueError):
        replace(bar().identity, interval="1Min")
    with pytest.raises(ValueError):
        replace(bar().identity, adjustment="split")
    with pytest.raises(ValueError):
        replace(bar().identity, bar_end=END + timedelta(seconds=1))


def test_fingerprint_deduplicates_decimal_spellings_ids_and_receive_time():
    original = bar()
    equivalent = replace(
        original,
        revision_id=uuid4(),
        open=Decimal("10.000"),
        volume=Decimal("100.000"),
        ingested_at=SEEN + timedelta(hours=1),
        available_at=SEEN + timedelta(hours=1),
        commit_version=9,
    )
    assert equivalent.fingerprint == original.fingerprint
    assert replace(original, close=Decimal("10.5")).fingerprint != original.fingerprint
    assert replace(original, identity=replace(original.identity, feed="sip")).fingerprint != (
        original.fingerprint
    )
    with localcontext() as context:
        context.prec = 2
        precise = bar(volume=Decimal("12345678901234567890.123456789012345678"))
        before = precise.fingerprint
    assert precise.fingerprint == before


def test_later_revision_is_immutable_and_bound_to_predecessor():
    original = bar()
    later = replace(
        original,
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=original.revision_id,
        close=Decimal("10.5"),
    )
    assert later.fingerprint != original.fingerprint
    assert original.local_revision == 1
    with pytest.raises(ValueError):
        replace(later, predecessor_id=later.revision_id)


def test_verified_publication_still_requires_local_ingestion_for_pit():
    item = bar(
        published_at=END,
        available_at=END,
        availability_basis=AvailabilityBasis.VERIFIED_PUBLICATION,
    )
    query = PointInTimeQuery(SEEN, END, SEEN, 2)
    assert not query.includes(item)


@pytest.mark.parametrize(
    "content,kind",
    [
        (NewsContent("Normalized headline", "en"), EvidenceType.NEWS),
        (FundamentalContent("revenue", Decimal("1"), "USD", START), EvidenceType.FUNDAMENTAL),
        (MacroEventContent("CPI", "US", Decimal("3.0"), "percent"), EvidenceType.MACRO_EVENT),
        (DerivedContent("return", Decimal("0.1"), "ratio", "v1", (ID,)), EvidenceType.DERIVED),
    ],
)
def test_discriminated_evidence_content(content, kind):
    assert evidence(content, kind).content == content
    with pytest.raises(ValueError, match="discriminator"):
        evidence(content, EvidenceType.BAR)


def test_quality_is_not_repaired_by_high_model_confidence():
    item = evidence(model_confidence=Decimal("1"), quality=QualityStatus.INVALID)
    assert item.quality == QualityStatus.INVALID
    assert not PointInTimeQuery(SEEN, SEEN, SEEN, 2).includes(item)
    with pytest.raises(ValueError):
        evidence(model_confidence=0.99)


def test_evidence_fingerprint_and_id_ignore_evidence_ids_and_receive_times():
    first = evidence()
    second = replace(
        first,
        evidence_id=uuid4(),
        available_at=SEEN + timedelta(seconds=1),
        ingested_at=SEEN + timedelta(seconds=1),
    )
    assert first.fingerprint == second.fingerprint
    assert first.deterministic_id == second.deterministic_id
    with pytest.raises(ValueError):
        replace(first, source_reference="https://example.test/?secret=value")


def test_content_recurrence_creates_distinct_occurrences_but_head_retry_is_idempotent():
    from packages.research.storage import evidence_for_bar, prepare_bar_revision

    incoming_a = bar()
    first_a = prepare_bar_revision(incoming_a, None)
    incoming_b = replace(
        incoming_a,
        close=Decimal("10.5"),
        available_at=SEEN + timedelta(seconds=1),
        ingested_at=SEEN + timedelta(seconds=1),
    )
    correction_b = prepare_bar_revision(incoming_b, first_a)
    returned_a = prepare_bar_revision(
        replace(
            incoming_a,
            available_at=SEEN + timedelta(seconds=2),
            ingested_at=SEEN + timedelta(seconds=2),
        ),
        correction_b,
    )
    retry_a = prepare_bar_revision(
        replace(
            incoming_a,
            available_at=SEEN + timedelta(seconds=3),
            ingested_at=SEEN + timedelta(seconds=3),
        ),
        returned_a,
    )
    assert first_a.fingerprint == returned_a.fingerprint
    assert first_a.fingerprint != correction_b.fingerprint
    assert len({first_a.revision_id, correction_b.revision_id, returned_a.revision_id}) == 3
    assert (first_a.local_revision, correction_b.local_revision, returned_a.local_revision) == (
        1,
        2,
        3,
    )
    assert correction_b.predecessor_id == first_a.revision_id
    assert returned_a.predecessor_id == correction_b.revision_id
    assert retry_a is returned_a and retry_a.ingested_at == SEEN + timedelta(seconds=2)
    first_evidence, returned_evidence = evidence_for_bar(first_a), evidence_for_bar(returned_a)
    assert first_evidence.fingerprint == returned_evidence.fingerprint
    assert first_evidence.evidence_id != returned_evidence.evidence_id
    assert prepare_bar_revision(replace(incoming_a, revision_id=uuid4()), None).revision_id == (
        first_a.revision_id
    )


def test_pit_boundaries_backfill_snapshot_and_version():
    item = bar()
    query = PointInTimeQuery(SEEN, SEEN, SEEN, 2)
    assert query.includes(item)
    assert query.includes(evidence())
    assert not replace(query, knowledge_at=END).includes(item)
    assert not replace(query, snapshot_at=SEEN - timedelta(microseconds=1)).includes(item)
    assert not replace(query, snapshot_version=1).includes(item)
    assert not query.includes(replace(item, quality=QualityStatus.UNKNOWN_CALENDAR))
    with pytest.raises(ValueError):
        replace(query, knowledge_at=SEEN + timedelta(microseconds=1))


def test_context_is_snapshot_bound_and_has_explicit_unavailable_metrics():
    missing = ContextMetric(MetricStatus.NOT_COMPUTABLE)
    context = MarketContext(
        ID,
        PointInTimeQuery(SEEN, SEEN, SEEN, 2),
        (uuid4(),),
        QualityStatus.MISSING_EXPECTED,
        (EvidenceType.NEWS,),
        2,
        "returns-v1",
        missing,
        missing,
    )
    assert context.volatility.value is None
    with pytest.raises(ValueError):
        replace(context, data_version=3)
    with pytest.raises(ValueError):
        ContextMetric(MetricStatus.COMPUTABLE)
    with pytest.raises(ValueError):
        replace(context, evidence_ids=[])


@pytest.mark.parametrize(
    "adapter,request_type",
    [
        (UnavailableNewsSource(), NewsRequest),
        (UnavailableFundamentalsSource(), FundamentalsRequest),
        (UnavailableMacroEventsSource(), MacroEventsRequest),
    ],
)
def test_missing_adapters_raise_explicit_redacted_not_implemented(adapter, request_type):
    assert adapter.capability().status == SourceStatus.NOT_IMPLEMENTED
    with pytest.raises(SourceError) as failure:
        # These absent adapters must fail before performing or scheduling any I/O.
        adapter.fetch(request_type(ID, START, END)).send(None)
    assert failure.value.code == SourceErrorCode.NOT_IMPLEMENTED
    assert str(failure.value) == "NOT_IMPLEMENTED"


def test_requests_and_pages_are_bounded_typed_and_immutable():
    options = dict(
        symbol="SPY", feed="iex", interval=BarInterval.FIFTEEN_MINUTES, adjustment=Adjustment.RAW
    )
    assert HistoricalBarsRequest(ID, START, END, **options).adjustment.value == "raw"
    with pytest.raises(ValueError):
        HistoricalBarsRequest(ID, START, START + timedelta(days=367), **options)
    with pytest.raises(ValueError):
        HistoricalBarsRequest(ID, START, END, limit=1001, **options)
    with pytest.raises(ValueError):
        HistoricalBarsPage([bar()])
    with pytest.raises(ValueError):
        NewsPage((evidence(),))
    assert HistoricalBarsPage((bar(),)).has_more is False
