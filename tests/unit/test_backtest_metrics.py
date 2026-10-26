from decimal import Decimal

from packages.backtesting.metrics import calculate_metrics
from packages.backtesting.simulator import simulate
from tests.unit.test_backtest_models import config
from tests.unit.test_backtest_simulator import bar, dataset


def test_independent_metrics_include_start_cash_drawdown():
    cfg = config(fee_usd=Decimal("1"), slippage_bps=Decimal("100"))
    result = simulate(
        cfg, dataset(tuple(bar(i, close="9" if i >= 2 else "10") for i in range(4)), cfg)
    )
    metrics = calculate_metrics(result)
    assert metrics.end_equity == Decimal("88.21782200")
    assert metrics.net_return == Decimal("-0.117821780000")
    assert metrics.max_drawdown == Decimal("0.117821780000")
    assert metrics.turnover == Decimal("0.989999980000")
    assert metrics.fees_usd == Decimal("1.00000000")
    assert metrics.slippage_usd == Decimal("0.98019800")
    assert metrics.total_cost_usd == Decimal("1.98019800")
    assert (metrics.decision_count, metrics.fill_count, metrics.bar_count) == (4, 1, 4)
