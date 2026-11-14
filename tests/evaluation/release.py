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


def release_violations(
    cases, binding, runs, *,
    approval_verified,
    baseline_binding=None,
    baseline_runs=None,
    allowed_changes=(),
    adjudications=None,
    initial_baseline_reason=None,
):
    """Return blockers without modifying the provider profile."""
    violations = list(coverage_gaps(cases))
    expected_ids = {
        case["caseId"] for case in cases
        if case["split"] == "holdout" and not case["offlineOnly"]
    }
    if (
        tuple(run.repetition for run in runs) != REPETITIONS
        or any(
            run.binding != binding or not run.approval_id
            or approval_verified(run) is not True
            or run.score.get("mode") != "model"
            or run.score.get("passed") is not True
            or run.score.get("complete") is not True
            or run.score.get("criticalFindings") != []
            or set(run.score.get("caseResults", {})) != expected_ids
            for run in runs
        )
    ):
        return [*violations, "unauthorized_or_incomplete_repetitions"]
    if baseline_binding is None and baseline_runs is None:
        if not isinstance(initial_baseline_reason, str) or not initial_baseline_reason.strip():
            violations.append("initial_baseline_reason_missing")
        return violations
    if baseline_binding is None or baseline_runs is None:
        return [*violations, "baseline_binding_incomplete"]
    def metadata(item):
        return {
            "datasetSha256": item.dataset_sha256, "scorerSha256": item.scorer_sha256,
            "schemaSha256": item.schema_sha256, "promptSha256": item.prompt_sha256,
            "holdoutSha256": item.holdout_sha256, "model": item.model, "api": item.api,
            "settings": item.settings_sha256, "split": "holdout",
            "repetitions": list(item.repetitions),
        }

    comparison = compare_versions(
        metadata(baseline_binding), metadata(binding),
        {run.repetition: run.score for run in baseline_runs},
        {run.repetition: run.score for run in runs},
        allowed_changes=allowed_changes, adjudications=adjudications,
    )
    if comparison["passed"] is not True:
        violations.append("paired_regression")
    return violations
