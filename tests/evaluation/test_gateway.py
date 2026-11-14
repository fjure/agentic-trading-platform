from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    approval = Approval("synthetic", binding, now + timedelta(minutes=1), 1, 1, 1)
    projection = b'{"excerpt":"Synthetische Frage","confirmedContext":{}}'
    request = DispatchRequest(
        uuid4(), "b" * 64, sha256(projection).hexdigest(), Provider.OPENAI, "model",
        "profile", 1, 1, 1, "v1", now + timedelta(seconds=20),
    )
    case = EvaluationInput("DE-01", "Synthetische Frage", {})
    prepare = Mock(return_value=PreparedCase(replace(request, model="other"), projection))
    gateway = Mock()
    gateway.dispatch.return_value = ProviderResult(ResultState.OUTCOME_UNKNOWN)
    settle = Mock(return_value=None)
    supervisor = GatewaySupervisor(gateway, Mock(), prepare, settle)
    with pytest.raises(ValueError, match="EVALUATION_DISPATCH_BINDING_MISMATCH"):
        supervisor.execute(approval, 1, case)
    gateway.dispatch.assert_not_called()
    prepare.return_value = PreparedCase(request, projection)
    assert supervisor.execute(approval, 1, case).status == "timeout"
    gateway.dispatch.assert_called_once_with(request)
    settle.assert_called_once()
