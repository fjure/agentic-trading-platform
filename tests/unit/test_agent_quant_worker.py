from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.backtesting.models import BacktestBlocked, BacktestBudgetExceeded
from services.worker import quant_lab


@pytest.fixture
def approved_batch(monkeypatch):
    worker = quant_lab.QuantLabWorker(None, owner=uuid4(), code_version="fixture")
    lease = SimpleNamespace(batch_id=uuid4(), row={
        "code_version": "fixture", "definition_id": uuid4(), "slots": 4,
        "bar_steps": 0, "publication_bytes": 100,
    })
    budget = {
        "calendarConfirmed": True, "withinLimits": True, "candidateCount": 2,
        "reservedQueueSlots": 4, "snapshotRows": 3, "simulationBarSteps": 14,
        "publicationBytes": 100,
    }
    receipt = {
        "license_body": {"status": "CONFIRMED"}, "trust_body": {"decisions": [object()]},
    }
    phases = tuple(
        SimpleNamespace(
            binding=SimpleNamespace(phase=phase),
            dataset=SimpleNamespace(steps=[object()], warmup=[object()]),
        )
        for phase in ("training", "holdout")
    )
    inputs = SimpleNamespace(manifest=object(), phases=phases, references={"one": object()})
    worker.storage = Mock()
    worker.storage.approval_budget.return_value = budget
    worker.storage.definition.return_value = {
        "body": {}, "predecessor_id": None, "definition_version": 1,
    }
    worker.storage.source_receipt.return_value = receipt
    worker._gate = Mock(side_effect=[None, "TRUST_REVOKED"])
    worker._bind = Mock()
    worker._run_phase = Mock()
    monkeypatch.setattr(quant_lab, "definition_domain", Mock(return_value=SimpleNamespace(
        queue_slots=4, candidates=(object(), object()),
    )))
    monkeypatch.setattr(quant_lab, "load_quant_inputs", Mock(return_value=inputs))
    monkeypatch.setattr(quant_lab, "QuantValidationContext", Mock())
    return worker, lease, budget, receipt


def test_current_source_gate_runs_again_after_binding_and_before_simulation(approved_batch):
    worker, lease, _, _ = approved_batch
    gate_and_bind = Mock()
    gate_and_bind.attach_mock(worker._gate, "gate")
    gate_and_bind.attach_mock(worker._bind, "bind")
    with pytest.raises(BacktestBlocked, match="TRUST_REVOKED"):
        worker._process(lease, Mock())
    assert [call[0] for call in gate_and_bind.mock_calls] == ["gate", "bind", "gate"]
    worker._run_phase.assert_not_called()


@pytest.mark.parametrize(("ceiling", "limit"), [
    ("snapshotRows", 2),
    ("simulationBarSteps", 13),
    ("publicationBytes", 99),
    ("candidateCount", 1),
    ("reservedQueueSlots", 3),
    ("calendarConfirmed", False),
])
def test_approved_ceiling_blocks_before_binding(approved_batch, ceiling, limit):
    worker, lease, budget, _ = approved_batch
    budget[ceiling] = limit
    with pytest.raises(BacktestBudgetExceeded):
        worker._process(lease, Mock())
    worker._bind.assert_not_called()
    worker._run_phase.assert_not_called()


def test_missing_pinned_license_blocks_before_loading_data(approved_batch):
    worker, lease, _, receipt = approved_batch
    receipt["license_body"]["status"] = "UNKNOWN"
    with pytest.raises(BacktestBlocked, match="LICENSE_UNKNOWN"):
        worker._process(lease, Mock())
    quant_lab.load_quant_inputs.assert_not_called()
