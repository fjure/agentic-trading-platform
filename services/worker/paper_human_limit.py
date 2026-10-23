"""Pure human LIMIT admission; confirmed terms are never resized or repriced."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from packages.domain.models import Capability, Market, MarketState, utc_time
from packages.domain.paper_workflow import HumanOrderRequest, PaperOrderSide
from services.worker.paper_limit import PaperPlanningInputs, _joint_step

_EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class HumanLimitAssessment:
    reason_code: str | None = None
    valid_until: datetime | None = None
    input_hash: str | None = None


def assess_human_limit(request, state, inputs, now):
    """Validate a human request against public proofs without a model decision."""
    if (type(request) is not HumanOrderRequest or type(inputs) is not PaperPlanningInputs
            or type(state) is not MarketState):
        return HumanLimitAssessment("RECONCILIATION_REQUIRED")
    now = utc_time(now)
    p, c = inputs.policy, inputs.capability
    if any(getattr(request, key) != getattr(inputs, key) for key in (
        "account_generation", "credential_generation", "policy_version", "profile_version",
        "budget_version", "limit_policy_version", "risk_budget",
    )):
        return HumanLimitAssessment("VERSION_CONFLICT")
    if (state.market is not Market.US_EQUITY or state.instrument_id != request.symbol
            or inputs.market_state_id != state.id or inputs.market_evidence_id != state.evidence.id
            or inputs.trace_id != state.trace_id
            or inputs.quote_context_version != inputs.context_version
            or inputs.portfolio_version != state.portfolio.ledger_version
            or inputs.limit_policy_version != p.version or inputs.capability_version != c.version
            or c.instrument_id != state.instrument_id or c.broker_id != "alpaca-paper"
            or inputs.equity != state.portfolio.equity
            or inputs.held_exposure < state.portfolio.exposure
            or inputs.remaining_cash > state.portfolio.cash
            or inputs.held_quantity != state.position.quantity
            or inputs.instrument_exposure > inputs.held_exposure
            or inputs.instrument_reserved_exposure > inputs.reserved_exposure):
        return HumanLimitAssessment("POLICY_MISMATCH")
    if (not request.created_at <= now < request.valid_until
            or request.valid_until > request.created_at+timedelta(seconds=p.proposal_ttl_seconds)
            or not now < inputs.approval_valid_until):
        return HumanLimitAssessment("APPROVAL_EXPIRED")
    if (not timedelta() <= now-inputs.quote_provider_time < timedelta(seconds=p.quote_age_seconds)
            or not inputs.quote_provider_time <= inputs.quote_observed_at <= now
            or not timedelta() <= now-state.as_of < timedelta(seconds=120)):
        return HumanLimitAssessment("STALE_QUOTE")
    if (not timedelta() <= now-inputs.capability_provider_time
            < timedelta(seconds=p.capability_age_seconds)
            or not inputs.capability_provider_time <= inputs.capability_observed_at <= now
            or not timedelta() <= now-inputs.capability_observed_at
            < timedelta(seconds=p.capability_age_seconds)
            or not now < inputs.capability_valid_until):
        return HumanLimitAssessment("CAPABILITY_UNCONFIRMED")
    start, end = inputs.session_start.astimezone(_EASTERN), inputs.session_end.astimezone(_EASTERN)
    if (not inputs.session_open or not inputs.session_start <= now < inputs.session_end
            or start.date() != end.date() or start.weekday() >= 5
            or start.time() != time(9, 30) or not time(9, 30) < end.time() <= time(16)):
        return HumanLimitAssessment("SESSION_CLOSED")
    if (inputs.bid <= 0 or inputs.ask < inputs.bid or inputs.equity <= 0
            or not 0 < inputs.risk_budget <= Decimal("1000000")
            or not c.permits(order_type="LIMIT", fractional=False, regular_session=True)
            or c.quantity_step is None or c.price_tick is None):
        return HumanLimitAssessment("CAPABILITY_UNCONFIRMED")
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        fractional = (
            inputs.fractionable is Capability.CONFIRMED
            and inputs.fractional_limit_day is Capability.CONFIRMED
            and c.permits(order_type="LIMIT", fractional=True, regular_session=True)
        )
        if request.quantity != request.quantity.to_integral_value() and not fractional:
            return HumanLimitAssessment("CAPABILITY_UNCONFIRMED")
        quantity_step = _joint_step(c.quantity_step, Decimal(1).scaleb(
            -p.quantity_decimals if fractional else 0,
        ))
        price_step = _joint_step(
            c.price_tick, Decimal("0.01") if request.limit_price >= 1 else Decimal("0.0001"),
        )
        if request.quantity % quantity_step != 0 or request.limit_price % price_step != 0:
            return HumanLimitAssessment("INVALID_REQUEST")
        reference = inputs.ask if request.side is PaperOrderSide.BUY else inputs.bid
        distance = min(p.max_price_distance_fraction, Decimal("0.001"))
        if abs(request.limit_price-reference) > reference*distance:
            return HumanLimitAssessment("POLICY_MISMATCH")
        buffer = Decimal(1)+p.cash_buffer_fraction
        if request.side is PaperOrderSide.BUY:
            if inputs.held_quantity != 0:
                return HumanLimitAssessment("POLICY_MISMATCH")
            capacity = min(
                inputs.remaining_cash,
                inputs.risk_budget-inputs.held_exposure-inputs.reserved_exposure*buffer,
                inputs.equity*p.max_position_fraction-inputs.instrument_exposure
                - inputs.instrument_reserved_exposure*buffer,
            )
            if request.quantity*request.limit_price*buffer > capacity:
                return HumanLimitAssessment("BUDGET_EXCEEDED")
        elif request.quantity > inputs.held_quantity-inputs.open_exit_quantity:
            return HumanLimitAssessment("INSUFFICIENT_POSITION")
        expiry = min(request.valid_until, now+timedelta(seconds=p.proposal_ttl_seconds),
                     inputs.quote_provider_time+timedelta(seconds=p.quote_age_seconds),
                     inputs.capability_provider_time+timedelta(seconds=p.capability_age_seconds),
                     inputs.capability_observed_at+timedelta(seconds=p.capability_age_seconds),
                     inputs.capability_valid_until, inputs.approval_valid_until, inputs.session_end)
        if expiry <= now:
            return HumanLimitAssessment("APPROVAL_EXPIRED")
        return HumanLimitAssessment(None, expiry, inputs.digest())
