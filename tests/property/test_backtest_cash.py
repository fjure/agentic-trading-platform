from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from packages.backtesting.simulator import simulate
from tests.unit.test_backtest_models import config
from tests.unit.test_backtest_simulator import bar, dataset


@settings(max_examples=80)
@given(
    cash=st.integers(1, 1000000),
    price=st.integers(1, 10000),
    fee=st.integers(0, 1000),
    slippage=st.integers(0, 100),
)
def test_buy_is_cash_financed_and_raster_exact(cash, price, fee, slippage):
    cfg = config(start_cash=Decimal(cash), fee_usd=Decimal(fee), slippage_bps=Decimal(slippage))
    result = simulate(cfg, dataset(tuple(bar(i, str(price)) for i in range(4)), cfg))
    assert all(p.cash >= 0 and p.quantity >= 0 for p in result.equity)
    assert all(p.quantity % cfg.quantity_step == 0 for p in result.equity)
    if result.fills:
        (fill,) = result.fills
        assert fill.notional + fill.fee <= cfg.start_cash
        assert result.equity[-1].cash == cfg.start_cash - fill.notional - fill.fee
