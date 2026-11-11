"""Security checks at the private Research supervisor HTTP boundary."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from services.control_api.agent_research_routes import OPERATIONS, AgentResearchError
from services.control_api.research_client import AgentResearchClient
from services.runtime import agent_research_relay as relay

ROOT = Path(__file__).resolve().parents[2]
AUTH = relay.AgentRelayAuthentication(b"a" * 32)
AUTHORITY = {
    "actor": "local-operator", "key": None, "trace": "b" * 32, "scope": "c" * 64,
}
REGISTRY = next(op for op in OPERATIONS if op.operation_id == "getAgentRegistry")
DECIDE = next(op for op in OPERATIONS if op.operation_id == "decideAgentProcessing")


def test_relay_allowlist_covers_research_operations_and_excludes_setup():
    assert relay._ALLOWED_OPERATIONS == {op.operation_id for op in OPERATIONS if not op.setup}


def _transport(app):
    def request(incoming):
        assert incoming.method == "POST"
        assert str(incoming.url) == relay.RELAY_URL
        assert incoming.headers["Accept-Encoding"] == "identity"
        browser_headers = {"authorization", "cookie", "origin", "x-csrf-token", "idempotency-key"}
        assert browser_headers.isdisjoint(incoming.headers)
        with TestClient(app) as server:
            response = server.post(
                relay.RELAY_PATH, headers=incoming.headers, content=incoming.content,
            )
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=httpx.ByteStream(response.content))

    return httpx.MockTransport(request)


def test_processing_decline_crosses_the_private_boundary_with_scoped_authority():
    path = ROOT / "contracts/examples/v1/agent-processing-fixtures.json"
    fixture = json.loads(path.read_bytes())
    authority = AUTHORITY | {"key": "decline-000000001"}
    params = {"id": "dialogue-1", "turnId": "turn-1"}
    captured = []

    def handler(operation, params, query, payload, **authority):
        captured.append((operation, params, query, payload, authority))
        return 200, fixture["status"]

    app = relay.create_agent_research_relay(AUTH, handler)
    client = AgentResearchClient(AUTH, transport=_transport(app))
    assert client(DECIDE, params, {}, fixture["decline"], **authority) == (200, fixture["status"])
    assert captured == [(DECIDE, params, {}, fixture["decline"], authority)]


@pytest.mark.parametrize("alteration", [
    {"actor": "anonymous"}, {"scope": "browser-session-secret"}, {"iat": 0},
    {"key": "unexpected-write-key"}, {"query": {"callback": "unapproved.invalid"}},
    {"operation": "saveAgentProviderCredential"}, {"operation": "createBrokerOrder"},
    {"session": "forbidden-extra-field"},
])
def test_private_relay_revalidates_signed_envelopes_before_dispatch(alteration):
    def handler(*_, **__):
        pytest.fail("unexpected dispatch")

    valid = json.loads(relay.request_body(REGISTRY, {}, {}, None, **AUTHORITY))
    malformed = json.dumps(valid | alteration).encode()
    headers = {relay.SIGNATURE_HEADER: AUTH.request_signature(malformed),
               "Content-Type": "application/json"}
    with TestClient(relay.create_agent_research_relay(AUTH, handler)) as server:
        assert server.post(relay.RELAY_PATH, content=malformed, headers=headers).status_code == 400


def test_private_relay_rejects_unsigned_tampered_and_oversized_requests():
    def handler(*_, **__):
        pytest.fail("unexpected dispatch")

    body = relay.request_body(REGISTRY, {}, {}, None, **AUTHORITY)
    headers = {relay.SIGNATURE_HEADER: AUTH.request_signature(body),
               "Content-Type": "application/json"}
    large = b"x" * (relay.MAX_REQUEST + 1)
    with TestClient(relay.create_agent_research_relay(AUTH, handler)) as server:
        assert server.post(relay.RELAY_PATH, content=body).status_code == 403
        tampered = server.post(relay.RELAY_PATH, content=body + b" ", headers=headers)
        assert tampered.status_code == 403
        assert server.post(relay.RELAY_PATH, content=large, headers=headers).status_code == 400


def test_private_relay_returns_only_a_typed_domain_failure():
    def handler(*_, **__):
        raise AgentResearchError(409, "VERSION_CONFLICT")

    client = AgentResearchClient(AUTH, transport=_transport(relay.create_agent_research_relay(
        AUTH, handler,
    )))
    status, body = client(REGISTRY, {}, {}, None, **AUTHORITY)
    assert status == body["status"] == 409
    assert body["code"] == "VERSION_CONFLICT" and body["traceId"] == AUTHORITY["trace"]


def test_quant_start_cannot_be_relayed_to_research_even_with_a_valid_signature():
    def handler(*_, **__):
        pytest.fail("Research must never admit a Quant batch")

    body = json.loads(relay.request_body(REGISTRY, {}, {}, None, **AUTHORITY))
    body.update(operation="decideAgentQuantApproval", params={"id": "approval-1"},
                key="approval-command-01", payload={"action": "APPROVE_AND_START",
                    "expectedApprovalVersion": 1, "bindingHash": "a" * 64})
    encoded = json.dumps(body).encode()
    with TestClient(relay.create_agent_research_relay(AUTH, handler)) as server:
        response = server.post(relay.RELAY_PATH, content=encoded, headers={
            relay.SIGNATURE_HEADER: AUTH.request_signature(encoded),
            "Content-Type": "application/json",
        })
    assert response.status_code == 400


@pytest.mark.parametrize("failure", ["wrong-signature", "wrong-request", "extra-field", "large"])
def test_client_fails_closed_on_untrusted_replies(failure):
    def request(incoming):
        value = {"items": []}
        if failure == "extra-field":
            value["private"] = "synthetic-canary"
        body = json.dumps({"status": 200, "value": value}).encode()
        if failure == "large":
            body = b"x" * (relay.MAX_REPLY + 1)
        bound_request = incoming.content if failure != "wrong-request" else b"different-request"
        signature = AUTH.response_signature(bound_request, body)
        if failure == "wrong-signature":
            signature = "0" * 64
        return httpx.Response(200, headers={
            relay.SIGNATURE_HEADER: signature, "Content-Type": "application/json",
        }, stream=httpx.ByteStream(body))

    client = AgentResearchClient(AUTH, transport=httpx.MockTransport(request))
    with pytest.raises(AgentResearchError) as error:
        client(REGISTRY, {}, {}, None, **AUTHORITY)
    assert (error.value.status, error.value.code) == (503, "PROVIDER_UNAVAILABLE")
    assert error.value.__context__ is None
