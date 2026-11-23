"""Model release checks over three version-bound reports and the sealed catalog."""

from dataclasses import dataclass

from .harness import REPETITIONS, RunBinding
from .scorer import compare_versions, coverage_gaps


@dataclass(frozen=True, slots=True)
class RecordedRun:
    binding: RunBinding
    repetition: int
    approval_id: str
    score: dict


def _verified_runs(runs, binding, expected_ids, approval_verified):
    return (
        binding.repetitions == REPETITIONS
        and tuple(run.repetition for run in runs) == REPETITIONS
        and all(
            type(run.repetition) is int
            and run.binding == binding
            and isinstance(run.approval_id, str)
            and run.approval_id.strip()
            and run.score.get("mode") == "model"
            and run.score.get("complete") is True
            and isinstance(run.score.get("caseResults"), dict)
            and set(run.score["caseResults"]) == expected_ids
            and approval_verified(run) is True
            for run in runs
        )
    )


def release_violations(
    cases, binding, runs, *,
    approval_verified,
    baseline_binding=None,
    baseline_runs=None,
    allowed_changes=(),
    adjudications=None,
    adjudication_verified=None,
    initial_baseline_reason=None,
):
    """Return blockers without modifying the provider profile.

    The trusted verifiers check authority over the exact recorded runs and
    review provenance over the case-level decision and both sets of runs.
    """
    violations = list(coverage_gaps(cases))
    expected_ids = {
        case["caseId"] for case in cases
        if case["split"] == "holdout" and not case["offlineOnly"]
    }
    runs = tuple(runs)
    if not _verified_runs(runs, binding, expected_ids, approval_verified) or any(
        run.score.get("passed") is not True or run.score.get("criticalFindings") != []
        for run in runs
    ):
        return [*violations, "unauthorized_or_incomplete_repetitions"]
    if baseline_binding is None and baseline_runs is None:
        if not isinstance(initial_baseline_reason, str) or not initial_baseline_reason.strip():
            violations.append("initial_baseline_reason_missing")
        return violations
    if baseline_binding is None or baseline_runs is None:
        return [*violations, "baseline_binding_incomplete"]
    baseline_runs = tuple(baseline_runs)
    if not _verified_runs(baseline_runs, baseline_binding, expected_ids, approval_verified):
        return [*violations, "unauthorized_or_incomplete_baseline_repetitions"]

    def metadata(item):
        return {
            "datasetSha256": item.dataset_sha256, "scorerSha256": item.scorer_sha256,
            "schemaSha256": item.schema_sha256, "promptSha256": item.prompt_sha256,
            "holdoutSha256": item.holdout_sha256, "model": item.model, "api": item.api,
            "settings": item.settings_sha256, "split": "holdout",
            "repetitions": list(item.repetitions),
        }

    verified_adjudications = {
        key: decision
        for key, decision in (adjudications or {}).items()
        if adjudication_verified is not None
        and adjudication_verified(key, decision, baseline_runs, runs) is True
    }
    comparison = compare_versions(
        metadata(baseline_binding), metadata(binding),
        {run.repetition: run.score for run in baseline_runs},
        {run.repetition: run.score for run in runs},
        allowed_changes=allowed_changes, adjudications=verified_adjudications,
    )
    if comparison["passed"] is not True:
        violations.append("paired_regression")
    return violations
