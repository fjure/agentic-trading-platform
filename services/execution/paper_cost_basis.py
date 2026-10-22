"""Weighted cost allocation from authoritative total basis, preserving every ledger unit."""

from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from packages.domain.models import decimal_value
from services.execution.reconciliation import _exact

_UNIT = Decimal("0.000000000000000001")


def costed_fill(position, basis, side, quantity, price):
    basis, quantity, price = map(decimal_value, (basis, quantity, price))
    if (
        basis < 0
        or quantity <= 0
        or price <= 0
        or side not in {"BUY", "SELL"}
        or position.quantity == 0
        and basis != 0
    ):
        raise ValueError("POSITION_COST_BASIS_INVALID")
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        proceeds = _exact(quantity * price)
        if side == "BUY":
            remaining = _exact(position.quantity + quantity)
            updated_basis = _exact(basis + proceeds)
            # This display average is separate from the authoritative total.
            average = _exact((updated_basis / remaining).quantize(_UNIT, rounding=ROUND_HALF_EVEN))
            return replace(position, quantity=remaining, average_price=average), updated_basis
        if quantity > position.quantity:
            raise ValueError("SELL_BASIS_OR_QUANTITY_INVALID")
        remaining = _exact(position.quantity - quantity)
        # Allocate at the ledger's 18-place precision. Keep the exact residual
        # on the remainder; the final sale consumes it, never qty*rounded_average.
        allocated = (
            basis
            if remaining == 0
            else _exact(
                (basis * quantity / position.quantity).quantize(_UNIT, rounding=ROUND_HALF_EVEN)
            )
        )
        updated_basis = _exact(basis - allocated)
        return replace(
            position,
            quantity=remaining,
            average_price=position.average_price if remaining else None,
            realized_pnl=_exact(position.realized_pnl + proceeds - allocated),
        ), updated_basis
