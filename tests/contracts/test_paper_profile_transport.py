"""Authenticated profile transport; SQL persistence is certified separately."""

from copy import deepcopy

import httpx
import pytest

from services.control_api.paper_client import PaperClient
from services.control_api.paper_commands import PaperCommandError
from tests.contracts.test_paper_transport import ORIGIN, client
from tests.contracts.test_paper_workflow_contracts import example, validator


class Profiles:
    def __init__(self):
        self.calls = []

    def read_profile(self, trace):
        return example("paper-trading-profile") | {"traceId": trace}

    def save_profile(self, payload, actor, key, trace):
        self.calls.append((deepcopy(payload), actor, key, trace))
        return example("paper-trading-profile.configured") | {"traceId": trace}


def test_public_get_preserves_unconfigured_null_budget_and_versions():
    response = client(Profiles()).get("/api/v1/paper/trading-profile")
    assert response.status_code == 200
    validator("PaperTradingProfile").validate(response.json())
    assert response.json()["riskBudget"] is None and response.json()["profileVersion"] == 0


def test_public_put_binds_actor_idempotency_trace_and_explicit_configuration():
    profiles = Profiles()
    payload = example("paper-trading-profile-update-request")
    response = client(profiles).put("/api/v1/paper/trading-profile", json=payload, headers={
        "Origin": ORIGIN, "Idempotency-Key": "profile-save-key-0001",
        "traceparent": "00-" + "b"*32 + "-" + "c"*16 + "-00",
    })
    assert response.status_code == 200, response.text
    validator("PaperTradingProfile").validate(response.json())
    assert profiles.calls == [(payload, "local-operator", "profile-save-key-0001", "b"*32)]
    assert "entryGateOpen" not in response.json()


@pytest.mark.parametrize("change", [
    {"feeAttestation": {"scope": "alpaca-paper-direct-retail-us-equity-zero-v1", "confirmed": 1}},
    {"confirmation": "START_PAPER"}, {"riskBudget": "0"}, {"expectedProfileVersion": -1},
    {"brokerSecret": "synthetic-only-canary"},
])
def test_invalid_save_never_reaches_profile_port(change):
    profiles = Profiles()
    payload = example("paper-trading-profile-update-request") | change
    response = client(profiles).put("/api/v1/paper/trading-profile", json=payload, headers={
        "Origin": ORIGIN, "Idempotency-Key": "profile-save-key-0001",
    })
    assert response.status_code == 400 and response.json()["code"] == "INVALID_REQUEST"
    assert profiles.calls == [] and "synthetic-only-canary" not in response.text


@pytest.mark.parametrize("authenticated,origin,expected", [
    (False, ORIGIN, 401), (True, "http://evil.invalid", 403),
])
def test_save_requires_local_session_and_exact_origin(authenticated, origin, expected):
    profiles = Profiles()
    response = client(profiles, authenticated=authenticated).put(
        "/api/v1/paper/trading-profile", json=example("paper-trading-profile-update-request"),
        headers={"Origin": origin, "Idempotency-Key": "profile-save-key-0001"},
    )
    assert response.status_code == expected and profiles.calls == []


def test_private_relay_uses_fixed_host_and_strict_public_projection():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=example("paper-trading-profile"))

    relay = PaperClient(transport=httpx.MockTransport(handle))
    assert relay.read_profile("a"*32)["riskBudget"] is None
    assert str(calls[0].url) == "http://paper-api:8012/trading-profile?trace=" + "a"*32
    assert calls[0].method == "GET"
    bad = example("paper-trading-profile") | {"apiKey": "synthetic-only-canary"}
    relay = PaperClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=bad)))
    with pytest.raises(PaperCommandError, match="TEMPORARILY_UNAVAILABLE"):
        relay.read_profile("a"*32)
