from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID

import pytest

from packages.backtesting.comparison import (
    BatchComparisonReference,
    BoundPhaseAnalytics,
    ComparisonBinding,
    build_comparison_report,
    build_fold_activity_ranges,
    build_fold_overview,
    compare_batches,
    compare_phase,
    validation_sensitivity,
)
from packages.backtesting.experiment import (
    REFERENCE_CONFIGURATIONS,
    ExperimentFold,
    PhaseProtocol,
    PhaseSeedBinding,
    PhaseWindow,
    SearchSpace,
)
from packages.backtesting.experiment_metrics import analyze_phase
from packages.backtesting.experiment_protocol import (
    SelectionReliability,
    ValidationSelectionInput,
    select_validation,
)
from packages.backtesting.models import fingerprint
from tests.contracts.test_quant_lab_examples import fixture, validator
from tests.unit.test_quant_lab_metrics import (
    STEP,
    D,
    bar_starts,
    calendar_for_days,
    daily_ledger,
    full_window,
    ledger,
)
from tests.unit.test_quant_lab_models import definition, quant_manifest

STRICT = SelectionReliability("STRICT_PIT", "STRICT_PIT_EVIDENCE_COMPLETE", "PASS")
FLAT, BUY_HOLD = REFERENCE_CONFIGURATIONS


def comparison_fixture(*, two_candidates=False, start_cash="100000000", validation_days=20):
    calendars = (
        calendar_for_days(1, first=date(2024, 1, 2)),
        calendar_for_days(validation_days, first=date(2024, 1, 3)),
        calendar_for_days(21, first=date(2024, 3, 1)),
        calendar_for_days(1, first=date(2024, 5, 1)),
    )
    windows = tuple(map(full_window, calendars))
    protocol = PhaseProtocol((ExperimentFold(*windows[:3]),), windows[-1])
    changes = {"protocol": protocol, "start_cash": D(start_cash)}
    if two_candidates:
        changes["search_spaces"] = (
            SearchSpace("flat-v1", ()),
            SearchSpace("ema-cross-v1", (("fast", (2,)), ("slow", (3,)))),
        )
    experiment = definition(**changes)
    manifest = quant_manifest(experiment)
    phases = tuple(
        PhaseSeedBinding(
            fold,
            name,
            window,
            tuple(window.start - i * STEP for i in range(experiment.warmup_count, 0, -1)),
            bar_starts(calendar, window),
        )
        for (fold, name, _, window), calendar in zip(protocol.phases, calendars, strict=True)
    )
    return replace(manifest, phase_bindings=phases), dict(
        zip(("training", "validation", "test", "holdout"), calendars, strict=True)
    )


def phase_row(manifest, calendars, phase, configuration, result=None):
    calendar = calendars[phase]
    window = full_window(calendar)
    if result is None:
        result = ledger(
            bar_starts(calendar, window),
            start_cash=manifest.definition.start_cash,
            strategy=configuration.strategy_id,
        )
    return BoundPhaseAnalytics(
        manifest,
        None if phase == "holdout" else 0,
        phase,
        configuration,
        analyze_phase(result, calendar, window),
        fingerprint(("fixture", configuration.candidate_id, result.result_hash))
        if phase == "validation"
        else None,
    )


def selection(manifest, rows, scope="fold:0", *, frozen_at=None):
    inputs = tuple(
        ValidationSelectionInput(
            row.candidate_id,
            0,
            row.validation_input_hash,
            manifest.manifest_hash,
            row.analytics.selection_values,
            row.analytics.selection_values is not None,
            True,
            True,
            STRICT,
        )
        for row in rows
    )
    return select_validation(
        manifest.definition.strategy_selection_policy,
        tuple(candidate.candidate_id for candidate in manifest.definition.candidates),
        inputs,
        scope=scope,
        frozen_at=frozen_at or manifest.snapshot_at,
        fold_indices=(0,) if scope == "final" else None,
    )


def test_compare_batches_distinguishes_bindings_without_a_common_rank():
    manifest, _ = comparison_fixture()
    alternative = replace(
        manifest,
        definition=replace(
            manifest.definition,
            strategy_selection_policy=replace(
                manifest.definition.strategy_selection_policy,
                minimum_entry_count=1,
            ),
        ),
    )
    different = replace(
        manifest,
        snapshot_version=6,
        code_version="0" * 40,
        definition=replace(manifest.definition, fee_usd=D(1)),
    )
    base = ComparisonBinding.from_manifest(manifest)
    other = ComparisonBinding.from_manifest(alternative)
    assert base.fingerprint != other.fingerprint
    assert base.differences(other) == ("STRATEGY_SELECTION_POLICY",)
    assert base.differences(ComparisonBinding.from_manifest(different)) == (
        "SNAPSHOT",
        "COSTS",
        "BUILD",
    )
    phases = manifest.phase_bindings
    fewer_bars = replace(
        manifest,
        phase_bindings=(
            replace(phases[0], expected_bar_starts=phases[0].expected_bar_starts[:-1]),
            *phases[1:],
        ),
    )
    assert base.differences(ComparisonBinding.from_manifest(fewer_bars)) == ("PHASE_PROTOCOL",)
    refs = (
        BatchComparisonReference(UUID(int=1), UUID(int=11), base, "COMPLETE"),
        BatchComparisonReference(UUID(int=2), UUID(int=12), base, "COMPLETE"),
        BatchComparisonReference(UUID(int=3), UUID(int=13), other, "COMPLETE"),
        BatchComparisonReference(UUID(int=4), None, base, "INCOMPLETE"),
    )
    compared = compare_batches(refs)
    assert [len(group["batchIds"]) for group in compared["groups"]] == [2, 1]
    assert compared["crossBatchRanking"] is None
    assert compared["incomparabilities"][0]["reasons"] == ["STRATEGY_SELECTION_POLICY"]
    assert compared["incomparabilities"][1]["reasons"] == ["INCOMPLETE_RESULTS"]
    for standalone in (False, True):
        validator("QuantLabExperimentComparison", standalone).validate(compared)


def test_same_fold_degradation_uses_different_daily_sample_sizes_and_separate_benchmarks():
    manifest, calendars = comparison_fixture()
    candidate = manifest.definition.candidates[0]
    start_cash = manifest.definition.start_cash
    validation = phase_row(
        manifest,
        calendars,
        "validation",
        candidate,
        daily_ledger(calendars["validation"], [".01", ".03"] * 10),
    )
    test = phase_row(
        manifest,
        calendars,
        "test",
        candidate,
        daily_ledger(calendars["test"], ["-.01"] * 21),
    )
    flat = phase_row(manifest, calendars, "test", FLAT)
    buy_hold = phase_row(
        manifest,
        calendars,
        "test",
        BUY_HOLD,
        daily_ledger(
            calendars["test"], ["0"] * 21, strategy=BUY_HOLD.strategy_id, start_cash=start_cash
        ),
    )
    selected = selection(manifest, (validation,))
    result = compare_phase(
        test,
        flat=flat,
        buy_hold=buy_hold,
        paired_validation=validation,
        selection=selected,
    )
    wire = result.to_wire()
    assert selected.status == "SELECTED"
    assert wire["oosDegradation"]["value"] == "0.030000000000"
    assert wire["oosDegradation"]["observationCount"] == 41
    assert wire["netReturnDifferenceFlat"]["value"] == wire["netReturn"]["value"]
    assert wire["netReturnDifferenceBuyHold"]["value"] == wire["netReturn"]["value"]
    assert "IEX_PARTIAL_MARKET" in wire["netReturn"]["limitations"]
    unbound = replace(validation, validation_input_hash=None)
    different_link = replace(validation, validation_input_hash="a" * 64)
    different_values = replace(
        phase_row(manifest, calendars, "validation", candidate),
        validation_input_hash=validation.validation_input_hash,
    )
    for invalid in (unbound, different_link, different_values):
        metric = compare_phase(
            test,
            paired_validation=invalid,
            selection=selected,
        ).to_wire()["oosDegradation"]
        assert (metric["status"], metric["reasonCode"], metric["value"]) == (
            "N/A",
            "NO_PAIRED_VALIDATION",
            None,
        )
    holdout = phase_row(manifest, calendars, "holdout", candidate)
    unavailable = compare_phase(
        holdout,
        flat=flat,
        paired_validation=validation,
        selection=selected,
    ).to_wire()
    assert unavailable["oosDegradation"]["reasonCode"] == "NO_PAIRED_VALIDATION"
    assert unavailable["netReturnDifferenceFlat"]["reasonCode"] == "INCOMPARABLE_BINDINGS"
    for standalone in (False, True):
        validator("QuantLabPerformanceMetrics", standalone).validate(wire)


@pytest.mark.parametrize(
    ("bars", "entries", "gain", "low_exposure", "few", "flat_like"),
    [
        (99, 1, "10", False, True, False),
        (100, 1, "10000", True, True, False),
        (101, 1, "10", True, True, False),
        (99, 1, ".09999999", False, True, True),
        (99, 1, ".10000000", False, True, True),
        (99, 1, ".10000001", False, True, False),
        (99, 1, "-.10000000", False, True, True),
        (99, 1, "-.10000001", False, True, False),
        (99, 2, ".10000000", False, True, True),
        (99, 3, ".10000000", False, False, True),
    ],
)
def test_descriptive_warnings_use_actual_activity_and_exact_thresholds(
    bars,
    entries,
    gain,
    low_exposure,
    few,
    flat_like,
):
    manifest, calendars = comparison_fixture(start_cash="1000")
    phase = "validation"
    calendar = calendars[phase]
    starts = bar_starts(calendar, full_window(calendar))[:bars]
    window = PhaseWindow(starts[0], starts[-1] + STEP)
    bindings = list(manifest.phase_bindings)
    definition = manifest.definition
    first = definition.protocol.folds[0]
    protocol = replace(definition.protocol, folds=(replace(first, validation=window),))
    bindings[1] = replace(bindings[1], window=window, expected_bar_starts=starts)
    manifest = replace(
        manifest,
        definition=replace(definition, protocol=protocol),
        phase_bindings=tuple(bindings),
    )
    candidate = manifest.definition.candidates[0]
    activity = {bars - 1: ("BUY", "1", "100", "1", "1")}
    for entry in range(entries - 1):
        activity[2 + 4 * entry] = ("BUY", "1", "100", "0", "0")
        activity[4 + 4 * entry] = ("SELL", "1", "100", "0", "0")
    result = ledger(
        starts,
        transactions=activity,
        closes=[D(100)] * (bars - 1) + [D(101) + D(gain)],
    )
    analytics = BoundPhaseAnalytics(
        manifest, 0, phase, candidate, analyze_phase(result, calendar, window)
    )
    flat = BoundPhaseAnalytics(
        manifest,
        0,
        phase,
        FLAT,
        analyze_phase(ledger(starts, strategy="flat-v1"), calendar, window),
    )
    wire = compare_phase(analytics, flat=flat).to_wire()
    warnings = wire["activityWarnings"]
    assert warnings["status"] == "AVAILABLE"
    assert ("LOW_BAR_EXPOSURE" in warnings["codes"]) is low_exposure
    assert ("FEW_POSITION_ENTRIES" in warnings["codes"]) is few
    assert ("FLAT_LIKE_NET_RETURN" in warnings["codes"]) is flat_like
    assert "NO_FILLS" not in warnings["codes"]
    assert "NO_EXPOSURE" not in warnings["codes"]
    assert wire["fees"]["value"] == "1.00000000"
    assert wire["activity"]["entryCount"]["value"] == entries
    if gain == "10000":
        assert wire["netReturn"]["value"] == "10.000000000000"


def test_validation_only_sensitivity_keeps_missing_cells_and_removes_unbound_rank():
    manifest, calendars = comparison_fixture(two_candidates=True)
    rows = tuple(
        phase_row(manifest, calendars, "validation", candidate)
        for candidate in manifest.definition.candidates
    )
    chosen = selection(manifest, rows)
    cells = validation_sensitivity(manifest, (chosen,), rows)
    assert sorted(cell["rank"] for cell in cells) == [1, 2]
    assert all(cell["netReturn"]["value"] == "0.000000000000" for cell in cells)
    for cell in cells:
        validator("QuantLabSensitivityCell").validate(cell)
    incomplete = validation_sensitivity(manifest, (chosen,), rows[:1])
    assert all(cell["rank"] is None for cell in incomplete)
    missing = next(cell for cell in incomplete if cell["status"] == "INCOMPLETE")
    assert missing["selectionValues"] is None and missing["netReturn"]["value"] is None
    foreign = phase_row(manifest, calendars, "test", manifest.definition.candidates[0])
    with pytest.raises(ValueError, match="unique validation cells"):
        validation_sensitivity(manifest, (chosen,), (foreign,))


def test_fold_overview_keeps_selection_and_missing_oos_separate():
    manifest, calendars = comparison_fixture()
    candidate = manifest.definition.candidates[0]
    validation = phase_row(manifest, calendars, "validation", candidate)
    chosen = selection(manifest, (validation,))
    test = phase_row(manifest, calendars, "test", candidate)
    complete = build_fold_overview(manifest, (chosen,), (test,))
    assert complete["selectedFoldCount"] == complete["completeOosFoldCount"] == 1
    assert complete["netReturn"]["median"]["value"] == "0.000000000000"
    assert complete["warnings"] == ["SMALL_FOLD_SAMPLE"]
    activity = build_fold_activity_ranges(manifest, (chosen,), (test,))
    assert activity["entryCount"]["median"]["value"] == "0.000000000000"
    assert activity["decisionCount"]["median"]["value"] == "534.000000000000"
    validator("QuantLabFoldOverview").validate(complete)
    missing = build_fold_overview(manifest, (chosen,), ())
    assert missing["selectedFoldCount"] == 1 and missing["completeOosFoldCount"] == 0
    assert missing["netReturn"]["median"]["reasonCode"] == "NO_SELECTED_CANDIDATE"
    assert missing["netReturn"]["median"]["value"] is None
    holdout = phase_row(manifest, calendars, "holdout", candidate)
    with pytest.raises(ValueError, match="selected candidate test"):
        build_fold_overview(manifest, (chosen,), (holdout,))


def test_complete_nonselection_report_and_later_warnings_leave_report_hash_fixed():
    manifest, calendars = comparison_fixture(validation_days=19)
    candidate = manifest.definition.candidates[0]
    validation = phase_row(manifest, calendars, "validation", candidate)
    fold = selection(manifest, (validation,))
    final = selection(manifest, (validation,), "final")
    assert fold.status == final.status == "NO_ELIGIBLE_CANDIDATE"
    args = {
        "report_id": UUID(int=42),
        "batch_id": UUID(int=43),
        "created_at": manifest.snapshot_at,
        "selections": (fold, final),
        "validation_rows": (validation,),
        "test_rows": (),
        "research_history": fixture("history-no-registered-prior-use"),
        "complete": True,
    }
    report = build_comparison_report(manifest, **args)
    assert report["status"] == "COMPLETE"
    assert report["sensitivity"][0]["rank"] is None
    assert report["foldOverview"]["selectedFoldCount"] == 0
    updated = build_comparison_report(
        manifest,
        **args,
        current_warnings=(
            {
                "observedAt": report["createdAt"],
                "code": "LATER_REGISTERED_OVERLAP",
                "relatedBatchId": str(UUID(int=44)),
            },
        ),
    )
    assert updated["reportHash"] == report["reportHash"]
    moved_at = manifest.snapshot_at + timedelta(minutes=1)
    moved = build_comparison_report(
        manifest,
        **(
            args
            | {
                "report_id": UUID(int=142),
                "batch_id": UUID(int=143),
                "created_at": moved_at,
                "selections": (
                    selection(manifest, (validation,), frozen_at=moved_at),
                    selection(manifest, (validation,), "final", frozen_at=moved_at),
                ),
            }
        ),
    )
    assert all(moved[key] != report[key] for key in ("id", "batchId", "createdAt", "selections"))
    for original, relocated in zip(report["selections"], moved["selections"], strict=True):
        assert original["frozenAt"] != relocated["frozenAt"]
        assert original["selectionHash"] == relocated["selectionHash"]
    assert moved["reportHash"] == report["reportHash"]
    history = args["research_history"] | {"registryHash": "b" * 64}
    rebound = build_comparison_report(manifest, **(args | {"research_history": history}))
    assert rebound["reportHash"] != report["reportHash"]
    validator("QuantLabComparisonReport").validate(updated)


def test_final_report_rejects_foreign_winners_and_nonidentical_validation_cells():
    manifest, calendars = comparison_fixture()
    candidate = manifest.definition.candidates[0]
    validation = phase_row(manifest, calendars, "validation", candidate)
    fold = selection(manifest, (validation,))
    test = phase_row(manifest, calendars, "test", candidate)
    args = {
        "report_id": UUID(int=42),
        "batch_id": UUID(int=43),
        "created_at": manifest.snapshot_at,
        "validation_rows": (validation,),
        "test_rows": (test,),
        "research_history": fixture("history-no-registered-prior-use"),
        "complete": True,
    }
    assert (
        build_comparison_report(
            manifest,
            selections=(fold, selection(manifest, (validation,), "final")),
            **args,
        )["status"]
        == "COMPLETE"
    )

    foreign = "a" * 64
    foreign_final = select_validation(
        manifest.definition.strategy_selection_policy,
        (foreign,),
        (
            ValidationSelectionInput(
                foreign,
                0,
                "b" * 64,
                manifest.manifest_hash,
                validation.analytics.selection_values,
                True,
                True,
                True,
                STRICT,
            ),
        ),
        scope="final",
        frozen_at=manifest.snapshot_at,
        fold_indices=(0,),
    )
    assert foreign_final.selected_candidate_id == foreign
    with pytest.raises(ValueError, match="final selection candidate set"):
        build_comparison_report(manifest, selections=(fold, foreign_final), **args)

    cell = ValidationSelectionInput(
        candidate.candidate_id,
        0,
        validation.validation_input_hash,
        manifest.manifest_hash,
        validation.analytics.selection_values,
        True,
        True,
        True,
        STRICT,
    )
    extra_fold_final = select_validation(
        manifest.definition.strategy_selection_policy,
        (candidate.candidate_id,),
        (cell, replace(cell, fold_index=1, input_hash="d" * 64)),
        scope="final",
        frozen_at=manifest.snapshot_at,
        fold_indices=(0, 1),
    )
    with pytest.raises(ValueError, match="every declared validation fold"):
        build_comparison_report(manifest, selections=(fold, extra_fold_final), **args)

    calendar = calendars["validation"]
    alternate = phase_row(
        manifest,
        calendars,
        "validation",
        candidate,
        daily_ledger(calendar, [".01"] * len(calendar.sessions)),
    )
    with pytest.raises(ValueError, match="final selection differs from bound validation evidence"):
        build_comparison_report(
            manifest,
            selections=(fold, selection(manifest, (alternate,), "final")),
            **args,
        )
    other_link = replace(validation, validation_input_hash="c" * 64)
    with pytest.raises(ValueError, match="final selection input hashes differ"):
        build_comparison_report(
            manifest,
            selections=(fold, selection(manifest, (other_link,), "final")),
            **args,
        )


def test_ledger_drawdown_values_on_both_sides_of_one_display_boundary():
    manifest, calendars = comparison_fixture()
    candidate = manifest.definition.candidates[0]
    calendar = calendars["validation"]
    starts = bar_starts(calendar, full_window(calendar))

    for end, expected in (
        ("90000000.00004", "SELECTED"),
        ("89999999.99996", "NO_ELIGIBLE_CANDIDATE"),
    ):
        result = ledger(
            starts,
            closes=[D("100000000")] * (len(starts) - 1) + [D(end)],
            transactions={2: ("BUY", "1", "100000000", "0", "0")},
            start_cash="100000000",
        )
        validation = phase_row(manifest, calendars, "validation", candidate, result)
        assert validation.analytics.to_wire()["maxDrawdown"]["value"] == "0.100000000000"
        decision = selection(manifest, (validation,))
        assert decision.status == expected
        assert (
            decision.to_wire()["candidates"][0]["validationValues"][0]["selectionValues"]
            == validation.analytics.selection_values.to_wire()
        )


def test_exact_two_thirds_activity_fails_display_equal_explicit_minimum():
    manifest, calendars = comparison_fixture(validation_days=21)
    calendar = replace(
        calendars["validation"],
        sessions=tuple(
            replace(session, closes_at=session.opens_at + 26 * STEP)
            for session in calendars["validation"].sessions
        ),
    )
    window = full_window(calendar)
    starts = bar_starts(calendar, window)
    candidate = manifest.definition.candidates[0]
    policy = replace(
        manifest.definition.strategy_selection_policy,
        minimum_invested_bar_ratio="0.666666666667",
    )
    phases = manifest.phase_bindings
    manifest = replace(
        manifest,
        definition=replace(manifest.definition, strategy_selection_policy=policy),
        phase_bindings=(phases[0], replace(phases[1], expected_bar_starts=starts), *phases[2:]),
    )
    calendars["validation"] = calendar
    result = ledger(
        starts,
        start_cash="100000000",
        transactions={len(starts) // 3: ("BUY", "1", "100", "0", "0")},
    )
    validation = phase_row(manifest, calendars, "validation", candidate, result)
    assert (
        validation.analytics.to_wire()["exposure"]["investedBarRatio"]["value"] == "0.666666666667"
    )
    assert validation.analytics.selection_values.to_wire()["investedBarRatio"] == (
        "0." + "6" * 37 + "7"
    )
    rejected = selection(manifest, (validation,))
    assert rejected.status == "NO_ELIGIBLE_CANDIDATE"
    assert rejected.to_wire()["candidates"][0]["reasons"] == ["MINIMUM_INVESTED_BAR_RATIO_NOT_MET"]
