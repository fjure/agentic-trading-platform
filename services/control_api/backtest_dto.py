"""Strict public backtest DTOs, independently enforcing canonical wire shapes.

No provider objects, persistence rows or arbitrary diagnostic payloads cross this boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, regex_engine="python-re")

    @model_validator(mode="after")
    def semantic_shape(self):
        timestamps = {
            "trainingStart",
            "trainingEnd",
            "testStart",
            "testEnd",
            "observedAt",
            "start",
            "end",
            "committedAt",
            "valuationAt",
            "knowledgeAt",
            "ingestedAt",
            "availableAt",
            "warmupStart",
            "warmupEnd",
            "acceptedAt",
            "updatedAt",
            "completedAt",
            "snapshotAt",
            "at",
            "signalAt",
            "executedAt",
            "observationCutoff",
            "barStart",
        }
        for key in type(self).model_fields:
            value = getattr(self, key)
            if key in timestamps and value is not None:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
        name = type(self).__name__
        if name == "BacktestSmaParameters" and self.fast >= self.slow:
            raise ValueError("invalid SMA ordering")
        if name == "BacktestFold" and not (
            datetime.fromisoformat(self.trainingStart.replace("Z", "+00:00"))
            < datetime.fromisoformat(self.trainingEnd.replace("Z", "+00:00"))
            <= datetime.fromisoformat(self.testStart.replace("Z", "+00:00"))
            < datetime.fromisoformat(self.testEnd.replace("Z", "+00:00"))
        ):
            raise ValueError("invalid fold ordering")
        if name == "BacktestRequest":
            if self.strategyId == "sma-cross-v1":
                if not isinstance(self.parameters, BacktestSmaParameters):
                    raise ValueError("SMA parameters required")
            elif not isinstance(self.parameters, BacktestEmptyParameters):
                raise ValueError("strategy takes no parameters")
        if name == "BacktestRun":
            terminal = self.status in {"SUCCEEDED", "BLOCKED", "FAILED"}
            if terminal != (self.completedAt is not None):
                raise ValueError("invalid completion state")
            if self.status == "SUCCEEDED":
                if (
                    not self.publicationComplete
                    or not self.resultHash
                    or not self.manifestHash
                    or self.blockers
                ):
                    raise ValueError("incomplete success")
            elif self.publicationComplete or self.resultHash is not None:
                raise ValueError("unpublished result")
            if self.status in {"BLOCKED", "FAILED"} and not self.blockers:
                raise ValueError("missing failure cause")
        if name in {"BacktestSummaryPage", "BacktestEquityPage", "BacktestFillsPage"}:
            if self.status == "SUCCEEDED":
                if not self.publicationComplete or not self.resultHash or not self.manifestHash:
                    raise ValueError("incomplete publication")
            elif self.publicationComplete or self.items or self.nextCursor or self.resultHash:
                raise ValueError("unpublished result")
        return self


BacktestId = Annotated[
    str, Field(pattern="^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]

BacktestTimestamp = Annotated[
    str, Field(pattern="^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d{1,6})?Z$")
]

BacktestHash = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]

BacktestDecimal = Annotated[str, Field(pattern="^-?(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$")]

BacktestNonnegativeDecimal = Annotated[
    str, Field(pattern="^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$")
]

BacktestStartCash = Annotated[
    str,
    Field(
        pattern="^(?:(?:[1-9][0-9]{0,8})(?:\\.[0-9]{1,8})?|0\\.(?!0{1,8}$)[0-9]{1,8}|1000000000(?:\\.0{1,8})?)$"
    ),
]

BacktestFeeUsd = Annotated[
    str, Field(pattern="^(?:(?:0|[1-9][0-9]{0,2})(?:\\.[0-9]{1,8})?|1000(?:\\.0{1,8})?)$")
]

BacktestSlippageBps = Annotated[
    str, Field(pattern="^(?:(?:0|[1-9][0-9]?)(?:\\.[0-9]{1,8})?|100(?:\\.0{1,8})?)$")
]

BacktestStrategyId = Literal["flat-v1", "buy-hold-v1", "sma-cross-v1"]


class BacktestEmptyParameters(DTO):
    pass


class BacktestSmaParameters(DTO):
    fast: Annotated[int, Field(ge=2, le=99)]
    slow: Annotated[int, Field(ge=3, le=100)]


BacktestParameters = BacktestEmptyParameters | BacktestSmaParameters


class BacktestFold(DTO):
    trainingStart: BacktestTimestamp
    trainingEnd: BacktestTimestamp
    testStart: BacktestTimestamp
    testEnd: BacktestTimestamp


class BacktestRequest(DTO):
    instrumentId: BacktestId
    strategyId: BacktestStrategyId
    version: Literal["1"]
    parameters: BacktestParameters
    startCash: BacktestStartCash
    feeUsd: BacktestFeeUsd
    slippageBps: BacktestSlippageBps
    quantityStep: Literal["0.000001"]
    folds: Annotated[list[BacktestFold], Field(max_length=12, min_length=1)]


class BacktestInstrument(DTO):
    instrumentId: BacktestId
    symbol: Literal["SPY", "QQQ"]


class BacktestParameterBounds(DTO):
    fastMinimum: Literal[2]
    slowMaximum: Literal[100]
    ordering: Literal["fast < slow"]


class BacktestStrategy(DTO):
    strategyId: BacktestStrategyId
    version: Literal["1"]
    parameterBounds: BacktestParameterBounds | None
    defaultParameters: BacktestParameters


class BacktestAssumptions(DTO):
    simulation: Literal["LOCAL_SIMULATION"]
    provider: Literal["ALPACA"]
    feed: Literal["iex"]
    interval: Literal["15Min"]
    adjustment: Literal["raw"]
    currency: Literal["USD"]
    positionPolicy: Literal["LONG_FLAT"]
    fillModel: Literal["next-regular-bar-open-v1"]
    signalDelaySeconds: Literal[60]
    outcomeCutoffSeconds: Literal[300]
    decimalPrecision: Literal[38]
    rounding: Literal["ROUND_HALF_EVEN"]
    moneyScale: Literal[8]
    returnScale: Literal[12]
    dividendsIncluded: Literal[False]
    forceLiquidation: Literal[False]


class BacktestBudgets(DTO):
    maximumFolds: Literal[12]
    maximumCalendarDays: Literal[366]
    maximumInputRows: Literal[100000]
    attemptSeconds: Literal[300]
    maximumAttempts: Literal[3]
    maximumActiveJobs: Literal[1]


class BacktestStrategyCatalog(DTO):
    items: Annotated[list[BacktestStrategy], Field(max_length=3, min_length=3)]
    instruments: Annotated[list[BacktestInstrument], Field(max_length=2, min_length=2)]
    assumptions: BacktestAssumptions
    budgets: BacktestBudgets
    observedAt: BacktestTimestamp


BacktestState = Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "BLOCKED", "FAILED"]

BacktestReasonCode = Literal[
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


class BacktestBlocker(DTO):
    code: BacktestReasonCode
    foldIndex: Annotated[int, Field(ge=0, le=11)] | None
    barStart: BacktestTimestamp | None
    evidenceId: BacktestId | None


class BacktestSnapshot(DTO):
    version: Annotated[int, Field(ge=0, le=9223372036854775807)]
    committedAt: BacktestTimestamp


class BacktestBarIdentity(DTO):
    instrumentId: BacktestId
    provider: Literal["ALPACA"]
    feed: Literal["iex"]
    interval: Literal["15Min"]
    start: BacktestTimestamp
    end: BacktestTimestamp
    adjustment: Literal["raw"]


class BacktestRevision(DTO):
    bar: BacktestBarIdentity
    revisionId: BacktestId
    localRevision: Annotated[int, Field(ge=1, le=2147483647)]
    fingerprint: BacktestHash
    ingestedAt: BacktestTimestamp
    availableAt: BacktestTimestamp


class BacktestDecisionInput(DTO):
    valuationAt: BacktestTimestamp
    knowledgeAt: BacktestTimestamp
    inputHash: BacktestHash
    revisions: Annotated[list[BacktestRevision], Field(max_length=100000, min_length=0)]


class BacktestOutcomeInput(DTO):
    revision: BacktestRevision
    observationCutoff: BacktestTimestamp


class BacktestEvidenceRevision(DTO):
    evidenceId: BacktestId
    fingerprint: BacktestHash
    revision: Annotated[int, Field(ge=1, le=2147483647)]
    ingestedAt: BacktestTimestamp


class BacktestBoundFold(DTO):
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    window: BacktestFold
    warmupStart: BacktestTimestamp
    warmupEnd: BacktestTimestamp
    warmupRuleVersion: Literal["regular-bars-slow-minus-one-v1"]
    expectedWarmupBars: Annotated[list[BacktestBarIdentity], Field(max_length=99, min_length=0)]


class BacktestManifest(DTO):
    manifestHash: BacktestHash
    inputHash: BacktestHash
    request: BacktestRequest
    symbol: Literal["SPY", "QQQ"]
    strategyVersion: Literal["1"]
    simulatorVersion: Literal["backtest-simulator-v1"]
    metricsVersion: Literal["backtest-metrics-v1"]
    codeVersion: Annotated[str, Field(pattern="^[0-9a-f]{40}$")]
    snapshot: BacktestSnapshot
    assumptions: BacktestAssumptions
    folds: Annotated[list[BacktestBoundFold], Field(max_length=12, min_length=1)]
    decisionInputs: Annotated[list[BacktestDecisionInput], Field(max_length=100000, min_length=0)]
    outcomeInputs: Annotated[list[BacktestOutcomeInput], Field(max_length=100000, min_length=0)]
    calendarRevisions: Annotated[
        list[BacktestEvidenceRevision], Field(max_length=100000, min_length=0)
    ]
    qualityRevisions: Annotated[
        list[BacktestEvidenceRevision], Field(max_length=100000, min_length=0)
    ]
    corporateActionCoverage: Annotated[
        list[BacktestEvidenceRevision], Field(max_length=100000, min_length=0)
    ]


class BacktestFoldReference(DTO):
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    resultHash: BacktestHash


class BacktestRun(DTO):
    runId: BacktestId
    status: BacktestState
    request: BacktestRequest
    acceptedAt: BacktestTimestamp
    updatedAt: BacktestTimestamp
    completedAt: BacktestTimestamp | None
    snapshot: BacktestSnapshot
    manifestHash: BacktestHash | None
    resultHash: BacktestHash | None
    completedFolds: Annotated[list[BacktestFoldReference], Field(max_length=12, min_length=0)]
    blockers: Annotated[list[BacktestBlocker], Field(max_length=100, min_length=0)]
    attempt: Annotated[int, Field(ge=0, le=3)]
    publicationComplete: bool


class BacktestRunDetail(DTO):
    run: BacktestRun
    manifest: BacktestManifest | None


BacktestCursor = Annotated[str, Field(min_length=32, max_length=2048, pattern="^[A-Za-z0-9_-]+$")]


class BacktestRunPage(DTO):
    items: Annotated[list[BacktestRun], Field(max_length=200, min_length=0)]
    nextCursor: BacktestCursor | None
    snapshotAt: BacktestTimestamp


class BacktestMetrics(DTO):
    startCash: BacktestStartCash
    endEquity: BacktestNonnegativeDecimal
    netReturn: BacktestDecimal
    maxDrawdown: BacktestNonnegativeDecimal
    turnover: BacktestNonnegativeDecimal
    feeTotalUsd: BacktestNonnegativeDecimal
    slippageTotalUsd: BacktestNonnegativeDecimal
    endCash: BacktestNonnegativeDecimal
    endQuantity: BacktestNonnegativeDecimal
    valuation: Literal["MARK_TO_MARKET"]
    decisionCount: Annotated[int, Field(ge=0, le=100000)]
    fillCount: Annotated[int, Field(ge=0, le=100000)]


class BacktestCoverage(DTO):
    expectedBars: Annotated[int, Field(ge=0, le=100000)]
    observedBars: Annotated[int, Field(ge=0, le=100000)]
    warmupExpectedBars: Annotated[int, Field(ge=0, le=99)]
    warmupObservedBars: Annotated[int, Field(ge=0, le=99)]
    complete: Literal[True]
    corporateActions: Literal["CERTIFIED_ABSENT"]


class BacktestReferenceMetrics(DTO):
    strategyId: Literal["flat-v1", "buy-hold-v1"]
    metrics: BacktestMetrics


class BacktestFoldSummary(DTO):
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    resultHash: BacktestHash
    window: BacktestFold
    metrics: BacktestMetrics
    references: Annotated[list[BacktestReferenceMetrics], Field(max_length=2, min_length=2)]
    coverage: BacktestCoverage


class BacktestEquityPoint(DTO):
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    strategyId: BacktestStrategyId
    at: BacktestTimestamp
    cash: BacktestNonnegativeDecimal
    quantity: BacktestNonnegativeDecimal
    close: BacktestNonnegativeDecimal
    equity: BacktestNonnegativeDecimal
    revisionId: BacktestId


class BacktestFill(DTO):
    foldIndex: Annotated[int, Field(ge=0, le=11)]
    strategyId: BacktestStrategyId
    sequence: Annotated[int, Field(ge=1, le=100000)]
    signalAt: BacktestTimestamp
    executedAt: BacktestTimestamp
    side: Literal["BUY", "SELL"]
    quantity: BacktestNonnegativeDecimal
    rawOpen: BacktestNonnegativeDecimal
    price: BacktestNonnegativeDecimal
    notionalUsd: BacktestNonnegativeDecimal
    feeUsd: BacktestFeeUsd
    slippageUsd: BacktestNonnegativeDecimal
    cashAfter: BacktestNonnegativeDecimal
    quantityAfter: BacktestNonnegativeDecimal
    revisionId: BacktestId
    observationCutoff: BacktestTimestamp


class BacktestSummaryPage(DTO):
    runId: BacktestId
    status: BacktestState
    resultType: Literal["summary"]
    publicationComplete: bool
    completedFolds: Annotated[list[BacktestFoldReference], Field(max_length=12, min_length=0)]
    items: Annotated[list[BacktestFoldSummary], Field(max_length=200, min_length=0)]
    nextCursor: BacktestCursor | None
    manifestHash: BacktestHash | None
    resultHash: BacktestHash | None


class BacktestEquityPage(DTO):
    runId: BacktestId
    status: BacktestState
    resultType: Literal["equity"]
    publicationComplete: bool
    completedFolds: Annotated[list[BacktestFoldReference], Field(max_length=12, min_length=0)]
    items: Annotated[list[BacktestEquityPoint], Field(max_length=200, min_length=0)]
    nextCursor: BacktestCursor | None
    manifestHash: BacktestHash | None
    resultHash: BacktestHash | None


class BacktestFillsPage(DTO):
    runId: BacktestId
    status: BacktestState
    resultType: Literal["fills"]
    publicationComplete: bool
    completedFolds: Annotated[list[BacktestFoldReference], Field(max_length=12, min_length=0)]
    items: Annotated[list[BacktestFill], Field(max_length=200, min_length=0)]
    nextCursor: BacktestCursor | None
    manifestHash: BacktestHash | None
    resultHash: BacktestHash | None


class BacktestResults(RootModel[BacktestSummaryPage | BacktestEquityPage | BacktestFillsPage]):
    pass


BacktestProblemCode = Literal[
    "INVALID_REQUEST",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CURSOR_INVALID",
    "SNAPSHOT_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "UNAVAILABLE",
]


class BacktestProblem(DTO):
    type: Literal["urn:agentic-trading-lab:backtest-problem"]
    title: Literal["Backtest request failed"]
    status: Literal[400, 401, 403, 404, 409, 503]
    code: BacktestProblemCode
    traceId: Annotated[str, Field(pattern="^[0-9a-f]{32}$")]


RESPONSE_MODELS = {
    "read_strategies": BacktestStrategyCatalog,
    "create_run": BacktestRun,
    "read_runs": BacktestRunPage,
    "read_run": BacktestRunDetail,
    "read_results": BacktestResults,
}

# Keep the legacy classes above for Strict readiness consumers. Backtest HTTP
# responses validate against the additive canonical dual-mode contract.
from services.control_api.retrospective_dto import COMPONENT_MODELS  # noqa: E402

RESPONSE_MODELS = {
    "read_strategies": COMPONENT_MODELS["BacktestStrategyCatalog"],
    "create_run": COMPONENT_MODELS["BacktestRun"],
    "read_runs": COMPONENT_MODELS["BacktestRunPage"],
    "read_run": COMPONENT_MODELS["BacktestRunDetail"],
    "read_results": COMPONENT_MODELS["BacktestResults"],
    "create_assessment": COMPONENT_MODELS["BacktestAssessment"],
    "read_assessment": COMPONENT_MODELS["BacktestAssessment"],
    "read_reliability_report": COMPONENT_MODELS["BacktestReliabilityReportPage"],
}
