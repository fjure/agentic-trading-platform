"""APF-R05 closed, immutable Quant approval identity."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID

import pytest

from packages.agent_research.quant_approval import ApprovalBinding, ApprovalDenied
from packages.backtesting.jobs import BacktestError
from packages.research.models import content_fingerprint
from services.control_api.quant_lab_service import QuantLabService

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
HASH = "a" * 64


@pytest.fixture
def document():
    return {
        "runId": "run-1", "runVersion": 3, "operatorId": "operator-1",
        "ideaId": "idea-1", "ideaVersion": 2, "hypothesisVersion": 1, "hypothesisHash": HASH,
        "approximation": {
            "decisionId": "decision-1", "revision": 1, "hash": HASH,
            "description": "Näherung ausdrücklich angenommen",
        },
        "experimentId": str(UUID(int=1)), "definitionVersion": 2, "definitionHash": HASH,
        "validationId": str(UUID(int=2)), "validationHash": HASH, "researchMode": "STRICT_PIT",
        "resourceBudget": {
            "schemaVersion": "quant-lab-v1", "candidateCount": 1, "reservedQueueSlots": 3,
            "snapshotRows": 500, "simulationBarSteps": 1500, "publicationBytes": 1000,
            "calendarConfirmed": True, "withinLimits": True,
        },
        "activePolicies": [{"policyId": "quant-policy-1", "revision": 3, "hash": HASH}],
        "expiresAt": "2026-11-10T12:05:00Z",
    }


def test_canonical_binding_is_immutable_and_matches_repo_fingerprint(document):
    binding = ApprovalBinding.parse(document)
    reordered = ApprovalBinding.parse(dict(reversed(list(document.items()))))
    assert binding.binding_hash == reordered.binding_hash == content_fingerprint(document)
    assert binding.expires_at == NOW + timedelta(minutes=5)
    binding.to_document()["activePolicies"].clear()
    document["activePolicies"].clear()
    assert binding.to_document()["activePolicies"]
    assert "Näherung" not in repr(binding)
    with pytest.raises(AttributeError):
        binding._canonical_text = "{}"


def test_private_start_rechecks_operator_even_on_an_identical_retry():
    authorize = Mock(side_effect=[True, False])
    service = QuantLabService(
        object(), code_version="a" * 40, cursor_key=bytes(range(32)),
        approval_engine=object(), authorize_operator=authorize,
    )
    service._approval_storage = Mock()
    batch = UUID(int=2)
    service._approval_storage.enqueue_approved.return_value = batch
    service._batch_detail = Mock(return_value={"id": str(batch)})
    request = {"actor": "local-operator", "key": "repeat-approval-key", "trace": "a" * 32}
    approval = str(UUID(int=1))
    assert service.start_approved_experiment_batch(approval, HASH, **request) == {"id": str(batch)}
    with pytest.raises(BacktestError, match="APPROVAL_REQUIRED"):
        service.start_approved_experiment_batch(approval, HASH, **request)
    service._approval_storage.enqueue_approved.assert_called_once()


def test_admission_denial_is_audited_without_changing_the_error():
    service = QuantLabService(
        object(), code_version="a" * 40, cursor_key=bytes(range(32)),
        approval_engine=object(), authorize_operator=lambda _: True,
    )
    service._approval_storage = Mock()
    service._approval_storage.enqueue_approved.side_effect = BacktestError(429, "QUEUE_EXHAUSTED")
    approval, trace = UUID(int=1), "a" * 32
    with pytest.raises(BacktestError, match="QUANT_CAPACITY_EXHAUSTED"):
        service.start_approved_experiment_batch(
            str(approval), HASH, actor="local-operator", key="repeat-approval-key", trace=trace,
        )
    service._approval_storage.record_approval_denial.assert_called_once_with(
        approval, "local-operator", trace, "QUANT_CAPACITY_EXHAUSTED",
    )


@pytest.mark.parametrize("field,value", [
    ("providerKey", "do-not-echo"),
    ("expiresAt", "2026-11-10T13:05:00+01:00"),
    ("validationId", "not-a-uuid"),
    ("runVersion", 3.0),
    ("hypothesisHash", "not-a-hash"),
    ("activePolicies", [{"policyId": "same", "revision": 1, "hash": HASH}] * 2),
])
def test_invalid_binding_is_closed_and_redacted(document, field, value):
    document[field] = value
    with pytest.raises(ApprovalDenied, match="^INVALID_APPROVAL_BINDING$"):
        ApprovalBinding.parse(document)


def test_every_public_binding_field_is_required(document):
    for field in document:
        with pytest.raises(ApprovalDenied, match="^INVALID_APPROVAL_BINDING$"):
            ApprovalBinding.parse({key: value for key, value in document.items() if key != field})


@pytest.mark.parametrize("field,value", [
    ("runVersion", 4), ("ideaVersion", 3), ("hypothesisVersion", 2),
    ("approximation", None), ("definitionHash", "b" * 64), ("validationHash", "b" * 64),
    ("researchMode", "RETROSPECTIVE_EXPLORATORY"),
    ("activePolicies", [{"policyId": "quant-policy-1", "revision": 4, "hash": HASH}]),
])
def test_changed_authoritative_binding_is_stale(document, field, value):
    binding = ApprovalBinding.parse(document)
    current = ApprovalBinding.parse(document | {field: value})
    with pytest.raises(ApprovalDenied, match="^APPROVAL_STALE$"):
        binding.require_current(
            current, expected_hash=binding.binding_hash, operator_id="operator-1", now=NOW,
        )


def test_exact_actor_hash_and_unexpired_database_time_are_required(document):
    binding = ApprovalBinding.parse(document)
    binding.require_current(
        binding, expected_hash=binding.binding_hash, operator_id="operator-1", now=NOW,
    )
    for operator, digest, now, code in [
        ("other", binding.binding_hash, NOW, "APPROVAL_REQUIRED"),
        ("operator-1", "ä" * 64, NOW, "APPROVAL_STALE"),
        ("operator-1", "b" * 64, NOW, "APPROVAL_STALE"),
        ("operator-1", binding.binding_hash, binding.expires_at, "APPROVAL_EXPIRED"),
        (
            "operator-1", binding.binding_hash, NOW.astimezone(timezone(timedelta(hours=1))),
            "APPROVAL_STALE",
        ),
    ]:
        with pytest.raises(ApprovalDenied, match=f"^{code}$"):
            binding.require_current(binding, expected_hash=digest, operator_id=operator, now=now)


@pytest.mark.parametrize("field,value,code", [
    ("calendarConfirmed", False, "APPROVAL_STALE"),
    ("simulationBarSteps", None, "APPROVAL_STALE"),
    ("withinLimits", False, "QUANT_CAPACITY_EXHAUSTED"),
])
def test_exact_binding_cannot_authorize_unknown_or_invalid_budget(document, field, value, code):
    unsafe = deepcopy(document)
    unsafe["resourceBudget"][field] = value
    binding = ApprovalBinding.parse(unsafe)
    with pytest.raises(ApprovalDenied, match=f"^{code}$"):
        binding.require_current(
            binding, expected_hash=binding.binding_hash, operator_id="operator-1", now=NOW,
        )
