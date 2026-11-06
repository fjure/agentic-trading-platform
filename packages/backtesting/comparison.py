"""Comparison bindings and phase-separated Quant Lab report projections."""

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from itertools import combinations
from types import MappingProxyType
from uuid import UUID

from packages.domain.models import utc_time
from packages.research.calendar import expected_windows
from packages.research.models import BarInterval

from .experiment import (
    MAX_FOLDS,
    REFERENCE_CONFIGURATIONS,
    SCHEMA_VERSION,
    CandidateConfiguration,
    QuantExperimentManifest,
)
from .experiment_metrics import (
    CONTEXT,
    DAILY,
    LIMITATIONS,
    Metric,
    PhaseAnalytics,
    unavailable,
)
from .experiment_protocol import SelectionDecision, decimal_median
from .models import digest, fingerprint
from .simulator import RETROSPECTIVE_VALUATION_METHOD, money

COMPARISON_LIMITATIONS = (*LIMITATIONS, "PHASE_RESETS", "BAR_WEIGHTED_NOT_TIME_WEIGHTED")


@dataclass(frozen=True)
class ComparisonBinding:
    """Versioned D6 preimage split by the contract's incomparability reasons."""

    dimensions: tuple[tuple[str, object], ...]

    @classmethod
    def from_manifest(cls, manifest: QuantExperimentManifest):
        definition = manifest.definition
        return cls(
            (
                (
                    "SNAPSHOT",
                    (
                        manifest.snapshot_version,
                        manifest.snapshot_at,
                        manifest.decision_data,
                        manifest.outcome_data,
                        manifest.calendar_revisions,
                        manifest.quality_revisions,
                        manifest.coverage_revisions,
                    ),
                ),
                ("RESEARCH_MODE", definition.research_mode),
                (
                    "DATA_SELECTION_POLICY",
                    (
                        definition.data_selection_policy_version,
                        manifest.evidence_policy_version,
                        manifest.trust_fingerprint,
                        manifest.license_receipt_fingerprint,
                    ),
                ),
                ("STRATEGY_SELECTION_POLICY", definition.strategy_selection_policy.binding()),
                ("INSTRUMENT", (definition.instrument_id, definition.symbol)),
                ("FEED", definition.feed),
                ("INTERVAL", definition.interval),
                ("ADJUSTMENT", definition.adjustment),
                ("CURRENCY", definition.currency),
                ("START_CASH", definition.start_cash),
                ("COSTS", (definition.fee_usd, definition.slippage_bps, definition.quantity_step)),
                ("FILL_VERSION", manifest.fill_version),
                (
                    "PHASE_PROTOCOL",
                    (
                        definition.protocol.protocol_hash,
                        tuple(
                            (row.fold_index, row.phase, row.window, row.expected_bar_starts)
                            for row in manifest.phase_bindings
                        ),
                    ),
                ),
                (
                    "COMMON_SEED",
                    (
                        definition.warmup_count,
                        tuple(row.expected_seed_starts for row in manifest.phase_bindings),
                    ),
                ),
                ("SIMULATOR_VERSION", manifest.simulator_version),
                ("METRICS_VERSION", manifest.metrics_version),
                ("BUILD", manifest.code_version),
                ("BENCHMARK", REFERENCE_CONFIGURATIONS),
            )
        )

    @property
    def fingerprint(self):
        return fingerprint(("quant-comparison-v1", self.dimensions))

    def differences(self, other):
        right = dict(other.dimensions)
        return tuple(key for key, value in self.dimensions if value != right[key])


@dataclass(frozen=True)
class BatchComparisonReference:
    batch_id: UUID
    report_id: UUID | None
    binding: ComparisonBinding
    status: str

    def __post_init__(self):
        if self.status not in ("COMPLETE", "INCOMPLETE", "INCOMPARABLE") or (
            self.status == "COMPLETE" and self.report_id is None
        ):
            raise ValueError("complete batches require a persistent report")


def compare_batches(references: tuple[BatchComparisonReference, ...]):
    if not 1 <= len(references) <= 4 or len({row.batch_id for row in references}) != len(
        references
    ):
        raise ValueError("one to four distinct batch references required")
    groups, incomparabilities = {}, []
    for reference in references:
        if reference.status == "COMPLETE":
            key = reference.binding.fingerprint
            group = groups.setdefault(
                key,
                {
                    "comparisonFingerprint": key,
                    "batchIds": [],
                    "reportIds": [],
                },
            )
            group["batchIds"].append(str(reference.batch_id))
            group["reportIds"].append(str(reference.report_id))
    for left, right in combinations(references, 2):
        reasons = list(left.binding.differences(right.binding))
        if left.status != "COMPLETE" or right.status != "COMPLETE":
            reasons.append("INCOMPLETE_RESULTS")
        if reasons:
            incomparabilities.append(
                {
                    "leftBatchId": str(left.batch_id),
                    "rightBatchId": str(right.batch_id),
                    "reasons": reasons,
                }
            )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "batchIds": [str(row.batch_id) for row in references],
        "groups": list(groups.values()),
        "incomparabilities": incomparabilities,
        "crossBatchRanking": None,
        "limitations": list(COMPARISON_LIMITATIONS),
    }


@dataclass(frozen=True)
class BoundPhaseAnalytics:
    manifest: QuantExperimentManifest
    fold_index: int | None
    phase: str
    configuration: CandidateConfiguration
    analytics: PhaseAnalytics
    validation_input_hash: str | None = None

    def __post_init__(self):
        if self.validation_input_hash is not None:
            if self.phase != "validation":
                raise ValueError("a selection input hash requires a validation phase")
            digest(self.validation_input_hash)
        binding = next(
            (
                row
                for row in self.manifest.phase_bindings
                if (row.fold_index, row.phase) == (self.fold_index, self.phase)
            ),
            None,
        )
        result = self.analytics
        with localcontext(CONTEXT):
            start_cash = money(self.manifest.definition.start_cash)
        if (
            binding is None
            or binding.window != result.phase
            or self.configuration
            not in (*self.manifest.definition.candidates, *REFERENCE_CONFIGURATIONS)
            or self.configuration.strategy_id != result.strategy_id
            or result.start_cash != start_cash
            or (result.valuation_method == RETROSPECTIVE_VALUATION_METHOD)
            != (self.manifest.definition.research_mode == "RETROSPECTIVE_EXPLORATORY")
        ):
            raise ValueError("analytics do not match the bound phase and strategy")
        schedule = expected_windows(
            result.calendar,
            BarInterval.FIFTEEN_MINUTES,
            result.phase.start,
            result.phase.end,
            result.phase.end,
        )
        if tuple(start for start, _ in schedule.windows) != binding.expected_bar_starts:
            raise ValueError("analytics calendar does not match the manifest's expected bars")

    @property
    def candidate_id(self):
        return self.configuration.candidate_id


def _return_difference(row, reference, configuration):
    if reference is None or (
        reference.configuration != configuration
        or reference.manifest != row.manifest
        or (reference.fold_index, reference.phase) != (row.fold_index, row.phase)
        or reference.analytics.calendar != row.analytics.calendar
    ):
        return unavailable("N/A", "INCOMPARABLE_BINDINGS")
    candidate = row.analytics.metrics["netReturn"]
    benchmark = reference.analytics.metrics["netReturn"]
    if candidate.value is None:
        return candidate
    if benchmark.value is None:
        return benchmark
    return Metric(candidate.value - benchmark.value, candidate.observation_count)


def _degradation(row, paired_validation, selection):
    if (
        row.phase != "test"
        or paired_validation is None
        or selection is None
        or selection.status != "SELECTED"
        or selection.scope != f"fold:{row.fold_index}"
        or selection.selected_candidate_id != row.candidate_id
        or selection.policy != row.manifest.definition.strategy_selection_policy
        or paired_validation.phase != "validation"
        or paired_validation.fold_index != row.fold_index
        or paired_validation.manifest != row.manifest
        or paired_validation.candidate_id != row.candidate_id
        or paired_validation.validation_input_hash not in selection.validation_input_hashes
    ):
        return unavailable("N/A", "NO_PAIRED_VALIDATION", frequency=DAILY)
    validation, test = paired_validation.analytics, row.analytics
    try:
        frozen = _fold_selections(row.manifest, (selection,))[row.fold_index]
    except ValueError:
        return unavailable("N/A", "NO_PAIRED_VALIDATION", frequency=DAILY)
    winner = next(item for item in frozen["candidates"] if item["candidateId"] == row.candidate_id)
    if (
        validation.selection_values is None
        or validation.selection_values.to_wire() != winner["validationValues"][0]["selectionValues"]
    ):
        return unavailable("N/A", "NO_PAIRED_VALIDATION", frequency=DAILY)
    for phase in (validation, test):
        if phase.daily_mean is None:
            return phase.metrics["annualizedVolatility"]
    count = sum(
        phase.metrics["annualizedVolatility"].observation_count for phase in (validation, test)
    )
    return Metric(validation.daily_mean - test.daily_mean, count, DAILY)


def compare_phase(
    row: BoundPhaseAnalytics,
    *,
    buy_hold=None,
    flat=None,
    paired_validation=None,
    selection=None,
):
    """Benchmark deltas and same-fold degradation for the selected test candidate."""
    with localcontext(CONTEXT):
        metrics = dict(row.analytics.metrics)
        metrics.update(
            netReturnDifferenceBuyHold=_return_difference(
                row, buy_hold, REFERENCE_CONFIGURATIONS[1]
            ),
            netReturnDifferenceFlat=_return_difference(row, flat, REFERENCE_CONFIGURATIONS[0]),
            oosDegradation=_degradation(row, paired_validation, selection),
        )
    return replace(row.analytics, metrics=MappingProxyType(metrics))


def _descriptive_range(values, *, missing_reason="NO_SELECTED_CANDIDATE"):
    n = len(values)
    if not n:
        return {
            name: unavailable("N/A", missing_reason).to_wire(limitations=COMPARISON_LIMITATIONS)
            for name in ("median", "minimum", "maximum")
        } | {"completeFoldCount": 0}
    return {
        name: Metric(value, n).to_wire(limitations=COMPARISON_LIMITATIONS)
        for name, value in (
            ("median", decimal_median(values)),
            ("minimum", min(values)),
            ("maximum", max(values)),
        )
    } | {"completeFoldCount": n}


def _fold_selections(manifest, selections):
    count = len(manifest.definition.protocol.folds)
    by_fold = {}
    for selection in selections:
        if not isinstance(selection, SelectionDecision):
            raise TypeError("typed validation selection required")
        if selection.policy != manifest.definition.strategy_selection_policy:
            raise ValueError("selection policy mismatch")
        wire = selection.to_wire()
        candidates = sorted(row.candidate_id for row in manifest.definition.candidates)
        if (
            wire["candidateIds"] != candidates
            or [row["candidateId"] for row in wire["candidates"]] != candidates
            or (
                wire["selectedCandidateId"] is not None
                and wire["selectedCandidateId"] not in candidates
            )
        ):
            raise ValueError("selection candidate set mismatch")
        fold = wire["fold"]
        if (
            type(fold) is not int
            or not 0 <= fold < count
            or fold in by_fold
            or wire["scope"] != "FOLD"
            or selection.scope != f"fold:{fold}"
            or any(
                [cell["fold"] for cell in row["validationValues"]] != [fold]
                for row in wire["candidates"]
            )
        ):
            raise ValueError("foreign, final, or duplicate fold selection")
        by_fold[fold] = wire
    return by_fold


def _selected_test_metrics(manifest, selections, rows):
    by_fold = _fold_selections(manifest, selections)
    chosen = {
        fold: wire["selectedCandidateId"]
        for fold, wire in by_fold.items()
        if wire["status"] == "SELECTED"
    }
    seen, complete = set(), []
    for row in rows:
        if (
            row.manifest != manifest
            or row.phase != "test"
            or chosen.get(row.fold_index) != row.candidate_id
            or row.fold_index in seen
        ):
            raise ValueError("only the selected candidate test can contribute to each fold")
        seen.add(row.fold_index)
        if row.analytics.metrics["netReturn"].status == "VALID":
            complete.append(row.analytics.metrics)
    return len(chosen), complete


def build_fold_overview(manifest, selections, rows):
    """Descriptive ranges over only completed, selected candidate OOS folds."""
    selected_count, complete = _selected_test_metrics(manifest, selections, rows)
    fields = (
        "netReturn",
        "maxDrawdown",
        "investedBarRatio",
        "meanGrossExposureRatio",
        "maxGrossExposureRatio",
    )
    warnings = ["SMALL_FOLD_SAMPLE"] if len(complete) < 3 else []
    if not complete:
        warnings.append("NO_SELECTED_CANDIDATE")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "plannedFoldCount": len(manifest.definition.protocol.folds),
        "selectedFoldCount": selected_count,
        "completeOosFoldCount": len(complete),
        "positiveOosFoldCount": sum(row["netReturn"].value > 0 for row in complete),
        **{
            name: _descriptive_range(
                tuple(row[name].value for row in complete if row[name].status == "VALID"),
                missing_reason="NON_POSITIVE_EQUITY" if complete else "NO_SELECTED_CANDIDATE",
            )
            for name in fields
        },
        "activity": _fold_activity_ranges(complete),
        "warnings": warnings,
    }


def build_fold_activity_ranges(manifest, selections, rows):
    """Separate activity distributions for consumers of the phase ledger."""
    _, complete = _selected_test_metrics(manifest, selections, rows)
    return _fold_activity_ranges(complete)


def _fold_activity_ranges(complete):
    names = (
        "decisionCount",
        "fillCount",
        "buyFillCount",
        "sellFillCount",
        "entryCount",
        "exitCount",
        "endOpenPositionCount",
        "investedBarCount",
        "equityBarCount",
    )
    return {
        name: _descriptive_range(tuple(Decimal(row[name].value) for row in complete))
        for name in names
    }


def validation_sensitivity(manifest, selections, rows):
    """One cell per planned validation configuration, with rank from bound selection only."""
    by_fold = _fold_selections(manifest, selections)
    hashes_by_fold = {
        int(selection.scope.split(":")[1]): set(selection.validation_input_hashes)
        for selection in selections
    }
    candidates = {row.candidate_id: row for row in manifest.definition.candidates}
    cells = {}
    for row in rows:
        key = row.fold_index, row.candidate_id
        if (
            row.manifest != manifest
            or row.phase != "validation"
            or row.candidate_id not in candidates
            or key in cells
        ):
            raise ValueError("only unique validation cells for declared candidates are accepted")
        if (
            row.validation_input_hash is not None
            and row.fold_index in hashes_by_fold
            and row.validation_input_hash not in hashes_by_fold[row.fold_index]
        ):
            raise ValueError("validation input hash differs from the frozen fold selection")
        cells[key] = row.analytics
    fold_count = len(manifest.definition.protocol.folds)
    sensitivity = []
    for fold in range(fold_count):
        missing_cells = any(
            (phase := cells.get((fold, key))) is None or phase.selection_values is None
            for key in candidates
        )
        decisions = {
            candidate["candidateId"]: candidate
            for candidate in by_fold.get(fold, {}).get("candidates", ())
        }
        for candidate_id, configuration in sorted(candidates.items()):
            phase = cells.get((fold, candidate_id))
            exact = phase.selection_values.to_wire() if phase and phase.selection_values else None
            decision = decisions.get(candidate_id)
            if (
                decision
                and exact is not None
                and exact != decision["validationValues"][0]["selectionValues"]
            ):
                raise ValueError("validation metrics differ from the frozen selection")
            missing = unavailable("INCOMPLETE", "INCOMPLETE_BAR_SERIES")
            sensitivity.append(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "candidateId": candidate_id,
                    "configuration": {
                        "strategyId": configuration.strategy_id,
                        "version": configuration.strategy_version,
                        "parameters": dict(configuration.parameters),
                    },
                    "fold": fold,
                    "status": "COMPLETE" if exact else "INCOMPLETE",
                    "selectionValues": exact,
                    "rank": decision["rank"] if decision and not missing_cells else None,
                    **{
                        field: (phase.metrics[field] if phase else missing).to_wire()
                        for field in ("netReturn", "maxDrawdown")
                    },
                }
            )
    return sensitivity


def _check_final_selection(manifest, final, fold_selections, sensitivity):
    wire = final.to_wire()
    candidates = sorted(candidate.candidate_id for candidate in manifest.definition.candidates)
    folds = list(range(len(manifest.definition.protocol.folds)))
    if (
        wire["scope"] != "FINAL"
        or wire["fold"] is not None
        or wire["candidateIds"] != candidates
        or [candidate["candidateId"] for candidate in wire["candidates"]] != candidates
        or (
            wire["selectedCandidateId"] is not None
            and wire["selectedCandidateId"] not in candidates
        )
    ):
        raise ValueError("final selection candidate set or scope mismatch")

    by_fold = _fold_selections(manifest, fold_selections)
    frozen_cells = {
        (fold, candidate["candidateId"]): candidate["validationValues"][0]
        for fold, decision in by_fold.items()
        for candidate in decision["candidates"]
    }
    exact_cells = {
        (cell["fold"], cell["candidateId"]): cell["selectionValues"]
        for cell in sensitivity
        if cell["selectionValues"] is not None
    }
    for candidate in wire["candidates"]:
        values = candidate["validationValues"]
        if [value["fold"] for value in values] != folds:
            raise ValueError("final selection must cover every declared validation fold")
        for value in values:
            key = value["fold"], candidate["candidateId"]
            if (key in frozen_cells and value != frozen_cells[key]) or (
                key in exact_cells and value["selectionValues"] != exact_cells[key]
            ):
                raise ValueError("final selection differs from bound validation evidence")
    if len(by_fold) == len(folds) and set(final.validation_input_hashes) != {
        bound_hash
        for selection in fold_selections
        for bound_hash in selection.validation_input_hashes
    }:
        raise ValueError("final selection input hashes differ from the frozen fold selections")


def build_comparison_report(
    manifest,
    *,
    report_id,
    batch_id,
    created_at,
    selections,
    validation_rows,
    test_rows,
    research_history,
    complete,
    current_warnings=(),
):
    """Project the immutable batch report with a hash over its research evidence."""
    utc_time(created_at)
    fold_selections = tuple(row for row in selections if row.scope != "final")
    final_selections = tuple(row for row in selections if row.scope == "final")
    if len(final_selections) > 1:
        raise ValueError("duplicate final selection")
    sensitivity = validation_sensitivity(manifest, fold_selections, validation_rows)
    overview = build_fold_overview(manifest, fold_selections, test_rows)
    if final_selections:
        _check_final_selection(manifest, final_selections[0], fold_selections, sensitivity)
    complete = (
        complete
        and len(fold_selections) == overview["plannedFoldCount"]
        and bool(final_selections)
        and all(row.status != "INCOMPLETE" for row in selections)
        and all(cell["status"] == "COMPLETE" for cell in sensitivity)
        and overview["selectedFoldCount"] == overview["completeOosFoldCount"]
    )
    if not complete:
        for cell in sensitivity:
            cell["rank"] = None
    policy = manifest.definition.strategy_selection_policy
    if any(row.policy != policy for row in selections):
        raise ValueError("selection policy mismatch")
    if not 1 <= len(selections) <= MAX_FOLDS + 1:
        raise ValueError("at least one bound selection report required")
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "id": str(report_id),
        "batchId": str(batch_id),
        "createdAt": created_at.isoformat().replace("+00:00", "Z"),
        "comparisonFingerprint": ComparisonBinding.from_manifest(manifest).fingerprint,
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "strategySelectionPolicy": dict(
            zip(
                (
                    "policyId",
                    "maxDrawdown",
                    "minValidationSessions",
                    "minimumEntryCount",
                    "minimumInvestedBarRatio",
                ),
                policy.binding()[:5],
                strict=True,
            )
        ),
        "strategySelectionPolicyHash": policy.policy_hash,
        "selections": [row.to_wire() for row in sorted(selections, key=lambda row: row.scope)],
        "foldOverview": overview,
        "sensitivity": sensitivity,
        "researchHistory": research_history,
        "reasons": [] if complete else ["INCOMPLETE_CANDIDATE_SET"],
        "limitations": list(COMPARISON_LIMITATIONS),
    }
    evidence = {
        key: value for key, value in body.items() if key not in ("id", "batchId", "createdAt")
    }
    evidence["selections"] = [
        {key: value for key, value in selection.items() if key != "frozenAt"}
        for selection in body["selections"]
    ]
    return body | {"reportHash": fingerprint(evidence), "currentWarnings": list(current_warnings)}
