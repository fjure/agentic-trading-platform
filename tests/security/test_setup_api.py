"""Public auth/body/projection boundaries; only synthetic credential canaries."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth

ORIGIN = "http://127.0.0.1:8000"
CANARY = "synthetic-setup-input-never-return-this"


class Setup:
    def __init__(self):
        self.calls = 0
        self.extra = False

    def read_integrations(self):
        value = {
            name: {"status": "UNCONFIGURED", "restartRequired": False} for name in ("alpaca", "jev")
        }
        if self.extra:
            value["alpaca"]["apiKey"] = CANARY
        return value

    def save_alpaca(self, payload):
        self.calls += 1
        assert payload["apiKey"] == CANARY
        return {"status": "CONFIGURED", "restartRequired": True}

    def save_jev(self, payload):
        return self.save_alpaca(payload)


def client(setup=None, *, authenticated=True):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, setup=setup),
        base_url=ORIGIN,
        client=("127.0.0.1", 46011),
        raise_server_exceptions=False,
    )
    if authenticated:
        result = http.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": ORIGIN, "Idempotency-Key": "setup-activate-0001"},
        )
        assert result.status_code == 204
    return http


def write(http, value, *, origin=ORIGIN, provider="alpaca"):
    return http.put(
        "/api/v1/integrations/" + provider,
        json=value,
        headers={"Origin": origin, "Idempotency-Key": "setup-request-0001"},
    )


def payload():
    return {
        "apiKey": CANARY,
        "apiSecret": CANARY,
        "expectedAccountId": "bafcc130-92d2-48da-8db3-6d1a67a8dc17",
        "paperOnly": True,
    }


def test_store_success_returns_only_status_and_never_activates_trading():
    setup = Setup()
    with client(setup) as http:
        for provider, value in (("alpaca", payload()), ("jev", {"apiKey": CANARY})):
            response = write(http, value, provider=provider)
            assert response.status_code == 200
            assert response.json() == {"status": "CONFIGURED", "restartRequired": True}
            assert CANARY not in response.text
            assert response.headers["cache-control"] == "no-store"
    assert setup.calls == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("apiKey", ""),
        ("apiKey", " " + CANARY),
        ("apiKey", CANARY * 20),
        ("apiSecret", CANARY + "\n"),
        ("expectedAccountId", CANARY),
        ("paperOnly", False),
        ("baseUrl", CANARY),
    ],
)
def test_invalid_secret_requests_never_echo_body_or_reach_store(field, value):
    setup = Setup()
    with client(setup) as http:
        response = write(http, {**payload(), field: value})
        assert response.status_code == 400
        assert CANARY not in response.text
    assert setup.calls == 0


@pytest.mark.parametrize("authenticated,origin", [(False, ORIGIN), (True, "http://evil.invalid")])
def test_unauthorized_origin_or_session_has_no_store_effect(authenticated, origin):
    setup = Setup()
    with client(setup, authenticated=authenticated) as http:
        response = write(http, payload(), origin=origin)
        assert response.status_code in (400, 401, 403)
        assert CANARY not in response.text
    assert setup.calls == 0


def test_internal_status_cannot_smuggle_credential_fields():
    setup = Setup()
    setup.extra = True
    with client(setup) as http:
        response = http.get("/api/v1/integrations")
        assert response.status_code == 503
        assert CANARY not in response.text


def test_missing_services_remain_unavailable_and_never_echo_input():
    with client() as http:
        response = write(http, payload())
        assert response.status_code == 503
        assert CANARY not in response.text


@pytest.mark.parametrize(
    "value", ["0", "-1", "NaN", "Infinity", "1000000.01", "1e2", "0.0000000000000000001"]
)
def test_demo_rejects_invalid_budgets_before_runtime(value):
    with client() as http:
        response = http.post(
            "/api/v1/demo/runs",
            json={"initialBudget": value, "currency": "USD", "strategy": "mock-neutral"},
            headers={"Origin": ORIGIN, "Idempotency-Key": "demo-invalid-0001"},
        )
        assert response.status_code == 400


def test_duplicate_and_large_secret_bodies_are_redacted():
    setup = Setup()
    with client(setup) as http:
        for raw in ('{"apiKey":"' + CANARY + '","apiKey":"duplicate"}', CANARY * 1000):
            response = http.put(
                "/api/v1/integrations/jev",
                content=raw,
                headers={
                    "Origin": ORIGIN,
                    "Idempotency-Key": "setup-invalid-0001",
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 400
            assert CANARY not in response.text
    assert setup.calls == 0
