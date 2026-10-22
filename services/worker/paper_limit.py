"""Pure Paper LIMIT sizing from immutable public proofs; no broker or SQL ports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from enum import Enum
from math import lcm
from zoneinfo import ZoneInfo

from packages.domain.models import (
    BrokerCapabilities,
    Capability,
    Choice,
    Market,
    decimal_value,
    utc_time,
)

_EASTERN = ZoneInfo("America/New_York")


def _version(value):
    if type(value) is not int or not 1 <= value <= 9007199254740991:
        raise ValueError("PAPER_VERSION_INVALID")


def _canonical(value):
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Decimal):
        raw = format(value, "f")
        return raw.rstrip("0").rstrip(".") if "." in raw else raw
    if isinstance(value, datetime):
        return utc_time(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass(frozen=True, slots=True)
class PaperLimitPolicy:
    version: int = 1
    proposal_ttl_seconds: int = 30
    quote_age_seconds: int = 30
    capability_age_seconds: int = 120
    max_price_distance_fraction: Decimal = Decimal("0.001")
    quantity_decimals: int = 9
    cash_buffer_fraction: Decimal = Decimal("0.01")
    max_position_fraction: Decimal = Decimal("0.10")
    order_type: str = "LIMIT"
    time_in_force: str = "DAY"
    session: str = "REGULAR"
    currency: str = "USD"

    def __post_init__(self):
        _version(self.version)
        for name, maximum in (("proposal_ttl_seconds", 30), ("quote_age_seconds", 120),
                              ("capability_age_seconds", 120)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("PAPER_LIMIT_POLICY_INVALID")
        if type(self.quantity_decimals) is not int or not 0 <= self.quantity_decimals <= 9:
            raise ValueError("PAPER_LIMIT_POLICY_INVALID")
        for name in ("max_price_distance_fraction", "cash_buffer_fraction",
                     "max_position_fraction"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if (not 0 < self.max_price_distance_fraction <= Decimal("0.01")
                or not Decimal("0.01") <= self.cash_buffer_fraction <= Decimal("1")
                or not 0 < self.max_position_fraction <= Decimal("0.10")
                or (self.order_type, self.time_in_force, self.session, self.currency)
                != ("LIMIT", "DAY", "REGULAR", "USD")):
            raise ValueError("PAPER_LIMIT_POLICY_INVALID")


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperPlanningInputs:
    policy: PaperLimitPolicy
    market_state_id: str
    market_evidence_id: str
    trace_id: str
    quote_source_id: str
    quote_evidence_id: str
    quote_evidence_hash: str
    context_version: int
    quote_context_version: int
    portfolio_version: int
    account_generation: int
    credential_generation: int
    policy_version: int
    profile_version: int
    limit_policy_version: int
    budget_version: int
    capability_version: int
    capability: BrokerCapabilities
    fractionable: Capability
    fractional_limit_day: Capability
    bid: Decimal
    ask: Decimal
    quote_provider_time: datetime
    quote_observed_at: datetime
    capability_provider_time: datetime
    capability_observed_at: datetime
    capability_valid_until: datetime
    approval_valid_until: datetime
    session_start: datetime
    session_end: datetime
    session_open: bool
    risk_budget: Decimal
    remaining_cash: Decimal
    equity: Decimal
    held_exposure: Decimal
    reserved_exposure: Decimal
    instrument_exposure: Decimal
    instrument_reserved_exposure: Decimal
    held_quantity: Decimal
    open_exit_quantity: Decimal

    def __post_init__(self):
        if (type(self.policy) is not PaperLimitPolicy
                or type(self.capability) is not BrokerCapabilities):
            raise ValueError("PAPER_PROOF_INVALID")
        for name in ("context_version", "quote_context_version", "portfolio_version",
                     "account_generation", "credential_generation", "policy_version",
                     "profile_version", "limit_policy_version", "budget_version",
                     "capability_version"):
            _version(getattr(self, name))
        for name in ("market_state_id", "market_evidence_id", "quote_source_id",
                     "quote_evidence_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,255}", value) is None:
                raise ValueError("PAPER_PROOF_INVALID")
        if (not isinstance(self.trace_id, str)
                or re.fullmatch(r"[0-9a-f]{32}", self.trace_id) is None
                or not isinstance(self.quote_evidence_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", self.quote_evidence_hash) is None
                or type(self.session_open) is not bool
                or not isinstance(self.fractionable, Capability)
                or not isinstance(self.fractional_limit_day, Capability)):
            raise ValueError("PAPER_PROOF_INVALID")
        for name in ("bid", "ask", "risk_budget", "remaining_cash", "equity", "held_exposure",
                     "reserved_exposure", "instrument_exposure", "instrument_reserved_exposure",
                     "held_quantity", "open_exit_quantity"):
            value = decimal_value(getattr(self, name))
            if value < 0:
                raise ValueError("PAPER_PROOF_INVALID")
            object.__setattr__(self, name, value)
        for name in ("quote_provider_time", "quote_observed_at", "capability_provider_time",
                     "capability_observed_at",
                     "capability_valid_until", "approval_valid_until", "session_start",
                     "session_end"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))

    def digest(self):
        return hashlib.sha256(json.dumps(
            _canonical(self), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PaperLimitOutcome:
    reason_code: str
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    valid_until: datetime | None = None
    input_hash: str | None = None


def _joint_step(provider_step, minimum_step):
    # Both grids must hold, including non-power-of-ten provider increments.
    scale = Decimal("1000000000000000000")
    return Decimal(lcm(int(provider_step * scale), int(minimum_step * scale))) / scale


def evaluate_paper_limit(*, decision, market_state, inputs, now):
    """Planning proof only; every normal risk/reservation/submit gate still applies."""
    if type(inputs) is not PaperPlanningInputs:
        return PaperLimitOutcome("PAPER_PROOF_MISSING")
    now = utc_time(now)
    p, c, s = inputs.policy, inputs.capability, market_state
    if (s.market is not Market.US_EQUITY or s.instrument_id not in {"SPY", "QQQ"}
            or inputs.market_state_id != s.id or inputs.market_evidence_id != s.evidence.id
            or inputs.trace_id != s.trace_id or decision.market_state_id != s.id
            or decision.trace_id != s.trace_id
            or inputs.quote_context_version != inputs.context_version
            or inputs.portfolio_version != s.portfolio.ledger_version
            or inputs.limit_policy_version != p.version or inputs.capability_version != c.version
            or c.instrument_id != s.instrument_id or c.broker_id != "alpaca-paper"
            or inputs.equity != s.portfolio.equity or inputs.held_exposure < s.portfolio.exposure
            or inputs.remaining_cash > s.portfolio.cash
            or inputs.held_quantity != s.position.quantity
            or inputs.instrument_exposure > inputs.held_exposure
            or inputs.instrument_reserved_exposure > inputs.reserved_exposure):
        return PaperLimitOutcome("PAPER_PROOF_MISMATCH")
    if (not timedelta() <= now - inputs.quote_provider_time < timedelta(seconds=p.quote_age_seconds)
            or not inputs.quote_provider_time <= inputs.quote_observed_at <= now
            or not timedelta() <= now - inputs.capability_provider_time
            < timedelta(seconds=p.capability_age_seconds)
            or not inputs.capability_provider_time <= inputs.capability_observed_at <= now
            or not timedelta() <= now - inputs.capability_observed_at
            < timedelta(seconds=p.capability_age_seconds)
            or not now < inputs.capability_valid_until
            or not now < inputs.approval_valid_until
            or not timedelta() <= now - s.as_of < timedelta(seconds=120)):
        return PaperLimitOutcome("PAPER_PROOF_STALE")
    start, end = inputs.session_start.astimezone(_EASTERN), inputs.session_end.astimezone(_EASTERN)
    if (not inputs.session_open or not inputs.session_start <= now < inputs.session_end
            or start.date() != end.date() or start.weekday() >= 5
            or start.time() != time(9, 30) or not time(9, 30) < end.time() <= time(16)):
        return PaperLimitOutcome("SESSION_CLOSED")
    if (inputs.bid <= 0 or inputs.ask < inputs.bid or inputs.risk_budget <= 0
            or inputs.risk_budget > Decimal("1000000") or inputs.equity <= 0
            or not c.permits(order_type="LIMIT", fractional=False, regular_session=True)):
        return PaperLimitOutcome("CAPABILITY_UNCONFIRMED")
    if decision.choice is Choice.ABSTAIN:
        return PaperLimitOutcome("PAPER_ABSTAIN")
    if decision.choice not in {Choice.LONG, Choice.FLAT}:
        return PaperLimitOutcome("PAPER_ABSTAIN")
    if decision.choice is Choice.LONG and inputs.held_quantity != 0:
        return PaperLimitOutcome("PAPER_POSITION_ALREADY_HELD")
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        fractional = (inputs.fractionable is Capability.CONFIRMED
                      and inputs.fractional_limit_day is Capability.CONFIRMED
                      and c.permits(order_type="LIMIT", fractional=True, regular_session=True))
        quantity_step = _joint_step(c.quantity_step, Decimal(1).scaleb(
            -p.quantity_decimals if fractional else 0,
        ))
        reference = inputs.ask if decision.choice is Choice.LONG else inputs.bid
        price_step = _joint_step(
            c.price_tick, Decimal("0.01") if reference >= 1 else Decimal("0.0001"),
        )
        rounding = ROUND_CEILING if decision.choice is Choice.LONG else ROUND_FLOOR
        price = (reference / price_step).to_integral_value(rounding=rounding) * price_step
        if (price <= 0 or price != price.quantize(Decimal("0.000001"))
                or abs(price - reference) > reference * p.max_price_distance_fraction):
            return PaperLimitOutcome("PAPER_LIMIT_DISTANCE")
        buffer = Decimal(1) + p.cash_buffer_fraction
        if decision.choice is Choice.LONG:
            capacity = min(inputs.remaining_cash,
                           inputs.risk_budget-inputs.held_exposure-inputs.reserved_exposure*buffer,
                           inputs.equity*p.max_position_fraction-inputs.instrument_exposure
                           - inputs.instrument_reserved_exposure*buffer)
            quantity = (capacity / (price * buffer) / quantity_step).to_integral_value(
                rounding=ROUND_FLOOR,
            ) * quantity_step
        else:
            quantity = ((inputs.held_quantity-inputs.open_exit_quantity) / quantity_step
                        ).to_integral_value(rounding=ROUND_FLOOR) * quantity_step
        if quantity <= 0:
            return PaperLimitOutcome("MINIMUM_QUANTITY")
        # Canonical output precision is at most nine quantity/six price decimals.
        quantity = quantity.quantize(Decimal(1).scaleb(-p.quantity_decimals if fractional else 0))
        price = price.quantize(Decimal("0.000001"))
        expiry = min(decision.valid_until, now+timedelta(seconds=p.proposal_ttl_seconds),
                     inputs.quote_provider_time+timedelta(seconds=p.quote_age_seconds),
                     inputs.capability_provider_time+timedelta(seconds=p.capability_age_seconds),
                     inputs.capability_observed_at+timedelta(seconds=p.capability_age_seconds),
                     inputs.capability_valid_until, inputs.approval_valid_until, inputs.session_end)
        if expiry <= now:
            return PaperLimitOutcome("PAPER_PROOF_STALE")
        return PaperLimitOutcome("PAPER_LIMIT_PLANNED", decimal_value(quantity),
                                 decimal_value(price), expiry, inputs.digest())
