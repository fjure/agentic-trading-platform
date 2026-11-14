from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from . import harness
from .harness import Approval, AttemptOutcome, RunBinding, file_sha, run_holdout


class SyntheticSupervisor:
    def __init__(self):
        self.consumed = False
        self.calls = []

    def consume(self, approval, cases):
        assert cases == ("DE-01",)
        if self.consumed:
            return False
        self.consumed = True
        return True

    def execute(self, approval, repetition, case):
        assert case.case_id == "DE-01"
        self.calls.append(repetition)
        return AttemptOutcome("timeout")


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "read_verified_holdout", cases)
    names = ("dataset", "holdout", "prompt", "schema", "scorer", "settings")
    files = {name: tmp_path / name for name in names}
    for name, path in files.items():
        path.write_text(name, encoding="utf-8")
    binding = RunBinding("test", "synthetic", "test", *(file_sha(files[name]) for name in names))
    return binding, files


def cases(*_):
    return [
        dict(caseId="DE-01", input="Welche Daten fehlen?", dialogueState={}, offlineOnly=False),
        dict(caseId="DE-02", input="Wird lokal gesperrt.", dialogueState={}, offlineOnly=True),
    ]


def approval(binding, *, limit="3"):
    return Approval(
        "synthetic", binding, datetime.now(UTC) + timedelta(minutes=1),
        max_calls=3, max_cost=Decimal(limit), cost_bound=Decimal("1"),
    )


def test_authorization_binding_blocks_every_dispatch(evaluation):
    binding, files = evaluation
    supervisor = SyntheticSupervisor()
    with pytest.raises(ValueError, match="EVALUATION_AUTHORIZATION_REQUIRED"):
        run_holdout(binding, files, None, supervisor)
    files["prompt"].write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="EVALUATION_BINDING_MISMATCH"):
        run_holdout(binding, files, approval(binding), supervisor)
    assert not supervisor.consumed and not supervisor.calls


def test_unknown_cost_holds_budget_and_prevents_replay(evaluation):
    binding, files = evaluation
    supervisor = SyntheticSupervisor()
    grant = approval(binding, limit="2")
    result = run_holdout(binding, files, grant, supervisor)
    assert result == {
        "complete": False, "calls": 2,
        "runs": {1: {"DE-01": "timeout"}, 2: {"DE-01": "timeout"}, 3: {}},
    }
    with pytest.raises(ValueError, match="EVALUATION_AUTHORIZATION_REQUIRED"):
        run_holdout(binding, files, grant, supervisor)
    assert supervisor.calls == [1, 2]
