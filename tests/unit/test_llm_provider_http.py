"""Exercise the provider HTTP boundary with synthetic requests and responses."""

import json
from dataclasses import replace

import httpx
import pytest

from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
from services.llm_gateway.provider_wire import Provider, ResultState
from tests.unit.test_llm_provider_wire import SCHEMA, response

PROFILE = Capability(
    Provider.OPENAI, "fixture-model", "fixture-profile", 1, 1, 1,
    "fixture-v1", "fixed instructions",
    json.dumps(SCHEMA.schema), 128, 2,
)
PROJECTION = '{"excerpt":"Prüfe diese Idee.","confirmedContext":{}}'.encode()


@pytest.mark.parametrize(("provider", "url", "field"), [
    (Provider.OPENAI, "https://api.openai.com/v1/responses", "input"),
    (Provider.ANTHROPIC, "https://api.anthropic.com/v1/messages", "messages"),
])
def test_fixed_http_route_and_exact_projection(provider, url, field):
    def handler(request):
        assert request.method == "POST" and str(request.url) == url
        content = json.loads(request.content)
        assert content[field] == [{"role": "user", "content": PROJECTION.decode()}]
        assert "authorization" not in request.headers and "x-api-key" not in request.headers
        return httpx.Response(200, json=response(provider, '{"hypothesis":"synthetic"}'))

    result = OfflineProviderHttp(httpx.MockTransport(handler)).send(
        replace(PROFILE, provider=provider), PROJECTION,
    )
    assert result.state is ResultState.ACCEPTED and result.usage is None


@pytest.mark.parametrize(("status", "expected"), [
    (307, ResultState.REDIRECT_DENIED), (429, ResultState.RATE_LIMITED),
    (401, ResultState.REJECTED_BEFORE_EXECUTION), (503, ResultState.OUTCOME_UNKNOWN),
])
def test_http_failure_never_retries_or_follows_redirect(status, expected):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(status, headers={"location": "https://127.0.0.1/private"})

    result = OfflineProviderHttp(httpx.MockTransport(handler)).send(PROFILE, PROJECTION)
    assert result.state is expected and result.output is None
    assert len(calls) == 1


def test_timeout_and_non_double_transport_fail_closed():
    def timeout(_request):
        raise httpx.ReadTimeout("synthetic detail")

    assert OfflineProviderHttp(httpx.MockTransport(timeout)).send(
        PROFILE, PROJECTION,
    ).state is ResultState.OUTCOME_UNKNOWN
    with pytest.raises(ValueError, match="PROVIDER_UNAVAILABLE"):
        OfflineProviderHttp(httpx.HTTPTransport())


@pytest.mark.parametrize("provider", list(Provider))
def test_provider_cannot_echo_a_synthetic_credential(provider):
    canary = "offline-canary-" + "x" * 32

    def handler(request):
        if provider is Provider.OPENAI:
            assert request.headers["authorization"] == f"Bearer {canary}"
        else:
            assert request.headers["x-api-key"] == canary
        return httpx.Response(200, json=response(provider, json.dumps({"hypothesis": canary})))

    result = OfflineProviderHttp(httpx.MockTransport(handler)).send(
        replace(PROFILE, provider=provider), PROJECTION, synthetic_credential=canary,
    )
    assert result.state is ResultState.INVALID_OUTPUT and canary not in repr(result)
