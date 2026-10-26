"""Public simulated fills remain exact under caller Decimal contexts and bounded writes."""

from decimal import Decimal, localcontext

import pytest

from packages.backtesting.evaluation import evaluate
from packages.backtesting.jobs import encoded
from packages.backtesting.models import BacktestBudgetExceeded, WarmupBinding
from packages.backtesting.storage import fold_public
from tests.unit.test_backtest_models import config
from tests.unit.test_backtest_simulator import dataset


def test_public_cash_projection_is_context_independent_and_matches_terminal_equity():
    cfg = config(fee_usd=Decimal("1"), slippage_bps=Decimal("100"))
    data = dataset(cfg=cfg)
    data.warmup = ()
    data.warmup_binding = WarmupBinding(cfg.folds[0].test_start, cfg.folds[0].test_start, ())
    result = evaluate(cfg, (data,)).folds[0]
    expected = fold_public(result, data, {})
    with localcontext() as caller:
        caller.prec = 6
        assert fold_public(result, data, {}) == expected
    fill = expected["fills"][0]
    assert fill["sequence"] == 1
    assert Decimal(fill["cashAfter"]) == result.strategy.simulation.equity[-1].cash
    assert Decimal(fill["quantityAfter"]) == result.strategy.simulation.equity[-1].quantity


def test_oversized_persistent_envelope_is_a_budget_failure():
    with pytest.raises(BacktestBudgetExceeded):
        encoded({"data": "x" * (12 * 1024 * 1024)})
