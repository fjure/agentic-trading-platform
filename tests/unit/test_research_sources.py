"""Typed fixture normalization and honest source capability reporting."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.research.models import EvidenceType, QualityStatus
from packages.research.ports import (
    BarInterval,
    LicenseStatus,
    SourceCapability,
    SourceStatus,
)
from packages.research.sources import (
    EvidenceProvenance,
    FundamentalInput,
    MacroEventInput,
    NewsInput,
    normalize_fundamental,
    normalize_macro_event,
    normalize_news,
    research_source_catalog,
)

INSTRUMENT = UUID("ce85d363-e845-4b4a-9b11-6de080bb43da")
EVENT = datetime(2026, 10, 23, 14, 0, tzinfo=UTC)
OBSERVED = EVENT + timedelta(minutes=1)


def provenance(**changes):
    base = EvidenceProvenance(
        source="fixture",
        source_reference="fixture:record-1",
        instrument_id=INSTRUMENT,
        market="US_EQUITY",
        event_time=EVENT,
        published_at=None,
        available_at=OBSERVED,
        ingested_at=OBSERVED,
        quality=QualityStatus.VALID,
    )
    return replace(base, **changes)


def test_typed_normalizers_create_deterministic_discriminated_evidence():
    news_input = NewsInput(provenance(), "Normalized headline", "en")
    fundamental_input = FundamentalInput(provenance(), "revenue", Decimal("1200.500"), "USD", EVENT)
    macro_input = MacroEventInput(
        provenance(instrument_id=None, market="US"), "CPI", "US", Decimal("3.2"), "percent"
    )

    news = normalize_news(news_input)
    fundamental = normalize_fundamental(fundamental_input)
    macro = normalize_macro_event(macro_input)

    assert news.evidence_type == EvidenceType.NEWS
    assert normalize_news(news_input).evidence_id == news.evidence_id
    assert fundamental.evidence_type == EvidenceType.FUNDAMENTAL
    assert fundamental.content.value == Decimal("1200.5")
    assert macro.evidence_type == EvidenceType.MACRO_EVENT
    assert macro.instrument_id is None
    assert macro.content.actual == Decimal("3.2")
    assert len({news.fingerprint, fundamental.fingerprint, macro.fingerprint}) == 3


def test_repeated_content_in_a_later_occurrence_has_a_new_evidence_id():
    first = normalize_news(NewsInput(provenance(data_revision=1), "A", "en"))
    changed = normalize_news(NewsInput(provenance(data_revision=2), "B", "en"))
    returned = normalize_news(NewsInput(provenance(data_revision=3), "A", "en"))
    assert first.fingerprint == returned.fingerprint
    assert first.evidence_id != returned.evidence_id
    assert changed.evidence_id not in (first.evidence_id, returned.evidence_id)


def test_normalization_rejects_untyped_inputs_and_url_source_references():
    with pytest.raises(ValueError, match="typed news"):
        normalize_news({"headline": "raw provider payload"})
    with pytest.raises(ValueError, match="invalid normalized identifier"):
        normalize_news(
            NewsInput(
                provenance(source_reference="https://example.test/?token=secret"),
                "Headline",
                "en",
            )
        )


def test_catalog_never_claims_missing_bar_adapter_and_names_absent_capabilities():
    catalog = research_source_catalog()
    assert [(entry.name, entry.status) for entry in catalog] == [
        ("historical-bars", SourceStatus.NOT_IMPLEMENTED),
        ("news", SourceStatus.NOT_IMPLEMENTED),
        ("fundamentals", SourceStatus.NOT_IMPLEMENTED),
        ("macro", SourceStatus.NOT_IMPLEMENTED),
        ("regime", SourceStatus.NOT_IMPLEMENTED),
    ]
    assert catalog[-1].evidence_type is None
    assert all(entry.license_status == LicenseStatus.UNKNOWN for entry in catalog)

    actual = SourceCapability(
        "alpaca-history",
        EvidenceType.BAR,
        SourceStatus.BLOCKED,
        LicenseStatus.UNKNOWN,
        feeds=("iex",),
        intervals=(BarInterval.FIFTEEN_MINUTES,),
    )
    updated = research_source_catalog(actual)
    assert updated[0].name == "alpaca-history"
    assert updated[0].status == SourceStatus.BLOCKED
    assert updated[0].capability == actual
    with pytest.raises(ValueError, match="bar evidence"):
        research_source_catalog(
            SourceCapability("news", EvidenceType.NEWS, SourceStatus.IMPLEMENTED)
        )
