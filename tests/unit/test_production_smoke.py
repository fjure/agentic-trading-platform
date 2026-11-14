from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from packages.agent_research.budget import ModelOutcome
from packages.agent_research.runs import RunState
from services.llm_gateway.probe import PROBE_PROJECTION, production_probe_capability
from services.llm_gateway.provider_wire import ProviderResult, ResultState
from services.runtime.research_smoke import ProductionSmoke
from tests.unit.test_llm_provider_http import PROFILE


def test_smoke_unknown_usage_remains_held_and_is_not_retried():
    authority, gateway = Mock(), Mock()
    deadline = datetime.now(UTC) + timedelta(seconds=20)
    grant = SimpleNamespace(grant_id=uuid4(), commitment="a" * 64, expires_at=deadline)
    authority.lookup.return_value = grant
    authority.reserve.return_value = SimpleNamespace(status="RESERVED")
    work = SimpleNamespace(input_ref=grant.grant_id.hex, dispatch_deadline=deadline)
    capability = production_probe_capability(PROFILE, 32)
    smoke = ProductionSmoke(authority, gateway, capability, Mock())
    claim = Mock()

    def send(request):
        assert request.purpose == "PROBE"
        assert request.projection_sha256 == sha256(PROBE_PROJECTION).hexdigest()
        assert smoke.consume(request).projection == PROBE_PROJECTION
        return ProviderResult(ResultState.OUTCOME_UNKNOWN)

    gateway.dispatch.side_effect = send
    assert smoke.step(claim, work, ()).target is RunState.BLOCKED
    authority.settle.assert_called_once_with(grant, claim, ModelOutcome.OUTCOME_UNKNOWN)
    assert smoke.step(claim, work, ("recovered",)).target is RunState.BLOCKED
    gateway.dispatch.assert_called_once()
