"""Research values: exact decimals, immutable revisions and conservative observed-as-of time."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid5

from packages.domain.models import decimal_value, utc_string, utc_time


def _label(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
        raise ValueError("invalid normalized identifier")
    if "://" in value:
        raise ValueError("URLs are not source references")


def _uuid(value: UUID) -> None:
    if not isinstance(value, UUID):
        raise ValueError("UUID required")


def _version(value: int, *, zero: bool = False) -> None:
    if type(value) is not int or not (0 if zero else 1) <= value <= 9007199254740991:
        raise ValueError("invalid version")


def _canonical(value: object) -> object:
    if isinstance(value, Decimal):
        # Never normalize() under the caller's decimal context: it can round.
        exact = decimal_value(value)
        return (
            "0"
            if exact == 0
            else format(exact, "f").rstrip("0").rstrip(".")
            if "." in format(exact, "f")
            else format(exact, "f")
        )
    if isinstance(value, datetime):
        return utc_string(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical object keys must be strings")
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def content_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    CRYPTO = "CRYPTO"
    FOREX = "FOREX"
    DERIVATIVE = "DERIVATIVE"


class BarInterval(StrEnum):
    FIFTEEN_MINUTES = "15Min"
    ONE_DAY = "1Day"


class Adjustment(StrEnum):
    RAW = "raw"


class AvailabilityBasis(StrEnum):
    FIRST_OBSERVED = "FIRST_OBSERVED"
    VERIFIED_PUBLICATION = "VERIFIED_PUBLICATION"


class QualityStatus(StrEnum):
    VALID = "VALID"
    MISSING_EXPECTED = "MISSING_EXPECTED"
    NO_OBSERVATION = "NO_OBSERVATION"
    UNKNOWN_CALENDAR = "UNKNOWN_CALENDAR"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class EvidenceType(StrEnum):
    BAR = "BAR"
    NEWS = "NEWS"
    FUNDAMENTAL = "FUNDAMENTAL"
    MACRO_EVENT = "MACRO_EVENT"
    DERIVED = "DERIVED"


class IngestionJobStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    PARTIAL = "PARTIAL"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Instrument:
    instrument_id: UUID
    asset_class: AssetClass
    mic: str
    currency: str
    base_unit: str
    quote_unit: str

    def __post_init__(self) -> None:
        _uuid(self.instrument_id)
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("unknown asset class")
        if not re.fullmatch(r"[A-Z0-9]{4}", self.mic):
            raise ValueError("MIC must contain four uppercase characters")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("currency required")
        _label(self.base_unit)
        _label(self.quote_unit)


@dataclass(frozen=True)
class SymbolMapping:
    instrument_id: UUID
    provider: str
    symbol: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _uuid(self.instrument_id)
        _label(self.provider)
        _label(self.symbol)
        if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,15}", self.symbol):
            raise ValueError("invalid provider symbol")
        utc_time(self.valid_from)
        if self.valid_to is not None and utc_time(self.valid_to) <= self.valid_from:
            raise ValueError("symbol validity must be a nonempty half-open interval")


def validate_symbol_mappings(mappings: tuple[SymbolMapping, ...]) -> None:
    """Reject ambiguous symbols and overlapping mappings for one instrument/provider."""
    for index, left in enumerate(mappings):
        for right in mappings[index + 1 :]:
            same_key = left.provider == right.provider and (
                left.instrument_id == right.instrument_id or left.symbol == right.symbol
            )
            overlaps = (left.valid_to is None or right.valid_from < left.valid_to) and (
                right.valid_to is None or left.valid_from < right.valid_to
            )
            if same_key and overlaps:
                raise ValueError("overlapping symbol mappings")


@dataclass(frozen=True)
class BarIdentity:
    instrument_id: UUID
    provider: str
    feed: str
    interval: BarInterval
    bar_start: datetime
    bar_end: datetime
    adjustment: Adjustment = Adjustment.RAW

    def __post_init__(self) -> None:
        _uuid(self.instrument_id)
        _label(self.provider)
        _label(self.feed)
        utc_time(self.bar_start)
        utc_time(self.bar_end)
        if not isinstance(self.interval, BarInterval) or not isinstance(
            self.adjustment, Adjustment
        ):
            raise ValueError("unsupported interval or adjustment")
        if self.bar_end <= self.bar_start:
            raise ValueError("bar window must be nonempty")
        if self.interval == BarInterval.FIFTEEN_MINUTES and (
            self.bar_end - self.bar_start != timedelta(minutes=15)
        ):
            raise ValueError("15Min bar must span exactly 15 minutes")


def _availability(
    event_time: datetime,
    published_at: datetime | None,
    available_at: datetime,
    ingested_at: datetime,
    basis: AvailabilityBasis,
) -> None:
    for value in (event_time, available_at, ingested_at):
        utc_time(value)
    if published_at is not None:
        utc_time(published_at)
    if not isinstance(basis, AvailabilityBasis):
        raise ValueError("unknown availability basis")
    if basis == AvailabilityBasis.FIRST_OBSERVED:
        if published_at is not None or available_at != ingested_at:
            raise ValueError(
                "FIRST_OBSERVED requires unknown publication and availability=ingestion"
            )
    elif published_at is None or available_at < published_at:
        raise ValueError("verified publication requires a publication bound")
    if available_at < event_time or ingested_at < event_time:
        raise ValueError("evidence cannot precede its event")


@dataclass(frozen=True)
class BarRevision:
    revision_id: UUID
    identity: BarIdentity
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    ingested_at: datetime
    available_at: datetime
    published_at: datetime | None = None
    availability_basis: AvailabilityBasis = AvailabilityBasis.FIRST_OBSERVED
    local_revision: int = 1
    predecessor_id: UUID | None = None
    provider_revision: str | None = None
    schema_version: int = 1
    commit_version: int = 0
    quality: QualityStatus = QualityStatus.VALID

    def __post_init__(self) -> None:
        _uuid(self.revision_id)
        if not isinstance(self.identity, BarIdentity):
            raise ValueError("bar identity required")
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("prices must be positive and volume nonnegative")
        if self.high < max(self.open, self.close, self.low) or self.low > min(
            self.open, self.close
        ):
            raise ValueError("implausible OHLC")
        _availability(
            self.identity.bar_end,
            self.published_at,
            self.available_at,
            self.ingested_at,
            self.availability_basis,
        )
        _version(self.local_revision)
        _version(self.schema_version)
        _version(self.commit_version, zero=True)
        if (self.local_revision == 1) != (self.predecessor_id is None):
            raise ValueError("revision predecessor required exactly after first revision")
        if self.predecessor_id is not None:
            _uuid(self.predecessor_id)
            if self.predecessor_id == self.revision_id:
                raise ValueError("revision cannot precede itself")
        if self.provider_revision is not None:
            _label(self.provider_revision)
        if not isinstance(self.quality, QualityStatus):
            raise ValueError("unknown quality")

    @property
    def fingerprint(self) -> str:
        return content_fingerprint(
            (
                self.schema_version,
                self.identity,
                self.open,
                self.high,
                self.low,
                self.close,
                self.volume,
                self.provider_revision,
                self.published_at,
            )
        )


@dataclass(frozen=True)
class BarContent:
    revision_id: UUID
    bar_fingerprint: str

    def __post_init__(self) -> None:
        _uuid(self.revision_id)
        if not re.fullmatch(r"[a-f0-9]{64}", self.bar_fingerprint):
            raise ValueError("invalid bar fingerprint")


@dataclass(frozen=True)
class NewsContent:
    headline: str
    language: str

    def __post_init__(self) -> None:
        if not isinstance(self.headline, str) or not 1 <= len(self.headline) <= 500:
            raise ValueError("normalized headline required")
        if not re.fullmatch(r"[a-z]{2}", self.language):
            raise ValueError("language must be ISO two-letter code")


@dataclass(frozen=True)
class FundamentalContent:
    metric: str
    value: Decimal
    unit: str
    period_end: datetime

    def __post_init__(self) -> None:
        _label(self.metric)
        _label(self.unit)
        object.__setattr__(self, "value", decimal_value(self.value))
        utc_time(self.period_end)


@dataclass(frozen=True)
class MacroEventContent:
    event_code: str
    region: str
    actual: Decimal | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        _label(self.event_code)
        _label(self.region)
        if (self.actual is None) != (self.unit is None):
            raise ValueError("macro value requires its unit")
        if self.actual is not None:
            object.__setattr__(self, "actual", decimal_value(self.actual))
            _label(self.unit)


@dataclass(frozen=True)
class DerivedContent:
    metric: str
    value: Decimal
    unit: str
    algorithm_version: str
    input_evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        for value in (self.metric, self.unit, self.algorithm_version):
            _label(value)
        object.__setattr__(self, "value", decimal_value(self.value))
        if not isinstance(self.input_evidence_ids, tuple) or not self.input_evidence_ids:
            raise ValueError("immutable evidence inputs required")
        for evidence_id in self.input_evidence_ids:
            _uuid(evidence_id)


EvidenceContent = BarContent | NewsContent | FundamentalContent | MacroEventContent | DerivedContent
_CONTENT_TYPES = dict(
    zip(
        EvidenceType,
        (BarContent, NewsContent, FundamentalContent, MacroEventContent, DerivedContent),
        strict=True,
    )
)


@dataclass(frozen=True)
class ResearchEvidence:
    evidence_id: UUID
    evidence_type: EvidenceType
    source: str
    source_reference: str
    instrument_id: UUID | None
    market: str
    event_time: datetime
    published_at: datetime | None
    available_at: datetime
    ingested_at: datetime
    content: EvidenceContent
    quality: QualityStatus
    availability_basis: AvailabilityBasis = AvailabilityBasis.FIRST_OBSERVED
    model_confidence: Decimal | None = None
    schema_version: int = 1
    data_revision: int = 1
    commit_version: int = 0

    def __post_init__(self) -> None:
        _uuid(self.evidence_id)
        if self.instrument_id is not None:
            _uuid(self.instrument_id)
        for value in (self.source, self.source_reference, self.market):
            _label(value)
        if (
            not isinstance(self.evidence_type, EvidenceType)
            or type(self.content) is not (_CONTENT_TYPES[self.evidence_type])
        ):
            raise ValueError("content does not match evidence discriminator")
        if not isinstance(self.quality, QualityStatus):
            raise ValueError("unknown quality")
        _availability(
            self.event_time,
            self.published_at,
            self.available_at,
            self.ingested_at,
            self.availability_basis,
        )
        _version(self.schema_version)
        _version(self.data_revision)
        _version(self.commit_version, zero=True)
        if self.model_confidence is not None:
            value = decimal_value(self.model_confidence)
            if not 0 <= value <= 1:
                raise ValueError("confidence must be in [0,1]")
            object.__setattr__(self, "model_confidence", value)

    @property
    def fingerprint(self) -> str:
        # A bar's random local revision UUID is not provider content.
        content = (
            self.content.bar_fingerprint if isinstance(self.content, BarContent) else self.content
        )
        return content_fingerprint(
            (
                self.schema_version,
                self.evidence_type,
                self.source,
                self.source_reference,
                self.instrument_id,
                self.market,
                self.event_time,
                self.published_at,
                content,
            )
        )

    @property
    def deterministic_id(self) -> UUID:
        # Content can recur after a correction. BAR identity binds that observation occurrence,
        # while the content fingerprint deliberately remains stable across A -> B -> A.
        identity = self.fingerprint
        if isinstance(self.content, BarContent):
            identity = f"BAR:{self.content.revision_id}:{identity}"
        return uuid5(UUID("fe856bb6-e0ed-485d-8729-41ea2cb8bd02"), identity)


@dataclass(frozen=True)
class PointInTimeQuery:
    valuation_at: datetime
    knowledge_at: datetime
    snapshot_at: datetime
    snapshot_version: int

    def __post_init__(self) -> None:
        for value in (self.valuation_at, self.knowledge_at, self.snapshot_at):
            utc_time(value)
        if self.knowledge_at > self.valuation_at:
            raise ValueError("knowledge cannot exceed valuation")
        _version(self.snapshot_version, zero=True)

    def includes(self, value: BarRevision | ResearchEvidence) -> bool:
        event = value.identity.bar_end if isinstance(value, BarRevision) else value.event_time
        return (
            event <= self.valuation_at
            and value.available_at <= self.knowledge_at
            and value.ingested_at <= min(self.knowledge_at, self.snapshot_at)
            and value.commit_version <= self.snapshot_version
            and value.quality == QualityStatus.VALID
        )


class MetricStatus(StrEnum):
    COMPUTABLE = "COMPUTABLE"
    NOT_COMPUTABLE = "NOT_COMPUTABLE"


@dataclass(frozen=True)
class ContextMetric:
    status: MetricStatus
    value: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MetricStatus):
            raise ValueError("unknown metric status")
        if (self.status == MetricStatus.COMPUTABLE) != (self.value is not None):
            raise ValueError("value must match metric status")
        if self.value is not None:
            object.__setattr__(self, "value", decimal_value(self.value))


@dataclass(frozen=True)
class MarketContext:
    instrument_id: UUID
    query: PointInTimeQuery
    evidence_ids: tuple[UUID, ...]
    quality: QualityStatus
    missing_sources: tuple[EvidenceType, ...]
    data_version: int
    calculation_version: str
    price_change: ContextMetric
    volatility: ContextMetric

    def __post_init__(self) -> None:
        _uuid(self.instrument_id)
        _version(self.data_version, zero=True)
        _label(self.calculation_version)
        if (
            not isinstance(self.query, PointInTimeQuery)
            or self.data_version > self.query.snapshot_version
        ):
            raise ValueError("context version exceeds query snapshot")
        if not isinstance(self.evidence_ids, tuple) or not isinstance(self.missing_sources, tuple):
            raise ValueError("immutable context references required")
        for value in self.evidence_ids:
            _uuid(value)
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("duplicate context evidence")
        if not isinstance(self.quality, QualityStatus) or any(
            not isinstance(value, EvidenceType) for value in self.missing_sources
        ):
            raise ValueError("unknown context quality or source")
        if not isinstance(self.price_change, ContextMetric) or not isinstance(
            self.volatility, ContextMetric
        ):
            raise ValueError("typed metrics required")
