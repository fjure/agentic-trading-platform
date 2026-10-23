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
