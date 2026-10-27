"""RDR-06: geschlossene DTOs und echte Session-/Origin-/Transportgrenzen."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.collection_client import CollectionClient
from services.control_api.collection_dto import RESPONSE_MODELS, CollectionPlanUpdate
from services.control_api.collection_routes import CURRENT_TRACE
from services.control_api.collection_service import CollectionError

ROOT = Path(__file__).resolve().parents[2]
BASE = "/api/v1/research/collection/plans"
ORIGIN = "http://127.0.0.1:8000"


def example(name):
    return json.loads((ROOT / f"contracts/examples/v1/collection-{name}.valid.json").read_text())


class Spy:
    def __init__(self):
        self.calls = []

    def read_plans(self, query):
        self.calls.append(("read", query))
        return example("plans.disabled")

    def update_plan(self, *args):
        self.calls.append(("update", args))
        return example("plan.blocked")


def client(service=None, activate=True):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, collection=service),
        base_url=ORIGIN,
        client=("127.0.0.1", 46120),
        raise_server_exceptions=False,
    )
    if activate:
        assert (
            http.post(
                "/api/v1/session",
                json={"activationCode": auth.issue_activation_code()},
                headers={"Origin": ORIGIN, "Idempotency-Key": "activate-plans-001"},
            ).status_code
            == 204
        )
    return http


@pytest.mark.parametrize(
    "method,name",
    [
        ("read_plans", "plans.disabled"),
        ("update_plan", "plan.active"),
        ("update_plan", "plan.blocked"),
        ("update_plan", "plan.paused-inflight"),
    ],
)
def test_strict_plan_responses_accept_examples_and_reject_nested_claims(method, name):
    value = example(name)
    assert RESPONSE_MODELS[method].model_validate(value).model_dump() == value
    plan = value["items"][0] if "items" in value else value
    plan["providerRights"]["certificate"] = "synthetic-private-canary"
    with pytest.raises(ValueError):
        RESPONSE_MODELS[method].model_validate(value)


@pytest.mark.parametrize(
    "payload",
    [
        {"enabled": "false", "expectedVersion": 0},
        {"enabled": 1, "expectedVersion": 0},
        {"enabled": True, "expectedVersion": True},
        {"enabled": True, "expectedVersion": -1},
        {"enabled": True, "expectedVersion": 1.0},
        {"enabled": True, "expectedVersion": 2**53},
        {"enabled": True, "expectedVersion": 0, "globalEnabled": True},
        {"enabled": True, "expectedVersion": 0, "provider": "foreign"},
    ],
)
def test_plan_request_rejects_coercion_and_gate_overrides(payload):
    with pytest.raises(ValueError):
        CollectionPlanUpdate.model_validate(payload)


def test_authentication_origin_and_closed_filters_precede_service_access():
    spy = Spy()
    identifier = str(uuid4())
    http = client(spy, activate=False)
    assert http.get(BASE).status_code == 401
    assert (
        http.put(
            BASE + "/" + identifier,
            json={"enabled": True, "expectedVersion": 0},
            headers={"Origin": ORIGIN, "Idempotency-Key": "plans-request-0001"},
        ).status_code
        == 401
    )
    http = client(spy)
    assert http.get(BASE, headers={"Origin": "https://foreign.invalid"}).status_code == 403
    assert (
        http.put(
            BASE + "/" + identifier,
            json={"enabled": True, "expectedVersion": 0},
            headers={"Origin": "https://foreign.invalid", "Idempotency-Key": "plans-request-0001"},
        ).status_code
        == 403
    )
    assert http.get(BASE + "?source=foreign").status_code == 400
    assert spy.calls == []


@pytest.mark.parametrize(
    "content,extra,status",
    [
        (b'{"enabled":true,"enabled":false,"expectedVersion":0}', {}, 400),
        (b'{"enabled":true,"expectedVersion":NaN}', {}, 400),
        (b'{"enabled":true,"expectedVersion":0,"apiKey":"synthetic-canary"}', {}, 400),
        (b'{"enabled":false,"expectedVersion":0}', {"If-Match": "0"}, 400),
        (b"x" * (16 * 1024 * 1024 + 1), {}, 413),
    ],
    ids=["duplicate", "nonfinite", "unknown-field", "if-match", "oversized"],
)
def test_bad_bodies_and_uncontracted_headers_are_redacted(content, extra, status):
    spy = Spy()
    response = client(spy).put(
        BASE + "/" + str(uuid4()),
        content=content,
        headers={
            "Origin": ORIGIN,
            "Idempotency-Key": "plans-request-0001",
            "Content-Type": "application/json",
            **extra,
        },
    )
    assert response.status_code == status
    assert set(response.json()) == {"type", "title", "status", "code", "traceId"}
    assert "synthetic-canary" not in response.text and not spy.calls


def test_named_operations_validate_outputs_and_propagate_trace_context():
    spy = Spy()
    http = client(spy)
    assert http.get(BASE).status_code == 200
    value = example("update.enable")
    response = http.put(
        BASE + "/" + str(uuid4()),
        json=value,
        headers={"Origin": ORIGIN, "Idempotency-Key": "plans-request-0001"},
    )
    assert response.status_code == 200
    assert spy.calls[-1][1][1] == value
    assert len(spy.calls[-1][1][4]) == 32
    spy.read_plans = lambda _: {"privateQuery": "synthetic-canary"}
    response = http.get(BASE)
    assert response.status_code == 503 and "synthetic-canary" not in response.text
    assert client().get(BASE).status_code == 503


def test_fixed_private_relay_preserves_version_key_trace_and_no_ambient_auth():
    identifier = example("plan.blocked")["instrumentId"]

    def handler(request):
        assert str(request.url) == "http://paper-api:8012" + BASE + "/" + identifier
        assert request.method == "PUT"
        assert json.loads(request.content) == {"enabled": True, "expectedVersion": 0}
        assert request.headers["Idempotency-Key"] == "plans-request-0001"
        assert request.headers["traceparent"].split("-")[1] == "a" * 32
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(
            200, stream=httpx.ByteStream(json.dumps(example("plan.blocked")).encode())
        )

    token = CURRENT_TRACE.set("a" * 32)
    try:
        relay = CollectionClient(transport=httpx.MockTransport(handler))
        assert relay.update_plan(
            identifier,
            {"enabled": True, "expectedVersion": 0},
            "local-operator",
            "plans-request-0001",
            "a" * 32,
        ) == example("plan.blocked")
    finally:
        CURRENT_TRACE.reset(token)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://foreign.invalid"}),
        httpx.Response(200, content=b'{"items":[],"items":[]}'),
        httpx.Response(200, content=b"NaN"),
        httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"invalid")
        ),
        httpx.Response(200, headers={"content-length": str(16 * 1024 * 1024 + 1)}),
        httpx.Response(503, json={"code": "UNAVAILABLE", "privateQuery": "synthetic"}),
    ],
)
def test_relay_refuses_unbounded_malformed_compressed_or_redirected_responses(response):
    relay = CollectionClient(transport=httpx.MockTransport(lambda _: copy.copy(response)))
    with pytest.raises(CollectionError) as error:
        relay.read_plans({})
    assert (error.value.status, error.value.code) == (503, "UNAVAILABLE")
