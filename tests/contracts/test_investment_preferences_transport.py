"""Verify the real onboarding HTTP boundary and canonical schemas without external I/O."""

from copy import deepcopy

from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import AuthDenied, LocalAuth
from services.control_api.investment_preferences_routes import PREFERENCES_PATH
from services.control_api.investment_preferences_store import PreferencesError
from tests.contracts.test_control_api import ORIGIN, validate, write

DEFAULTS = {
    "experienceLevel": "UNSPECIFIED",
    "goals": [],
    "horizon": "UNSPECIFIED",
    "lossTolerance": "UNSPECIFIED",
    "contextReference": {"context": "FAKE", "namespace": "fake"},
    "onboardingSteps": [],
}


class PreferencesPort:
    def __init__(self):
        self.calls = []
        self.conflict = False
        self.receipts = {}

    def read(self, actor):
        self.calls.append(("read", actor))
        return deepcopy(DEFAULTS) | {"schemaVersion": "1", "version": 0, "updatedAt": None}

    def update(self, actor, payload, key):
        self.calls.append(("update", actor, payload, key))
        if self.conflict:
            raise PreferencesError(409, "IDEMPOTENCY_CONFLICT")
        result = {k: v for k, v in payload.items() if k in DEFAULTS} | {
            "schemaVersion": "1", "version": 1, "updatedAt": "2026-11-24T09:00:00Z",
        }
        self.receipts[actor, key] = (deepcopy(payload), result)
        return result

    def replay(self, actor, payload, key):
        receipt = self.receipts.get((actor, key))
        if receipt is None:
            return None
        if payload != receipt[0]:
            raise PreferencesError(409, "IDEMPOTENCY_CONFLICT")
        return receipt[1]


def create_client(backend, demo=None):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, preferences=backend, demo=demo), base_url=ORIGIN,
        client=("127.0.0.1", 46000), raise_server_exceptions=False,
    )
    return http, auth


def activate(http, auth):
    assert write(
        http, "/api/v1/session", {"activationCode": auth.issue_activation_code()},
        **{"Idempotency-Key": "onboarding-test-activation"},
    ).status_code == 204


def test_preferences_session_and_origin_preserve_canonical_problem_and_actor_scope():
    backend = PreferencesPort()
    http, auth = create_client(backend)
    denied = http.get(PREFERENCES_PATH)
    assert denied.status_code == 401
    validate("InvestmentProblemV1", denied.json())
    assert backend.calls == []
    activate(http, auth)
    response = http.get(PREFERENCES_PATH)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    validate("InvestmentPreferencesV1", response.json())
    assert backend.calls == [("read", "local-operator")]
    payload = deepcopy(DEFAULTS) | {"expectedVersion": 0, "reset": False, "lossTolerance": "HIGH"}
    validate("InvestmentPreferencesUpdateV1", payload)
    response = write(http, PREFERENCES_PATH, payload, method="PUT", Origin="http://other.invalid")
    assert response.status_code == 403
    validate("InvestmentProblemV1", response.json())
    assert backend.calls == [("read", "local-operator")]
    response = write(http, PREFERENCES_PATH, payload | {"actorId": "foreign"}, method="PUT")
    assert response.status_code == 422
    validate("InvestmentProblemV1", response.json())
    assert backend.calls == [("read", "local-operator")]
    response = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert response.status_code == 200
    validate("InvestmentPreferencesV1", response.json())
    assert backend.calls[-1] == ("update", "local-operator", payload, "action-key-00001")
    backend.conflict = True
    response = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert response.status_code == 409 and response.json()["code"] == "IDEMPOTENCY_CONFLICT"
    validate("InvestmentProblemV1", response.json())


def test_demo_selection_requires_existing_run_and_get_never_starts_one():
    class DemoReadOnly:
        def __init__(self):
            self.reads = []
            self.offline = False

        def get_run(self, run_id, *, actor):
            self.reads.append((run_id, actor))
            if self.offline:
                raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
            if run_id == "foreign":
                raise AuthDenied(404, "RESOURCE_NOT_FOUND")
            return {"runId": run_id} if (run_id, actor) == ("demo-1", "local-operator") else None

    backend, demo = PreferencesPort(), DemoReadOnly()
    http, auth = create_client(backend, demo)
    activate(http, auth)
    assert http.get(PREFERENCES_PATH).status_code == 200
    assert not demo.reads
    payload = deepcopy(DEFAULTS) | {
        "expectedVersion": 0, "reset": False,
        "contextReference": {"context": "DEMO", "namespace": "demo", "demoRunId": "unknown"},
    }
    response = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert response.status_code == 404
    validate("InvestmentProblemV1", response.json())
    assert backend.calls == [("read", "local-operator")]
    payload["contextReference"]["demoRunId"] = "foreign"
    response = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert response.status_code == 404
    payload["contextReference"]["demoRunId"] = "demo-1"
    response = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert response.status_code == 200
    validate("InvestmentPreferencesV1", response.json())
    assert demo.reads == [
        ("unknown", "local-operator"), ("foreign", "local-operator"), ("demo-1", "local-operator"),
    ]
    demo.offline = True
    replay = write(http, PREFERENCES_PATH, payload, method="PUT")
    assert replay.status_code == 200 and replay.json() == response.json()
    conflict = write(http, PREFERENCES_PATH, payload | {"horizon": "LONG"}, method="PUT")
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert demo.reads == [
        ("unknown", "local-operator"), ("foreign", "local-operator"), ("demo-1", "local-operator"),
    ]
