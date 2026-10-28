from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from packages.backtesting.dataset import DecisionStep, FoldDataset
from packages.backtesting.models import WarmupBinding
from packages.backtesting.simulator import SimulationBlocked, simulate
from packages.research.models import BarIdentity, BarInterval, BarRevision
from tests.unit.test_backtest_models import T, config


def bar(index, price="10", close=None, start=None):
    start = start or T + timedelta(minutes=15 * index)
    end = start + timedelta(minutes=15)
    price, close = Decimal(price), Decimal(close or price)
    return BarRevision(
        UUID(int=index + 1),
        BarIdentity(UUID(int=1), "alpaca", "iex", BarInterval.FIFTEEN_MINUTES, start, end),
        price,
        max(price, close),
        min(price, close),
        close,
        Decimal("100"),
        end + timedelta(seconds=30),
        end + timedelta(seconds=30),
        commit_version=1,
    )


def dataset(bars=None, cfg=None):
    cfg = cfg or config()
    bars = bars or tuple(bar(i) for i in range(4))
    steps = tuple(
        DecisionStep(
            signal_bar=b,
            input_bars=bars[: i + 1],
            fill_target=next(
                (
                    future.identity.bar_start
                    for future in bars
                    if future.identity.bar_start >= b.identity.bar_end + timedelta(seconds=60)
                ),
                None,
            ),
        )
        for i, b in enumerate(bars)
    )
    return FoldDataset(
        fold=cfg.folds[0],
        steps=steps,
        outcome_bars=bars,
        expected_bar_starts=tuple(b.identity.bar_start for b in bars),
        warmup=(),
        warmup_binding=WarmupBinding(cfg.folds[0].test_start, cfg.folds[0].test_start, ()),
    )


def test_hand_calculated_fill_cash_and_end_mark():
    cfg = config(fee_usd=Decimal("1"), slippage_bps=Decimal("100"))
    data = dataset(tuple(bar(i, close="12" if i == 3 else "10") for i in range(4)), cfg)
    result = simulate(cfg, data)
    (fill,) = result.fills
    assert fill.at == T + timedelta(minutes=30)  # 14:16 signal cannot fill 14:15.
    assert fill.price == Decimal("10.10000000")
    assert fill.quantity == Decimal("9.801980")
    assert fill.notional == Decimal("98.99999800")
    assert fill.slippage_cost == Decimal("0.98019800")
    assert result.equity[-1].cash == Decimal("0.00000200")
    assert result.equity[-1].quantity == Decimal("9.801980")
    assert result.equity[-1].equity == Decimal("117.62376200")
    with localcontext() as ctx:
        ctx.prec = 5
        assert simulate(cfg, data).result_hash == result.result_hash


def test_zero_quantity_missing_bar_and_fill_before_signal():
    cfg = config(start_cash=Decimal("0.5"), fee_usd=Decimal("1"))
    assert not simulate(cfg, dataset(cfg=cfg)).fills
    data = dataset()
    data = replace(data, outcome_bars=data.outcome_bars[:2] + data.outcome_bars[3:])
    with pytest.raises(SimulationBlocked, match="OUTCOME_DATA_MISSING"):
        simulate(config(), data)
    data = dataset()
    data = replace(
        data, steps=(replace(data.steps[0], fill_target=T + timedelta(minutes=15)), *data.steps[1:])
    )
    with pytest.raises(SimulationBlocked, match="FILL_BEFORE_SIGNAL"):
        simulate(config(), data)


def test_future_outcomes_do_not_change_decision_hash():
    data = dataset()
    original = simulate(config(), data)
    data = replace(
        data,
        outcome_bars=tuple(
            replace(b, open=Decimal("20"), high=Decimal("20")) if i >= 2 else b
            for i, b in enumerate(data.outcome_bars)
        ),
    )
    changed = simulate(config(), data)
    assert changed.decisions == original.decisions
    assert changed.fills != original.fills
    bad = dataset()
    bad = replace(
        bad,
        steps=(
            replace(bad.steps[0], input_bars=(bad.steps[0].signal_bar, bad.outcome_bars[1])),
            *bad.steps[1:],
        ),
    )
    with pytest.raises(SimulationBlocked, match="POINT_IN_TIME"):
        simulate(config(), bad)


def test_late_outcome_revision_and_overnight_fill():
    data = dataset()
    late = data.outcome_bars[2]
    data = replace(
        data,
        outcome_bars=(
            *data.outcome_bars[:2],
            replace(
                late,
                available_at=late.identity.bar_end + timedelta(seconds=301),
                ingested_at=late.identity.bar_end + timedelta(seconds=301),
            ),
            data.outcome_bars[3],
        ),
    )
    with pytest.raises(SimulationBlocked, match="OUTCOME_DATA_INVALID"):
        simulate(config(), data)
    cfg = config(folds=(replace(config().folds[0], test_end=T + timedelta(days=2)),))
    bars = (bar(0), bar(1), bar(2, start=T + timedelta(days=1)))
    assert simulate(cfg, dataset(bars, cfg)).fills[0].at == T + timedelta(days=1)


def sma_dataset():
    cfg = config(
        strategy_id="sma-cross-v1",
        parameters=(("fast", 2), ("slow", 3)),
        fee_usd=Decimal("1"),
        slippage_bps=Decimal("100"),
        folds=(replace(config().folds[0], test_end=T + timedelta(hours=2)),),
    )
    bars = tuple(bar(i, close=close) for i, close in enumerate(("3", "4", "1", "1", "1", "1")))
    warmup = (
        bar(100, "1", start=T - timedelta(minutes=30)),
        bar(101, "2", start=T - timedelta(minutes=15)),
    )
    data = dataset(bars, cfg)
    data = replace(
        data,
        warmup=warmup,
        steps=tuple(
            replace(step, input_bars=warmup + bars[: i + 1]) for i, step in enumerate(data.steps)
        ),
    )
    return cfg, data


def test_sma_sale_and_no_warmup_fills_independent_math():
    cfg, data = sma_dataset()
    result = simulate(cfg, data)
    buy, sell = result.fills
    assert buy.at == T + timedelta(minutes=30)
    assert sell.at == T + timedelta(minutes=60)
    assert sell.quantity == Decimal("9.801980")
    assert sell.price == Decimal("9.90000000")
    assert sell.notional == Decimal("97.03960200")
    assert result.equity[-1].cash == Decimal("96.03960400")
    assert result.equity[-1].quantity == 0
    assert all(fill.at >= cfg.folds[0].test_start for fill in result.fills)


def test_sale_fee_must_be_covered_and_sma_missing_warmup_blocks():
    cfg, data = sma_dataset()
    data = replace(
        data,
        outcome_bars=tuple(
            bar(4, "0.01") if i == 4 else b for i, b in enumerate(data.outcome_bars)
        ),
    )
    with pytest.raises(SimulationBlocked, match="SELL_FEE_EXCEEDS_PROCEEDS"):
        simulate(cfg, data)
    cfg, data = sma_dataset()
    data = replace(
        data,
        steps=(replace(data.steps[0], input_bars=(data.steps[0].signal_bar,)), *data.steps[1:]),
    )
    with pytest.raises(SimulationBlocked, match="WARMUP_MISSING"):
        simulate(cfg, data)


def test_checkpoint_each_bar_and_last_signal_expires():
    visits = []
    result = simulate(config(), dataset(), checkpoint=lambda: visits.append(1))
    assert len(visits) == 4
    assert result.decisions[-1].fill_target is None
    assert result.equity[-1].quantity > 0


def test_missing_schedule_target_blocks_instead_of_silent_fill_skip():
    data = dataset()
    data = replace(data, steps=(replace(data.steps[0], fill_target=None), *data.steps[1:]))
    with pytest.raises(SimulationBlocked, match="FILL_SCHEDULE_MISMATCH"):
        simulate(config(), data)
