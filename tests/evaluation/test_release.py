from dataclasses import replace

from tests.evaluation import release
from tests.evaluation.harness import REPETITIONS, RunBinding
from tests.evaluation.release import RecordedRun, release_violations


def test_release_requires_every_authorized_model_repetition(monkeypatch):
    monkeypatch.setattr(release, "coverage_gaps", lambda cases: [])
    case = dict(caseId="DE-01", split="holdout", offlineOnly=False)
    binding = RunBinding("profile", "model", "v1", *["a" * 64] * 6)
    score = dict(
        mode="model", passed=True, complete=True, criticalFindings=[], caseResults={"DE-01": {}},
    )
    runs = tuple(RecordedRun(binding, repeat, "operator-proof", score) for repeat in REPETITIONS)
    assert release_violations(
        [case], binding, runs[:-1], approval_verified=lambda run: True,
    ) == ["unauthorized_or_incomplete_repetitions"]
    assert release_violations(
        [case], binding, runs, approval_verified=lambda run: run.repetition != 2,
    ) == ["unauthorized_or_incomplete_repetitions"]
    assert release_violations(
        [case], binding, runs, approval_verified=lambda run: True,
    ) == ["initial_baseline_reason_missing"]
    assert release_violations(
        [case], binding, runs, approval_verified=lambda run: True,
        initial_baseline_reason="First approved model baseline",
    ) == []


def _paired_fixture(monkeypatch):
    monkeypatch.setattr(release, "coverage_gaps", lambda cases: [])
    cases = [{"caseId": "DE-01", "split": "holdout", "offlineOnly": False}]
    baseline = RunBinding("profile", "baseline", "v1", *["a" * 64] * 6)
    candidate = replace(baseline, model="candidate")

    def runs(binding, *, degraded=False):
        return tuple(
            RecordedRun(binding, repeat, f"{binding.model}-{repeat}", {
                "mode": "model",
                "passed": True,
                "complete": True,
                "criticalFindings": [],
                "caseResults": {"DE-01": {
                    "version": 1, "group": "beginner", "applicable": True,
                    "critical": False, "status": "answered", "correct": True,
                    "questions": {"fp": int(degraded and repeat == 2), "fn": 0},
                    "abstentionCorrect": None,
                }},
            })
            for repeat in REPETITIONS
        )

    return cases, baseline, candidate, runs


def test_release_rejects_unverified_baseline_repetitions(monkeypatch):
    cases, baseline, candidate, runs = _paired_fixture(monkeypatch)
    prior = runs(baseline)

    def check(baseline_runs):
        return release_violations(
            cases,
            candidate,
            runs(candidate),
            approval_verified=lambda run: run.approval_id
            == f"{run.binding.model}-{run.repetition}",
            baseline_binding=baseline,
            baseline_runs=baseline_runs,
            allowed_changes=("model",),
        )

    assert check(prior) == []
    invalid_repetitions = (
        prior[:-1],
        (prior[0], prior[1], prior[1], prior[2]),
        (*prior[:-1], replace(prior[-1], repetition=True)),
        (*prior[:-1], replace(prior[-1], binding=candidate, approval_id="candidate-3")),
        (*prior[:-1], replace(prior[-1], approval_id="stale")),
        (*prior[:-1], replace(prior[-1], score=prior[-1].score | {"mode": "offline"})),
        (*prior[:-1], replace(prior[-1], score=prior[-1].score | {"complete": False})),
        (*prior[:-1], replace(prior[-1], score=prior[-1].score | {"caseResults": {}})),
    )
    for invalid in invalid_repetitions:
        assert check(invalid) == ["unauthorized_or_incomplete_baseline_repetitions"]


def test_release_requires_bound_independent_regression_decision(monkeypatch):
    cases, baseline, candidate, runs = _paired_fixture(monkeypatch)
    key = "2:DE-01:questions"
    decision = {
        "approved": True,
        "independent": True,
        "reviewer": "reviewer",
        "reason": "Documented adjudication",
    }
    kwargs = {
        "approval_verified": lambda run: True,
        "baseline_binding": baseline,
        "baseline_runs": runs(baseline),
        "allowed_changes": ("model",),
        "adjudications": {key: decision},
    }
    candidate_runs = runs(candidate, degraded=True)
    assert release_violations(cases, candidate, candidate_runs, **kwargs) == ["paired_regression"]

    def verified(current_key, current_decision, before, after):
        return (current_key, current_decision, before, after) == (
            key, decision, runs(baseline), candidate_runs,
        )

    assert release_violations(
        cases, candidate, candidate_runs, **kwargs, adjudication_verified=verified,
    ) == []
    kwargs["adjudications"] = {key: decision | {"reviewer": "unverified"}}
    assert release_violations(
        cases, candidate, candidate_runs, **kwargs, adjudication_verified=verified,
    ) == ["paired_regression"]
