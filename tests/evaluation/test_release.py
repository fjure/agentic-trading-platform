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
