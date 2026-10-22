"""Conservative isolated-Paper risk facts, never financial ledger rounding."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from packages.domain.models import decimal_value

_UNIT = Decimal("0.000000000000000001")
_BOUND = Decimal("100000000000000000000")


def _bound(value, rounding, *, nonnegative):
    if (not isinstance(value, Decimal) or not value.is_finite()
            or nonnegative and value < 0 or not -_BOUND < value < _BOUND):
        raise ValueError("PAPER_RISK_NUMERIC_INVALID")
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        result = value.quantize(_UNIT, rounding=rounding)
    return decimal_value(result)


def exposure_upper(value: Decimal) -> Decimal:
    """Represent nonnegative marked exposure without understating any dust."""
    return _bound(value, ROUND_CEILING, nonnegative=True)


def pnl_lower(value: Decimal) -> Decimal:
    """Represent unrealized return without overstating gains or hiding losses."""
    return _bound(value, ROUND_FLOOR, nonnegative=False)
