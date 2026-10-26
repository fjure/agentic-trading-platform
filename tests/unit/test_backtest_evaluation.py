from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from packages.backtesting.evaluation import evaluate
from packages.backtesting.simulator import SimulationBlocked
from tests.unit.test_backtest_models import config
from tests.unit.test_backtest_simulator import bar, dataset


def test_folds_reset_and_references_use_same_costs():
    first = config().folds[0]
    second = replace(
        first,
        test_start=first.test_start + timedelta(days=1),
        test_end=first.test_end + timedelta(days=1),
    )
    cfg = config(folds=(first, second), fee_usd=Decimal("1"))
    one = dataset(cfg=cfg)
    two = dataset(
        tuple(bar(i, start=second.test_start + timedelta(minutes=15 * i)) for i in range(4)), cfg
    )
    two.fold = second
    result = evaluate(cfg, (one, two))
    assert result.status == "SUCCEEDED"
    assert result.folds[0].strategy.metrics == result.folds[1].strategy.metrics
    assert result.folds[0].strategy == result.folds[0].buy_hold
    assert result.folds[0].flat.metrics.net_return == 0
    assert result.result_hash == evaluate(cfg, (one, two)).result_hash
    assert not hasattr(result, "net_return")


def test_later_blocked_fold_preserves_only_completed_folds():
    first = config().folds[0]
    cfg = config(
        folds=(
            first,
            replace(
                first,
                test_start=first.test_start + timedelta(days=1),
                test_end=first.test_end + timedelta(days=1),
            ),
        )
    )

    def data():
        yield dataset(cfg=cfg)
        raise SimulationBlocked("BLOCKED_WARMUP_MISSING", "second-fold")

    result = evaluate(cfg, data())
    assert result.status == "BLOCKED"
    assert len(result.folds) == 1
    assert result.blocked_code == "BLOCKED_WARMUP_MISSING"


def test_fold_callback_only_after_three_completed_simulations():
    complete = []
    result = evaluate(config(), (dataset(),), on_fold=complete.append)
    assert tuple(complete) == result.folds
    assert complete[0].flat.simulation.start_cash == complete[0].buy_hold.simulation.start_cash
