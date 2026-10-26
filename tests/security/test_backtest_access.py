"""BKT-07 authentication, Origin, hostile query and sanitized error boundaries."""

import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from tests.contracts.test_backtest_transport import example

BASE = "/api/v1/research/backtests"
ORIGIN = "http://127.0.0.1:8000"


class Spy:
    def __init__(self):
        self.calls = []

    def read_strategies(self):
        self.calls.append("read")
        return example("strategy-catalog")

    def create_run(self, *args):
        self.calls.append("write")
        return example("run")

    def read_runs(self, values):
        self.calls.append("list")
        return example("run-page")


def client(service=None, *, activate=True):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, backtesting=service),
        base_url=ORIGIN,
        client=("127.0.0.1", 46000),
        raise_server_exceptions=False,
    )
    if activate:
        response = http.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": ORIGIN, "Idempotency-Key": "activate-key-0001"},
        )
        assert response.status_code == 204
    return http


def test_session_and_origin_reject_before_side_effects():
    service = Spy()
    http = client(service, activate=False)
    assert http.get(BASE + "/strategies").status_code == 401
    response = http.post(
        BASE + "/runs",
        json=example("request"),
        headers={"Origin": ORIGIN, "Idempotency-Key": "backtest-test-0001"},
    )
    assert response.status_code == 401
    assert response.json()["type"] == "urn:agentic-trading-lab:backtest-problem"
    http = client(service)
    response = http.post(
        BASE + "/runs",
        json=example("request"),
        headers={"Origin": "https://invalid.example", "Idempotency-Key": "backtest-test-0001"},
    )
    assert response.status_code == 403
    assert service.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=201",
        "limit=1&limit=2",
        "status=LIVE",
        "cursor=bad",
        "apiKey=synthetic-canary",
        "instrumentId=SPY",
        "resultType=summary",
    ],
)
def test_hostile_queries_do_not_reach_service(query):
    service = Spy()
    response = client(service).get(BASE + "/runs?" + query)
    assert response.status_code == 400
    assert set(response.json()) == {"type", "title", "status", "code", "traceId"}
    assert service.calls == []


@pytest.mark.parametrize(
    "content",
    [
        b'{"startCash":"1","startCash":"2"}',
        b"NaN",
        b'{"rawPayload":{}}',
        b"x" * 16385,
    ],
)
def test_duplicate_nonfinite_and_oversized_bodies_fail_before_creation(content):
    service = Spy()
    response = client(service).post(
        BASE + "/runs",
        content=content,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/json",
            "Idempotency-Key": "backtest-test-0001",
        },
    )
    assert response.status_code == 400
    assert service.calls == []


def test_safe_fail_closed_dependency_and_response_validation():
    response = client().get(BASE + "/strategies")
    assert response.status_code == 503
    assert response.json()["code"] == "UNAVAILABLE"
    spy = Spy()
    spy.read_strategies = lambda: {"sql": "never exposed"}
    response = client(spy).get(BASE + "/strategies")
    assert response.status_code == 503
    assert "sql" not in response.text
