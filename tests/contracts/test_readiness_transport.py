"""RDR-05: reale Authgrenze, strikte DTOs und begrenzter privater Relay."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.backtesting.readiness_jobs import ReadinessError
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.readiness_client import ReadinessClient
from services.control_api.readiness_dto import RESPONSE_MODELS, ReadinessProblem
from services.control_api.readiness_routes import CURRENT_TRACE

ROOT = Path(__file__).resolve().parents[2]
BASE = "/api/v1/research/data-readiness/assessments"
ORIGIN = "http://127.0.0.1:8000"


def example(name):
    return json.loads((ROOT / f"contracts/examples/v1/readiness-{name}.valid.json").read_text())


class Spy:
    def __init__(self):
        self.calls = []

    def create_assessment(self, *args):
        self.calls.append(("create", args))
        return example("accepted")

    def read_assessment(self, identifier):
        self.calls.append(("detail", identifier))
        return example("assessment.blocked")

    def read_findings(self, identifier, values):
        self.calls.append(("findings", identifier, values))
        return example("findings.first-page")


def client(service=None, *, activate=True):
    auth = LocalAuth()
    result = TestClient(
        create_app(auth=auth, readiness=service),
        base_url=ORIGIN,
        client=("127.0.0.1", 46120),
        raise_server_exceptions=False,
    )
    if activate:
        response = result.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": ORIGIN, "Idempotency-Key": "activate-ready-001"},
        )
        assert response.status_code == 204
    return result


@pytest.mark.parametrize(
    "method,name",
    [
        ("create_assessment", "accepted"),
        ("read_assessment", "assessment.accepted"),
        ("read_assessment", "assessment.blocked"),
        ("read_assessment", "assessment.eligible"),
        ("read_assessment", "assessment.unknown"),
        ("read_assessment", "assessment.not-evaluated"),
        ("read_assessment", "assessment.revoked"),
        ("read_assessment", "assessment.budget-exhausted"),
        ("read_findings", "findings.first-page"),
        ("read_findings", "findings.last-page"),
        ("read_findings", "findings.unknown-calendar"),
    ],
)
def test_strict_response_dtos_accept_examples_and_forbid_unknown_fields(method, name):
    value = example(name)
    assert RESPONSE_MODELS[method].model_validate(value).model_dump() == value
    value["providerPayload"] = "synthetic-untrusted-canary"
    with pytest.raises(ValueError):
        RESPONSE_MODELS[method].model_validate(value)


def test_authentication_origin_and_reads_precede_every_service_call():
    spy = Spy()
    http = client(spy, activate=False)
    identifier = example("accepted")["assessmentId"]
    for suffix in ("/" + identifier, "/" + identifier + "/findings"):
        response = http.get(BASE + suffix)
        assert response.status_code == 401
        assert response.json()["type"] == "urn:agentic-trading-lab:readiness-problem"
    assert (
        http.post(
            BASE,
            json=example("request"),
            headers={"Origin": ORIGIN, "Idempotency-Key": "ready-request-0001"},
        ).status_code
        == 401
    )
    http = client(spy)
    assert (
        http.post(
            BASE,
            json=example("request"),
            headers={"Origin": "https://foreign.invalid", "Idempotency-Key": "ready-request-0001"},
        ).status_code
        == 403
    )
    assert (
        http.get(BASE + "/" + identifier, headers={"Origin": "https://foreign.invalid"}).status_code
        == 403
    )
    assert spy.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=201",
        "limit=1&limit=2",
        "category=LIVE",
        "cursor=bad",
        "source=anything",
        "apiKey=synthetic-canary",
    ],
)
def test_hostile_findings_queries_never_reach_service(query):
    spy = Spy()
    response = client(spy).get(BASE + "/" + str(uuid4()) + "/findings?" + query)
    assert response.status_code == 400
    assert set(response.json()) == {"type", "title", "status", "code", "traceId"}
    assert spy.calls == []


@pytest.mark.parametrize(
    "content,status",
    [
        (b'{"startCash":"1","startCash":"2"}', 400),
        (b"NaN", 400),
        (b'{"rawPayload":{}}', 400),
        (b"x" * (16 * 1024 * 1024 + 1), 413),
    ],
    ids=["duplicate", "nonfinite", "unknown-field", "oversized"],
)
def test_bad_duplicate_nonfinite_and_oversized_body_is_redacted(content, status):
    spy = Spy()
    response = client(spy).post(
        BASE,
        content=content,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "Idempotency-Key": "ready-request-0001",
        },
    )
    assert response.status_code == status
    assert spy.calls == []
    assert "rawPayload" not in response.text


def test_receipt_detail_and_pages_use_named_operations_and_trace_context():
    spy = Spy()
    http = client(spy)
    response = http.post(
        BASE,
        json=example("request"),
        headers={"Origin": ORIGIN, "Idempotency-Key": "ready-request-0001"},
    )
    assert response.status_code == 202
    identifier = response.json()["assessmentId"]
    assert http.get(BASE + "/" + identifier).status_code == 200
    assert (
        http.get(BASE + "/" + identifier + "/findings?limit=1&category=BAR_MISSING").status_code
        == 200
    )
    assert [call[0] for call in spy.calls] == ["create", "detail", "findings"]


def test_fail_closed_backend_and_invalid_output_are_never_returned():
    identifier = str(uuid4())
    assert client().get(BASE + "/" + identifier).status_code == 503
    spy = Spy()
    spy.read_assessment = lambda _: {"sql": "synthetic-private-query"}
    response = client(spy).get(BASE + "/" + identifier)
    assert response.status_code == 503 and "synthetic-private-query" not in response.text


def test_fixed_private_relay_carries_trace_without_ambient_auth():
    identifier = example("accepted")["assessmentId"]

    def handler(request):
        assert str(request.url) == "http://paper-api:8012" + BASE + "/" + identifier
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.headers["traceparent"].split("-")[1] == "a" * 32
        return httpx.Response(
            200, stream=httpx.ByteStream(json.dumps(example("assessment.blocked")).encode())
        )

    token = CURRENT_TRACE.set("a" * 32)
    try:
        relay = ReadinessClient(transport=httpx.MockTransport(handler))
        assert relay.read_assessment(identifier) == example("assessment.blocked")
    finally:
        CURRENT_TRACE.reset(token)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://foreign.invalid"}),
        httpx.Response(200, content=b'{"items":[],"items":[]}'),
        httpx.Response(200, content=b"NaN"),
        httpx.Response(503, json={"code": "UNAVAILABLE", "provider": "synthetic-private"}),
        httpx.Response(503, headers={"content-length": str(16 * 1024 * 1024 + 1)}),
        httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(b"invalid")
        ),
    ],
)
def test_relay_refuses_redirects_compressed_malformed_and_unbounded_responses(response):
    relay = ReadinessClient(transport=httpx.MockTransport(lambda _: copy.copy(response)))
    with pytest.raises(ReadinessError) as error:
        relay.read_assessment(str(uuid4()))
    assert (error.value.status, error.value.code) == (503, "UNAVAILABLE")


def test_status_code_mismatch_and_nested_fake_trust_are_rejected():
    value = example("problem.unavailable")
    value["code"] = "UNAUTHORIZED"
    with pytest.raises(ValueError):
        ReadinessProblem.model_validate(value)
    detail = example("assessment.blocked")
    detail["report"]["trust"]["integrity"]["certificate"] = "self-issued"
    with pytest.raises(ValueError):
        RESPONSE_MODELS["read_assessment"].model_validate(detail)
