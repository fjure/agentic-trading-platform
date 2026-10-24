"""Strict public Paper transport values; no broker or private configuration access."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Version = Annotated[int, Field(ge=1, le=9007199254740991)]
Symbol = Literal["SPY", "QQQ"]
Stamp = Annotated[str, Field(pattern=r"Z$", max_length=30)]
Budget = Annotated[
    str,
    Field(
        pattern=r"^(?=.*[1-9])(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,18})?|1000000(?:\.0{1,18})?)$"
    ),
]
Quantity = Annotated[
    str, Field(pattern=r"^(?=.*[1-9])(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,9})?$", max_length=30)
]
Price = Annotated[
    str, Field(pattern=r"^(?=.*[1-9])(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,6})?$", max_length=28)
]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, regex_engine="python-re")


def identity(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("INVALID_REQUEST")
    return value


class Verify(DTO):
    credentialGeneration: Version


class Prepare(Verify):
    accountGeneration: Version
    verificationOperationId: str

    _identity = field_validator("verificationOperationId")(identity)


class Terms(Verify):
    accountGeneration: Version
    policyVersion: Version
    profileVersion: Version
    limitPolicyVersion: Version
    budgetVersion: Version
    riskBudget: Budget
    currency: Literal["USD"]
    expiresAt: Stamp

    @field_validator("riskBudget")
    @classmethod
    def budget(cls, value):
        if not Decimal("0") < Decimal(value) <= Decimal("1000000"):
            raise ValueError("INVALID_REQUEST")
        return value

    @field_validator("expiresAt")
    @classmethod
    def stamp(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("INVALID_REQUEST")
        return value


class Start(Terms):
    symbols: Annotated[list[Symbol], Field(min_length=1, max_length=2)]
    strategy: Literal["mock-neutral", "neutral-baseline", "jev"]
    confirmation: Literal["START_PAPER"]

    @field_validator("symbols")
    @classmethod
    def symbols_unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("INVALID_REQUEST")
        return value


class Control(DTO):
    action: Literal["PAUSE", "STOP", "KILL"]


class Limit(Terms):
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    quantity: Quantity
    limitPrice: Price
    orderType: Literal["LIMIT"]
    timeInForce: Literal["DAY"]
    session: Literal["REGULAR"]
    confirmation: Literal["PLACE_PAPER_LIMIT"]

    @field_validator("quantity", "limitPrice")
    @classmethod
    def positive(cls, value):
        if Decimal(value) <= 0:
            raise ValueError("INVALID_REQUEST")
        return value


class Cancel(DTO):
    accountGeneration: Version
    confirmation: Literal["CANCEL_PAPER_ORDER"]


ErrorCode = Literal[
    "INVALID_REQUEST",
    "AUTHENTICATION_REQUIRED",
    "ORIGIN_REJECTED",
    "IDEMPOTENCY_CONFLICT",
    "OPERATION_NOT_FOUND",
    "ORDER_NOT_FOUND",
    "VERSION_CONFLICT",
    "CREDENTIALS_MISSING",
    "CREDENTIALS_INVALID",
    "ACCOUNT_IDENTITY_MISMATCH",
    "ACCOUNT_BLOCKED",
    "CURRENCY_UNSUPPORTED",
    "MARKET_DATA_UNAVAILABLE",
    "OPEN_ORDERS_PRESENT",
    "UNSUPPORTED_POSITION",
    "COST_BASIS_MISSING",
    "SNAPSHOT_CHANGED",
    "LEDGER_ALREADY_BOUND",
    "LEDGER_NOT_PREPARED",
    "CONNECTION_NOT_VERIFIED",
    "APPROVAL_EXPIRED",
    "ENTRY_PAUSED",
    "KILL_SWITCH_ACTIVE",
    "SESSION_CLOSED",
    "STALE_QUOTE",
    "CAPABILITY_UNCONFIRMED",
    "BUDGET_EXCEEDED",
    "INSUFFICIENT_POSITION",
    "MINIMUM_QUANTITY",
    "POLICY_MISMATCH",
    "RECONCILIATION_REQUIRED",
    "BROKER_STATUS_UNKNOWN",
    "EXTERNAL_ACTIVITY",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_REJECTED",
    "TEMPORARILY_UNAVAILABLE",
]
Trace = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Generation = Annotated[int, Field(ge=0, le=9007199254740991)]
DecimalString = Annotated[str, Field(pattern=r"^-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?$")]


class Operation(DTO):
    operationId: str
    operationType: Literal[
        "VERIFY_CONNECTION",
        "PREPARE_LEDGER",
        "START",
        "PAUSE",
        "STOP",
        "KILL",
        "PLACE_LIMIT_ORDER",
        "CANCEL_ORDER",
    ]
    status: Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "BLOCKED", "FAILED"]
    idempotencyKey: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{16,128}$")]
    acceptedGeneration: Version
    acceptedAt: Stamp
    updatedAt: Stamp
    traceId: Trace
    errorCode: ErrorCode | None = None
    orderId: str | None = None

    _identity = field_validator("operationId")(identity)
    _timestamps = field_validator("acceptedAt", "updatedAt")(Terms.stamp.__func__)

    @field_validator("orderId")
    @classmethod
    def order_identity(cls, value):
        return identity(value) if value is not None else value

    @model_validator(mode="before")
    @classmethod
    def outcome(cls, value):
        if isinstance(value, dict):
            failed = value.get("status") in {"BLOCKED", "FAILED"}
            if failed != ("errorCode" in value) or value.get("errorCode", "absent") is None:
                raise ValueError("INVALID_REQUEST")
            if "orderId" in value and value["orderId"] is None:
                raise ValueError("INVALID_REQUEST")
        return value


class Status(DTO):
    namespace: Literal["alpaca-paper"]
    environment: Literal["PAPER"]
    setupState: Literal["NOT_CONFIGURED", "SAVED", "VERIFIED", "PREPARED"]
    operatingState: Literal["DISABLED", "PAUSED", "RUNNING", "STOPPED", "HALTED"]
    entryGateOpen: bool
    credentialGeneration: Generation
    accountGeneration: Generation
    accountLabel: Annotated[str | None, Field(pattern=r"^paper-[0-9a-f]{8}$", max_length=14)]
    currency: Literal["USD"]
    cash: DecimalString | None = None
    riskBudget: Budget | None = None
    verifiedAt: Stamp | None
    reconciledAt: Stamp | None
    observedAt: Stamp
    marketDataFeed: Literal["UNVERIFIED", "IEX", "SIP"]
    providerVerification: Literal["UNVERIFIED", "CONFIRMED"]
    blockers: Annotated[list[ErrorCode], Field(max_length=37)]
    traceId: Trace

    @field_validator("verifiedAt", "reconciledAt", "observedAt")
    @classmethod
    def timestamps(cls, value):
        return Terms.stamp(value) if value is not None else value

    @model_validator(mode="before")
    @classmethod
    def gate(cls, value):
        if not isinstance(value, dict):
            return value
        if any(key in value and value[key] is None for key in ("cash", "riskBudget")):
            raise ValueError("INVALID_REQUEST")
        blockers = value.get("blockers", [])
        if isinstance(blockers, list) and len(set(blockers)) != len(blockers):
            raise ValueError("INVALID_REQUEST")
        if value.get("entryGateOpen") is True and (
            value.get("setupState") != "PREPARED"
            or value.get("operatingState") != "RUNNING"
            or value.get("providerVerification") != "CONFIRMED"
            or blockers
        ):
            raise ValueError("INVALID_REQUEST")
        return value


NonnegativeVersion = Annotated[int, Field(ge=0, le=9007199254740991)]
Strategy = Literal["mock-neutral", "neutral-baseline", "jev"]


class FeeAttestation(DTO):
    scope: Literal["alpaca-paper-direct-retail-us-equity-zero-v1"]
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("INVALID_REQUEST")
        return value


class ProfileUpdate(DTO):
    riskBudget: Budget
    currency: Literal["USD"]
    strategy: Strategy
    credentialGeneration: Version
    accountGeneration: Version
    expectedProfileVersion: NonnegativeVersion
    confirmation: Literal["SAVE_PAPER_PROFILE"]
    feeAttestation: FeeAttestation

    _budget = field_validator("riskBudget")(Terms.budget.__func__)


class Profile(DTO):
    namespace: Literal["alpaca-paper"]
    configured: bool
    currency: Literal["USD"]
    riskBudget: Budget | None
    strategy: Strategy | None
    strategyVersion: Literal["mock-neutral.v1", "neutral-baseline.v1", "jev.v1"] | None
    credentialGeneration: NonnegativeVersion
    accountGeneration: NonnegativeVersion
    policyVersion: NonnegativeVersion
    profileVersion: NonnegativeVersion
    budgetVersion: NonnegativeVersion
    limitPolicyVersion: Version
    feeAttestation: FeeAttestation | None
    traceId: Trace

    @model_validator(mode="after")
    def profile_bindings(self):
        nullable = (self.riskBudget, self.strategy, self.strategyVersion, self.feeAttestation)
        bindings = (
            self.credentialGeneration,
            self.accountGeneration,
            self.profileVersion,
            self.budgetVersion,
        )
        if self.configured:
            if any(value is None for value in nullable) or any(value < 1 for value in bindings):
                raise ValueError("INVALID_REQUEST")
            if self.policyVersion < 1 or self.strategyVersion != self.strategy + ".v1":
                raise ValueError("INVALID_REQUEST")
        elif any(value is not None for value in nullable) or any(bindings):
            raise ValueError("INVALID_REQUEST")
        return self


# Read models mirror the additive Paper contract. They reject private extras at
# both HTTP boundaries; missing evidence stays null rather than acquiring values.
ReadId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$", max_length=255)]
ReadDecimal = Annotated[str, Field(
    pattern=r"^-?(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$", max_length=40,
)]
ReadNonnegative = Annotated[str, Field(
    pattern=r"^(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$", max_length=39,
)]
ReadPositive = Annotated[str, Field(
    pattern=r"^(?=.*[1-9])(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$", max_length=39,
)]
Cursor = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=1024)]


def read_stamp(value):
    import re

    if value is not None:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
                            r"[0-9]{2}(?:\.[0-9]+)?Z", value):
            raise ValueError("INVALID_REQUEST")
        return Terms.stamp(value)
    return value


class ReadStatus(Status):
    _read_timestamps = field_validator("verifiedAt", "reconciledAt", "observedAt")(read_stamp)


class ReadOperation(Operation):
    _read_timestamps = field_validator("acceptedAt", "updatedAt")(read_stamp)


class ReadDTO(DTO):
    @field_validator("*", check_fields=False)
    @classmethod
    def timestamps(cls, value, info):
        if value is not None and info.field_name in {
            "observedAt", "validUntil", "providerTime", "startsAt", "endsAt",
            "asOf", "updatedAt", "occurredAt", "requestedAt", "nextAttemptAt",
        }:
            return read_stamp(value)
        return value


class Portfolio(ReadDTO):
    currency: Literal["USD"]
    equity: ReadNonnegative
    cash: ReadNonnegative
    exposure: ReadNonnegative
    ledgerVersion: Version
    asOf: Stamp
    quality: Literal["VALID", "STALE", "GAP", "CONFLICT", "INVALID"]
    reconciliation: Literal["CURRENT", "PENDING", "MISMATCH"]


class Position(ReadDTO):
    symbol: Symbol
    quantity: ReadNonnegative
    averagePrice: ReadPositive | None
    costBasis: ReadNonnegative | None
    realizedPnl: ReadDecimal
    ledgerVersion: Version


class TimedEvidence(ReadDTO):
    observedAt: Stamp
    validUntil: Stamp


class QuoteEvidence(TimedEvidence):
    quoteId: ReadId
    sourceId: Literal["alpaca-market-data:iex"]
    bid: ReadPositive
    ask: ReadPositive
    providerTime: Stamp
    sourceCapabilityVersion: Version


class CapabilityEvidence(TimedEvidence):
    version: Version
    limitOrderConfirmed: bool
    fractionalConfirmed: bool
    quantityStep: ReadPositive
    priceTick: ReadPositive


class SessionEvidence(TimedEvidence):
    version: Version
    open: bool
    startsAt: Stamp
    endsAt: Stamp


class MarketEvidence(ReadDTO):
    symbol: Symbol
    marketStateId: ReadId | None
    quote: QuoteEvidence | None
    capability: CapabilityEvidence | None
    session: SessionEvidence | None


class Snapshot(ReadDTO):
    namespace: Literal["alpaca-paper"]
    observedAt: Stamp
    status: ReadStatus
    profile: Profile
    verificationOperationId: str | None
    portfolio: Portfolio | None
    positions: Annotated[list[Position] | None, Field(max_length=2)]
    markets: Annotated[list[MarketEvidence], Field(max_length=2)]

    @field_validator("verificationOperationId")
    @classmethod
    def verification_identity(cls, value):
        return identity(value) if value is not None else value

    @model_validator(mode="after")
    def financial_basis(self):
        if (self.portfolio is None) != (self.positions is None):
            raise ValueError("INVALID_REQUEST")
        return self


class CancelOverlay(ReadDTO):
    state: Literal["READY", "BACKOFF", "LOOKUP_ONLY", "FINISHED"]
    requestedAt: Stamp
    nextAttemptAt: Stamp | None
    deleteAttempts: Annotated[int, Field(ge=0, le=3)]


class Order(ReadDTO):
    orderId: str
    clientOrderId: ReadId
    proposalId: ReadId
    proposalSource: Literal["MODEL_DECISION", "HUMAN_REQUEST"]
    decisionId: ReadId | None
    humanRequestId: str | None
    traceId: Trace
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    orderType: Literal["MARKET", "LIMIT"]
    timeInForce: Literal["DAY"] | None
    session: Literal["REGULAR"] | None
    quantity: ReadPositive
    limitPrice: ReadPositive | None
    status: Literal[
        "CREATED", "RISK_RESERVED", "SUBMITTING", "ACKNOWLEDGED", "PARTIALLY_FILLED",
        "FILLED", "REJECTED", "CANCEL_PENDING", "CANCELED", "UNKNOWN",
    ]
    filledQuantity: ReadNonnegative
    averageFillPrice: ReadPositive | None
    updatedAt: Stamp
    cancel: CancelOverlay | None

    _identity = field_validator("orderId")(identity)

    @field_validator("humanRequestId")
    @classmethod
    def human_identity(cls, value):
        return identity(value) if value is not None else value

    @model_validator(mode="after")
    def original_terms(self):
        human = self.proposalSource == "HUMAN_REQUEST"
        if human != (self.humanRequestId is not None) or human == (self.decisionId is not None):
            raise ValueError("INVALID_REQUEST")
        if (self.orderType == "LIMIT") != (self.limitPrice is not None):
            raise ValueError("INVALID_REQUEST")
        return self


class Fill(ReadDTO):
    fillId: ReadId
    orderId: str
    symbol: Symbol
    side: Literal["BUY", "SELL"]
    quantity: ReadPositive
    price: ReadPositive
    occurredAt: Stamp
    fee: ReadNonnegative | None
    currency: Literal["USD"]
    traceId: Trace

    _identity = field_validator("orderId")(identity)


class OrderPage(DTO):
    namespace: Literal["alpaca-paper"]
    items: Annotated[list[Order], Field(max_length=50)]
    nextCursor: Cursor | None


class FillPage(DTO):
    namespace: Literal["alpaca-paper"]
    items: Annotated[list[Fill], Field(max_length=50)]
    nextCursor: Cursor | None


class OperationPage(DTO):
    namespace: Literal["alpaca-paper"]
    items: Annotated[list[ReadOperation], Field(max_length=50)]
    nextCursor: Cursor | None


class TraceEvent(ReadDTO):
    eventId: ReadId
    occurredAt: Stamp
    role: Literal["CONTROL_API", "WORKER", "EXECUTION", "WATCHDOG", "SYSTEM"]
    action: Literal[
        "OPERATION_ACCEPTED", "OPERATION_UPDATED", "PROPOSAL_CREATED", "RISK_APPROVED",
        "RISK_REJECTED", "ORDER_UPDATED", "EXECUTION_REPORTED", "RECONCILIATION_OBSERVED",
        "CANCEL_LOOKUP", "CANCEL_DELETE", "CANCEL_OBSERVED",
    ]
    entityId: ReadId
    entityVersion: Version
    reasonCodes: Annotated[list[ErrorCode], Field(max_length=37)]

    @field_validator("reasonCodes")
    @classmethod
    def unique_reasons(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("INVALID_REQUEST")
        return value


class TraceView(DTO):
    namespace: Literal["alpaca-paper"]
    traceId: Trace
    complete: bool
    items: Annotated[list[TraceEvent], Field(max_length=32)]


READ_MODELS = {
    "read_snapshot": Snapshot, "read_orders": OrderPage, "read_fills": FillPage,
    "read_operations": OperationPage, "read_trace": TraceView,
}


def read_query(items, *, private=False, paged=False):
    """Strict query spelling, multiplicity and bounds before any internal relay."""
    import re

    values = {}
    allowed = ({"trace"} if private else set()) | ({"limit", "cursor"} if paged else set())
    for name, value in items:
        if name not in allowed or name in values:
            raise ValueError("INVALID_REQUEST")
        values[name] = value
    if private:
        if not re.fullmatch(r"[0-9a-f]{32}", values.get("trace", "")):
            raise ValueError("INVALID_REQUEST")
    if paged:
        raw = values.get("limit", "25")
        if not re.fullmatch(r"[1-9][0-9]?", raw) or int(raw) > 50:
            raise ValueError("INVALID_REQUEST")
        values["limit"] = int(raw)
        if "cursor" in values and not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", values["cursor"]):
            raise ValueError("INVALID_REQUEST")
        values.setdefault("cursor", None)
    return values


def read_arguments(limit, cursor, trace):
    if type(limit) is not int or cursor is not None and type(cursor) is not str:
        raise ValueError("INVALID_REQUEST")
    return read_query(
        [("trace", trace), ("limit", str(limit))] +
        ([("cursor", cursor)] if cursor is not None else []), private=True, paged=True,
    )
