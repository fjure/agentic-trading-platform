"""Rotation metadata must pause first; synthetic secret input reaches setup only."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.paper_commands import PaperCommandError
from services.setup.app import create_app as setup_app
from services.setup.store import SetupUnavailable

ORIGIN = "http://127.0.0.1:8000"
CANARY = "synthetic-rotation-secret-canary-never-an-actual-key"
ACCOUNT = "11111111-2222-4333-8444-555555555555"
PAYLOAD = {"apiKey": CANARY, "apiSecret": CANARY + "-secret",
           "expectedAccountId": ACCOUNT, "paperOnly": True}


class Paper:
    def __init__(self, events, *, completed=True):
        self.events, self.completed, self.paused = events, completed, False

    def rotation_begin(self, actor, key, trace):
        self.events.append(("begin", actor, key, trace))
        self.paused = True
        return 7

    def rotation_complete(self, generation):
        self.events.append(("complete", generation))
        return self.completed

    def read_status(self, _trace):
        saved = self.completed and any(event[0] == "complete" for event in self.events)
        return {"setupState": "SAVED" if saved else "NOT_CONFIGURED"}


class Setup:
    def __init__(self, events, *, error=None):
        self.events, self.error = events, error
        self.saved = None

    def save_alpaca_generation(self, payload, generation):
        self.events.append(("save", generation))
        self.saved = deepcopy(payload)
        if self.error:
            raise self.error
        return {"status": "CONFIGURED", "restartRequired": True}

    def read_integrations(self):
        return {
            name: {"status": "UNCONFIGURED", "restartRequired": False}
            for name in ("alpaca", "jev")
        }


def client(paper, setup):
    auth = LocalAuth()
    http = TestClient(create_app(auth=auth, setup=setup, paper=paper), base_url=ORIGIN,
                      client=("127.0.0.1", 46011), raise_server_exceptions=False)
    assert http.post("/api/v1/session", json={"activationCode": auth.issue_activation_code()},
                     headers={"Origin": ORIGIN, "Idempotency-Key": "rotation-session-01"}
                     ).status_code == 204
    return http


def save(http):
    return http.put("/api/v1/integrations/alpaca", json=PAYLOAD, headers={
        "Origin": ORIGIN, "Idempotency-Key": "rotation-request-01",
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-00",
    })


def test_rotation_pauses_before_private_save_then_completes_with_metadata_only():
    events = []
    paper, setup = Paper(events), Setup(events)
    with client(paper, setup) as http:
        response = save(http)
    assert response.status_code == 200
    assert response.json() == {"status": "CONFIGURED", "restartRequired": True}
    assert events == [("begin", "local-operator", "rotation-request-01", "a" * 32),
                      ("save", 7), ("complete", 7)]
    assert setup.saved == PAYLOAD and paper.paused
    assert CANARY not in repr(events) and ACCOUNT not in repr(events)
    assert CANARY not in response.text and response.headers["cache-control"] == "no-store"


def test_fresh_generation_save_is_visible_without_legacy_file_or_credential_read():
    events = []
    paper, setup = Paper(events), Setup(events)
    with client(paper, setup) as http:
        assert http.get("/api/v1/integrations").json()["alpaca"]["status"] == "UNCONFIGURED"
        assert save(http).status_code == 200
        response = http.get("/api/v1/integrations")
    assert response.status_code == 200
    assert response.json() == {
        "alpaca": {"status": "CONFIGURED", "restartRequired": True},
        "jev": {"status": "UNCONFIGURED", "restartRequired": False},
    }
    assert CANARY not in response.text and ACCOUNT not in response.text


@pytest.mark.parametrize("failure", [SetupUnavailable(), RuntimeError(CANARY)])
def test_crash_during_private_save_leaves_pause_in_place_and_never_completes(failure, caplog):
    events = []
    paper, setup = Paper(events), Setup(events, error=failure)
    with client(paper, setup) as http:
        response = save(http)
    assert response.status_code == 503
    assert response.json()["code"] == "TEMPORARILY_UNAVAILABLE"
    assert [event[0] for event in events] == ["begin", "save"] and paper.paused
    assert CANARY not in response.text and CANARY not in caplog.text


def test_stale_rotation_completion_is_conflict_without_retry_or_success():
    events = []
    paper, setup = Paper(events, completed=False), Setup(events)
    with client(paper, setup) as http:
        response = save(http)
    assert response.status_code == 409 and response.json()["code"] == "VERSION_CONFLICT"
    assert [event[0] for event in events] == ["begin", "save", "complete"] and paper.paused
    assert CANARY not in response.text


def test_rejected_rotation_begin_never_reaches_private_save_or_complete():
    class RejectedPaper(Paper):
        def rotation_begin(self, *_):
            self.events.append(("begin",))
            raise PaperCommandError(409, "BROKER_STATUS_UNKNOWN")
    events = []
    paper, setup = RejectedPaper(events), Setup(events)
    with client(paper, setup) as http:
        response = save(http)
    assert response.status_code == 409
    assert response.json()["code"] == "BROKER_STATUS_UNKNOWN"
    assert events == [("begin",)] and setup.saved is None
    assert CANARY not in response.text


@pytest.mark.parametrize("generation", ["0", "01", "-1", "true", "9007199254740992"])
def test_private_generation_endpoint_rejects_noncanonical_or_unbounded_generation(generation):
    class Store:
        def save_generation(self, *_):
            raise AssertionError("invalid generation reached storage")
    with TestClient(setup_app(Store()), raise_server_exceptions=False) as http:
        response = http.put("/integrations/alpaca/generations/" + generation, json=PAYLOAD)
    assert response.status_code == 400 and response.json() == {"code": "INVALID_REQUEST"}
    assert CANARY not in response.text


def test_private_generation_endpoint_binds_allocator_generation_without_echoing_input():
    class Store:
        def save_generation(self, provider, generation, value):
            assert (provider, generation) == ("alpaca", 7)
            assert value["apiKey"] == CANARY and value["apiSecret"] == CANARY + "-secret"
            return {"status": "CONFIGURED", "credentialGeneration": generation}
    with TestClient(setup_app(Store()), raise_server_exceptions=False) as http:
        response = http.put("/integrations/alpaca/generations/7", json=PAYLOAD)
    assert response.status_code == 200
    assert response.json() == {"status": "CONFIGURED", "credentialGeneration": 7}
    assert CANARY not in response.text
