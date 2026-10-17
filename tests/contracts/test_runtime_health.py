"""D09 / PTV-21: health proves local liveness/readiness, never order permission."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "http://127.0.0.1:8000"
PRIVATE = "http://127.0.0.1:8001"
CANARY = "synthetic-secret-health-canary"
SCHEMA = json.loads((ROOT / "contracts/schemas/v1/runtime-health.schema.json").read_text())
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text())


def client_for(health=None, *, peer="127.0.0.1", transport=PRIVATE):
    app = create_app(auth=LocalAuth(PUBLIC, transport_origin=PRIVATE), health=health)
    return TestClient(
        app, base_url=transport, client=(peer, 43210),
        headers={"Host": "127.0.0.1:8000", "Origin": PUBLIC},
    )


def assert_health(response):
    Draft202012Validator(SCHEMA).validate(response.json())
    Draft202012Validator(OAS["components"]["schemas"]["RuntimeHealth"]).validate(response.json())
    assert response.headers["cache-control"] == "no-store"
    assert CANARY not in response.text
    assert CANARY not in str(response.headers)


def test_health_and_ready_are_session_free_but_control_reads_remain_authenticated():
    with client_for(lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}}) as client:
        for path in ("/health", "/ready"):
            result = client.get(path)
            assert result.status_code == 200
            assert_health(result)
        assert client.get("/api/v1/mode").status_code == 401


def test_optional_jev_failure_cannot_block_database_readiness():
    value = {"status": "DEGRADED", "checks": {"DATABASE": "HEALTHY", "DECISION": "UNAVAILABLE"}}
    with client_for(lambda: value) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == value
        assert_health(ready)
        live = client.get("/health")
        assert live.status_code == 200
        assert live.json()["status"] == "HEALTHY"


def test_database_down_changes_readiness_without_falsely_stopping_process_liveness():
    value = {"status": "UNAVAILABLE", "checks": {"DATABASE": "UNAVAILABLE"}}
    with client_for(lambda: value) as client:
        live, ready = client.get("/health"), client.get("/ready")
        assert live.status_code == 200 and live.json()["status"] == "HEALTHY"
        assert ready.status_code == 503
        assert_health(live)
        assert_health(ready)


def test_health_callback_exception_is_redacted_without_echoing_exception_or_url():
    def fail():
        raise RuntimeError(f"https://{CANARY}.invalid/private")

    with client_for(fail) as client:
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert ready.json() == {"status": "UNAVAILABLE", "checks": {"DATABASE": "UNAVAILABLE"}}
        assert_health(ready)


@pytest.mark.parametrize("value", [
    {}, {"status": "HEALTHY", "checks": {}},
    {"status": "HEALTHY", "checks": {"DATABASE": CANARY}},
    {"status": CANARY, "checks": {"DATABASE": "HEALTHY"}},
    {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY", "raw": CANARY}},
    {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}, "payload": CANARY},
])
def test_invalid_callback_fields_fail_closed_in_both_runtime_and_schema(value):
    assert not Draft202012Validator(SCHEMA).is_valid(value)
    with client_for(lambda: copy.deepcopy(value)) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["checks"] == {"DATABASE": "UNAVAILABLE"}
        assert_health(response)


@pytest.mark.parametrize("headers", [
    {"Host": "evil.invalid:8000"}, {"Host": "127.0.0.1:8001"},
    {"Origin": PRIVATE}, {"Origin": "http://evil.invalid:8000"},
    {"Forwarded": "for=127.0.0.1"}, {"X-Forwarded-Host": "127.0.0.1:8000"},
])
@pytest.mark.parametrize("path", ["/health", "/ready"])
def test_health_routes_retain_actual_transport_public_host_and_origin_boundaries(headers, path):
    with client_for(lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}}) as client:
        assert client.get(path, headers=headers).status_code == 400


@pytest.mark.parametrize("peer,transport", [
    ("192.0.2.1", PRIVATE), ("127.0.0.1", PUBLIC),
    ("127.0.0.1", "http://192.0.2.1:8001"),
])
def test_loopback_header_cannot_forge_real_peer_or_local_socket(peer, transport):
    with client_for(peer=peer, transport=transport) as client:
        assert client.get("/health").status_code == 400
        assert client.get("/ready").status_code == 400


def test_canonical_health_operations_and_fixed_schema_mirror():
    public = OAS["components"]["schemas"]["RuntimeHealth"]
    assert public == {k: v for k, v in SCHEMA.items() if k not in {"$id", "$schema", "title"}}
    for path, operation in (("/health", "getRuntimeHealth"), ("/ready", "getRuntimeReadiness")):
        assert set(OAS["paths"][path]) == {"get"}
        assert OAS["paths"][path]["get"]["security"] == []
        assert OAS["paths"][path]["get"]["operationId"] == operation
