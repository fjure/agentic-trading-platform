"""Snapshot-bound simulation with independent Decimal and chronology oracles."""

from copy import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.backtesting.dataset import DatasetBlocked, FoldDataset, Snapshot
from packages.backtesting.evaluation import evaluate
from packages.backtesting.metrics import calculate_metrics
from packages.backtesting.models import ExperimentConfig, Fold, StrictPitPolicyV1, WarmupBinding
from packages.backtesting.retrospective import (
    RetrospectiveDataset,
    RetrospectiveManifest,
    RetrospectiveTrustContext,
    RetrospectiveValidationContext,
    materialize_retrospective_fold,
)
from packages.backtesting.simulator import simulate
from packages.research.models import BarIdentity, BarInterval, BarRevision
from tests.unit.test_backtest_models import config as strict_config
from tests.unit.test_backtest_simulator import dataset as strict_dataset

T = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


def experiment(*, sma=False, folds=1, change=None):
    """Actual receipt is next day; no fabricated historical visibility."""
    observed = T + timedelta(days=folds + 1)
    snapshot = Snapshot(100, observed + timedelta(hours=1))
    warmup = 2 if sma else 0
    configured, materialized, all_bars = [], [], []
    for index in range(folds):
        start = T + timedelta(days=index)
        test_start = start + timedelta(minutes=15 * warmup)
        fold = Fold(
            start if warmup else start - timedelta(minutes=30),
            test_start,
            test_start,
            start + timedelta(minutes=15 * (6 + warmup)),
        )
        configured.append(fold)
        closes = (
            ("1", "2", "3", "4", "1", "1", "1", "1") if sma else ("10", "10", "10", "12", "8", "10")
        )
        bars = tuple(
            BarRevision(
                UUID(int=100 * index + n + 1),
                BarIdentity(
                    UUID(int=1),
                    "alpaca",
                    "iex",
                    BarInterval.FIFTEEN_MINUTES,
                    start + timedelta(minutes=15 * n),
                    start + timedelta(minutes=15 * (n + 1)),
                ),
                Decimal("10"),
                max(Decimal("10"), Decimal(close)),
                min(Decimal("10"), Decimal(close)),
                Decimal(close),
                Decimal("100"),
                observed,
                observed,
                commit_version=n + 1,
            )
            for n, close in enumerate(closes)
        )
        if change:
            bars = change(bars)
        all_bars.append(bars)
        calendar = (
            dict(
                session_id=UUID(int=1000 + index),
                session_date=start.date(),
                opens_at=start,
                closes_at=fold.test_end,
                version="a" * 64,
                source="alpaca-v2-calendar",
                commit_version=1,
                ingested_at=observed,
            ),
        )
        materialized.append(
            materialize_retrospective_fold(
                RetrospectiveDataset(snapshot, bars),
                fold,
                UUID(int=1),
                warmup,
                calendar,
            )
        )
    config = ExperimentConfig(
        UUID(int=1),
        "SPY",
        "sma-cross-v1" if sma else "buy-hold-v1",
        (("fast", 2), ("slow", 3)) if sma else (),
        Decimal("100"),
        Decimal("1"),
        Decimal("100"),
        Decimal("0.000001"),
        tuple(configured),
    )
    manifest = RetrospectiveManifest(
        config,
        snapshot,
        tuple(materialized),
        RetrospectiveTrustContext(snapshot, "b" * 64),
        "c" * 40,
        "d" * 64,
    )
    return config, tuple(materialized), manifest.validation_context(), tuple(all_bars)


def test_current_receipts_use_common_decimal_ledger_and_simulated_market_cutoff():
    config, (data,), context, (bars,) = experiment()
    receipts = tuple((bar.available_at, bar.ingested_at) for bar in bars)
    result = simulate(config, data, validation_context=context)
    (fill,) = result.fills
    assert fill.at == T + timedelta(minutes=30)
    assert fill.price == Decimal("10.10000000")
    assert fill.quantity == Decimal("9.801980")  # floor((100-1)/10.1, six decimals)
    assert fill.notional == Decimal("98.99999800")
    assert fill.fee == Decimal("1.00000000")
    assert fill.slippage_cost == Decimal("0.98019800")
    assert tuple(point.equity for point in result.equity) == tuple(
        map(
            Decimal,
            (
                "100",
                "100",
                "98.019802",
                "117.623762",
                "78.415842",
                "98.019802",
            ),
        )
    )
    assert result.equity[-1].cash == Decimal("0.00000200")
    metrics = calculate_metrics(result)
    assert metrics.max_drawdown == Decimal("0.333333327666")
    assert metrics.net_return == Decimal("-0.019801980000")
    assert metrics.total_cost_usd == Decimal("1.98019800")
    assert metrics.turnover == Decimal("0.989999980000")
    assert result.valuation_method == "raw-iex-close-retrospective-market-v1"
    assert fill.outcome_cutoff == bars[2].identity.bar_end
    assert all(point.outcome_cutoff == point.at for point in result.equity)
    assert all(bar.ingested_at > point.at for bar, point in zip(bars, result.equity, strict=True))
    assert tuple((bar.available_at, bar.ingested_at) for bar in bars) == receipts
    assert all(a is b for a, b in zip(data.outcome_bars, bars, strict=True))


def test_sma_round_trip_hand_arithmetic_and_untouched_benchmark_costs():
    config, datasets, context, _ = experiment(sma=True)
    evaluation = evaluate(config, datasets, validation_context=context)
    assert evaluation.status == "SUCCEEDED"
    fold = evaluation.folds[0]
    buy, sell = fold.strategy.simulation.fills
    start = config.folds[0].test_start
    assert (buy.at, sell.at) == (start + timedelta(minutes=30), start + timedelta(minutes=60))
    assert sell.quantity == buy.quantity == Decimal("9.801980")
    assert sell.price == Decimal("9.90000000")
    assert sell.notional == Decimal("97.03960200")
    assert fold.strategy.metrics.end_cash == Decimal("96.03960400")
    assert fold.strategy.metrics.end_quantity == 0
    assert fold.strategy.metrics.fees_usd == Decimal("2.00000000")
    assert fold.strategy.metrics.slippage_usd == Decimal("1.96039600")
    assert fold.strategy.metrics.net_return == Decimal("-0.039603960000")
    assert fold.flat.metrics.end_equity == Decimal("100.00000000")
    assert fold.flat.metrics.fill_count == 0
    assert fold.buy_hold.simulation.fills == (buy,)
    assert fold.buy_hold.metrics.end_equity == Decimal("9.80198200")
    assert fold.buy_hold.metrics.fees_usd == Decimal("1.00000000")
    assert fold.buy_hold.metrics.slippage_usd == Decimal("0.98019800")
    assert all(
        item.simulation.start_cash == Decimal("100")
        for item in (
            fold.strategy,
            fold.flat,
            fold.buy_hold,
        )
    )


def test_identical_context_is_deterministic_across_decimal_contexts_and_fold_resets():
    config, datasets, context, _ = experiment(folds=2)
    first = evaluate(config, datasets, validation_context=context)
    with localcontext() as caller:
        caller.prec = 5
        repeated = evaluate(config, iter(datasets), validation_context=context)
    assert repeated == first
    assert repeated.result_hash == first.result_hash
    assert first.folds[0].strategy.metrics == first.folds[1].strategy.metrics
    for fold in first.folds:
        assert fold.strategy == fold.buy_hold
        assert fold.flat.metrics.end_cash == Decimal("100")
        assert fold.strategy.simulation.equity[0].cash == Decimal("100")


def test_future_market_prices_cannot_change_prior_signal_or_steer_signal_from_fill_price():
    config, (data,), context, _ = experiment(sma=True)
    original = simulate(config, data, validation_context=context)

    def future_prices(bars):
        # Future fill opens change but every strategy close stays identical.
        return tuple(
            replace(bar, open=Decimal("20"), high=Decimal("20")) if n >= 4 else bar
            for n, bar in enumerate(bars)
        )

    changed_config, (changed_data,), changed_context, _ = experiment(sma=True, change=future_prices)
    changed = simulate(changed_config, changed_data, validation_context=changed_context)
    assert changed.decisions[:2] == original.decisions[:2]
    assert tuple(d.target for d in changed.decisions) == tuple(d.target for d in original.decisions)
    assert changed.fills != original.fills
    assert changed.equity != original.equity
    assert changed_context.manifest.manifest_hash != context.manifest.manifest_hash
    # The frozen original experiment still has the original exact result.
    assert simulate(config, data, validation_context=context) == original


def test_no_signal_time_or_already_started_bar_fill_and_last_targets_expire():
    config, (data,), context, _ = experiment()
    calls = []
    result = simulate(config, data, validation_context=context, checkpoint=lambda: calls.append(1))
    assert len(calls) == len(data.expected_bar_starts)
    assert result.decisions[0].signal_at == T + timedelta(minutes=16)
    assert result.decisions[0].fill_target == T + timedelta(minutes=30)
    assert result.fills[0].at > result.decisions[0].signal_at
    assert result.fills[0].at != T + timedelta(minutes=15)
    assert result.decisions[-2].fill_target is None
    assert result.decisions[-1].fill_target is None
    assert all(fill.at >= config.folds[0].test_start for fill in result.fills)
    with pytest.raises(DatasetBlocked, match="FILL_SCHEDULE_MISMATCH"):
        replace(
            data,
            steps=(replace(data.steps[0], fill_target=T + timedelta(minutes=15)), *data.steps[1:]),
        )
    with pytest.raises(DatasetBlocked, match="FILL_SCHEDULE_MISMATCH"):
        replace(
            data,
            steps=(replace(data.steps[0], fill_target=data.steps[0].knowledge_at), *data.steps[1:]),
        )
    with pytest.raises(DatasetBlocked, match="POINT_IN_TIME_INPUT"):
        replace(
            data,
            steps=(replace(data.steps[0], input_bars=(data.outcome_bars[1],)), *data.steps[1:]),
        )


@pytest.mark.parametrize(
    "invalid", [False, True, StrictPitPolicyV1(), SimpleNamespace(validate=lambda *a: None)]
)
def test_untyped_or_wrong_policy_context_is_rejected_without_callback(invalid):
    config, (data,), _, _ = experiment()
    with pytest.raises(ValueError, match="closed retrospective validation context"):
        simulate(config, data, validation_context=invalid)
    with pytest.raises(ValueError, match="closed retrospective validation context"):
        evaluate(config, (data,), validation_context=invalid)


def test_context_subclass_and_dataset_impostor_cannot_supply_validation_callbacks():
    config, (data,), context, _ = experiment()

    class InjectedContext(RetrospectiveValidationContext):
        def validate(self, config, dataset):
            raise AssertionError("untrusted callback was executed")

    with pytest.raises(ValueError, match="closed retrospective validation context"):
        simulate(config, data, validation_context=InjectedContext(context.manifest))
    with pytest.raises(ValueError, match="Strict PIT"):
        simulate(config, SimpleNamespace(**vars(data)))


@pytest.mark.parametrize("seconds", (60, 61))
def test_strict_signal_receipt_boundary_and_error_code_are_unchanged(seconds):
    config, data = strict_config(), strict_dataset()
    signal = data.steps[0].signal_bar
    receipt = signal.identity.bar_end + timedelta(seconds=seconds)
    signal = replace(signal, available_at=receipt, ingested_at=receipt)
    data = replace(
        data,
        steps=(
            replace(data.steps[0], signal_bar=signal, input_bars=(signal,)),
            *data.steps[1:],
        ),
    )
    if seconds == 60:
        assert simulate(config, data).valuation_method == "raw-iex-close-ex-post-300s-v1"
    else:
        with pytest.raises(ValueError, match="BLOCKED_POINT_IN_TIME_INPUT"):
            simulate(config, data)


@pytest.mark.parametrize("seconds", (300, 301))
def test_strict_outcome_receipt_boundary_and_error_code_are_unchanged(seconds):
    config, data = strict_config(), strict_dataset()
    outcome = data.outcome_bars[2]
    receipt = outcome.identity.bar_end + timedelta(seconds=seconds)
    data = replace(
        data,
        outcome_bars=(
            *data.outcome_bars[:2],
            replace(outcome, available_at=receipt, ingested_at=receipt),
            *data.outcome_bars[3:],
        ),
    )
    if seconds == 300:
        assert simulate(config, data).valuation_method == "raw-iex-close-ex-post-300s-v1"
    else:
        with pytest.raises(ValueError, match="BLOCKED_OUTCOME_DATA_INVALID"):
            simulate(config, data)


def test_dataset_context_mixups_fail_closed_and_do_not_fall_back_to_another_mode():
    config, (data,), context, _ = experiment()
    with pytest.raises(ValueError, match="Strict PIT"):
        simulate(config, data)
    with pytest.raises(ValueError, match="Strict PIT"):
        evaluate(config, (data,))
    strict = FoldDataset(
        data.fold,
        data.warmup,
        data.steps,
        data.outcome_bars,
        data.expected_bar_starts,
        WarmupBinding(data.fold.test_start, data.fold.test_start, ()),
    )
    with pytest.raises(ValueError, match="policy/dataset mismatch"):
        simulate(config, strict, validation_context=context)
    with pytest.raises(ValueError, match="policy/dataset mismatch"):
        evaluate(config, (strict,), validation_context=context)
    with pytest.raises(ValueError, match="OUTCOME_DATA_INVALID"):
        simulate(config, strict)
    different = replace(context.manifest, config=replace(config, fee_usd=Decimal("2")))
    wrong_context = RetrospectiveValidationContext(different)
    with pytest.raises(DatasetBlocked, match="DATASET_BINDING"):
        simulate(config, data, validation_context=wrong_context)
    blocked = evaluate(config, (data,), validation_context=wrong_context)
    assert blocked.status == "BLOCKED"
    assert blocked.folds == ()
    assert blocked.blocked_code == "BLOCKED_DATASET_BINDING"
    other_snapshot = replace(data.snapshot, version=101)
    other_context = replace(
        context,
        manifest=replace(
            context.manifest,
            snapshot=other_snapshot,
            datasets=(replace(data, snapshot=other_snapshot),),
            trust_context=replace(context.manifest.trust_context, snapshot=other_snapshot),
        ),
    )
    with pytest.raises(DatasetBlocked, match="DATASET_BINDING"):
        simulate(config, data, validation_context=other_context)


def test_manifest_references_are_revalidated_before_any_evaluation():
    config, (data,), context, _ = experiment()
    # Simulate a corrupted in-memory/storage restore after construction.
    corrupt = copy(data)
    object.__setattr__(corrupt, "revision_references", data.revision_references[:-1])
    manifest = copy(context.manifest)
    object.__setattr__(manifest, "datasets", (corrupt,))
    corrupted_context = RetrospectiveValidationContext(manifest)
    completed, visits = [], []
    blocked = evaluate(
        config,
        (corrupt,),
        validation_context=corrupted_context,
        on_fold=completed.append,
        checkpoint=lambda: visits.append(1),
    )
    assert blocked.status == "BLOCKED"
    assert blocked.blocked_code == "BLOCKED_DATASET_BINDING"
    assert blocked.folds == ()
    assert completed == visits == []


def test_unbound_changed_prices_or_fingerprint_fail_before_simulation():
    config, (data,), context, _ = experiment()
    changed_config, (changed_data,), _, _ = experiment(
        change=lambda bars: (
            replace(bars[0], high=Decimal("11"), close=Decimal("11")),
            *bars[1:],
        )
    )
    assert config == changed_config
    with pytest.raises(DatasetBlocked, match="DATASET_BINDING"):
        simulate(config, changed_data, validation_context=context)
    corrupt = copy(data)
    object.__setattr__(
        corrupt,
        "revision_references",
        (
            replace(data.revision_references[0], fingerprint="e" * 64),
            *data.revision_references[1:],
        ),
    )
    manifest = copy(context.manifest)
    object.__setattr__(manifest, "datasets", (corrupt,))
    with pytest.raises(DatasetBlocked, match="REVISION_MISMATCH"):
        simulate(config, corrupt, validation_context=RetrospectiveValidationContext(manifest))


@pytest.mark.parametrize(
    "field,value",
    (
        ("policy", StrictPitPolicyV1()),
        ("code_version", "invalid-build"),
        ("trust_context", StrictPitPolicyV1()),
    ),
)
def test_direct_simulate_revalidates_complete_manifest_before_ledger(field, value):
    config, (data,), context, _ = experiment()
    corrupt = copy(context.manifest)
    object.__setattr__(corrupt, field, value)
    calls = []
    with pytest.raises(ValueError):
        simulate(
            config,
            data,
            validation_context=RetrospectiveValidationContext(corrupt),
            checkpoint=lambda: calls.append(1),
        )
    assert calls == []
