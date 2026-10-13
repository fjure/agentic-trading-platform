"""Versioned domain values. No adapter payloads or side effects belong here."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?\Z")
_TRACE = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def decimal_value(value: Decimal | str) -> Decimal:
    """Accept an exact, non-exponent NUMERIC(38,18) value without rounding."""
    if not isinstance(value, (Decimal, str)):
        raise ValueError("decimal must be Decimal or plain decimal string")
    if isinstance(value, Decimal) and (not value.is_finite() or value.as_tuple().exponent > 0):
        raise ValueError("decimal is nonfinite or exponential")
    raw = format(value, "f") if isinstance(value, Decimal) else value
    if not _DECIMAL.fullmatch(raw):
        raise ValueError("decimal is nonfinite, exponential, or outside NUMERIC(38,18)")
    result = Decimal(raw)
    if result.as_tuple().exponent < -18:
        raise ValueError("decimal exceeds NUMERIC(38,18)")
    return result


def decimal_string(value: Decimal | str) -> str:
    """Render a validated Decimal without exponent notation."""
    return format(decimal_value(value), "f")


def utc_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be UTC aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp must use UTC, not a local offset")
    return value.astimezone(UTC)


def utc_string(value: datetime) -> str:
    return utc_time(value).isoformat().replace("+00:00", "Z")


def _id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identity must be nonempty")


def _trace(value: str) -> None:
    if not isinstance(value, str) or not _TRACE.fullmatch(value):
        raise ValueError("trace ID must be 32 lowercase hex characters")


def _positive_version(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("version must be a positive integer")


def _nonnegative(value: Decimal | str) -> Decimal:
    result = decimal_value(value)
    if result < 0:
        raise ValueError("value must be nonnegative")
    return result


def _positive(value: Decimal | str) -> Decimal:
    result = decimal_value(value)
    if result <= 0:
        raise ValueError("value must be positive")
    return result


def _fraction(value: Decimal | str) -> Decimal:
    result = decimal_value(value)
    if not 0 <= result <= 1:
        raise ValueError("fraction outside [0, 1]")
    return result


def _decimal_map(value: Mapping[str, Decimal]) -> Mapping[str, Decimal]:
    if not isinstance(value, Mapping):
        raise ValueError("decimal map required")
    return MappingProxyType({key: decimal_value(item) for key, item in value.items()})


class Market(StrEnum):
    US_EQUITY = "US_EQUITY"
    CRYPTO = "CRYPTO"


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    ETF = "ETF"
    CRYPTO = "CRYPTO"


class Quality(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    GAP = "GAP"
    CONFLICT = "CONFLICT"
    INVALID = "INVALID"


class Mode(StrEnum):
    DISABLED = "DISABLED"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


class SafetyState(StrEnum):
    RUNNING = "RUNNING"
    PAUSE_NEW = "PAUSE_NEW"
    HALTED = "HALTED"


class Target(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"


class Choice(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"
    ABSTAIN = "ABSTAIN"


class Capability(StrEnum):
    CONFIRMED = "CONFIRMED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_RESERVED = "RISK_RESERVED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"


class ReservationState(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    CONSUMED = "CONSUMED"
    HELD_UNKNOWN = "HELD_UNKNOWN"


class Reconciliation(StrEnum):
    CURRENT = "CURRENT"
    PENDING = "PENDING"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    source_id: str
    instrument_id: str
    intervals: tuple[str, ...]
    freshness_seconds: int | None
    news: Capability = Capability.UNKNOWN
    papers: Capability = Capability.UNKNOWN
    history: Capability = Capability.UNKNOWN

    def __post_init__(self) -> None:
        _id(self.source_id)
        _id(self.instrument_id)
        object.__setattr__(self, "intervals", tuple(self.intervals))
        if self.freshness_seconds is not None and (
            type(self.freshness_seconds) is not int or self.freshness_seconds < 0
        ):
            raise ValueError("freshness must be nonnegative seconds")
        for name in ("news", "papers", "history"):
            if not isinstance(getattr(self, name), Capability):
                raise ValueError("capability must be explicitly classified")

    def supports_interval(self, interval: str) -> bool:
        return interval in self.intervals


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    broker_id: str
    instrument_id: str
    version: int
    paper: Capability = Capability.UNKNOWN
    regular_session: Capability = Capability.UNKNOWN
    continuous_session: Capability = Capability.UNKNOWN
    fractional: Capability = Capability.UNKNOWN
    market_order: Capability = Capability.UNKNOWN
    limit_order: Capability = Capability.UNKNOWN
    quantity_step: Decimal | None = None
    price_tick: Decimal | None = None

    def __post_init__(self) -> None:
        _id(self.broker_id)
        _id(self.instrument_id)
        _positive_version(self.version)
        for name in (
            "paper",
            "regular_session",
            "continuous_session",
            "fractional",
            "market_order",
            "limit_order",
        ):
            if not isinstance(getattr(self, name), Capability):
                raise ValueError("capability must be explicitly classified")
        for name in ("quantity_step", "price_tick"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, _positive(value))

    def permits(self, *, order_type: str, fractional: bool, regular_session: bool) -> bool:
        if type(fractional) is not bool or type(regular_session) is not bool:
            return False
        order_capability = self.market_order if order_type == "MARKET" else self.limit_order
        if order_type not in ("MARKET", "LIMIT"):
            return False
        return (
            all(
                capability is Capability.CONFIRMED
                for capability in (
                    self.paper,
                    order_capability,
                    self.fractional if fractional else Capability.CONFIRMED,
                    self.regular_session if regular_session else self.continuous_session,
                )
            )
            and self.quantity_step is not None
            and (order_type != "LIMIT" or self.price_tick is not None)
        )


@dataclass(frozen=True, slots=True)
class DecisionCapabilities:
    engine_id: str
    supported_inputs: tuple[str, ...]
    result_fields: tuple[str, ...]
    available: Capability = Capability.UNKNOWN

    def __post_init__(self) -> None:
        _id(self.engine_id)
        object.__setattr__(self, "supported_inputs", tuple(self.supported_inputs))
        object.__setattr__(self, "result_fields", tuple(self.result_fields))
        if not isinstance(self.available, Capability):
            raise ValueError("capability must be explicitly classified")

    def supports(self, inputs: frozenset[str], outputs: frozenset[str]) -> bool:
        return (
            self.available is Capability.CONFIRMED
            and inputs <= frozenset(self.supported_inputs)
            and outputs <= frozenset(self.result_fields)
        )


@dataclass(frozen=True, slots=True)
class Instrument:
    id: str
    market: Market
    broker_symbol: str
    asset_class: str
    currency: str
    version: int
    session_name: str
    quantity_step: Decimal | None = None
    price_tick: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (
            self.id,
            self.broker_symbol,
            self.asset_class,
            self.currency,
            self.session_name,
        ):
            _id(value)
        if not isinstance(self.market, Market):
            raise ValueError("unsupported market")
        try:
            asset_class = AssetClass(self.asset_class)
        except ValueError as exc:
            raise ValueError("unsupported asset class") from exc
        if (self.market is Market.CRYPTO) != (asset_class is AssetClass.CRYPTO):
            raise ValueError("asset class and market disagree")
        object.__setattr__(self, "asset_class", asset_class)
        _positive_version(self.version)
        for name in ("quantity_step", "price_tick"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, _positive(value))


@dataclass(frozen=True, slots=True)
class InstrumentUniverse:
    """Configured instruments accepted at the domain ingress boundary."""

    instruments: tuple[Instrument, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instruments", tuple(self.instruments))
        ids = [instrument.id for instrument in self.instruments]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("instrument universe must be nonempty and unique")

    def require_bar(self, bar: MarketBar) -> Instrument:
        for instrument in self.instruments:
            if instrument.id == bar.instrument_id:
                return instrument
        raise ValueError("unknown instrument")


@dataclass(frozen=True, slots=True)
class MarketBar:
    id: str
    instrument_id: str
    start_at: datetime
    end_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    event_time: datetime
    received_at: datetime
    available_at: datetime
    quality: Quality
    interval: str = "PT15M"
    closed: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.instrument_id)
        if self.interval != "PT15M" or self.closed is not True:
            raise ValueError("only closed 15-minute bars are admissible")
        for name in ("start_at", "end_at", "event_time", "received_at", "available_at"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))
        if not self.start_at < self.end_at or (self.end_at - self.start_at).total_seconds() != 900:
            raise ValueError("bar must cover exactly 15 minutes")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _positive(getattr(self, name)))
        object.__setattr__(self, "volume", _nonnegative(self.volume))
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC range inconsistent")
        if not isinstance(self.quality, Quality):
            raise ValueError("invalid quality")
        _positive_version(self.version)


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    id: str
    instrument_id: str
    sources: tuple[str, ...]
    available_at: datetime
    conflicts: tuple[str, ...]
    quality: Quality
    neutral: bool = True

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.instrument_id)
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))
        object.__setattr__(self, "available_at", utc_time(self.available_at))
        if self.neutral is not True or not isinstance(self.quality, Quality):
            raise ValueError("evidence must be neutral and quality-classified")


@dataclass(frozen=True, slots=True)
class Position:
    id: str
    instrument_id: str
    currency: str
    quantity: Decimal
    average_price: Decimal | None
    realized_pnl: Decimal
    ledger_version: int

    def __post_init__(self) -> None:
        for value in (self.id, self.instrument_id, self.currency):
            _id(value)
        object.__setattr__(self, "quantity", _nonnegative(self.quantity))
        if self.average_price is not None:
            object.__setattr__(self, "average_price", _positive(self.average_price))
        object.__setattr__(self, "realized_pnl", decimal_value(self.realized_pnl))
        _positive_version(self.ledger_version)


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    id: str
    currency: str
    equity: Decimal
    cash: Decimal
    exposure: Decimal
    as_of: datetime
    ledger_version: int
    quality: Quality
    reconciliation: Reconciliation

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.currency)
        for name in ("equity", "cash", "exposure"):
            object.__setattr__(self, name, _nonnegative(getattr(self, name)))
        object.__setattr__(self, "as_of", utc_time(self.as_of))
        _positive_version(self.ledger_version)
        if not isinstance(self.quality, Quality) or not isinstance(
            self.reconciliation, Reconciliation
        ):
            raise ValueError("invalid portfolio state")


@dataclass(frozen=True, slots=True)
class MarketState:
    id: str
    instrument_id: str
    market: Market
    bar: MarketBar
    evidence: EvidenceBundle
    portfolio: PortfolioSnapshot
    position: Position
    features: Mapping[str, Decimal]
    spread: Decimal
    event_time: datetime
    received_at: datetime
    available_at: datetime
    as_of: datetime
    quality: Quality
    trace_id: str

    def __post_init__(self) -> None:
        _id(self.id)
        _trace(self.trace_id)
        if not isinstance(self.market, Market) or not isinstance(self.quality, Quality):
            raise ValueError("invalid market state")
        if any(
            part.instrument_id != self.instrument_id
            for part in (self.bar, self.evidence, self.position)
        ):
            raise ValueError("instrument identities differ")
        object.__setattr__(self, "features", _decimal_map(self.features))
        object.__setattr__(self, "spread", _nonnegative(self.spread))
        for name in ("event_time", "received_at", "available_at", "as_of"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))
        if self.bar.end_at > self.as_of or self.bar.available_at > self.as_of:
            raise ValueError("bar not yet available")
        if self.evidence.available_at > self.as_of or self.available_at > self.as_of:
            raise ValueError("evidence not yet available")


_REGIME_CHOICES = frozenset({"TREND", "RANGE", "UNCERTAIN"})
_DIRECTION_CHOICES = frozenset(choice.value for choice in Choice)


def _distribution(values: Mapping[str, Decimal], choices: frozenset[str]) -> Mapping[str, Decimal]:
    if not isinstance(values, Mapping) or set(values) != choices:
        raise ValueError("distribution must contain exactly the declared choices")
    frozen = MappingProxyType({key: _fraction(value) for key, value in values.items()})
    if sum(frozen.values(), Decimal("0")) != 1:
        raise ValueError("distribution must sum exactly to one")
    return frozen


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    question_version: str
    choice: str
    confidence: Decimal
    distribution: Mapping[str, Decimal]
    kind: str = "CHOICE"

    def __post_init__(self) -> None:
        _id(self.question_version)
        if self.kind != "CHOICE":
            raise ValueError("choice answer requires CHOICE kind")
        choices = (
            frozenset(self.distribution) if isinstance(self.distribution, Mapping) else frozenset()
        )
        if choices not in (_REGIME_CHOICES, _DIRECTION_CHOICES) or self.choice not in choices:
            raise ValueError("unsupported choice answer")
        object.__setattr__(self, "confidence", _fraction(self.confidence))
        object.__setattr__(self, "distribution", _distribution(self.distribution, choices))


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    question_version: str
    score: Decimal
    legend: Mapping[str, str]
    distribution: Mapping[str, Decimal]
    confidence: Decimal
    kind: str = "SCORE"

    def __post_init__(self) -> None:
        _id(self.question_version)
        if self.kind != "SCORE":
            raise ValueError("score answer requires SCORE kind")
        score = decimal_value(self.score)
        if not 0 <= score <= 9:
            raise ValueError("score outside [0, 9]")
        object.__setattr__(self, "score", score)
        if not isinstance(self.legend, Mapping) or len(self.legend) < 2:
            raise ValueError("score legend needs at least two entries")
        if any(
            not isinstance(key, str)
            or not re.fullmatch(r"[0-9]", key)
            or not isinstance(label, str)
            or not label.strip()
            for key, label in self.legend.items()
        ):
            raise ValueError("invalid score legend")
        object.__setattr__(self, "legend", MappingProxyType(dict(self.legend)))
        object.__setattr__(
            self, "distribution", _distribution(self.distribution, frozenset(self.legend))
        )
        object.__setattr__(self, "confidence", _fraction(self.confidence))


@dataclass(frozen=True, slots=True)
class NoulAnswer:
    question_version: str
    noul: Decimal
    kind: str = "NOUL"

    def __post_init__(self) -> None:
        _id(self.question_version)
        if self.kind != "NOUL":
            raise ValueError("noul answer requires NOUL kind")
        object.__setattr__(self, "noul", _fraction(self.noul))


@dataclass(frozen=True, slots=True)
class TypedAnswers:
    regime: ChoiceAnswer
    evidence_quality: ScoreAnswer
    signal_conflict: NoulAnswer
    shadow_direction: ChoiceAnswer | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.regime, ChoiceAnswer)
            or set(self.regime.distribution) != _REGIME_CHOICES
        ):
            raise ValueError("regime must be a three-choice answer")
        if not isinstance(self.evidence_quality, ScoreAnswer) or not isinstance(
            self.signal_conflict, NoulAnswer
        ):
            raise ValueError("score and noul answers required")
        if self.shadow_direction is not None and (
            not isinstance(self.shadow_direction, ChoiceAnswer)
            or set(self.shadow_direction.distribution) != _DIRECTION_CHOICES
        ):
            raise ValueError("shadow direction must be LONG/FLAT/ABSTAIN")


@dataclass(frozen=True, slots=True)
class DecisionResult:
    id: str
    market_state_id: str
    choice: Choice
    model_id: str | None
    prompt_version: str
    question_version: str
    config_version: str
    input_hash: str
    latency_ms: int
    valid_until: datetime
    trace_id: str
    reason_code: str
    confidence: Decimal | None = None
    distribution: Mapping[str, Decimal] | None = None
    typed_answers: TypedAnswers | None = None
    api_units: Decimal | None = None
    estimated_cost: Decimal | None = None
    regime: str | None = None
    evidence_quality: Decimal | None = None
    signal_conflict: Decimal | None = None
    shadow_choice: Choice | None = None

    def __post_init__(self) -> None:
        for value in (
            self.id,
            self.market_state_id,
            self.prompt_version,
            self.question_version,
            self.config_version,
            self.reason_code,
        ):
            _id(value)
        _trace(self.trace_id)
        if not _HASH.fullmatch(self.input_hash):
            raise ValueError("input hash must be SHA-256 hex")
        if (
            not isinstance(self.choice, Choice)
            or type(self.latency_ms) is not int
            or self.latency_ms < 0
        ):
            raise ValueError("invalid decision metadata")
        object.__setattr__(self, "valid_until", utc_time(self.valid_until))
        if self.typed_answers is None:
            if self.choice is not Choice.ABSTAIN or any(
                value is not None
                for value in (
                    self.model_id,
                    self.confidence,
                    self.distribution,
                    self.regime,
                    self.evidence_quality,
                    self.signal_conflict,
                    self.shadow_choice,
                )
            ):
                raise ValueError("missing answers require ABSTAIN with null model/confidence")
            if self.reason_code not in (
                "ABSTAIN_MISSING_EVIDENCE",
                "ABSTAIN_INVALID_RESPONSE",
                "ABSTAIN_UNAVAILABLE",
            ):
                raise ValueError("invalid abstention reason")
        else:
            _id(self.model_id)
            if self.confidence is None or self.distribution is None:
                raise ValueError("typed decision needs confidence and distribution")
            if not isinstance(self.typed_answers, TypedAnswers):
                raise ValueError("typed answers must be closed typed answers")
            if self.regime != self.typed_answers.regime.choice:
                raise ValueError("top regime differs from typed regime")
            if self.evidence_quality is None or self.signal_conflict is None:
                raise ValueError("typed result needs quality and conflict values")
            object.__setattr__(self, "evidence_quality", _fraction(self.evidence_quality))
            object.__setattr__(self, "signal_conflict", _fraction(self.signal_conflict))
            if self.signal_conflict != self.typed_answers.signal_conflict.noul:
                raise ValueError("top conflict differs from typed answer")
            if self.typed_answers.shadow_direction is None:
                if self.shadow_choice is not None:
                    raise ValueError("shadow choice has no typed answer")
            elif self.shadow_choice != self.typed_answers.shadow_direction.choice:
                raise ValueError("shadow choice differs from typed answer")
            object.__setattr__(self, "confidence", _fraction(self.confidence))
            object.__setattr__(
                self, "distribution", _distribution(self.distribution, _DIRECTION_CHOICES)
            )
        for name in ("api_units", "estimated_cost"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, _nonnegative(value))


@dataclass(frozen=True, slots=True)
class TradeProposal:
    id: str
    decision_id: str
    market_state_id: str
    instrument_id: str
    target: Target
    quantity: Decimal
    created_at: datetime
    valid_until: datetime
    trace_id: str
    rationale_codes: tuple[str, ...] = ()
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.decision_id, self.market_state_id, self.instrument_id):
            _id(value)
        _trace(self.trace_id)
        if not isinstance(self.target, Target):
            raise ValueError("target must be LONG or FLAT")
        object.__setattr__(self, "quantity", _positive(self.quantity))
        if self.limit_price is not None:
            object.__setattr__(self, "limit_price", _positive(self.limit_price))
        object.__setattr__(self, "created_at", utc_time(self.created_at))
        object.__setattr__(self, "valid_until", utc_time(self.valid_until))
        if self.valid_until <= self.created_at:
            raise ValueError("proposal expires before creation")
        object.__setattr__(self, "rationale_codes", tuple(self.rationale_codes))


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    id: str
    version: int
    currency: str
    max_positions_per_instrument: int
    max_positions_total: int
    max_position_fraction: Decimal
    max_daily_loss_fraction: Decimal
    effective_at: datetime
    actor_id: str
    audit_event_id: str
    simulation: bool = True

    def __post_init__(self) -> None:
        for value in (self.id, self.currency, self.actor_id, self.audit_event_id):
            _id(value)
        _positive_version(self.version)
        for name in ("max_positions_per_instrument", "max_positions_total"):
            _positive_version(getattr(self, name))
        for name in ("max_position_fraction", "max_daily_loss_fraction"):
            object.__setattr__(self, name, _fraction(getattr(self, name)))
        object.__setattr__(self, "effective_at", utc_time(self.effective_at))
        if self.simulation is not True:
            raise ValueError("only simulation policy is supported")


@dataclass(frozen=True, slots=True)
class ExposureSnapshot:
    currency: str
    equity: Decimal
    gross_notional: Decimal
    reserved_notional: Decimal

    def __post_init__(self) -> None:
        _id(self.currency)
        for name in ("equity", "gross_notional", "reserved_notional"):
            object.__setattr__(self, name, _nonnegative(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class RiskDecision:
    id: str
    proposal_id: str
    approved: bool
    reason_code: str
    reason_codes: tuple[str, ...]
    policy_version: int
    exposure_snapshot: ExposureSnapshot
    decided_at: datetime
    valid_until: datetime
    trace_id: str
    reservation_id: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.proposal_id)
        _id(self.reason_code)
        _trace(self.trace_id)
        if type(self.approved) is not bool:
            raise ValueError("risk approval must be boolean")
        _positive_version(self.policy_version)
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        if not self.reason_codes or self.reason_code not in self.reason_codes:
            raise ValueError("primary reason must occur in reason codes")
        object.__setattr__(self, "decided_at", utc_time(self.decided_at))
        object.__setattr__(self, "valid_until", utc_time(self.valid_until))
        if self.valid_until <= self.decided_at:
            raise ValueError("risk decision expires before creation")
        if self.reservation_id is not None:
            _id(self.reservation_id)


@dataclass(frozen=True, slots=True)
class RiskReservation:
    id: str
    proposal_id: str
    risk_decision_id: str
    currency: str
    reserved_notional: Decimal
    state: ReservationState
    version: int
    created_at: datetime
    trace_id: str
    released_at: datetime | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.proposal_id, self.risk_decision_id, self.currency):
            _id(value)
        _trace(self.trace_id)
        object.__setattr__(self, "reserved_notional", _nonnegative(self.reserved_notional))
        _positive_version(self.version)
        if not isinstance(self.state, ReservationState):
            raise ValueError("invalid reservation state")
        object.__setattr__(self, "created_at", utc_time(self.created_at))
        if self.released_at is not None:
            object.__setattr__(self, "released_at", utc_time(self.released_at))


@dataclass(frozen=True, slots=True)
class OrderIntent:
    id: str
    proposal_id: str
    risk_decision_id: str
    client_order_id: str
    instrument_id: str
    side: str
    order_type: str
    quantity: Decimal
    capability_version: int
    created_at: datetime
    trace_id: str
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        for value in (
            self.id,
            self.proposal_id,
            self.risk_decision_id,
            self.client_order_id,
            self.instrument_id,
        ):
            _id(value)
        _trace(self.trace_id)
        if self.side not in ("BUY", "SELL") or self.order_type not in ("MARKET", "LIMIT"):
            raise ValueError("unsupported order side/type")
        object.__setattr__(self, "quantity", _positive(self.quantity))
        _positive_version(self.capability_version)
        object.__setattr__(self, "created_at", utc_time(self.created_at))
        if self.order_type == "LIMIT":
            if self.limit_price is None:
                raise ValueError("limit order needs price")
            object.__setattr__(self, "limit_price", _positive(self.limit_price))
        elif self.limit_price is not None:
            raise ValueError("market order cannot have limit price")


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    id: str
    intent_id: str
    client_order_id: str
    broker_order_id: str | None
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    version: int
    updated_at: datetime
    trace_id: str

    def __post_init__(self) -> None:
        for value in (self.id, self.intent_id, self.client_order_id):
            _id(value)
        _trace(self.trace_id)
        if self.broker_order_id is not None:
            _id(self.broker_order_id)
        if not isinstance(self.status, OrderStatus):
            raise ValueError("invalid order status")
        object.__setattr__(self, "requested_quantity", _positive(self.requested_quantity))
        object.__setattr__(self, "filled_quantity", _nonnegative(self.filled_quantity))
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("fill exceeds requested quantity")
        _positive_version(self.version)
        object.__setattr__(self, "updated_at", utc_time(self.updated_at))


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    id: str
    order_id: str
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    reconciliation: Reconciliation
    occurred_at: datetime
    received_at: datetime
    trace_id: str
    broker_event_id: str | None = None
    average_fill_price: Decimal | None = None
    fee: Decimal | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.order_id)
        _trace(self.trace_id)
        if not isinstance(self.status, OrderStatus) or not isinstance(
            self.reconciliation, Reconciliation
        ):
            raise ValueError("invalid execution state")
        object.__setattr__(self, "requested_quantity", _positive(self.requested_quantity))
        object.__setattr__(self, "filled_quantity", _nonnegative(self.filled_quantity))
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("fill exceeds request")
        for name in ("occurred_at", "received_at"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))
        if self.average_fill_price is not None:
            object.__setattr__(self, "average_fill_price", _positive(self.average_fill_price))
        if self.fee is not None:
            object.__setattr__(self, "fee", _nonnegative(self.fee))
        for value in (self.broker_event_id, self.reason_code):
            if value is not None:
                _id(value)


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    input_hash: str
    config_version: str
    checkpoint_id: str | None
    status: str
    heartbeat_at: datetime
    output_id: str | None
    trace_id: str

    def __post_init__(self) -> None:
        _id(self.id)
        _id(self.config_version)
        _id(self.status)
        _trace(self.trace_id)
        if not _HASH.fullmatch(self.input_hash):
            raise ValueError("invalid input hash")
        object.__setattr__(self, "heartbeat_at", utc_time(self.heartbeat_at))


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    occurred_at: datetime
    actor_id: str
    action: str
    reason_codes: tuple[str, ...]
    entity_id: str
    entity_version: int
    trace_id: str
    policy_version: int | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.actor_id, self.action, self.entity_id):
            _id(value)
        _trace(self.trace_id)
        object.__setattr__(self, "occurred_at", utc_time(self.occurred_at))
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        _positive_version(self.entity_version)
        if self.policy_version is not None:
            _positive_version(self.policy_version)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    event_type: str
    occurred_at: datetime
    aggregate_id: str
    aggregate_version: int
    trace_id: str
    payload: object = field(repr=False)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _id(self.event_id)
        _id(self.aggregate_id)
        _trace(self.trace_id)
        _positive_version(self.aggregate_version)
        if self.schema_version != 1:
            raise ValueError("unsupported event schema version")
        expected = {
            "market.state.v1": MarketState,
            "decision.completed.v1": DecisionResult,
            "trade.proposed.v1": TradeProposal,
            "risk.decided.v1": RiskDecision,
            "risk.reserved.v1": RiskReservation,
            "execution.reported.v1": ExecutionReport,
        }
        if self.event_type not in expected or not isinstance(
            self.payload, expected[self.event_type]
        ):
            raise ValueError("event type/payload mismatch")
        object.__setattr__(self, "occurred_at", utc_time(self.occurred_at))


def domain_fields(model: object) -> tuple[str, ...]:
    """Small introspection hook for persistence mappers; never serializes secrets."""
    return tuple(item.name for item in fields(model))
