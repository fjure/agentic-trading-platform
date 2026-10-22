"""Imported rounded averages cannot replace exact total basis or lose sale residuals."""

from decimal import Decimal, localcontext

import pytest

from packages.domain.models import Position
from services.execution.paper_cost_basis import costed_fill


def position():
    return Position("position", "SPY", "USD", Decimal("60"), Decimal("12.83"), Decimal(0), 1)


def test_full_exit_uses_770_total_instead_of_769_80_rounded_average_product():
    result, basis = costed_fill(position(), Decimal("770"), "SELL", Decimal("60"), Decimal("20"))
    assert basis == result.quantity == 0
    assert result.average_price is None
    assert result.realized_pnl == Decimal("430")


def test_repeated_partial_sales_preserve_exact_residual_and_total_profit():
    current, basis = position(), Decimal("770")
    with localcontext() as context:
        context.prec = 6
        for _ in range(60):
            current, basis = costed_fill(current, basis, "SELL", Decimal(1), Decimal("20"))
    assert current.quantity == basis == 0
    assert current.realized_pnl == Decimal("430")


def test_buy_adds_exact_fill_cost_and_full_exit_conserves_total_basis():
    current, basis = costed_fill(position(), Decimal("770"), "BUY", Decimal(1), Decimal("10"))
    assert basis == Decimal("780") and current.quantity == 61
    result, basis = costed_fill(current, basis, "SELL", Decimal("61"), Decimal("15"))
    assert basis == 0 and result.realized_pnl == Decimal("135")


@pytest.mark.parametrize(
    "basis,side,quantity,price",
    [
        ("-1", "SELL", "1", "20"),
        ("770", "SELL", "61", "20"),
        ("770", "SELL", "0", "20"),
        ("770", "SHORT", "1", "20"),
        ("770", "BUY", "1", "-20"),
    ],
)
def test_invalid_cost_or_order_never_mutates_a_position(basis, side, quantity, price):
    original = position()
    with pytest.raises(ValueError):
        costed_fill(original, Decimal(basis), side, Decimal(quantity), Decimal(price))
    assert original.quantity == 60 and original.average_price == Decimal("12.83")
