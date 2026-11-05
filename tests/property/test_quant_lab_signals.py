"""Rational, chronology, perturbation and context properties of the closed port."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction
from uuid import UUID

import pytest

from packages.backtesting.experiment import QuantExperimentManifest
from packages.backtesting.indicators import EMA, QuantSignalMachine, WilderRSI
from packages.backtesting.retrospective import RetrospectiveRevisionReference
from packages.backtesting.simulator import QuantValidationContext, simulate_quant
from packages.backtesting.strategies import Target
from tests.unit.test_backtest_models import T
from tests.unit.test_quant_lab_strategies import quant_fixture


@pytest.mark.parametrize("period", range(2, 11))
def test_recursive_ema_matches_independent_rational_weight_expansion(period):
    prices = tuple(Decimal((i * 17) % 23 + 1) for i in range(30))
    ema = EMA(period)
    alpha, decay = Fraction(2, period + 1), Fraction(period - 1, period + 1)
    for index, close in enumerate(prices):
        actual = ema.update(close)
        if index + 1 < period:
            assert actual is None
            continue
        steps = index + 1 - period
        expected = decay**steps * sum(Fraction(c) for c in prices[:period]) / period
        expected += sum(
            alpha * decay ** (index - j) * Fraction(prices[j]) for j in range(period, index + 1)
        )
        with localcontext() as ctx:
            ctx.prec = 60
            exact = Decimal(expected.numerator) / Decimal(expected.denominator)
            assert abs(actual - exact) < Decimal("1e-34")


@pytest.mark.parametrize("period", range(2, 11))
@pytest.mark.parametrize("direction", [-1, 0, 1])
def test_wilder_monotone_and_constant_boundary_property(period, direction):
    rsi = WilderRSI(period)
    for n in range(30):
        result = rsi.update(Decimal(50 + direction * n))
        if n >= period:
            assert result == {-1: Decimal(0), 0: Decimal(50), 1: Decimal(100)}[direction]


def test_identity_duplicate_reverse_and_reseed_fail_before_signal_update():
    signal = QuantSignalMachine("ema-cross-v1", (("fast", 2), ("slow", 3)))
    signal.seed(((T, Decimal(10)), (T + timedelta(minutes=15), Decimal(11))))
    at = T + timedelta(minutes=30)
    assert signal.update(at, Decimal(12)) == Target.LONG
    prior = (signal.fast.value, signal.slow.value, signal.target)
    for bad_at in (T, at):
        with pytest.raises(ValueError):
            signal.update(bad_at, Decimal(999))
        assert prior == (signal.fast.value, signal.slow.value, signal.target)
    with pytest.raises(ValueError):
        signal.seed(())


@pytest.mark.parametrize("period", range(2, 8))
def test_wilder_average_matches_independent_weighted_rational_formula(period):
    prices = tuple(Decimal((i * 17) % 23 + 1) for i in range(25))
    deltas = tuple(Fraction(b - a) for a, b in zip(prices, prices[1:], strict=False))
    decay = Fraction(period - 1, period)
    rsi = WilderRSI(period)
    for index, close in enumerate(prices):
        rsi.update(close)
        if index < period:
            continue
        steps = index - period
        gains = tuple(max(d, Fraction(0)) for d in deltas)
        losses = tuple(max(-d, Fraction(0)) for d in deltas)
        with localcontext() as ctx:
            ctx.prec = 60
            for actual, values in ((rsi.gain, gains), (rsi.loss, losses)):
                expected = decay**steps * sum(values[:period]) / period
                expected += sum(
                    decay ** (index - 1 - j) * values[j] / period for j in range(period, index)
                )
                exact = Decimal(expected.numerator) / Decimal(expected.denominator)
                assert abs(actual - exact) < Decimal("1e-34")


def rebound(context, phases):
    manifest = context.manifest
    from packages.backtesting.dataset import Snapshot

    bound = QuantExperimentManifest.from_phase_datasets(
        manifest.definition,
        phases,
        Snapshot(manifest.snapshot_version, manifest.snapshot_at),
        code_version=manifest.code_version,
        trust_fingerprint=manifest.trust_fingerprint,
        license_receipt_fingerprint=manifest.license_receipt_fingerprint,
    )
    return QuantValidationContext(bound, phases)


@pytest.mark.parametrize("mode", ["STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"])
def test_outcome_only_perturbation_changes_fills_never_signal_inputs(mode):
    definition, phases, context = quant_fixture(mode)
    candidate = next(c for c in definition.candidates if c.strategy_id == "ema-cross-v1")
    phase = phases[0]
    original = simulate_quant(definition, phase, candidate, validation_context=context)
    outcome = phase.dataset.outcome_bars[-1]
    changed = replace(outcome, revision_id=UUID(int=9999), open=Decimal(99), high=Decimal(99))
    updates = {"outcome_bars": (*phase.dataset.outcome_bars[:-1], changed)}
    if mode == "STRICT_PIT":
        updates["outcome_cutoffs"] = (
            *phase.dataset.outcome_cutoffs[:-1],
            (changed.revision_id, changed.identity.bar_end + timedelta(seconds=300)),
        )
    else:
        # Both signal and altered outcome revisions are explicitly bound.
        updates["revision_references"] = tuple(
            sorted(
                (
                    *phase.dataset.revision_references,
                    RetrospectiveRevisionReference.from_bar(changed),
                ),
                key=lambda r: (r.identity.bar_start, r.revision_id),
            )
        )
    modified = replace(phase, dataset=replace(phase.dataset, **updates))
    changed_phases = (modified, *phases[1:])
    changed_context = rebound(context, changed_phases)
    result = simulate_quant(definition, modified, candidate, validation_context=changed_context)
    assert result.decisions == original.decisions
    assert result.fills != original.fills


@pytest.mark.parametrize("mode", ["STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"])
def test_future_signal_perturbation_preserves_all_prior_decisions(mode):
    definition, phases, context = quant_fixture(mode)
    phase = phases[0]
    last = phase.dataset.steps[-1]
    changed = replace(last.signal_bar, revision_id=UUID(int=9998), close=Decimal(1), low=Decimal(1))
    updates = {
        "steps": (
            *phase.dataset.steps[:-1],
            replace(last, signal_bar=changed, input_bars=(changed,)),
        )
    }
    if mode == "RETROSPECTIVE_EXPLORATORY":
        updates["revision_references"] = tuple(
            sorted(
                (
                    *phase.dataset.revision_references,
                    RetrospectiveRevisionReference.from_bar(changed),
                ),
                key=lambda r: (r.identity.bar_start, r.revision_id),
            )
        )
    modified = replace(phase, dataset=replace(phase.dataset, **updates))
    changed_context = rebound(context, (modified, *phases[1:]))
    for candidate in definition.candidates:
        original = simulate_quant(definition, phase, candidate, validation_context=context)
        result = simulate_quant(definition, modified, candidate, validation_context=changed_context)
        assert result.decisions[:-1] == original.decisions[:-1]
        assert result.decisions[-1].input_hash != original.decisions[-1].input_hash


@pytest.mark.parametrize("mode", ["STRICT_PIT", "RETROSPECTIVE_EXPLORATORY"])
def test_missing_bar_future_input_and_wrong_snapshot_fail_closed(mode):
    definition, phases, context = quant_fixture(mode)
    with pytest.raises(ValueError):
        incomplete = replace(phases[0].dataset, steps=phases[0].dataset.steps[:-1])
        rebound(context, (replace(phases[0], dataset=incomplete), *phases[1:]))
    with pytest.raises(ValueError):
        first = phases[0].dataset.steps[0]
        future = phases[0].dataset.steps[-1].signal_bar
        bad_step = replace(first, input_bars=(*first.input_bars, future))
        bad_data = replace(phases[0].dataset, steps=(bad_step, *phases[0].dataset.steps[1:]))
        rebound(context, (replace(phases[0], dataset=bad_data), *phases[1:]))
    with pytest.raises(ValueError):
        QuantValidationContext(replace(context.manifest, snapshot_version=1), phases)
