"""Strict public Research response values; no provider transport data is exposed."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

Version = Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
PositiveVersion = Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
Count = Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
AttemptCount = Annotated[int, Field(strict=True, ge=0, le=3)]
UUIDValue = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", max_length=36),
]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", max_length=64)]
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$",
        max_length=27,
    ),
]
ResearchDecimal = Annotated[
    str,
    Field(pattern=r"^-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?$", max_length=40),
]
Confidence = Annotated[str, Field(pattern=r"^(?:0(?:\.\d{1,18})?|1(?:\.0{1,18})?)$", max_length=20)]
Cursor = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=2048)]
SourceReference = Annotated[
    str,
    Field(pattern=r"^(?!.*://)[A-Za-z0-9][A-Za-z0-9:._/-]{0,255}$", max_length=256),
]
Market = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=64)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, regex_engine="python-re")

    @field_validator("*", check_fields=False)
    @classmethod
    def utc_timestamps(cls, value, info):
        if value is not None and info.field_name in {
            "start",
            "end",
            "eventTime",
            "publishedAt",
            "availableAt",
            "ingestedAt",
            "valuationAt",
            "knowledgeAt",
            "snapshotAt",
            "observedAt",
            "historyStart",
            "historyEnd",
            "periodEnd",
            "acceptedAt",
            "updatedAt",
            "retryAt",
        }:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
            ):
                raise ValueError("INVALID_REQUEST")
            try:
                datetime.fromisoformat(value[:-1] + "+00:00")
            except ValueError as error:
                raise ValueError("INVALID_REQUEST") from error
        return value


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _uuid(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value):
        raise ValueError("INVALID_REQUEST")
    return value


class ResearchBar(DTO):
    barId: UUIDValue
    instrumentId: UUIDValue
    provider: Literal["ALPACA"]
    feed: Literal["iex"]
    interval: Literal["15Min", "1Day"]
    adjustment: Literal["raw"]
    currency: Literal["USD"]
    start: Timestamp
    end: Timestamp
    open: ResearchDecimal
    high: ResearchDecimal
    low: ResearchDecimal
    close: ResearchDecimal
    volume: ResearchDecimal
    revision: PositiveVersion
    predecessorId: UUIDValue | None
    providerRevision: Annotated[str, Field(min_length=1, max_length=128)] | None
    eventTime: Timestamp
    publishedAt: Timestamp | None
    availableAt: Timestamp
    ingestedAt: Timestamp
    availabilityBasis: Literal["FIRST_OBSERVED"]
    fingerprint: Fingerprint
    evidenceId: UUIDValue
    quality: Literal[
        "VALID", "MISSING_EXPECTED", "NO_OBSERVATION", "UNKNOWN_CALENDAR", "CONFLICT", "INVALID"
    ]
    commitVersion: PositiveVersion

    @model_validator(mode="after")
    def valid_bar(self):
        if not _time(self.start) < _time(self.end) or _time(self.eventTime) != _time(self.end):
            raise ValueError("INVALID_REQUEST")
        if self.publishedAt is not None or _time(self.availableAt) != _time(self.ingestedAt):
            raise ValueError("INVALID_REQUEST")
        o, h, low, c, v = map(Decimal, (self.open, self.high, self.low, self.close, self.volume))
        if min(o, h, low, c) <= 0 or v < 0 or h < max(o, low, c) or low > min(o, h, c):
            raise ValueError("INVALID_REQUEST")
        return self


class BarContent(DTO):
    kind: Literal["BAR"]
    barId: UUIDValue


class NewsContent(DTO):
    kind: Literal["NEWS"]
    headline: Annotated[str, Field(min_length=1, max_length=500)]
    language: Annotated[str, Field(pattern=r"^[a-z]{2}$", max_length=2)]


class FundamentalContent(DTO):
    kind: Literal["FUNDAMENTAL"]
    metric: Annotated[str, Field(min_length=1, max_length=64)]
    value: ResearchDecimal
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    periodEnd: Timestamp


class MacroContent(DTO):
    kind: Literal["MACRO_EVENT"]
    eventCode: Annotated[str, Field(min_length=1, max_length=64)]
    region: Annotated[str, Field(min_length=1, max_length=64)]
    actual: ResearchDecimal | None
    unit: Annotated[str, Field(min_length=1, max_length=32)] | None

    @model_validator(mode="after")
    def actual_unit_pair(self):
        if (self.actual is None) != (self.unit is None):
            raise ValueError("INVALID_REQUEST")
        return self


class DerivedContent(DTO):
    kind: Literal["DERIVED"]
    metric: Literal["PRICE_CHANGE", "VOLATILITY"]
    value: ResearchDecimal
    algorithmVersion: Literal["decimal-population-v1"]
    evidenceIds: Annotated[list[UUIDValue], Field(max_length=200)]


EvidenceContent = Annotated[
    BarContent | NewsContent | FundamentalContent | MacroContent | DerivedContent,
    Field(discriminator="kind"),
]


class ResearchEvidence(DTO):
    evidenceId: UUIDValue
    instrumentId: UUIDValue | None
    sourceId: Annotated[str, Field(min_length=1, max_length=64)]
    sourceReference: SourceReference
    eventTime: Timestamp
    publishedAt: Timestamp | None
    availableAt: Timestamp
    ingestedAt: Timestamp
    availabilityBasis: Literal["FIRST_OBSERVED", "VERIFIED_PUBLICATION"]
    schemaVersion: Literal["1"]
    dataRevision: PositiveVersion
    predecessorId: UUIDValue | None
    fingerprint: Fingerprint
    quality: Literal[
        "VALID", "MISSING_EXPECTED", "NO_OBSERVATION", "UNKNOWN_CALENDAR", "CONFLICT", "INVALID"
    ]
    modelConfidence: Confidence | None
    content: EvidenceContent
    commitVersion: PositiveVersion
    market: Market

    @field_validator("evidenceId", "instrumentId", "predecessorId")
    @classmethod
    def uuid_fields(cls, value):
        return _uuid(value) if value is not None else value

    @model_validator(mode="after")
    def availability(self):
        if self.availabilityBasis == "FIRST_OBSERVED":
            if self.publishedAt is not None or _time(self.availableAt) != _time(self.ingestedAt):
                raise ValueError("INVALID_REQUEST")
        elif (
            self.publishedAt is None
            or _time(self.publishedAt) > _time(self.availableAt)
            or _time(self.availableAt) > _time(self.ingestedAt)
        ):
            raise ValueError("INVALID_REQUEST")
        return self


class Snapshot(DTO):
    valuationAt: Timestamp
    knowledgeAt: Timestamp
    snapshotAt: Timestamp
    snapshotVersion: Version
    semantics: Literal["observed-as-of"]

    @model_validator(mode="after")
    def knowledge_precedes_valuation(self):
        if _time(self.knowledgeAt) > _time(self.valuationAt):
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchInstrument(DTO):
    instrumentId: UUIDValue
    assetClass: Literal["EQUITY", "ETF", "CRYPTO", "FOREX", "DERIVATIVE"]
    venue: Annotated[str, Field(pattern=r"^[A-Z0-9]{4}$", max_length=4)]
    baseUnit: Annotated[str, Field(min_length=1, max_length=16)]
    quoteUnit: Annotated[str, Field(min_length=1, max_length=16)]
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$", max_length=3)]


class Coverage(DTO):
    instrumentId: UUIDValue
    provider: Literal["ALPACA"]
    feed: Literal["iex"]
    interval: Literal["15Min", "1Day"]
    adjustment: Literal["raw"]
    start: Timestamp
    end: Timestamp
    qualityRuleVersion: Literal["research-quality-v1"]
    calendarVersion: Fingerprint | None
    status: Literal[
        "VALID", "MISSING_EXPECTED", "NO_OBSERVATION", "UNKNOWN_CALENDAR", "CONFLICT", "INVALID"
    ]
    expected: Count | None
    observed: Count
    missing: Count | None
    unchecked: Count

    @model_validator(mode="after")
    def range_counts(self):
        if _time(self.start) >= _time(self.end):
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchBarPage(DTO):
    items: Annotated[list[ResearchBar], Field(max_length=200)]
    nextCursor: Cursor | None
    snapshot: Snapshot
    coverage: Annotated[list[Coverage], Field(max_length=200)]
    incomplete: bool
    dataVersion: Version

    @model_validator(mode="after")
    def versions(self):
        if self.dataVersion > self.snapshot.snapshotVersion or any(
            item.commitVersion > self.snapshot.snapshotVersion
            or _time(item.eventTime) > _time(self.snapshot.valuationAt)
            or _time(item.availableAt) > _time(self.snapshot.knowledgeAt)
            or _time(item.ingestedAt) > _time(self.snapshot.knowledgeAt)
            for item in self.items
        ):
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchEvidenceResult(DTO):
    evidence: ResearchEvidence
    snapshot: Snapshot
    incomplete: bool
    dataVersion: Version

    @model_validator(mode="after")
    def versions(self):
        if (
            self.dataVersion > self.snapshot.snapshotVersion
            or self.evidence.commitVersion > self.snapshot.snapshotVersion
            or _time(self.evidence.eventTime) > _time(self.snapshot.valuationAt)
            or _time(self.evidence.availableAt) > _time(self.snapshot.knowledgeAt)
            or _time(self.evidence.ingestedAt) > _time(self.snapshot.knowledgeAt)
        ):
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchSource(DTO):
    sourceId: Annotated[str, Field(min_length=1, max_length=64)]
    kind: Literal["BAR", "NEWS", "FUNDAMENTAL", "MACRO_EVENT", "REGIME"]
    state: Literal["IMPLEMENTED", "DISABLED", "BLOCKED", "NOT_IMPLEMENTED"]
    licenseStatus: Literal["UNKNOWN", "CONFIRMED", "BLOCKED"]
    policyVersion: Annotated[str, Field(min_length=1, max_length=64)] | None
    providerVerification: Literal["UNVERIFIED", "VERIFIED", "BLOCKED"]
    instruments: Annotated[list[ResearchInstrument], Field(max_length=2)]
    intervals: Annotated[list[Literal["15Min", "1Day"]], Field(max_length=2)]
    feed: Literal["iex"] | None
    adjustment: Literal["raw"] | None
    historyStart: Timestamp | None
    historyEnd: Timestamp | None
    limitations: Annotated[
        list[
            Literal[
                "FIRST_OBSERVED_ONLY",
                "IEX_NOT_FULL_MARKET",
                "NO_CALENDAR_PROOF",
                "NO_HISTORICAL_PUBLICATION",
                "NO_ADAPTER",
                "RIGHTS_UNVERIFIED",
            ]
        ],
        Field(max_length=10),
    ]

    @model_validator(mode="after")
    def history_range(self):
        if (self.historyStart is None) != (self.historyEnd is None):
            raise ValueError("INVALID_REQUEST")
        if self.historyStart is not None and self.historyEnd is not None:
            if _time(self.historyStart) >= _time(self.historyEnd):
                raise ValueError("INVALID_REQUEST")
        return self


class ResearchSourceCatalog(DTO):
    items: Annotated[list[ResearchSource], Field(max_length=5)]
    observedAt: Timestamp
    incomplete: bool


class ResearchMetric(DTO):
    status: Literal["COMPUTABLE", "NOT_COMPUTABLE"]
    value: ResearchDecimal | None
    algorithmVersion: Literal["decimal-population-v1"]
    evidenceIds: Annotated[list[UUIDValue], Field(max_length=200)]
    reason: (
        Literal[
            "INSUFFICIENT_BARS", "INCOMPLETE_WINDOW", "INCOMPATIBLE_INTERVALS", "INVALID_QUALITY"
        ]
        | None
    )

    @model_validator(mode="after")
    def status_matches_value(self):
        if self.status == "COMPUTABLE":
            if self.value is None or self.reason is not None or len(self.evidenceIds) < 3:
                raise ValueError("INVALID_REQUEST")
        elif self.value is not None or self.reason is None:
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchContext(DTO):
    instrumentId: UUIDValue
    start: Timestamp
    end: Timestamp
    interval: Literal["15Min", "1Day"]
    snapshot: Snapshot
    dataVersion: Version
    quality: Literal[
        "VALID", "MISSING_EXPECTED", "NO_OBSERVATION", "UNKNOWN_CALENDAR", "CONFLICT", "INVALID"
    ]
    coverage: Annotated[list[Coverage], Field(max_length=200)]
    evidenceIds: Annotated[list[UUIDValue], Field(max_length=200)]
    priceChange: ResearchMetric
    volatility: ResearchMetric
    missingSources: Annotated[
        list[Literal["BARS", "NEWS", "FUNDAMENTALS", "MACRO", "REGIME"]], Field(max_length=5)
    ]
    incomplete: bool

    @model_validator(mode="after")
    def consistent_context(self):
        if _time(self.start) >= _time(self.end) or self.dataVersion > self.snapshot.snapshotVersion:
            raise ValueError("INVALID_REQUEST")
        ids = set(self.evidenceIds)
        if any(
            not set(metric.evidenceIds) <= ids for metric in (self.priceChange, self.volatility)
        ):
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchJobRequest(DTO):
    instrumentId: UUIDValue
    start: Timestamp
    end: Timestamp
    interval: Literal["15Min", "1Day"]
    feed: Literal["iex"]
    adjustment: Literal["raw"]
    mode: Literal["BACKFILL", "UPDATE"]

    @model_validator(mode="after")
    def valid_request_range(self):
        start = _time(self.start)
        end = _time(self.end)
        if not start < end or (end - start).total_seconds() > 366 * 86400:
            raise ValueError("INVALID_REQUEST")
        return self


class ResearchJobResumeRequest(DTO):
    action: Literal["RESUME"]
    jobId: UUIDValue


class ResearchJobAction(RootModel[ResearchJobRequest | ResearchJobResumeRequest]):
    pass


ResearchErrorCode = Literal[
    "INVALID_REQUEST",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CURSOR_INVALID",
    "SNAPSHOT_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "UNAVAILABLE",
    "NOT_IMPLEMENTED",
    "BLOCKED_CREDENTIALS",
    "BLOCKED_LICENSE",
    "BLOCKED_ENTITLEMENT",
    "PROVIDER_TIMEOUT",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_RESPONSE_INVALID",
    "PAGINATION_CYCLE",
    "BUDGET_EXHAUSTED",
    "LEASE_LOST",
]


class ResearchJob(DTO):
    jobId: UUIDValue
    request: ResearchJobRequest
    status: Literal[
        "ACCEPTED", "RUNNING", "RETRY_WAIT", "PARTIAL", "BLOCKED", "SUCCEEDED", "FAILED"
    ]
    acceptedAt: Timestamp
    updatedAt: Timestamp
    retryAt: Timestamp | None
    pages: Count
    bars: Count
    attempts: AttemptCount
    errorCode: ResearchErrorCode | None
    traceId: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", max_length=32)]


class ResearchJobPage(DTO):
    items: Annotated[list[ResearchJob], Field(max_length=200)]
    nextCursor: Cursor | None
    snapshotAt: Timestamp
    snapshotVersion: Version


RESPONSE_MODELS = {
    "read_sources": ResearchSourceCatalog,
    "read_bars": ResearchBarPage,
    "read_evidence": ResearchEvidenceResult,
    "read_context": ResearchContext,
    "read_job": ResearchJob,
    "read_jobs": ResearchJobPage,
    "create_job": ResearchJob,
    "resume_job": ResearchJob,
}
