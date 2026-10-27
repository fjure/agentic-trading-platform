"""Geschlossene Diagnose-DTOS; keine SQLzeilen oder freien Providerinhalte."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from services.control_api.backtest_dto import DTO, BacktestBarIdentity, BacktestRequest

Id = Annotated[str, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")]
Utc = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}\Z")]
Code = Annotated[str, Field(max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*\Z")]
Opaque = Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*\Z")]
Number = Annotated[int, Field(ge=0, le=9007199254740991)]
Count = Annotated[int, Field(ge=0, le=100000)]
Exact = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?\Z")]
Eligibility = Literal["ELIGIBLE", "BLOCKED", "UNKNOWN", "NOT_EVALUATED"]
TrustStatus = Literal["VERIFIED", "REJECTED", "UNKNOWN", "NOT_EVALUATED"]
Category = Literal[
    "CALENDAR_UNKNOWN",
    "CALENDAR_CHANGED",
    "AMBIGUOUS_DATA",
    "BAR_MISSING",
    "OBSERVED_TOO_LATE",
    "WARMUP_MISSING",
    "QUALITY_REJECTED",
    "CORPORATE_ACTIONS_UNKNOWN",
    "CORPORATE_ACTIONS_CONFLICT",
    "TIME_UNPROVEN",
    "EVIDENCE_REVOKED",
    "NOT_EVALUATED",
]
Cursor = Annotated[
    str, Field(min_length=16, max_length=2048, pattern=r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
]
Link = Annotated[
    str,
    Field(
        pattern=r"^/api/v1/research/data-readiness/assessments/"
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z"
    ),
]


class ReadinessDTO(DTO):
    @model_validator(mode="after")
    def valid_dates(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is not None and name.endswith("At"):
                datetime.fromisoformat(value)
        return self


class ReadinessSafeReference(ReadinessDTO):
    kind: Literal[
        "BAR_REVISION",
        "CALENDAR_REVISION",
        "QUALITY_REVISION",
        "CORPORATE_ACTIONS_REVISION",
        "SOURCE",
        "KEY",
        "TRUST_DECISION",
        "TEMPORAL_PROOF",
        "OBSERVATION_RECEIPT",
    ]
    id: Opaque
    revision: Code | None
    fingerprint: Hash | None


Refs = Annotated[list[ReadinessSafeReference], Field(max_length=200)]


class ReadinessTrustResult(ReadinessDTO):
    status: TrustStatus
    method: Code
    ruleVersion: Code
    reason: Code
    references: Refs


class ReadinessTrustDimensions(ReadinessDTO):
    integrity: ReadinessTrustResult
    sourceAuthenticity: ReadinessTrustResult
    temporalAuthenticity: ReadinessTrustResult
    completeness: ReadinessTrustResult
    domainQuality: ReadinessTrustResult


class ReadinessSnapshot(ReadinessDTO):
    snapshotAt: Utc
    snapshotVersion: Number
    trustVersion: Number
    trustPolicyVersion: Code


class ReadinessInterval(ReadinessDTO):
    start: Utc
    end: Utc

    @model_validator(mode="after")
    def ordered(self):
        if datetime.fromisoformat(self.start) >= datetime.fromisoformat(self.end):
            raise ValueError("nonempty half-open interval required")
        return self


class ReadinessTimeliness(ReadinessDTO):
    marketEventAt: Utc
    barCloseAt: Utc
    providerPublishedAt: Utc | None
    firstObservedAt: Utc | None
    ingestedAt: Utc | None
    commitConfirmedAt: Utc | None
    commitVersion: Number | None
    publicationEvidence: ReadinessSafeReference | None
    clockStatus: Literal["COMPARABLE", "UNKNOWN", "INVALID"]

    @model_validator(mode="after")
    def publication_binding(self):
        if self.providerPublishedAt is not None and self.publicationEvidence is None:
            raise ValueError("publication proof reference required")
        return self


class ReadinessObservation(ReadinessDTO):
    planInstrumentId: Id
    generation: Number
    purpose: Literal["INITIAL_BAR", "REOBSERVE_BAR", "RECOVERY"]
    instrumentId: Id
    provider: Literal["alpaca"]
    feed: Literal["iex"]
    interval: Literal["15Min"]
    adjustment: Literal["raw"]
    bar: BacktestBarIdentity
    contentFingerprint: Hash
    revision: ReadinessSafeReference | None
    predecessor: ReadinessSafeReference | None
    receipt: ReadinessSafeReference
    times: ReadinessTimeliness


class ReadinessLatencyMetric(ReadinessDTO):
    status: Literal["MEASURED", "UNKNOWN", "N/A"]
    p50Seconds: Exact | None
    p95Seconds: Exact | None
    maximumSeconds: Exact | None
    sampleCount: Count
    unknownPairCount: Count
    invalidPairCount: Count

    @model_validator(mode="after")
    def measured_shape(self):
        quantiles = (self.p50Seconds, self.p95Seconds, self.maximumSeconds)
        if self.sampleCount == 0 and (
            self.status == "MEASURED" or any(v is not None for v in quantiles)
        ):
            raise ValueError("unknown quantiles required")
        if self.status == "MEASURED" and any(v is None for v in quantiles):
            raise ValueError("complete measured quantiles required")
        return self


class ReadinessRatio(ReadinessDTO):
    status: Literal["MEASURED", "UNKNOWN", "N/A"]
    numerator: Count
    denominator: Count
    denominatorBasis: Literal[
        "EXPECTED_BAR_IDENTITIES", "OBSERVED_BAR_IDENTITIES", "USABLE_BAR_OCCURRENCES"
    ]
    unknownCount: Count

    @model_validator(mode="after")
    def ratio_shape(self):
        if self.numerator > self.denominator:
            raise ValueError("numerator exceeds denominator")
        if self.denominator == 0 and (self.status != "N/A" or self.numerator != 0):
            raise ValueError("zero denominator must be N/A")
        return self


class ReadinessExpectedBarRatio(ReadinessRatio):
    denominatorBasis: Literal["EXPECTED_BAR_IDENTITIES"]


class ReadinessObservedBarRatio(ReadinessRatio):
    denominatorBasis: Literal["OBSERVED_BAR_IDENTITIES"]


class ReadinessOccurrenceRatio(ReadinessRatio):
    denominatorBasis: Literal["USABLE_BAR_OCCURRENCES"]


class ReadinessTimelinessMetrics(ReadinessDTO):
    sourceId: Code
    instrumentId: Id
    feed: Literal["iex"]
    interval: Literal["15Min"]
    adjustment: Literal["raw"]
    calendarVersion: Code | None
    period: ReadinessInterval
    snapshot: ReadinessSnapshot
    expectedBars: Count
    observedBars: Count
    unknownCalendarDays: Annotated[int, Field(ge=0, le=366)]
    receptionMinusClose: ReadinessLatencyMetric
    ingestionMinusReception: ReadinessLatencyMetric
    confirmationMinusIngestion: ReadinessLatencyMetric
    receivedBySignalDeadline: ReadinessExpectedBarRatio
    receivedByOutcomeDeadline: ReadinessExpectedBarRatio
    usableBySignalDeadline: ReadinessExpectedBarRatio
    usableByOutcomeDeadline: ReadinessExpectedBarRatio
    missingBars: ReadinessExpectedBarRatio
    lateForSignal: ReadinessExpectedBarRatio
    lateForOutcome: ReadinessExpectedBarRatio
    correctedBars: ReadinessObservedBarRatio
    duplicateResponses: ReadinessOccurrenceRatio
    limitations: Annotated[list[Code], Field(max_length=200)]


class ReadinessWindowBoundary(ReadinessDTO):
    reason: Code
    references: Refs


class ReadinessWindow(ReadinessDTO):
    kind: Literal["BAR_OBSERVATION", "STRICT_PIT", "COMPLETE_EXPERIMENT"]
    scope: Literal["SIGNAL", "OUTCOME", "SIGNAL_AND_OUTCOME"]
    deadlineSeconds: Literal[60, 300]
    firstBar: BacktestBarIdentity
    lastBar: BacktestBarIdentity
    period: ReadinessInterval
    expectedBars: Annotated[int, Field(ge=1, le=100000)]
    foldIndex: Annotated[int, Field(ge=0, le=11)] | None
    snapshot: ReadinessSnapshot
    policyVersion: Code
    leftBoundary: ReadinessWindowBoundary
    rightBoundary: ReadinessWindowBoundary
    references: Refs

    @model_validator(mode="after")
    def deadline_scope(self):
        if self.scope == "SIGNAL" and self.deadlineSeconds != 60:
            raise ValueError("signal deadline is +60s")
        if self.scope == "OUTCOME" and self.deadlineSeconds != 300:
            raise ValueError("outcome deadline is +300s")
        if self.kind == "COMPLETE_EXPERIMENT" and self.scope != "SIGNAL_AND_OUTCOME":
            raise ValueError("both scopes required")
        return self


Windows = Annotated[list[ReadinessWindow], Field(max_length=200)]


class ReadinessWindows(ReadinessDTO):
    barSignal: Windows
    barOutcome: Windows
    strictSignal: Windows
    strictOutcome: Windows
    completeExperiment: Windows
    complete: bool

    @model_validator(mode="after")
    def window_kinds(self):
        for name, kind, scope in (
            ("barSignal", "BAR_OBSERVATION", "SIGNAL"),
            ("barOutcome", "BAR_OBSERVATION", "OUTCOME"),
            ("strictSignal", "STRICT_PIT", "SIGNAL"),
            ("strictOutcome", "STRICT_PIT", "OUTCOME"),
            ("completeExperiment", "COMPLETE_EXPERIMENT", "SIGNAL_AND_OUTCOME"),
        ):
            if any(w.kind != kind or w.scope != scope for w in getattr(self, name)):
                raise ValueError("window group mismatch")
        return self


class ReadinessCategorySummary(ReadinessDTO):
    category: Category
    findingCount: Annotated[int, Field(ge=0, le=10000)]
    disjointIntervalCount: Annotated[int, Field(ge=0, le=10000)]


class ReadinessCategoryOverlap(ReadinessDTO):
    first: Category
    second: Category
    overlapIntervalCount: Annotated[int, Field(ge=0, le=10000)]


class ReadinessProviderRights(ReadinessDTO):
    licenseStatus: Literal["UNKNOWN", "CONFIRMED", "BLOCKED"]
    providerVerification: Literal["UNVERIFIED"]
    attestationRef: ReadinessSafeReference | None
    reason: Code


class ReadinessFinding(ReadinessDTO):
    findingId: Id
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    area: Literal[
        "CALENDAR", "WARMUP", "SIGNAL", "OUTCOME", "QUALITY", "CORPORATE_ACTIONS", "TRUST"
    ]
    category: Category
    eligibility: Eligibility
    backtestReason: (
        Literal[
            "BLOCKED_DATA_UNAVAILABLE",
            "BLOCKED_FIRST_OBSERVED",
            "BLOCKED_CALENDAR_UNKNOWN",
            "BLOCKED_QUALITY_UNKNOWN",
            "BLOCKED_CORPORATE_ACTIONS_UNKNOWN",
            "BLOCKED_CORPORATE_ACTION",
            "BLOCKED_WARMUP_MISSING",
            "BLOCKED_NEXT_BAR_MISSING",
            "BLOCKED_OUTCOME_UNAVAILABLE",
            "BLOCKED_SELL_FEES",
            "BUDGET_EXHAUSTED",
            "INTERNAL_FAILURE",
        ]
        | None
    )
    period: ReadinessInterval
    knowledgeAt: Utc
    references: Refs
    trust: ReadinessTrustDimensions
    observation: ReadinessObservation | None
    remedy: Code

    @model_validator(mode="after")
    def dependency_shape(self):
        if self.category in ("CALENDAR_UNKNOWN", "NOT_EVALUATED") and self.observation is not None:
            raise ValueError("unknown expected bar must not be invented")
        if self.category == "NOT_EVALUATED" and self.eligibility != "NOT_EVALUATED":
            raise ValueError("unchecked finding must remain unchecked")
        return self


class ReadinessLimits(ReadinessDTO):
    maxQueued: Literal[32]
    maxFolds: Literal[12]
    maxDays: Literal[366]
    maxRevisionRows: Literal[100000]
    maxCalendarRows: Literal[100000]
    maxQualityRows: Literal[100000]
    maxCoverageRows: Literal[100000]
    maxAttemptSeconds: Literal[300]
    maxAttempts: Literal[3]
    maxFindings: Literal[10000]
    maxPublicationBytes: Literal[12582912]
    maxPageSize: Literal[200]


class ReadinessReport(ReadinessDTO):
    assessmentId: Id
    requestHash: Hash
    reportHash: Hash
    policyVersion: Code
    codeVersion: Code
    snapshot: ReadinessSnapshot
    createdAt: Utc
    eligibility: Eligibility
    evaluationComplete: bool
    trust: ReadinessTrustDimensions
    findingCount: Annotated[int, Field(ge=0, le=10000)]
    categories: Annotated[list[ReadinessCategorySummary], Field(max_length=12)]
    categoryOverlaps: Annotated[list[ReadinessCategoryOverlap], Field(max_length=66)]
    metrics: Annotated[list[ReadinessTimelinessMetrics], Field(max_length=200)]
    windows: ReadinessWindows
    providerRights: ReadinessProviderRights
    limits: ReadinessLimits

    @model_validator(mode="after")
    def complete_positive(self):
        if self.eligibility == "ELIGIBLE" and (
            not self.evaluationComplete
            or not self.windows.complete
            or any(
                value.status != "VERIFIED"
                for value in (
                    self.trust.integrity,
                    self.trust.sourceAuthenticity,
                    self.trust.temporalAuthenticity,
                    self.trust.completeness,
                    self.trust.domainQuality,
                )
            )
        ):
            raise ValueError("positive report requires complete positive required checks")
        return self


class ReadinessRevocationWarning(ReadinessDTO):
    decisionRef: ReadinessSafeReference
    affectedReferences: Refs
    reason: Literal[
        "KEY_REVOKED",
        "KEY_COMPROMISED",
        "EVIDENCE_RETRACTED",
        "SOURCE_REVOKED",
        "TRUST_DECISION_REVISED",
    ]
    effectiveAt: Utc | None
    recordedAt: Utc
    compromiseTimeKnown: bool


class ReadinessRevocationProjection(ReadinessDTO):
    checkedAt: Utc
    currentTrustVersion: Number
    currentTrustPolicyVersion: Code
    warnings: Annotated[list[ReadinessRevocationWarning], Field(max_length=200)]
    complete: bool


class ReadinessAccepted(ReadinessDTO):
    assessmentId: Id
    status: Literal["ACCEPTED"]
    eligibility: Literal["NOT_EVALUATED"]
    detailLink: Link


class ReadinessAssessment(ReadinessDTO):
    assessmentId: Id
    status: Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED"]
    eligibility: Eligibility
    request: BacktestRequest
    requestHash: Hash
    acceptedAt: Utc
    updatedAt: Utc
    completedAt: Utc | None
    attempt: Annotated[int, Field(ge=0, le=3)]
    failureCode: Literal["BUDGET_EXHAUSTED", "ATTEMPTS_EXHAUSTED", "INTERNAL_FAILURE"] | None
    publicationComplete: bool
    report: ReadinessReport | None
    currentRevocation: ReadinessRevocationProjection
    detailLink: Link

    @model_validator(mode="after")
    def execution_shape(self):
        if self.status in ("ACCEPTED", "RUNNING") and (
            self.eligibility != "NOT_EVALUATED"
            or self.report is not None
            or self.publicationComplete
            or self.completedAt is not None
            or self.failureCode is not None
        ):
            raise ValueError("unpublished execution state required")
        if self.status == "SUCCEEDED" and (
            self.report is None
            or not self.publicationComplete
            or self.completedAt is None
            or self.failureCode is not None
            or self.report.eligibility != self.eligibility
            or self.report.requestHash != self.requestHash
            or self.report.assessmentId != self.assessmentId
        ):
            raise ValueError("complete atomic publication required")
        if self.status == "FAILED" and (
            self.report is not None
            or self.publicationComplete
            or self.eligibility != "UNKNOWN"
            or self.completedAt is None
            or self.failureCode is None
        ):
            raise ValueError("incomplete failed state required")
        return self


class ReadinessFindingPage(ReadinessDTO):
    assessmentId: Id
    reportHash: Hash
    snapshot: ReadinessSnapshot
    category: Category | None
    items: Annotated[list[ReadinessFinding], Field(max_length=200)]
    nextCursor: Cursor | None
    totalMatching: Annotated[int, Field(ge=0, le=10000)]


class ReadinessProblem(ReadinessDTO):
    type: Literal["urn:agentic-trading-lab:readiness-problem"]
    title: Literal["Research data request failed"]
    status: Literal[400, 401, 403, 404, 409, 413, 429, 503]
    code: Literal[
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "CURSOR_INVALID",
        "REPORT_NOT_READY",
        "IDEMPOTENCY_CONFLICT",
        "VERSION_CONFLICT",
        "QUEUE_EXHAUSTED",
        "PAYLOAD_TOO_LARGE",
        "UNAVAILABLE",
    ]
    traceId: Annotated[str, Field(pattern=r"^[0-9a-f]{32}\Z")]

    @model_validator(mode="after")
    def status_code_pair(self):
        allowed = {
            400: {"INVALID_REQUEST", "CURSOR_INVALID"},
            401: {"UNAUTHORIZED"},
            403: {"FORBIDDEN"},
            404: {"NOT_FOUND"},
            409: {"REPORT_NOT_READY", "IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT"},
            413: {"PAYLOAD_TOO_LARGE"},
            429: {"QUEUE_EXHAUSTED"},
            503: {"UNAVAILABLE"},
        }
        if self.code not in allowed[self.status]:
            raise ValueError("status/code mismatch")
        return self


RESPONSE_MODELS = {
    "create_assessment": ReadinessAccepted,
    "read_assessment": ReadinessAssessment,
    "read_findings": ReadinessFindingPage,
}
