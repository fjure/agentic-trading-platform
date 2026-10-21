"""Actual authenticated ASGI Paper routes against canonical strict contracts."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.paper_commands import PaperCommandError
from tests.contracts.test_paper_dto import requests
from tests.contracts.test_paper_workflow_contracts import example, validator

ORIGIN = "http://127.0.0.1:8000"


class SpyPaper:
    """Transport-only spy; real SQL durability and fences are independently tested."""

    def __init__(self):
        self.calls = []
        self.failure = None
        self.bad_response = False

    def enqueue(self, operation, payload, actor, key, trace, order_id=None):
        self.calls.append((operation, deepcopy(payload), actor, key, trace, order_id))
        if self.failure:
            raise self.failure
        result = example("paper-operation")
        result.update(operationType=operation, idempotencyKey=key, traceId=trace)
        if self.bad_response:
            result["apiKey"] = "synthetic-canary"
        return result

    def read_status(self, trace):
        result = example("paper-workflow")
        result["traceId"] = trace
        return result

    def read_operation(self, operation_id, trace):
        if self.failure:
            raise self.failure
        return example("paper-operation")


def client(paper=None, *, authenticated=True):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, paper=paper),
        base_url=ORIGIN,
        client=("127.0.0.1", 46000),
        raise_server_exceptions=False,
    )
    if authenticated:
        code = auth.issue_activation_code()
        result = http.post(
            "/api/v1/session",
            json={"activationCode": code},
            headers={"Origin": ORIGIN, "Idempotency-Key": "session-key-0001"},
        )
        assert result.status_code == 204
    return http


def test_status_preserves_required_null_fields_without_provider_calls():
    spy = SpyPaper()
    response = client(spy).get("/api/v1/paper/status")
    assert response.status_code == 200
    validator("PaperStatus").validate(response.json())
    assert all(
        response.json()[field] is None for field in ("accountLabel", "verifiedAt", "reconciledAt")
    )
    assert spy.calls == []


@pytest.mark.parametrize(
    "index,path,operation",
    [
        (0, "verify-connection", "VERIFY_CONNECTION"),
        (1, "prepare-ledger", "PREPARE_LEDGER"),
        (2, "start", "START"),
        (3, "control", "KILL"),
        (4, "order-requests", "PLACE_LIMIT_ORDER"),
        (5, "orders/12345678-1234-4234-8234-123456789012/cancel", "CANCEL_ORDER"),
    ],
)
def test_all_mutations_bind_authenticated_actor_key_trace_and_return_acceptance(
    index, path, operation
):
    spy = SpyPaper()
    payload = requests()[index][2]
    if operation == "KILL":
        payload = {"action": "KILL"}
    response = client(spy).post(
        "/api/v1/paper/" + path,
        json=payload,
        headers={
            "Origin": ORIGIN,
            "Idempotency-Key": "paper-action-key-1",
            "traceparent": "00-" + "b" * 32 + "-" + "c" * 16 + "-00",
        },
    )
    assert response.status_code == 202, response.text
    validator("PaperOperation").validate(response.json())
    assert response.json()["status"] == "ACCEPTED"
    assert spy.calls[0][:5] == (
        operation,
        payload,
        "local-operator",
        "paper-action-key-1",
        "b" * 32,
    )
    assert spy.calls[0][5] == (path.split("/")[1] if operation == "CANCEL_ORDER" else None)


@pytest.mark.parametrize(
    "payload",
    [
        {"credentialGeneration": True},
        {"credentialGeneration": 0},
        {"credentialGeneration": 1, "apiKey": "synthetic-canary"},
    ],
)
def test_invalid_or_secret_bearing_inputs_never_reach_store(payload):
    spy = SpyPaper()
    result = client(spy).post(
        "/api/v1/paper/verify-connection",
        json=payload,
        headers={"Origin": ORIGIN, "Idempotency-Key": "paper-action-key-1"},
    )
    assert result.status_code == 400
    validator("PaperProblem").validate(result.json())
    assert spy.calls == [] and "synthetic-canary" not in result.text


def test_auth_origin_and_key_refusals_use_canonical_paper_problem():
    spy = SpyPaper()
    http = client(spy, authenticated=False)
    result = http.get("/api/v1/paper/status")
    assert result.status_code == 401
    validator("PaperProblem").validate(result.json())
    http = client(spy)
    for headers in [
        {"Origin": "https://untrusted.invalid", "Idempotency-Key": "paper-action-key-1"},
        {"Origin": ORIGIN},
    ]:
        result = http.post(
            "/api/v1/paper/verify-connection", json={"credentialGeneration": 1}, headers=headers
        )
        assert result.status_code in (400, 403)
        validator("PaperProblem").validate(result.json())
    assert spy.calls == []


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (PaperCommandError(409, "IDEMPOTENCY_CONFLICT"), 409, "IDEMPOTENCY_CONFLICT"),
        (RuntimeError("synthetic-canary-private-error"), 503, "TEMPORARILY_UNAVAILABLE"),
    ],
)
def test_durable_conflicts_and_unavailable_dependencies_are_sanitized(failure, status, code):
    spy = SpyPaper()
    spy.failure = failure
    response = client(spy).post(
        "/api/v1/paper/verify-connection",
        json={"credentialGeneration": 1},
        headers={"Origin": ORIGIN, "Idempotency-Key": "paper-action-key-1"},
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    validator("PaperProblem").validate(response.json())
    assert "synthetic-canary" not in response.text


def test_malformed_public_backend_response_is_unavailable_without_leak():
    spy = SpyPaper()
    spy.bad_response = True
    response = client(spy).post(
        "/api/v1/paper/verify-connection",
        json={"credentialGeneration": 1},
        headers={"Origin": ORIGIN, "Idempotency-Key": "paper-action-key-1"},
    )
    assert response.status_code == 503
    validator("PaperProblem").validate(response.json())
    assert "synthetic-canary" not in response.text


def test_operation_lookup_validates_uuid_and_missing_id():
    spy = SpyPaper()
    http = client(spy)
    assert http.get("/api/v1/paper/operations/not-a-uuid").status_code == 400
    spy.failure = PaperCommandError(404, "OPERATION_NOT_FOUND")
    response = http.get("/api/v1/paper/operations/12345678-1234-4234-8234-123456789012")
    assert response.status_code == 404
    validator("PaperProblem").validate(response.json())
