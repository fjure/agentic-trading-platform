"""Independent D2 arithmetic and shared-ledger, bound-mode regression."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from enum import StrEnum
from uuid import UUID

import pytest

from packages.backtesting.dataset import FrozenDataset, materialize_quant_phases
from packages.backtesting.experiment import (
    REFERENCE_CONFIGURATIONS,
    ExperimentDefinition,
    ExperimentFold,
    PhaseProtocol,
    PhaseWindow,
    QuantExperimentManifest,
    SearchSpace,
)
from packages.backtesting.indicators import EMA, QuantSignalMachine, WilderRSI
from packages.backtesting.retrospective import (
    RetrospectiveDataset,
    materialize_quant_retrospective_phases,
)
from packages.backtesting.simulator import QuantValidationContext, simulate, simulate_quant
from packages.backtesting.strategies import Target
from tests.unit.test_backtest_dataset import fixture
from tests.unit.test_backtest_models import config
from tests.unit.test_backtest_simulator import dataset
from tests.unit.test_quant_lab_models import policy


def test_registered_fingerprint_rejects_changed_target_enum(monkeypatch):
    from packages.backtesting import strategies

    class ChangedTarget(StrEnum):
        FLAT = "FLAT"
        LONG = "SHORT"

    strategies.resolve_strategy("buy-hold-v1", require_execution=True)
    monkeypatch.setattr(strategies, "Target", ChangedTarget)
    with pytest.raises(ValueError, match="fingerprint"):
        strategies.resolve_strategy("buy-hold-v1", require_execution=True)


def test_ema_hand_values_and_no_rolling_reseed():
    fast, slow = EMA(2), EMA(3)
    result = [(fast.update(Decimal(c)), slow.update(Decimal(c))) for c in (10, 11, 12, 13)]
    assert result[2:] == [(Decimal("11.5"), Decimal(11)), (Decimal("12.5"), Decimal(12))]
    # Recursive EMA(3) of 10,11,12,30,10 is 15.25; reseeding last 3 gives 52/3.
    slow = EMA(3)
    for c in (10, 11, 12, 30, 10):
        slow.update(Decimal(c))
    assert slow.value == Decimal("15.25")


@pytest.mark.parametrize(
    "closes,expected",
    [
        ((10, 11, 10, 11), (Decimal(50), Decimal(75))),
        ((10, 11, 12, 13), (Decimal(100), Decimal(100))),
        ((13, 12, 11, 10), (Decimal(0), Decimal(0))),
        ((10, 10, 10, 10), (Decimal(50), Decimal(50))),
    ],
)
def test_wilder_hand_oracles(closes, expected):
    rsi = WilderRSI(2)
    values = tuple(rsi.update(Decimal(c)) for c in closes)
    assert values[:2] == (None, None)
    assert values[2:] == expected


def machine(strategy="rsi-mean-reversion-v1", **params):
    return QuantSignalMachine(strategy, tuple(sorted(params.items())))


def test_seed_computes_indicators_without_target_change_and_hysteresis_inclusive():
    from tests.unit.test_backtest_models import T

    signal = machine(period=2, entry="50", exit="75")
    signal.seed(tuple((T + timedelta(minutes=i), Decimal(c)) for i, c in enumerate((10, 11, 10))))
    assert signal.rsi.value == 50 and signal.target == Target.FLAT
    # 10 -> 9 changes RSI from 50 to 25; inclusive entry chooses LONG.
    assert signal.update(T + timedelta(minutes=3), Decimal(9)) == Target.LONG
    assert signal.update(T + timedelta(minutes=4), Decimal(10)) == Target.LONG  # 62.5
    assert signal.update(T + timedelta(minutes=5), Decimal(11)) == Target.FLAT  # 81.25
    edge = machine(period=2, entry="50", exit="75")
    edge.seed(((T, Decimal(10)), (T + timedelta(minutes=1), Decimal(11))))
    assert edge.update(T + timedelta(minutes=2), Decimal(10)) == Target.LONG  # exactly 50
    assert edge.update(T + timedelta(minutes=3), Decimal(11)) == Target.FLAT  # exactly 75


def test_unrounded_threshold_and_ema_equality():
    from tests.unit.test_backtest_models import T

    for last, expected in (("10.000000000001", Target.FLAT), ("9.999999999999", Target.LONG)):
        signal = machine(period=2, entry="50", exit="75")
        signal.seed(((T, Decimal(10)), (T + timedelta(minutes=1), Decimal(11))))
        assert signal.update(T + timedelta(minutes=2), Decimal(last)) == expected
    signal = machine("ema-cross-v1", fast=2, slow=3)
    signal.seed(((T, Decimal(10)), (T + timedelta(minutes=1), Decimal(10))))
    assert signal.update(T + timedelta(minutes=2), Decimal(10)) == Target.FLAT


def quant_fixture(mode="STRICT_PIT"):
    instrument, start, snapshot, originals, calendar, proof, _ = fixture()
    bars = tuple(
        replace(
            originals[0],
            revision_id=UUID(int=1000 + n),
            commit_version=n + 1,
            identity=replace(
                originals[0].identity,
                bar_start=start + timedelta(minutes=15 * n),
                bar_end=start + timedelta(minutes=15 * (n + 1)),
            ),
            available_at=start + timedelta(minutes=15 * (n + 1), seconds=30),
            ingested_at=start + timedelta(minutes=15 * (n + 1), seconds=30),
            open=Decimal(10 + n),
            high=Decimal(10 + n),
            low=Decimal(10 + n),
            close=Decimal(10 + n),
        )
        for n in range(14)
    )
    calendar = ({**calendar[0], "closes_at": start + timedelta(hours=4)},)
    windows = tuple(
        PhaseWindow(start + timedelta(minutes=30 + 45 * i), start + timedelta(minutes=75 + 45 * i))
        for i in range(4)
    )
    spaces = tuple(
        SearchSpace(s, (("fast", (2,)), ("slow", (3,)))) for s in ("sma-cross-v1", "ema-cross-v1")
    ) + (
        SearchSpace(
            "rsi-mean-reversion-v1", (("period", (2,)), ("entry", ("30",)), ("exit", ("70",)))
        ),
    )
    definition = ExperimentDefinition(
        instrument,
        "SPY",
        "D2 fixture",
        spaces,
        PhaseProtocol((ExperimentFold(*windows[:3]),), windows[3]),
        policy(),
        research_mode=mode,
    )
    if mode == "STRICT_PIT":
        phases = materialize_quant_phases(
            FrozenDataset(snapshot, bars), definition, calendar, coverage_proofs=(proof,)
        )
    else:
        phases = materialize_quant_retrospective_phases(
            RetrospectiveDataset(snapshot, bars), definition, calendar
        )
    manifest = QuantExperimentManifest.from_phase_datasets(
        definition,
        phases,
        snapshot,
        code_version="b" * 40,
        trust_fingerprint="c" * 64,
        license_receipt_fingerprint="d" * 64,
    )
    return definition, phases, QuantValidationContext(manifest, phases)


@pytest.mark.parametrize("mode", ["STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"])
def test_all_five_closed_signals_share_ledger_fresh_phases_and_replay(mode):
    definition, phases, context = quant_fixture(mode)
    assert context.manifest_hash == context.manifest.manifest_hash
    for candidate in definition.candidates + REFERENCE_CONFIGURATIONS:
        for phase in phases:
            result = simulate_quant(definition, phase, candidate, validation_context=context)
            assert len(result.decisions) == len(result.equity) == 3
            assert result.equity[0].cash == definition.start_cash
            assert result.equity[0].quantity == 0
            assert all(f.at >= phase.binding.window.start for f in result.fills)
            assert result == simulate_quant(
                definition, phase, candidate, validation_context=context
            )
            with localcontext() as ctx:
                ctx.prec, ctx.rounding = 6, ROUND_DOWN
                assert result == simulate_quant(
                    definition, phase, candidate, validation_context=context
                )


@pytest.mark.parametrize("mode", ["STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"])
def test_fail_closed_context_and_seed_mutation(mode):
    definition, phases, context = quant_fixture(mode)
    candidate = definition.candidates[0]
    with pytest.raises(ValueError, match="context"):
        simulate_quant(definition, phases[0], candidate)
    with pytest.raises(ValueError):
        simulate_quant(
            replace(definition, fee_usd=Decimal(1)),
            phases[0],
            candidate,
            validation_context=context,
        )
    with pytest.raises((ValueError, RuntimeError)):
        bad = replace(
            phases[0], dataset=replace(phases[0].dataset, warmup=phases[0].dataset.warmup[1:])
        )
        simulate_quant(definition, bad, candidate, validation_context=context)


def test_existing_fill_fee_cash_oracle_and_legacy_hash_unchanged():
    cfg = config(fee_usd=Decimal(1), slippage_bps=Decimal(100))
    result = simulate(cfg, dataset(cfg=cfg))
    assert result.fills[0].quantity == Decimal("9.801980")
    assert result.fills[0].notional == Decimal("98.99999800")
    assert config().input_hash == "82ec4a7121c7bf3ade8aeb031f23ce60f3df28d1306924d35a8d863cd71c6cd6"


def test_complete_local_signal_artifact_change_is_rejected(monkeypatch):
    from pathlib import Path

    from packages.backtesting.strategies import resolve_strategy

    read = Path.read_text

    def changed(path, *args, **kwargs):
        text = read(path, *args, **kwargs)
        return text + "\n# changed implementation\n" if path.name == "indicators.py" else text

    monkeypatch.setattr(Path, "read_text", changed)
    for strategy in (
        "flat-v1",
        "buy-hold-v1",
        "sma-cross-v1",
        "ema-cross-v1",
        "rsi-mean-reversion-v1",
    ):
        with pytest.raises(ValueError, match="fingerprint"):
            resolve_strategy(strategy, require_execution=True)
