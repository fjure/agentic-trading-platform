"""Selection leakage, fold isolation and independent rational comparison properties."""

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from fractions import Fraction
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from packages.backtesting.experiment import SearchSpace
from packages.backtesting.experiment_protocol import (
    SelectionValues,
    freeze_selection_plan,
    select_validation,
)
from packages.backtesting.retrospective import RetrospectiveRevisionReference
from packages.backtesting.simulator import simulate_quant
from tests.property.test_quant_lab_signals import rebound
from tests.unit.test_backtest_models import T
from tests.unit.test_quant_lab_models import definition
from tests.unit.test_quant_lab_protocol import (
    POLICY,
    PRESERVATION,
    RETRO,
    cell,
    evidence,
    raw_hash,
    timeline,
)
from tests.unit.test_quant_lab_strategies import quant_fixture


def decimal_cents(value):
    return Decimal(value).scaleb(-2)


@given(
    st.lists(
        st.tuples(st.integers(-70, 100), st.integers(0, 20), st.integers(0, 200)),
        min_size=8,
        max_size=8,
    ),
    st.booleans(),
)
def test_fold_and_final_rankings_match_fraction_oracle_in_any_decimal_context(values, preserve):
    policy = PRESERVATION if preserve else POLICY
    ids = ("3" * 64, "7" * 64)
    rows = tuple(
        cell(candidate, *(str(decimal_cents(v)) for v in values[2 * fold + i]), fold=fold)
        for fold in range(4)
        for i, candidate in enumerate(ids)
    )

    def median(column):
        middle = sorted(column)[1:3]
        return sum(middle, Fraction(0)) / 2

    expected = {}
    for i, candidate in enumerate(ids):
        history = values[i::2]
        if any(dd > 10 for _, dd, _ in history):
            continue
        ret, dd, turnover = (
            median(tuple(Fraction(sample[column], 100) for sample in history))
            for column in range(3)
        )
        expected[candidate] = (
            (dd, -ret, turnover, candidate) if preserve else (-ret, dd, turnover, candidate)
        )
    result = select_validation(
        policy, ids, rows, scope="final", frozen_at=T, fold_indices=(0, 1, 2, 3)
    )
    with localcontext() as context:
        context.prec, context.rounding, context.traps[Inexact] = 2, ROUND_DOWN, True
        replay = select_validation(
            policy,
            ids[::-1],
            rows[::-1],
            scope="final",
            frozen_at=T + timedelta(seconds=1),
            fold_indices=(0, 1, 2, 3),
        )
    order = sorted(expected, key=expected.get)
    assert result.selected_candidate_id == (order[0] if order else None)
    assert replay.selection_hash == result.selection_hash
    for rank, candidate in enumerate(order, 1):
        item = evidence(result, candidate)
        assert item["rank"] == rank
        assert (
            tuple(
                Fraction(Decimal(item["rankingKey"][field]))
                for field in ("primary", "secondary", "turnover")
            )
            == expected[candidate][:3]
        )


@given(st.integers(-99, 10_000), st.integers(-99, 10_000), st.integers(-99, 10_000))
@settings(max_examples=40)
def test_s9_frozen_oos_and_holdout_slots_cannot_be_reranked(oos, holdout, analysis):
    spec = definition(
        protocol=timeline(2),
        search_spaces=(
            SearchSpace("flat-v1", ()),
            SearchSpace("buy-hold-v1", ()),
        ),
    )
    first, second = (candidate.candidate_id for candidate in spec.candidates)
    rows = (
        cell(first, "0.2", "0.02"),
        cell(second, "0.1", "0.01"),
        cell(first, "-0.1", "0.02", fold=1),
        cell(second, "0.12", "0.01", fold=1),
    )
    plan = freeze_selection_plan(spec, rows, frozen_at=T)
    frozen_slots = tuple((phase.phase, phase.candidate_ids) for phase in plan.phases[-3:])
    assert frozen_slots == (("test", (first,)), ("test", (second,)), ("holdout", (second,)))
    assert dict(plan.phases[-3].not_evaluated) == {second: "NOT_EVALUATED_BY_PROTOCOL"}
    later_results = tuple(
        {"phase": phase, "netReturn": str(decimal_cents(value))}
        for phase, value in (("test", oos), ("holdout", holdout), ("training", analysis))
    )
    for late in later_results:
        with pytest.raises(ValueError, match="only typed validation inputs"):
            freeze_selection_plan(spec, (*rows, late), frozen_at=T)
    rerun = freeze_selection_plan(spec, tuple(reversed(rows)), frozen_at=T + timedelta(seconds=1))
    assert rerun.selection_hashes == plan.selection_hashes
    assert rerun.phases == plan.phases


def validation_links(spec, phases, context):
    """Tiny independent ledger oracle. The partial session counts as zero full sessions."""
    validation = next(phase for phase in phases if phase.binding.phase == "validation")
    links = []
    for candidate in spec.candidates:
        result = simulate_quant(spec, validation, candidate, validation_context=context)
        peak = Fraction(result.start_cash)
        drawdown = Fraction(0)
        for point in result.equity:
            equity = Fraction(point.equity)
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)
        returns = Fraction(result.equity[-1].equity) / Fraction(result.start_cash) - 1
        turnover = sum((Fraction(abs(fill.notional)) for fill in result.fills), Fraction(0)) / (
            Fraction(result.start_cash)
        )
        ratio = Fraction(sum(point.quantity > 0 for point in result.equity), len(result.equity))
        with localcontext() as arithmetic:
            arithmetic.prec = 38
            exact_values = tuple(
                Decimal(value.numerator) / Decimal(value.denominator)
                for value in (returns, drawdown, turnover, ratio)
            )
        values = SelectionValues(*exact_values, sum(fill.side == "BUY" for fill in result.fills), 0)
        link = raw_hash(
            [
                candidate.candidate_id,
                values.to_wire(),
                [decision.input_hash for decision in result.decisions],
            ]
        )
        original = cell(candidate.candidate_id, "0", "0")
        links.append(
            replace(
                original,
                input_hash=link,
                manifest_hash=context.manifest_hash,
                values=values,
                reliability=RETRO,
            )
        )
    return tuple(links)


@pytest.mark.parametrize("later_phase", ["test", "holdout"])
def test_future_outcome_revisions_change_the_later_ledger_but_never_validation_identity(
    later_phase,
):
    spec, phases, context = quant_fixture("RETROSPECTIVE_EXPLORATORY")
    validation_inputs = validation_links(spec, phases, context)
    original_plan = freeze_selection_plan(spec, validation_inputs, frozen_at=T)
    changed_phase = next(phase for phase in phases if phase.binding.phase == later_phase)
    outcome = changed_phase.dataset.outcome_bars[-1]
    revised = replace(outcome, revision_id=UUID(int=9999), close=Decimal(100), high=Decimal(100))
    revised_dataset = replace(
        changed_phase.dataset,
        outcome_bars=(*changed_phase.dataset.outcome_bars[:-1], revised),
        revision_references=tuple(
            sorted(
                (
                    *changed_phase.dataset.revision_references,
                    RetrospectiveRevisionReference.from_bar(revised),
                ),
                key=lambda item: (item.identity.bar_start, item.revision_id),
            )
        ),
    )
    replacement = replace(changed_phase, dataset=revised_dataset)
    revised_phases = tuple(replacement if p is changed_phase else p for p in phases)
    revised_context = rebound(context, revised_phases)
    candidate = next(c for c in spec.candidates if c.strategy_id == "ema-cross-v1")
    original = simulate_quant(spec, changed_phase, candidate, validation_context=context)
    changed = simulate_quant(spec, replacement, candidate, validation_context=revised_context)
    assert original.equity != changed.equity and original.decisions == changed.decisions
    assert revised_context.manifest_hash != context.manifest_hash
    replay_inputs = validation_links(spec, revised_phases, revised_context)
    replay_plan = freeze_selection_plan(spec, replay_inputs, frozen_at=T)
    assert tuple(row.input_hash for row in replay_inputs) == tuple(
        row.input_hash for row in validation_inputs
    )
    assert replay_plan.selection_hashes == original_plan.selection_hashes
    assert replay_plan.phases == original_plan.phases


@given(st.sampled_from(("BLOCKED", "FAILED", "INCOMPLETE")), st.integers(0, 3))
def test_one_technical_failure_in_any_fold_revokes_every_oos_slot(status, fold):
    spec = definition(protocol=timeline(4))
    candidate = spec.candidates[0].candidate_id
    rows = tuple(cell(candidate, "0.2", "0.01", fold=i) for i in range(4))
    inputs = tuple(replace(row, status=status) if row.fold_index == fold else row for row in rows)
    plan = freeze_selection_plan(spec, inputs, frozen_at=T)
    assert not plan.complete
    assert [decision.status for decision in plan.decisions] == ["INCOMPLETE"] * 5
    assert all(phase.phase in ("training", "validation") for phase in plan.phases)
