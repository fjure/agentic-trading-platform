"""Pure normalization helpers and an honest catalogue of research sources.

These functions accept typed, already-normalized fixture values. They perform no
provider I/O and deliberately have no raw-payload or credential field.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

from packages.research.models import (
    AvailabilityBasis,
    EvidenceType,
    FundamentalContent,
    MacroEventContent,
    NewsContent,
    QualityStatus,
    ResearchEvidence,
    content_fingerprint,
)
from packages.research.ports import (
    LicenseStatus,
    SourceCapability,
    SourceStatus,
    UnavailableFundamentalsSource,
    UnavailableMacroEventsSource,
    UnavailableNewsSource,
)

_OCCURRENCE_NAMESPACE = UUID("3e352129-5dd4-4074-8da7-818e39e280aa")


@dataclass(frozen=True)
class EvidenceProvenance:
    """Typed provenance shared by normalized fixture inputs."""

    source: str
    source_reference: str
    instrument_id: UUID | None
    market: str
    event_time: datetime
    published_at: datetime | None
    available_at: datetime
    ingested_at: datetime
    quality: QualityStatus
    availability_basis: AvailabilityBasis = AvailabilityBasis.FIRST_OBSERVED
    model_confidence: Decimal | None = None
    schema_version: int = 1
    data_revision: int = 1
    commit_version: int = 0


@dataclass(frozen=True)
class NewsInput:
    provenance: EvidenceProvenance
    headline: str
    language: str


@dataclass(frozen=True)
class FundamentalInput:
    provenance: EvidenceProvenance
    metric: str
    value: Decimal
    unit: str
    period_end: datetime


@dataclass(frozen=True)
class MacroEventInput:
    provenance: EvidenceProvenance
    event_code: str
    region: str
    actual: Decimal | None = None
    unit: str | None = None


def _evidence(
    provenance: EvidenceProvenance,
    evidence_type: EvidenceType,
    content: NewsContent | FundamentalContent | MacroEventContent,
) -> ResearchEvidence:
    if not isinstance(provenance, EvidenceProvenance):
        raise ValueError("typed evidence provenance required")
    item = ResearchEvidence(
        evidence_id=UUID(int=0),
        evidence_type=evidence_type,
        source=provenance.source,
        source_reference=provenance.source_reference,
        instrument_id=provenance.instrument_id,
        market=provenance.market,
        event_time=provenance.event_time,
        published_at=provenance.published_at,
        available_at=provenance.available_at,
        ingested_at=provenance.ingested_at,
        content=content,
        quality=provenance.quality,
        availability_basis=provenance.availability_basis,
        model_confidence=provenance.model_confidence,
        schema_version=provenance.schema_version,
        data_revision=provenance.data_revision,
        commit_version=provenance.commit_version,
    )
    occurrence_key = content_fingerprint(
        (
            "research-evidence-occurrence-v1",
            evidence_type,
            provenance.source,
            provenance.source_reference,
            provenance.instrument_id,
            provenance.market,
            provenance.event_time,
            provenance.schema_version,
            provenance.data_revision,
        )
    )
    return replace(item, evidence_id=uuid5(_OCCURRENCE_NAMESPACE, occurrence_key))


def normalize_news(item: NewsInput) -> ResearchEvidence:
    """Create deterministic typed news evidence from a normalized fixture."""
    if not isinstance(item, NewsInput):
        raise ValueError("typed news input required")
    return _evidence(
        item.provenance,
        EvidenceType.NEWS,
        NewsContent(item.headline, item.language),
    )


def normalize_fundamental(item: FundamentalInput) -> ResearchEvidence:
    """Create deterministic typed fundamental evidence from a normalized fixture."""
    if not isinstance(item, FundamentalInput):
        raise ValueError("typed fundamental input required")
    return _evidence(
        item.provenance,
        EvidenceType.FUNDAMENTAL,
        FundamentalContent(item.metric, item.value, item.unit, item.period_end),
    )


def normalize_macro_event(item: MacroEventInput) -> ResearchEvidence:
    """Create deterministic typed macro evidence from a normalized fixture."""
    if not isinstance(item, MacroEventInput):
        raise ValueError("typed macro-event input required")
    return _evidence(
        item.provenance,
        EvidenceType.MACRO_EVENT,
        MacroEventContent(item.event_code, item.region, item.actual, item.unit),
    )


@dataclass(frozen=True)
class SourceCatalogEntry:
    """A visible source/category, including categories with no evidence adapter."""

    name: str
    evidence_type: EvidenceType | None
    capability: SourceCapability | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("source name required")
        if self.evidence_type is not None and not isinstance(self.evidence_type, EvidenceType):
            raise ValueError("unknown evidence type")
        if self.capability is not None:
            if not isinstance(self.capability, SourceCapability):
                raise ValueError("typed source capability required")
            if (
                self.capability.evidence_type != self.evidence_type
                or self.capability.source != self.name
            ):
                raise ValueError("catalog entry conflicts with capability")

    @property
    def status(self) -> SourceStatus:
        return self.capability.status if self.capability else SourceStatus.NOT_IMPLEMENTED

    @property
    def license_status(self) -> LicenseStatus:
        return self.capability.license_status if self.capability else LicenseStatus.UNKNOWN


def research_source_catalog(
    historical_bars: SourceCapability | None = None,
) -> tuple[SourceCatalogEntry, ...]:
    """Return known capabilities without claiming that an absent adapter works.

    A caller must pass the actual bar-adapter capability. With no capability
    supplied, historical bars are explicitly NOT_IMPLEMENTED. Regime is a
    category rather than an EvidenceType in the current domain model.
    """
    if historical_bars is None:
        bar_entry = SourceCatalogEntry(
            "historical-bars",
            EvidenceType.BAR,
            SourceCapability("historical-bars", EvidenceType.BAR, SourceStatus.NOT_IMPLEMENTED),
        )
    else:
        if not isinstance(historical_bars, SourceCapability):
            raise ValueError("typed historical bar capability required")
        if historical_bars.evidence_type != EvidenceType.BAR:
            raise ValueError("historical source must describe bar evidence")
        bar_entry = SourceCatalogEntry(
            historical_bars.source,
            historical_bars.evidence_type,
            historical_bars,
        )

    news = UnavailableNewsSource().capability()
    fundamentals = UnavailableFundamentalsSource().capability()
    macro = UnavailableMacroEventsSource().capability()
    return (
        bar_entry,
        SourceCatalogEntry(news.source, news.evidence_type, news),
        SourceCatalogEntry(fundamentals.source, fundamentals.evidence_type, fundamentals),
        SourceCatalogEntry(macro.source, macro.evidence_type, macro),
        SourceCatalogEntry("regime", None),
    )
