"""Authenticated ASGI transport checks for the APF-08 browser boundary."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from services.control_api.agent_research_routes import (
    OPERATIONS,
    PROVIDER_READINESS,
    AgentResearchError,
    problem_value,
    validate_payload,
    validate_response,
)
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth

ORIGIN = "http://127.0.0.1:8000"
KEY = "apf08-http-command-001"
PREVIEW = "/api/v1/research/agent/dialogues/dialogue-1/turns/turn-1/processing-previews"
STATUS = "/api/v1/research/agent/dialogues/dialogue-1/turns/turn-1/processing"
APPROVAL = "/api/v1/research/agent/approvals/approval-1/decision"
SETUP = "/api/v1/integrations/research-providers/OPENAI"
OAS = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "contracts/http/control-api.openapi.yaml").read_text(
        encoding="utf-8"
    )
)
WRITE_HEADERS = {"Origin": ORIGIN, "Idempotency-Key": KEY}


def operation_example(operation_id, *, status=None):
    for entry in OAS["paths"].values():
        for method in ("get", "post", "put"):
            operation = entry.get(method, {})
            if operation.get("operationId") != operation_id:
                continue
            content = (
                operation["requestBody"]["content"]
                if status is None
                else operation["responses"][str(status)]["content"]
            )
            return content["application/json"]["example"]
    raise AssertionError(operation_id)


def activate(http, auth, key):
    response = http.post(
        "/api/v1/session",
        headers=WRITE_HEADERS | {"Idempotency-Key": key},
        json={"activationCode": auth.issue_activation_code()},
    )
    assert response.status_code == 204


def client(auth, handler=None, setup_handler=None, approval_handler=None):
    return TestClient(
        create_app(auth=auth, agent_research=handler, agent_provider_setup=setup_handler,
                   agent_quant_approval=approval_handler),
        base_url=ORIGIN,
        client=("127.0.0.1", 42000),
    )


def check_problem(response, status, code):
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["traceparent"].split("-")[1] == response.json()["traceId"]
    assert set(response.json()) == {"type", "title", "status", "code", "traceId"}


def test_provider_readiness_is_redacted_and_independent_of_research_relay():
    auth = LocalAuth(clock=lambda: datetime(2026, 11, 13, tzinfo=UTC))

    def unexpected_dispatch(*args, **kwargs):
        pytest.fail("readiness must not dispatch to the research relay")

    http = client(auth, unexpected_dispatch)
    check_problem(http.get(PROVIDER_READINESS), 401, "SESSION_REQUIRED")
    activate(http, auth, KEY)
    response = http.get(PROVIDER_READINESS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    status = response.json()
    assert status["state"] == "DISABLED"
    luna = next(item for item in status["candidates"] if item["modelId"] == "gpt-6-luna")
    assert luna["evidenceState"] == "INCOMPLETE"
    assert set(luna["blockingReasons"]) == {
        "BILLABLE_INPUT_LIMIT_UNVERIFIED",
        "BILLING_TIERS_UNVERIFIED",
    }
    check_problem(http.get(PROVIDER_READINESS + "?model=other"), 422, "INVALID_REQUEST")


def test_default_installation_refuses_probe_and_activation():
    auth = LocalAuth(clock=lambda: datetime(2026, 11, 13, tzinfo=UTC))
    http = client(auth)
    activate(http, auth, KEY)
    check_problem(
        http.post(
            "/api/v1/research/agent/providers/OPENAI/probe",
            headers=WRITE_HEADERS,
            json=operation_example("probeAgentProvider"),
        ),
        503,
        "PROVIDER_UNAVAILABLE",
    )
    check_problem(
        http.post(
            SETUP + "/activate",
            headers=WRITE_HEADERS,
            json={
                "expectedCredentialVersion": 1,
                "profileId": "profile-001",
                "profileGeneration": 1,
                "modelGeneration": 1,
                "credentialGeneration": 1,
                "verifiedProbeId": "probe-001",
            },
        ),
        503,
        "PROVIDER_UNAVAILABLE",
    )


def test_wire_registry_and_mounted_routes_match_openapi():
    prefixes = ("/api/v1/research/agent/", "/api/v1/integrations/research-providers/")
    expected = {
        (path, method.upper(), op["operationId"]): op
        for path, item in OAS["paths"].items()
        if path.startswith(prefixes)
        for method, op in item.items()
        if method in {"get", "post", "put"}
    }
    assert {(op.path, op.method, op.operation_id) for op in OPERATIONS} == expected.keys()
    actual = {
        (route.path, method, route.operation_id)
        for route in create_app().routes
        if route.path.startswith(prefixes)
        for method in route.methods
    }
    assert actual == expected.keys()
    for operation in OPERATIONS:
        contract = expected[operation.path, operation.method, operation.operation_id]
        request = contract.get("requestBody", {}).get("content", {}).get("application/json")
        response = {
            int(status): item["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
            for status, item in contract["responses"].items()
            if status.startswith("2")
        }
        assert operation.request == (
            request["schema"]["$ref"].rsplit("/", 1)[1] if request else None
        )
        assert operation.responses == response
        parameters = [
            *OAS["paths"][operation.path].get("parameters", []),
            *contract.get("parameters", []),
        ]
        parameters = [
            OAS["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
            if "$ref" in param
            else param
            for param in parameters
        ]
        assert operation.query == tuple(
            param["name"] for param in parameters if param["in"] == "query"
        )


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda operation: operation.operation_id)
def test_route_validators_accept_canonical_operation_examples(operation):
    contract = OAS["paths"][operation.path][operation.method.lower()]
    request = contract.get("requestBody", {}).get("content", {}).get("application/json", {})
    if "example" in request:
        validate_payload(operation, request["example"])
    for example in request.get("examples", {}).values():
        validate_payload(operation, example["value"])
    for status in operation.responses:
        result = contract["responses"][str(status)]["content"]["application/json"]
        if "example" in result:
            validate_response(operation, status, result["example"])


def test_auth_and_csrf_fail_before_agent_handler():
    now = datetime(2026, 11, 8, tzinfo=UTC)
    auth = LocalAuth(clock=lambda: now)
    calls = []
    http = client(auth, lambda *args, **kwargs: calls.append((args, kwargs)))
    payload = operation_example("createAgentProcessingPreview")

    check_problem(http.get(STATUS), 401, "SESSION_REQUIRED")
    check_problem(http.post(PREVIEW, json=payload, headers=WRITE_HEADERS), 401, "SESSION_REQUIRED")
    activate(http, auth, "apf08-session-key-001")
    check_problem(http.post(PREVIEW, json=payload), 403, "REQUEST_FORBIDDEN")
    cross_origin = WRITE_HEADERS | {"Origin": "http://127.0.0.1:8100"}
    check_problem(http.post(PREVIEW, json=payload, headers=cross_origin), 403, "REQUEST_FORBIDDEN")
    check_problem(http.get(STATUS, headers=cross_origin), 403, "REQUEST_FORBIDDEN")
    check_problem(
        http.post(PREVIEW, json=payload, headers={"Origin": ORIGIN}), 422, "INVALID_REQUEST"
    )
    check_problem(
        http.post(PREVIEW, content="{}", headers=WRITE_HEADERS | {"Content-Type": "text/plain"}),
        422,
        "INVALID_REQUEST",
    )

    now += timedelta(hours=24)
    check_problem(http.post(PREVIEW, json=payload, headers=WRITE_HEADERS), 401, "SESSION_REQUIRED")
    assert calls == []


def test_closed_transport_forwards_only_normalized_scope_and_replay_context():
    auth = LocalAuth()
    calls, replays = [], {}
    result = operation_example("createAgentProcessingPreview", status=201)

    def handler(operation, params, query, payload, *, actor, key, trace, scope):
        calls.append((operation.operation_id, params, query, actor, key, trace, scope))
        stored = replays.setdefault((scope, key), payload)
        if stored != payload:
            raise AgentResearchError(409, "IDEMPOTENCY_CONFLICT")
        return 201, result

    http = client(auth, handler)
    activate(http, auth, "apf08-session-key-001")
    payload = operation_example("createAgentProcessingPreview")
    first = http.post(PREVIEW, json=payload, headers=WRITE_HEADERS)
    replay = http.post(PREVIEW, json=payload, headers=WRITE_HEADERS)
    conflict = http.post(PREVIEW, json=payload | {"expectedTurnVersion": 2}, headers=WRITE_HEADERS)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json() == result
    assert first.headers["cache-control"] == replay.headers["cache-control"] == "no-store"
    check_problem(conflict, 409, "IDEMPOTENCY_CONFLICT")
    assert len(replays) == 1
    assert all(
        call[:5]
        == (
            "createAgentProcessingPreview",
            {"id": "dialogue-1", "turnId": "turn-1"},
            {},
            "local-operator",
            KEY,
        )
        for call in calls
    )
    trace, scope = calls[0][-2:]
    assert trace == first.headers["traceparent"].split("-")[1]
    assert len(scope) == 64 and scope != http.cookies["atl_session"]
    assert {call[-1] for call in calls} == {scope}

    activate(http, auth, "apf08-session-key-002")
    assert http.post(PREVIEW, json=payload, headers=WRITE_HEADERS).status_code == 201
    assert calls[-1][-1] != scope


@pytest.mark.parametrize("query", ["?limit=201", "?cursor=a&cursor=b", "?unknown=1", "?limit=true"])
def test_invalid_pagination_fails_before_private_relay(query):
    auth = LocalAuth()
    calls = []
    http = client(auth, lambda *args, **kwargs: calls.append((args, kwargs)))
    activate(http, auth, "apf08-session-key-001")
    check_problem(http.get("/api/v1/research/agent/ideas" + query), 422, "INVALID_REQUEST")
    assert calls == []


def test_invalid_body_and_unexpected_private_fields_are_redacted():
    auth = LocalAuth()
    calls = []
    private = "synthetic-private-marker"
    result = operation_example("getAgentProcessingStatus", status=200)

    def handler(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, result | {"projection": private}

    http = client(auth, handler)
    activate(http, auth, "apf08-session-key-001")
    headers = WRITE_HEADERS | {"Content-Type": "application/json"}
    malformed = '{"profileId":"research-v1","profileId":"synthetic-private-marker"}'
    rejected = http.post(PREVIEW, content=malformed, headers=headers)
    check_problem(rejected, 422, "INVALID_REQUEST")
    assert not calls
    response = http.get(STATUS)
    check_problem(response, 503, "PROVIDER_UNAVAILABLE")
    assert len(calls) == 1
    assert private not in response.text + rejected.text


def test_approval_problem_codes_are_operation_specific():
    auth = LocalAuth()

    def handler(operation, *args, **context):
        value = problem_value(429, "QUANT_CAPACITY_EXHAUSTED", context["trace"], operation)
        return 429, value | {"title": "synthetic-private-title"}

    http = client(auth, handler, approval_handler=handler)
    activate(http, auth, "apf08-session-key-001")
    payload = operation_example("decideAgentQuantApproval")
    check_problem(
        http.post(APPROVAL, json=payload, headers=WRITE_HEADERS), 429, "QUANT_CAPACITY_EXHAUSTED"
    )
    response = http.get(STATUS)
    check_problem(response, 429, "BUDGET_EXHAUSTED")
    assert "synthetic-private-title" not in response.text


def test_setup_is_separate_and_refuses_an_unsafe_response():
    auth = LocalAuth()
    agent_calls, setup_calls = [], []
    synthetic = "synthetic-test-value-never-a-provider-key"
    result = operation_example("saveAgentProviderCredential", status=200)

    def setup_handler(operation, params, query, payload, **context):
        setup_calls.append(operation.operation_id)
        return 200, result | {"credential": payload["credential"]}

    http = client(auth, lambda *args, **kwargs: agent_calls.append(args), setup_handler)
    activate(http, auth, "apf08-session-key-001")
    payload = {"expectedCredentialVersion": 1, "credential": synthetic}
    response = http.put(SETUP, json=payload, headers=WRITE_HEADERS)
    check_problem(response, 503, "PROVIDER_UNAVAILABLE")
    assert synthetic not in response.text
    assert setup_calls == ["saveAgentProviderCredential"]
    assert agent_calls == []
