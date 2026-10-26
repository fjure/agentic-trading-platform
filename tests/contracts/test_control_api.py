"""PTV-18: echter ASGI-Transport gegen den kanonischen HTTP-Vertrag, ohne Netzwerk."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_stages import pending_contract_operations
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth

ORIGIN = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text())
URI = "https://agentic-trading-lab.invalid/control-api"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)


def validate(name, data):
    Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + name},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    ).validate(data)


class SpyBackend:
    """HTTP-Port-Testdouble; dauerhafte Transaktionen werden separat mit PG geprüft."""

    def __init__(self):
        self.calls = []
        self.failure = None
        self.mode = dict(
            mode="DISABLED",
            pauseNew=True,
            killSwitch=False,
            health="UNAVAILABLE",
            reconciliation="PENDING",
            changedAt="2026-10-15T12:00:00Z",
            traceId="a" * 32,
        )
        self.policy = dict(
            maxPositionsPerInstrument=1,
            maxOpenPositions=2,
            maxPositionEquityFraction="0.10",
            maxDailyLossFraction="0.01",
            version=1,
            simulationOnly=True,
            effectiveAt="2026-10-15T12:00:00Z",
            changedAt="2026-10-15T12:00:00Z",
            changedBy="local-operator",
            auditEventId="audit_1",
        )

    def read_mode(self, trace_id):
        if self.failure:
            raise RuntimeError(self.failure)
        return {**self.mode, "traceId": trace_id}

    def read_policy(self):
        return copy.deepcopy(self.policy), '"risk-policy-v1"'

    def mutate(self, operation, payload, actor_id, key, trace_id, if_match=None):
        self.calls.append((operation, payload, actor_id, key, trace_id, if_match))
        if self.failure:
            raise RuntimeError(self.failure)
        if operation == "updateRiskPolicy":
            if if_match is None:
                return 428, {"code": "PRECONDITION_REQUIRED", "traceId": trace_id}, {}
            if if_match != '"risk-policy-v1"':
                return 412, {"code": "VERSION_CONFLICT", "traceId": trace_id}, {}
            return 200, {**self.policy, **payload, "version": 2}, {"ETag": '"risk-policy-v2"'}
        if operation in {"resumeNewEntries", "setMode"}:
            return 503, {"code": "HEALTH_CHECK_FAILED", "traceId": trace_id}, {}
        return (
            200,
            {**self.mode, "traceId": trace_id, "killSwitch": operation == "activateKillSwitch"},
            {},
        )


def client(backend=None):
    auth = LocalAuth()
    api = create_app(auth=auth, backend=backend)
    http = TestClient(
        api, base_url=ORIGIN, client=("127.0.0.1", 46000), raise_server_exceptions=False
    )
    code = auth.issue_activation_code()
    result = http.post(
        "/api/v1/session",
        json={"activationCode": code},
        headers={"Origin": ORIGIN, "Idempotency-Key": "activate-key-0001"},
    )
    assert result.status_code == 204
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "SameSite=strict" in result.headers["set-cookie"]
    return http


def write(http, path, data, *, method="POST", **headers):
    return http.request(
        method,
        path,
        json=data,
        headers={"Origin": ORIGIN, "Idempotency-Key": "action-key-00001", **headers},
    )


def test_mode_policy_transport_matches_canonical_schema():
    http = client(SpyBackend())
    response = http.get(
        "/api/v1/mode", headers={"traceparent": "00-" + "b" * 32 + "-" + "c" * 16 + "-01"}
    )
    assert response.status_code == 200
    assert response.json()["traceId"] == "b" * 32
    validate("ModeState", response.json())
    response = http.get("/api/v1/risk-policy")
    assert response.status_code == 200
    validate("RiskPolicy", response.json())
    assert response.headers["ETag"] == '"risk-policy-v1"'
    for header in ("cache-control", "x-content-type-options", "content-security-policy"):
        assert header in response.headers


@pytest.mark.parametrize(
    "path,operation", [("pause", "pauseNewEntries"), ("kill", "activateKillSwitch")]
)
def test_control_transport_and_actor_trace_binding(path, operation):
    backend = SpyBackend()
    response = write(client(backend), "/api/v1/control/" + path, {"reason": "synthetic"})
    assert response.status_code == 200
    validate("ModeState", response.json())
    assert backend.calls[0][:4] == (
        operation,
        {"reason": "synthetic"},
        "local-operator",
        "action-key-00001",
    )


def test_etags_428_412_and_strong_success():
    backend = SpyBackend()
    http = client(backend)
    update = {
        k: backend.policy[k]
        for k in (
            "maxPositionsPerInstrument",
            "maxOpenPositions",
            "maxPositionEquityFraction",
            "maxDailyLossFraction",
        )
    }
    for tag, status, code in [
        (None, 428, "PRECONDITION_REQUIRED"),
        ('"old"', 412, "VERSION_CONFLICT"),
    ]:
        response = write(
            http, "/api/v1/risk-policy", update, method="PUT", **({"If-Match": tag} if tag else {})
        )
        assert response.status_code == status
        validate("Problem", response.json())
        assert response.json()["code"] == code
    response = write(
        http, "/api/v1/risk-policy", update, method="PUT", **{"If-Match": '"risk-policy-v1"'}
    )
    assert response.status_code == 200
    validate("RiskPolicy", response.json())
    assert response.headers["ETag"] == '"risk-policy-v2"'


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"mode": "LIVE", "reason": "test"},
        {"mode": "PAPER", "reason": ""},
        {"mode": "DISABLED", "reason": "test", "brokerKey": "CANARY"},
        {"mode": "PAPER", "reason": 7},
        {"mode": "SHADOW", "reason": "x" * 501},
    ],
)
def test_schema_negative_inputs_never_reach_backend(data):
    backend = SpyBackend()
    response = write(client(backend), "/api/v1/mode", data, method="PUT")
    assert response.status_code == 400
    validate("Problem", response.json())
    assert backend.calls == []
    assert "CANARY" not in response.text


@pytest.mark.parametrize(
    "raw",
    [
        '{"reason":"x","reason":"y"}',
        '{"reason":NaN}',
        '{"reason":Infinity}',
        "[1,2]",
        '"string"',
        "{",
        "[" * 1100,
        '{"reason":"' + "x" * 17000,
    ],
)
def test_malformed_ambiguous_and_bounded_json(raw):
    backend = SpyBackend()
    response = client(backend).post(
        "/api/v1/control/pause",
        content=raw,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "Idempotency-Key": "action-key-00001",
        },
    )
    assert response.status_code == 400
    validate("Problem", response.json())
    assert backend.calls == []


@given(
    st.one_of(
        st.booleans(),
        st.integers(),
        st.floats(allow_nan=False, allow_infinity=False),
        st.lists(st.integers()),
    )
)
@settings(max_examples=30, deadline=None)
def test_fuzz_strict_numeric_policy_fields(value):
    backend = SpyBackend()
    update = dict(
        maxPositionsPerInstrument=1,
        maxOpenPositions=2,
        maxPositionEquityFraction=value,
        maxDailyLossFraction="0.01",
    )
    response = write(client(backend), "/api/v1/risk-policy", update, method="PUT")
    assert response.status_code == 400
    assert not backend.calls


def test_failures_are_sanitized_and_default_app_fails_closed():
    backend = SpyBackend()
    backend.failure = "CANARY-private-provider-and-stack"
    http = client(backend)
    for response in (
        http.get("/api/v1/mode"),
        write(http, "/api/v1/control/pause", {"reason": "test"}),
    ):
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        validate("Problem", response.json())
        assert "CANARY" not in response.text
    assert client().get("/api/v1/mode").status_code == 503


def test_auth_csrf_and_forbidden_submit_paths():
    http = client(SpyBackend())
    response = http.post("/api/v1/control/pause", json={"reason": "test"})
    assert response.status_code == 400
    assert http.get("/api/v1/mode", headers={"Origin": "https://evil.invalid"}).status_code == 400
    assert write(http, "/api/v1/orders/submit", {"reason": "test"}).status_code == 404
    assert http.get("/openapi.json").status_code == 404
    assert http.get("/docs").status_code == 404
    http.cookies.clear()
    response = http.get("/api/v1/mode")
    assert response.status_code == 401
    validate("Problem", response.json())


def test_mutation_routes_are_exact_contract_subset():
    api = create_app()
    actual = {
        (route.path, method.lower(), route.operation_id)
        for route in api.routes
        for method in route.methods
    }
    pending_operations = pending_contract_operations()
    expected = {
        (path, method, op["operationId"])
        for path, methods in OAS["paths"].items()
        for method, op in methods.items()
        if method in {"post", "put"} and op["operationId"] not in pending_operations
    }
    assert {entry for entry in actual if entry[1] in {"post", "put"}} == expected


@pytest.mark.parametrize("operation", ["resume", "paper"])
def test_pending_recovery_is_never_reported_as_success(operation):
    backend = SpyBackend()
    http = client(backend)
    if operation == "paper":
        response = write(http, "/api/v1/mode", {"mode": "PAPER", "reason": "test"}, method="PUT")
    else:
        response = write(http, "/api/v1/control/resume", {"reason": "test"})
    assert response.status_code == 503
    validate("Problem", response.json())
    assert response.json()["code"] == "HEALTH_CHECK_FAILED"
    assert response.json()["type"].endswith("/control-refused")
    assert response.headers["Retry-After"] == "1"


def test_unknown_status_problem_remains_distinct_and_duplicate_preconditions_reject():
    class UnknownBackend(SpyBackend):
        def mutate(self, operation, payload, actor_id, key, trace_id, if_match=None):
            return 503, {"code": "BROKER_STATUS_UNKNOWN", "traceId": trace_id}, {}

    response = write(client(UnknownBackend()), "/api/v1/control/pause", {"reason": "test"})
    validate("Problem", response.json())
    assert response.status_code == 503 and response.json()["code"] == "BROKER_STATUS_UNKNOWN"
    backend = SpyBackend()
    http = client(backend)
    response = http.post(
        "/api/v1/control/pause",
        json={"reason": "test"},
        headers=[
            ("Origin", ORIGIN),
            ("Idempotency-Key", "action-key-00001"),
            ("If-Match", '"v1"'),
            ("If-Match", '"v2"'),
        ],
    )
    assert response.status_code == 400 and backend.calls == []
