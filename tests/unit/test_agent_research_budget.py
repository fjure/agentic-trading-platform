"""APF-R06 checks for exact quota arithmetic and unresolved attempts."""

from decimal import Decimal, localcontext

import pytest

from packages.agent_research.budget import (
    BudgetAmount,
    BudgetDenied,
    BudgetWindow,
    ModelOutcome,
    TokenPrices,
    Tokens,
    reconcile_model,
    require_capacity,
    reserve_model,
)


def test_admission_checks_every_scope_and_carries_open_reservations():
    request = BudgetAmount("USD", Decimal("0.02"), model_calls=1)
    empty = BudgetAmount("USD")
    allowed = BudgetWindow(BudgetAmount("USD", Decimal("0.03"), model_calls=2), empty, empty)
    provider = BudgetWindow(
        BudgetAmount("USD", Decimal("0.04"), model_calls=2),
        BudgetAmount("USD", Decimal("0.01"), model_calls=1),
        BudgetAmount("USD", Decimal("0.01")),
    )
    require_capacity((allowed, provider), request)
    carried = BudgetWindow(provider.limit, provider.consumed, BudgetAmount("USD", Decimal("0.02")))
    with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
        require_capacity((allowed, carried), request)


def test_independent_dimensions_and_currency_cannot_be_bypassed():
    empty = BudgetAmount("USD")
    limit = BudgetAmount("USD", Decimal(1), 100, 100, model_calls=1, experiments=0)
    window = BudgetWindow(limit, empty, empty)
    for request in (
        BudgetAmount("USD", input_tokens=101),
        BudgetAmount("USD", output_tokens=101),
        BudgetAmount("USD", model_calls=2),
        BudgetAmount("USD", experiments=1),
    ):
        with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
            require_capacity((window,), request)
    with pytest.raises(BudgetDenied, match="BUDGET_CURRENCY_MISMATCH"):
        require_capacity((window,), BudgetAmount("EUR"))
    with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
        require_capacity((), empty)


def test_reservation_prices_complete_context_and_rounds_up():
    tariff = TokenPrices(1, "USD", Decimal("0.000000000001"), Decimal("0.0000000000005"))
    with localcontext() as context:
        context.prec = 2
        reservation = reserve_model(tariff, Tokens(1, 1), 2)
    assert reservation.bound == BudgetAmount("USD", Decimal("0.000000000000000002"), 1, 1, 1)
    with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
        reserve_model(tariff, Tokens(2, 1), 2)
    with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
        reserve_model(None, Tokens(1, 1), 2)


@pytest.mark.parametrize("amount", [float("nan"), Decimal("NaN"), Decimal("-0.01"), 0.1])
def test_money_rejects_untyped_or_invalid_values(amount):
    with pytest.raises(BudgetDenied, match="INVALID_BUDGET"):
        BudgetAmount("USD", amount)


def test_unknown_attempt_retains_holds_and_unsent_retains_attempt_counter():
    tariff = TokenPrices(1, "USD", Decimal(1), Decimal(2))
    reservation = reserve_model(tariff, Tokens(100, 50), 150)
    unknown = reconcile_model(reservation, ModelOutcome.OUTCOME_UNKNOWN)
    assert unknown.confirmed_cost is unknown.estimated_cost is unknown.reported_tokens is None
    assert unknown.reserved == BudgetAmount("USD", Decimal("0.0002"), 100, 50)
    assert unknown.consumed.model_calls == 1
    unsent = reconcile_model(reservation, ModelOutcome.UNSENT)
    assert unsent.confirmed_cost == 0 and unsent.consumed.model_calls == 1
    assert unsent.reserved == BudgetAmount("USD")


@pytest.mark.parametrize("bounds_cost", [False, True])
def test_reported_tokens_and_billed_cost_stay_distinct(bounds_cost):
    tariff = TokenPrices(1, "USD", Decimal(1), Decimal(2), bounds_cost)
    reservation = reserve_model(tariff, Tokens(100, 50), 150)
    settlement = reconcile_model(reservation, ModelOutcome.COMPLETED, Tokens(50, 25))
    assert settlement.confirmed_cost is None and settlement.estimated_cost == Decimal("0.0001")
    expected_hold = Decimal("0.0001") if bounds_cost else Decimal("0.0002")
    assert settlement.reserved == BudgetAmount("USD", expected_hold)
    confirmed = reconcile_model(
        reservation, ModelOutcome.COMPLETED, confirmed_cost=Decimal("0.0003")
    )
    assert confirmed.consumed.cost == Decimal("0.0003") and confirmed.exceeded_bound
    assert confirmed.reserved == BudgetAmount("USD", input_tokens=100, output_tokens=50)
