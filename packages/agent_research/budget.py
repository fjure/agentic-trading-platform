"""Exact research quota arithmetic for use inside a locked ledger transaction."""

import re
from collections.abc import Iterable
from dataclasses import dataclass, fields
from decimal import ROUND_CEILING, Decimal, localcontext
from enum import StrEnum

_MAX_COUNT = 2**63 - 1
_MAX_COST = Decimal("99999999999999999999.999999999999999999")
_COST_QUANTUM = Decimal("0.000000000000000001")


class BudgetDenied(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _check_count(value: int) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise BudgetDenied("INVALID_BUDGET")


def _check_cost(value: Decimal) -> None:
    if (
        not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= _MAX_COST
        or value.as_tuple().exponent < -18
    ):
        raise BudgetDenied("INVALID_BUDGET")


def _check_currency(currency: str) -> None:
    if not isinstance(currency, str) or re.fullmatch(r"[A-Z]{3}", currency) is None:
        raise BudgetDenied("INVALID_BUDGET")


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    currency: str
    cost: Decimal = Decimal(0)
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    steps: int = 0
    tool_calls: int = 0
    experiments: int = 0
    ideas: int = 0

    def __post_init__(self) -> None:
        _check_currency(self.currency)
        _check_cost(self.cost)
        for dimension in _COUNTS:
            _check_count(getattr(self, dimension))


_COUNTS = tuple(
    field.name for field in fields(BudgetAmount) if field.name not in {"cost", "currency"}
)


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    limit: BudgetAmount
    consumed: BudgetAmount
    reserved: BudgetAmount


def require_capacity(windows: Iterable[BudgetWindow], requested: BudgetAmount) -> None:
    """Check all applicable, already locked scopes before any quota write.

    Consumed includes monotone usage. Reserved includes carried exposure when
    a provider or global scope spans unresolved earlier periods.
    """
    checked = False
    with localcontext() as context:
        context.prec = 80
        for window in windows:
            checked = True
            amounts = (window.limit, window.consumed, window.reserved)
            if any(amount.currency != requested.currency for amount in amounts):
                raise BudgetDenied("BUDGET_CURRENCY_MISMATCH")
            for dimension in ("cost", *_COUNTS):
                limit, consumed, reserved = (getattr(amount, dimension) for amount in amounts)
                if consumed + reserved + getattr(requested, dimension) > limit:
                    raise BudgetDenied("BUDGET_EXHAUSTED")
    if not checked:
        raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class TokenPrices:
    version: int
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    bounds_unconfirmed_cost: bool = False

    def __post_init__(self) -> None:
        _check_count(self.version)
        _check_currency(self.currency)
        _check_cost(self.input_per_million)
        _check_cost(self.output_per_million)
        if not self.version or type(self.bounds_unconfirmed_cost) is not bool:
            raise BudgetDenied("INVALID_BUDGET")


@dataclass(frozen=True, slots=True)
class Tokens:
    input: int
    output: int

    def __post_init__(self) -> None:
        _check_count(self.input)
        _check_count(self.output)


def _estimate(prices: TokenPrices, tokens: Tokens) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        gross = tokens.input * prices.input_per_million + tokens.output * prices.output_per_million
        cost = (gross / 1_000_000).quantize(_COST_QUANTUM, rounding=ROUND_CEILING)
    _check_cost(cost)
    return cost


@dataclass(frozen=True, slots=True)
class ModelReservation:
    prices: TokenPrices
    bound: BudgetAmount


def reserve_model(
    prices: TokenPrices | None, bound: Tokens, context_window: int,
) -> ModelReservation:
    """Price the full envelope input and maximum output before dispatch."""
    if prices is None:
        raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")
    _check_count(context_window)
    if not bound.input or not bound.output or bound.input + bound.output > context_window:
        raise BudgetDenied("BUDGET_EXHAUSTED")
    amount = BudgetAmount(prices.currency, _estimate(prices, bound), bound.input, bound.output, 1)
    return ModelReservation(prices, amount)


class ModelOutcome(StrEnum):
    UNSENT = "UNSENT"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class ModelSettlement:
    consumed: BudgetAmount
    reserved: BudgetAmount
    reported_tokens: Tokens | None
    estimated_cost: Decimal | None
    confirmed_cost: Decimal | None
    exceeded_bound: bool


def reconcile_model(
    reservation: ModelReservation,
    outcome: ModelOutcome,
    reported_tokens: Tokens | None = None,
    confirmed_cost: Decimal | None = None,
) -> ModelSettlement:
    """Project the new accounted amounts from the original attempt reservation.

    The transaction replaces the prior amounts idempotently. A verified
    non-dispatch releases token and money holds while preserving the attempt
    counter. Unverified values stay unknown and retain their original holds.
    """
    if not isinstance(outcome, ModelOutcome):
        raise BudgetDenied("INVALID_BUDGET")
    if confirmed_cost is not None:
        _check_cost(confirmed_cost)
    bound, prices = reservation.bound, reservation.prices
    if outcome is ModelOutcome.UNSENT:
        if reported_tokens is not None or confirmed_cost is not None:
            raise BudgetDenied("INVALID_BUDGET")
        return ModelSettlement(
            BudgetAmount(bound.currency, model_calls=1), BudgetAmount(bound.currency),
            None, None, Decimal(0), False,
        )

    estimate = None if reported_tokens is None else _estimate(prices, reported_tokens)
    reserved_cost = Decimal(0)
    if confirmed_cost is None:
        reserved_cost = bound.cost
        if estimate is not None and (prices.bounds_unconfirmed_cost or estimate > bound.cost):
            reserved_cost = estimate
    exceeded = (
        (confirmed_cost is not None and confirmed_cost > bound.cost)
        or (estimate is not None and estimate > bound.cost)
        or (reported_tokens is not None and (
            reported_tokens.input > bound.input_tokens
            or reported_tokens.output > bound.output_tokens
        ))
    )
    consumed = BudgetAmount(
        bound.currency, confirmed_cost if confirmed_cost is not None else Decimal(0),
        reported_tokens.input if reported_tokens else 0,
        reported_tokens.output if reported_tokens else 0,
        model_calls=1,
    )
    reserved = BudgetAmount(
        bound.currency, reserved_cost,
        bound.input_tokens if reported_tokens is None else 0,
        bound.output_tokens if reported_tokens is None else 0,
    )
    return ModelSettlement(consumed, reserved, reported_tokens, estimate, confirmed_cost, exceeded)
