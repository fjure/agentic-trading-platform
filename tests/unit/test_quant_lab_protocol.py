"""Literal acceptance oracles for D3 and the complete QSL selection contract."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from uuid import UUID

import pytest

from packages.backtesting.experiment import (
    CAPITAL_PRESERVATION,
    MAX_NET_RETURN,
    ExperimentFold,
    PhaseProtocol,
    PhaseSeedBinding,
    PhaseWindow,
    SearchSpace,
    StrategySelectionPolicy,
)
from packages.backtesting.experiment_protocol import (
    ExposureInterval,
    ExposureMarket,
    ExposureRound,
    ExposureWindow,
    ResearchCounts,
    SelectionReliability,
    SelectionValues,
    ValidationSelectionInput,
    canonical_selection_preimage,
    compare_exposures,
    decimal_median,
    display_decimal,
    freeze_selection_plan,
    plan_analysis_validation,
    planned_exposure_windows,
    select_validation,
)
from tests.contracts.test_quant_lab_examples import validator
from tests.unit.test_backtest_models import T
from tests.unit.test_quant_lab_models import definition, quant_manifest

A, B = "a" * 64, "b" * 64
POLICY = StrategySelectionPolicy(MAX_NET_RETURN, "0.10", 20)
PRESERVATION = replace(POLICY, policy_id=CAPITAL_PRESERVATION)
STRICT = SelectionReliability("STRICT_PIT", "STRICT_PIT_EVIDENCE_COMPLETE", "PASS")
RETRO = SelectionReliability("RETROSPECTIVE_EXPLORATORY", "EXPLORATORY_WITH_LIMITATIONS", "LIMITED")


def raw_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def cell(candidate, ret, dd, turnover="0.5", *, fold=0, ratio="0.5", entry=1, sessions=20):
    values = SelectionValues(*map(Decimal, (ret, dd, turnover, ratio)), entry, sessions)
    link_hash = raw_hash([candidate, fold, ret, dd, turnover, ratio, entry, sessions])
    return ValidationSelectionInput(
        candidate, fold, link_hash, "f" * 64, values, True, True, True, STRICT
    )


def select(rows, *, policy=POLICY, ids=(A, B), folds=None):
    return select_validation(
        policy,
        ids,
        tuple(rows),
        scope="final" if folds else "fold:0",
        frozen_at=T,
        fold_indices=folds,
    )


def evidence(decision, candidate):
    return next(
        item for item in decision.to_wire()["candidates"] if item["candidateId"] == candidate
    )


def selected(decision):
    return decision.to_wire()["selectedCandidateId"]


@pytest.mark.parametrize("policy,winner", [(POLICY, B), (PRESERVATION, A)])
def test_s1_tradeoff_selects_by_closed_policy(policy, winner):
    rows = (cell(A, "0.005", "0.01"), cell(B, "0.12", "0.08"))
    decision = select(rows, policy=policy)
    assert decision.status == "SELECTED"
    assert selected(decision) == winner
    assert evidence(decision, B)["rankingKey"] == {
        "primary": "-0.12" if policy == POLICY else "0.08",
        "secondary": "0.08" if policy == POLICY else "-0.12",
        "turnover": "0.5",
        "candidateId": B,
    }
    validator("QuantLabSelection").validate(decision.to_wire())
    validator("QuantLabSelection", standalone=True).validate(decision.to_wire())


def test_s2_s3_explicit_flat_and_negative_returns_use_the_same_objective():
    flat = cell(A, "0", "0", "0", ratio="0", entry=0)
    positive = cell(B, "0.12", "0.08")
    less_negative = cell(B, "-0.01", "0.08")
    more_negative = cell(A, "-0.03", "0.05")
    assert selected(select((flat, positive))) == B
    assert selected(select((flat, positive), policy=PRESERVATION)) == A
    assert selected(select((flat, less_negative))) == A
    result = select((more_negative, less_negative))
    assert selected(result) == B
    assert evidence(result, B)["medianSelectionValues"]["netReturn"] == "-0.01"


@pytest.mark.parametrize("above", ["0.100000000001", "0.1000000000004"])
def test_s4_dd_gate_is_inclusive_and_compares_before_display_rounding(above):
    rows = (cell(A, "0.2", above), cell(B, "0.15", "0.100000000000"))
    result = select(rows)
    assert selected(result) == B
    assert evidence(result, A)["reasons"] == ["MAX_DRAWDOWN_EXCEEDED"]
    assert evidence(result, A)["validationValues"][0]["selectionValues"]["maxDrawdown"] == above
    assert result.display_values()[B]["medianSelectionValues"]["maxDrawdown"] == "0.100000000000"
    if above.endswith("4"):
        assert (
            result.display_values()[A]["medianSelectionValues"]["maxDrawdown"] == "0.100000000000"
        )
    assert selected(select((cell(A, "0.2", "0.0999999999996"), rows[1]))) == A


def test_s5_complete_no_selection_has_reference_work_only():
    spec = definition(search_spaces=(SearchSpace("flat-v1", ()), SearchSpace("buy-hold-v1", ())))
    ids = tuple(candidate.candidate_id for candidate in spec.candidates)
    rows = tuple(cell(candidate, "0.2", "0.11") for candidate in ids)
    plan = freeze_selection_plan(spec, rows, frozen_at=T)
    assert plan.complete and plan.fitting_status == "NOT_TRAINABLE"
    assert [phase.role for phase in plan.phases] == [
        "IN_SAMPLE_ANALYSIS",
        "PARAMETER_SELECTION",
        "WALK_FORWARD_OOS",
        "FINAL_HOLDOUT",
    ]
    for decision in plan.decisions:
        assert decision.status == "NO_ELIGIBLE_CANDIDATE"
        assert selected(decision) is None
        validator("QuantLabSelection").validate(decision.to_wire())
    for phase in plan.phases[-2:]:
        assert phase.candidate_ids == () and set(phase.reference_ids) == set(ids)
        assert dict(phase.not_evaluated) == dict.fromkeys(ids, "NOT_EVALUATED_NO_SELECTION")


@pytest.mark.parametrize("sharpe_na", ["ZERO_VARIANCE", "INCOMPLETE_DAILY_SERIES"])
def test_s6_descriptive_sharpe_na_does_not_block_required_values(sharpe_na):
    row = cell(A, "0", "0", "0", ratio="0", entry=0)
    descriptive = {"sharpe": {"status": "N/A", "reasonCode": sharpe_na}}
    assert selected(select((row,), ids=(A,))) == A
    assert "sharpe" not in row.values.to_wire()
    with pytest.raises(TypeError, match="sharpe"):
        replace(row, **descriptive)
    result = select((replace(row, values=None),), ids=(A,))
    assert result.status == "INCOMPLETE"
    assert selected(result) is None
    assert evidence(result, A)["reasons"] == ["INVALID_NUMERIC_INPUT"]


def test_s7_exact_tie_ascii_identity_turnover_and_wire_copy():
    rows = (cell(B, "0.12", "0.08"), cell(A, "0.12", "0.08"))
    original = select(rows)
    replay = select_validation(
        POLICY, (B, A), tuple(reversed(rows)), scope="fold:0", frozen_at=T + timedelta(seconds=1)
    )
    assert original.selection_hash == replay.selection_hash
    assert selected(replay) == A
    assert [evidence(replay, candidate)["rank"] for candidate in (A, B)] == [1, 2]
    assert selected(select((cell(A, "0.12", "0.08", "0.51"), rows[0]))) == B
    body = replay.to_wire()
    body["candidates"][0]["rank"] = 32
    assert evidence(replay, A)["rank"] == 1
    with pytest.raises(FrozenInstanceError):
        replay.scope = "final"


@pytest.mark.parametrize(
    "change",
    [
        {"policy_id": CAPITAL_PRESERVATION},
        {"max_drawdown": "0.11"},
        {"min_validation_sessions": 21},
        {"minimum_entry_count": 0},
        {"minimum_invested_bar_ratio": "0"},
    ],
)
def test_s8_each_parameter_binds_definition_manifest_and_selection(change):
    baseline = definition()
    changed = replace(baseline, strategy_selection_policy=replace(POLICY, **change))
    ids = tuple(candidate.candidate_id for candidate in baseline.candidates)
    rows = (cell(ids[0], "0.12", "0.08", sessions=21),)
    first = select(rows, ids=ids)
    second = select(rows, ids=ids, policy=changed.strategy_selection_policy)
    assert selected(first) == selected(second) == ids[0]
    assert first.selection_hash != second.selection_hash
    assert first.to_wire()["validationInputHash"] == second.to_wire()["validationInputHash"]
    assert baseline.definition_hash != changed.definition_hash
    assert quant_manifest(baseline).manifest_hash != quant_manifest(changed).manifest_hash
    wire = first.to_wire()
    selection_hash = wire.pop("selectionHash")
    wire.pop("frozenAt")
    selector_fingerprint = "61ab136098bb3c364f0dacd4365d98565d98db20742edb563f3399293564d958"
    preimage = [
        "quant-selection-v1",
        [MAX_NET_RETURN, "0.1", 20, None, None, selector_fingerprint],
        "fold:0",
        [rows[0].input_hash],
        wire,
    ]
    assert raw_hash(preimage) == selection_hash
    assert canonical_selection_preimage(first) == preimage


def test_s10_final_never_accepts_a_bad_fold_even_with_good_median():
    rows = (
        cell(A, "0.20", "0.01"),
        cell(B, "0.10", "0.08", "0.51", ratio="0.1"),
        cell(A, "0.20", "0.19", fold=1),
        cell(B, "0.12", "0.08", "0.53", fold=1, ratio="0.3"),
    )
    result = select(rows, folds=(0, 1))
    validator("QuantLabSelection", standalone=True).validate(result.to_wire())
    assert selected(result) == B
    assert evidence(result, A)["medianSelectionValues"]["maxDrawdown"] == "0.1"
    assert evidence(result, A)["reasons"] == ["MAX_DRAWDOWN_EXCEEDED"]
    assert evidence(result, B)["medianSelectionValues"] == {
        "netReturn": "0.11",
        "maxDrawdown": "0.08",
        "turnover": "0.52",
        "investedBarRatio": "0.2",
    }
    assert Fraction(Decimal("0.11")) == (Fraction(10, 100) + Fraction(12, 100)) / 2
    assert decimal_median(
        (
            Decimal("0.12345678901234567890123456789012345678"),
            Decimal("0.12345678901234567890123456789012345679"),
        )
    ) == Decimal("0.12345678901234567890123456789012345678")
    assert display_decimal(Decimal("0.1234567890125")) == "0.123456789012"
    assert display_decimal(Decimal("0.1234567890135")) == "0.123456789014"


def test_s11_sample_activity_and_one_decimal38_step_report_all_failures():
    ratio = "0.666666666667"
    minimum = replace(POLICY, minimum_entry_count=1, minimum_invested_bar_ratio=ratio)
    below = "0.66666666666699999999999999999999999999"
    result = select(
        (
            cell(A, "0", "0.11", "0", ratio=below, entry=0, sessions=19),
            cell(B, "0", "0", "0", ratio=ratio, entry=1, sessions=20),
        ),
        policy=minimum,
    )
    assert selected(result) == B
    assert set(evidence(result, A)["reasons"]) == {
        "MAX_DRAWDOWN_EXCEEDED",
        "MINIMUM_ENTRIES_NOT_MET",
        "MINIMUM_SESSIONS_NOT_MET",
        "MINIMUM_INVESTED_BAR_RATIO_NOT_MET",
    }
    assert result.display_values()[A]["medianSelectionValues"]["investedBarRatio"] == ratio
    flat = cell(A, "0", "0", "0", ratio="0", entry=0)
    assert selected(select((flat,), ids=(A,))) == A
    assert select((flat,), ids=(A,), policy=minimum).status == "NO_ELIGIBLE_CANDIDATE"


def test_s12_one_failed_among_nine_blocks_ranking_unlike_nine_risk_violations():
    ids = tuple(f"{number:064x}" for number in range(1, 10))
    rows = tuple(cell(candidate, "0.20", "0.08") for candidate in ids)
    broken = select((*rows[:-1], replace(rows[-1], status="FAILED")), ids=ids)
    assert broken.status == "INCOMPLETE" and selected(broken) is None
    assert broken.to_wire()["eligibleCount"] == 8
    assert all(
        item["rank"] is item["rankingKey"] is None for item in broken.to_wire()["candidates"]
    )
    assert evidence(broken, ids[-1])["reasons"] == ["INCOMPLETE_CANDIDATE_SET"]
    validator("QuantLabSelection").validate(broken.to_wire())
    assert select(rows[:-1], ids=ids).status == "INCOMPLETE"
    rejected = select(tuple(cell(candidate, "0.20", "0.11") for candidate in ids), ids=ids)
    assert rejected.status == "NO_ELIGIBLE_CANDIDATE" and selected(rejected) is None
    with pytest.raises(ValueError, match="INCOMPLETE_CANDIDATE_SET"):
        select((), ids=ids)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"complete_data": False}, "INCOMPLETE_BAR_SERIES"),
        ({"calendar_confirmed": False}, "MISSING_CALENDAR"),
        ({"snapshot_bound": False}, "SNAPSHOT_UNAVAILABLE"),
        (
            {"reliability": replace(STRICT, evidence_status="EXPLORATORY_WITH_LIMITATIONS")},
            "RELIABILITY_BLOCKED",
        ),
        ({"reliability": replace(STRICT, rating="LIMITED")}, "RELIABILITY_BLOCKED"),
    ],
)
def test_incomplete_data_evidence_blocks_even_high_return(mutation, reason):
    row = cell(A, "0.90", "0")
    result = select((replace(row, **mutation),), ids=(A,))
    assert result.status == "INCOMPLETE"
    assert reason in evidence(result, A)["reasons"]
    assert select((replace(row, reliability=RETRO),), ids=(A,)).status == "SELECTED"


def test_incomparable_manifests_and_unbound_mode_fail_closed():
    first, second = cell(A, "0.1", "0"), cell(B, "0.2", "0")
    with pytest.raises(ValueError, match="INCOMPARABLE_BINDINGS"):
        select((first, replace(second, manifest_hash="e" * 64)))
    spec = definition()
    retro = replace(cell(spec.candidates[0].candidate_id, "0.1", "0"), reliability=RETRO)
    with pytest.raises(ValueError, match="INCOMPARABLE_BINDINGS"):
        freeze_selection_plan(spec, (retro,), frozen_at=T)


def timeline(folds=2):
    def window(day):
        return PhaseWindow(T + timedelta(days=day), T + timedelta(days=day, minutes=30))

    return PhaseProtocol(
        tuple(ExperimentFold(*(window(3 * i + d) for d in range(3))) for i in range(folds)),
        window(3 * folds),
    )


def test_phase_schedule_keeps_all_candidates_in_analysis_and_holds_every_oos_on_failure():
    spec = definition(protocol=timeline())
    candidate = spec.candidates[0].candidate_id
    first, second = cell(candidate, "0.1", "0"), cell(candidate, "0.2", "0", fold=1)
    complete = freeze_selection_plan(spec, (first, second), frozen_at=T)
    failed = freeze_selection_plan(spec, (first, replace(second, status="BLOCKED")), frozen_at=T)
    assert [d.scope for d in complete.decisions] == ["fold:0", "fold:1", "final"]
    assert all(p.candidate_ids == (candidate,) for p in complete.phases)
    assert complete.phases[-1].phase == "holdout"
    assert not failed.complete and all(d.status == "INCOMPLETE" for d in failed.decisions)
    assert failed.phases == plan_analysis_validation(spec)
    assert all(p.phase in ("training", "validation") for p in failed.phases)


def test_exposures_compare_all_roles_legacy_unknown_failed_and_retry_once():
    spec = definition(protocol=timeline(), hypothesis="another hypothesis")
    market = ExposureMarket(spec.instrument_id)
    phases = planned_exposure_windows(spec)
    current = ExposureRound(UUID(int=10), "BATCH", market, "VALIDATED", phases)
    old_run = ExposureRound(
        UUID(int=11),
        "RUN",
        market,
        "FAILED",
        (
            ExposureWindow("train", "UNKNOWN", phases[2].window),
            ExposureWindow(
                "test",
                "WALK_FORWARD_OOS",
                ExposureInterval(
                    phases[1].window.start + timedelta(minutes=1), phases[1].window.end
                ),
                result_available=True,
            ),
        ),
    )
    prior_batch = ExposureRound(
        UUID(int=12),
        "BATCH",
        market,
        "BLOCKED",
        (
            ExposureWindow("0:training", "IN_SAMPLE_ANALYSIS", phases[-1].window),
            ExposureWindow("0:validation", "PARAMETER_SELECTION", phases[1].window),
            ExposureWindow("holdout", "FINAL_HOLDOUT", phases[4].window),
        ),
        definition_hash="7" * 64,
        supersedes=old_run.source_id,
        candidate_counts=ResearchCounts(9, 6, 3, 2, 1, 3),
        reference_counts=ResearchCounts(2, 1, 1),
    )
    draft = replace(prior_batch, source_id=UUID(int=13), status="DRAFT")
    foreign = replace(prior_batch, source_id=UUID(int=14), market=replace(market, feed="sip"))
    unknown = ExposureRound(
        UUID(int=15),
        "RETROSPECTIVE",
        market,
        "ACCEPTED",
        (ExposureWindow("unknown", "UNKNOWN", None),),
    )
    report = compare_exposures(current, (draft, old_run, prior_batch, old_run, foreign, unknown))
    assert report.reuse_status == "REGISTERED_REUSE"
    assert report.external_knowledge_status == "EXTERNAL_KNOWLEDGE_UNKNOWN"
    assert len(report.registered_rounds) == 4 and report.draft_rounds == (draft,)
    assert report.unresolved_prior_rounds == (unknown,)
    assert report.prior_failed_round_count == 1 and report.prior_blocked_round_count == 2
    assert report.superseded_round_count == 1 and report.repeated_validation_count == 1
    assert {(o.current.role, o.prior.role) for o in report.overlaps} == {
        ("PARAMETER_SELECTION", "FINAL_HOLDOUT"),
        ("PARAMETER_SELECTION", "WALK_FORWARD_OOS"),
        ("PARAMETER_SELECTION", "PARAMETER_SELECTION"),
        ("FINAL_HOLDOUT", "IN_SAMPLE_ANALYSIS"),
        ("WALK_FORWARD_OOS", "UNKNOWN"),
    }
    assert prior_batch.candidate_counts.planned == 9 and prior_batch.reference_counts.planned == 2
    for overlap in report.overlaps:
        validator("QuantLabExposureOverlap").validate(overlap.to_wire())
    old_test = next(overlap for overlap in report.overlaps if overlap.prior.phase_key == "test")
    assert old_test.interval.start == phases[1].window.start + timedelta(minutes=1)
    assert {o.to_wire()["knowledgeStatus"] for o in report.overlaps} == {
        "REQUESTED",
        "RESULT_AVAILABLE",
    }


def test_exposure_half_open_boundaries_and_exact_gap_warmup_are_separate():
    spec = definition()
    market = ExposureMarket(spec.instrument_id)
    gap_seed, earlier_seed = T - timedelta(minutes=15), T - timedelta(days=3)
    bindings = tuple(
        PhaseSeedBinding(
            fold,
            phase,
            window,
            (earlier_seed, gap_seed),
            (window.start,),
        )
        for fold, phase, _, window in spec.protocol.phases
    )
    windows = planned_exposure_windows(spec, bindings)
    current = ExposureRound(UUID(int=10), "BATCH", market, "VALIDATED", windows)
    prior = ExposureRound(
        UUID(int=11),
        "RUN",
        market,
        "SUCCEEDED",
        (
            ExposureWindow("recent", "WALK_FORWARD_OOS", PhaseWindow(gap_seed, T)),
            ExposureWindow(
                "weekend",
                "UNKNOWN",
                PhaseWindow(
                    earlier_seed,
                    earlier_seed
                    + timedelta(
                        minutes=15,
                    ),
                ),
            ),
        ),
    )
    report = compare_exposures(current, (prior,))
    assert not report.overlaps and report.reuse_status == "NO_REGISTERED_PRIOR_USE"
    assert len(report.warmup_overlaps) == 2 * len(windows)
    assert {w.seed_starts for w in report.warmup_overlaps} == {(earlier_seed,), (gap_seed,)}
