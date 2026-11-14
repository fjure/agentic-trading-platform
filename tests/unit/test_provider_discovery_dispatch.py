from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from services.llm_gateway.discovery import ProviderDiscoveryDispatch
from services.llm_gateway.provider_wire import ProviderResult, ResultState
from tests.unit.test_llm_provider_http import PROFILE


def test_discovery_grant_is_exact_and_consumed_once():
    approved = []

    class Gateway:
        def dispatch(self, request):
            assert port.consume(replace(request, purpose="DIALOGUE")) is None
            assert port.consume(replace(request, projection_sha256="c" * 64)) is None
            assert port.consume(request).projection == b'{"fixture":true}'
            assert port.consume(request) is None
            return ProviderResult(ResultState.ACCEPTED)

    port = ProviderDiscoveryDispatch(Gateway(), PROFILE)
    result = port.send(
        uuid4(), "a" * 64, datetime.now(UTC) + timedelta(seconds=10), b'{"fixture":true}',
        authorize=lambda: approved.append(True),
    )
    assert result.state is ResultState.ACCEPTED and approved == [True]
