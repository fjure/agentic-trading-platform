from decimal import Decimal

import pytest

from packages.backtesting.strategies import REGISTRY, Target, decide, warmup_count


def test_closed_register_and_decisions():
    assert tuple(x.strategy_id for x in REGISTRY) == ("flat-v1", "buy-hold-v1", "sma-cross-v1")
    assert decide("flat-v1", (), (Decimal("1"),)) == Target.FLAT
    assert decide("buy-hold-v1", (), (Decimal("1"),)) == Target.LONG
    params = (("fast", 2), ("slow", 3))
    assert warmup_count("sma-cross-v1", params) == 2
    assert decide("sma-cross-v1", params, tuple(map(Decimal, ("1", "2", "3")))) == Target.LONG
    assert decide("sma-cross-v1", params, (Decimal("2"),) * 3) == Target.FLAT
    assert decide("sma-cross-v1", params, tuple(map(Decimal, ("3", "2", "1")))) == Target.FLAT


@pytest.mark.parametrize(
    "params",
    [
        (("fast", True), ("slow", 3)),
        (("fast", 1), ("slow", 3)),
        (("fast", 3), ("slow", 3)),
        (("fast", 2), ("slow", 101)),
        (("slow", 3), ("fast", 2)),
    ],
)
def test_invalid_parameters(params):
    with pytest.raises(ValueError):
        decide("sma-cross-v1", params, (Decimal("1"),) * 3)
