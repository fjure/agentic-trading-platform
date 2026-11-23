from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from unittest.mock import Mock
from uuid import uuid4

import pytest

from services.llm_gateway.offline_gateway import DispatchRequest
from services.llm_gateway.provider_wire import Provider, ProviderResult, ResultState

from .gateway import GatewaySupervisor, PreparedCase
from .harness import Approval, EvaluationInput, RunBinding


def test_gateway_only_dispatches_matching_supervisor_projection():
    now = datetime.now(UTC)
    binding = RunBinding("profile", "model", "v1", *["a" * 64] * 6)
    approval = Approval(
        "synthetic",
        binding,
        now + timedelta(minutes=1),
        1,
        Decimal("1"),
        Decimal("1"),
    )
    projection = b'{"excerpt":"Synthetische Frage","confirmedContext":{}}'
    request = DispatchRequest(
        uuid4(),
        "b" * 64,
        sha256(projection).hexdigest(),
        Provider.OPENAI,
        "model",
        "profile",
        1,
        1,
        1,
        "v1",
        now + timedelta(seconds=20),
    )
    case = EvaluationInput("DE-01", "Synthetische Frage", {})
    prepare = Mock(
        return_value=PreparedCase(replace(request, model="other"), projection, case.case_id, 1)
    )
    gateway = Mock()
    gateway.dispatch.return_value = ProviderResult(ResultState.OUTCOME_UNKNOWN)
    settle = Mock(return_value=None)
    store = Mock()
    store.reserve.return_value = True
    store.authorize.return_value = True
    supervisor = GatewaySupervisor(gateway, store, prepare, settle)
    with pytest.raises(ValueError, match="EVALUATION_DISPATCH_BINDING_MISMATCH"):
        supervisor.execute(approval, 1, case)
    gateway.dispatch.assert_not_called()
    prepare.return_value = PreparedCase(request, projection, case.case_id, 2)
    with pytest.raises(ValueError, match="EVALUATION_DISPATCH_BINDING_MISMATCH"):
        supervisor.execute(approval, 1, case)
    gateway.dispatch.assert_not_called()
    changed_context = projection.replace(
        b'"confirmedContext":{}', b'"confirmedContext":{"market":"SPY"}',
    )
    prepare.return_value = PreparedCase(
        replace(request, projection_sha256=sha256(changed_context).hexdigest()),
        changed_context, case.case_id, 1,
    )
    with pytest.raises(ValueError, match="EVALUATION_DISPATCH_BINDING_MISMATCH"):
        supervisor.execute(approval, 1, case)
    gateway.dispatch.assert_not_called()
    store.authorize.assert_not_called()
    prepare.return_value = PreparedCase(request, projection, case.case_id, 1)
    assert supervisor.execute(approval, 1, case).status == "timeout"
    gateway.dispatch.assert_called_once_with(request)
    settle.assert_called_once()
    store.settle.assert_called_once_with(approval, 1, case.case_id, "timeout", None, None)


@pytest.mark.parametrize("authority", ["reserve", "authorize"])
def test_gateway_requires_explicit_durable_authority(authority):
    store = Mock()
    store.reserve.return_value = True
    store.authorize.return_value = True
    getattr(store, authority).return_value = None
    gateway = Mock()
    now = datetime.now(UTC)
    binding = RunBinding("profile", "model", "v1", *["a" * 64] * 6)
    approval = Approval("synthetic", binding, now + timedelta(minutes=1), 1, Decimal(1), Decimal(1))
    projection = b'{"excerpt":"Synthetische Frage","confirmedContext":{}}'
    request = DispatchRequest(
        uuid4(), "b" * 64, sha256(projection).hexdigest(), Provider.OPENAI, "model",
        "profile", 1, 1, 1, "v1", now + timedelta(seconds=20),
    )
    case = EvaluationInput("DE-01", "Synthetische Frage", {})
    prepare = Mock(return_value=PreparedCase(request, projection, case.case_id, 1))
    supervisor = GatewaySupervisor(gateway, store, prepare, Mock())
    with pytest.raises(ValueError, match="EVALUATION_RESERVATION_DENIED"):
        supervisor.execute(approval, 1, case)
    gateway.dispatch.assert_not_called()
    if authority == "reserve":
        prepare.assert_not_called()
