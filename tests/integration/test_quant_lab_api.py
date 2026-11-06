"""ASGI auth and relay tests, including actual HTTP persistence against PostgreSQL."""

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.backtesting.jobs import BacktestError
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.backtest_client import BacktestClient
from services.control_api.quant_lab_dto import BASE, HEX, OPERATIONS, SCOPE_HEADER, validator
from services.runtime.paper_api import create_private_paper_app

ORIGIN = "http://127.0.0.1:8000"
ID = "11111111-1111-4111-8111-111111111111"
SECOND_ID = "22222222-2222-4222-8222-222222222222"
CANDIDATE = "a" * 64
CURSOR = "c" * 32
WRITE = {"Origin": ORIGIN, "Idempotency-Key": "action-key-00001"}
EXAMPLES = Path(__file__).resolve().parents[2] / "contracts/examples/v1"
RESPONSE_FIXTURES = {
    "get_strategies": "strategy-catalog",
    "create_experiment": "experiment-draft",
    "list_experiments": "experiment-page",
    "get_experiment": "experiment-draft",
    "validate_experiment": "validation",
    "start_experiment_batch": "batch-accepted",
    "list_experiment_batches": "batch-page",
    "get_experiment_batch": "batch-accepted",
    "list_candidates": "candidate-page",
    "list_results": "result-page",
    "get_comparison": "comparison-no-selection",
    "get_equity": "equity-page",
    "get_drawdown": "drawdown-page",
    "compare_experiments": "experiment-comparison",
    "get_reliability_report": "reliability-findings-page",
}
REQUEST_FIXTURES = {
    "ExperimentCreateRequest": "experiment-request-strict",
    "ValidationRequest": "validation-request",
    "BatchStartRequest": "batch-start-request",
}


def fixture(name):
    return json.loads((EXAMPLES / f"quant-lab-{name}.valid.json").read_text(encoding="utf-8"))


class SpyService:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.response = None
        self.cursor_scope = None

    def __getattr__(self, name):
        def invoke(*args, **context):
            self.calls.append((name, args, context))
            if self.failure is not None:
                raise self.failure
            if self.response is not None:
                return self.response
            if name == "list_experiments":
                if args == ({"cursor": CURSOR},):
                    if self.cursor_scope != context["scope"]:
                        raise BacktestError(400, "CURSOR_INVALID")
                else:
                    self.cursor_scope = context["scope"]
            return fixture(RESPONSE_FIXTURES[name])

        return invoke


def activate(http, auth, suffix):
    response = http.post(
        "/api/v1/session",
        json={"activationCode": auth.issue_activation_code()},
        headers={"Origin": ORIGIN, "Idempotency-Key": "activate-key-" + suffix},
    )
    assert response.status_code == 204


@pytest.fixture
def connected():
    with connect(SpyService()) as connection:
        yield connection


@contextmanager
def connect(service, *, legacy=None):
    auth = LocalAuth()
    forwarded = []
    with TestClient(
        create_private_paper_app(None, quant_lab=service, backtesting=legacy),
        base_url="http://paper-api:8012",
    ) as private:

        def relay(request):
            forwarded.append(request)
            response = private.request(
                request.method, str(request.url), content=request.content, headers=request.headers
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )

        backend = BacktestClient(transport=httpx.MockTransport(relay))
        public_app = create_app(auth=auth, quant_lab=backend.quant_lab, backtesting=backend)
        with TestClient(public_app, base_url=ORIGIN, client=("127.0.0.1", 46000)) as public:
            yield auth, service, private, public_app, public, forwarded


def problem(response, status, code):
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["cache-control"] == "no-store"
    if status == 429:
        assert 1 <= int(response.headers["retry-after"]) <= 300
    value = response.json()
    validator("Problem").validate(value)
    assert value["code"] == code


@pytest.mark.parametrize("operation", OPERATIONS.values(), ids=OPERATIONS.keys())
def test_wire_round_trip_all_operations(connected, operation):
    auth, service, _, _, public, forwarded = connected
    activate(public, auth, "0001")
    path = BASE + operation.path.format(id=ID, candidateId=CANDIDATE, reportId=SECOND_ID)
    query = {}
    if operation.service in {"get_equity", "get_drawdown"}:
        query = {"phase": "validation", "fold": 2, "role": "SEARCH_CANDIDATE"}
    elif operation.service == "compare_experiments":
        query = {"batchId": [ID, SECOND_ID]}
    elif operation.query:
        query = {"limit": 19}
    body = fixture(REQUEST_FIXTURES[operation.request]) if operation.request else None
    response = public.request(
        operation.method,
        path,
        params=query,
        json=body,
        headers=WRITE if body else {},
    )
    assert response.status_code == operation.status
    assert response.json() == fixture(RESPONSE_FIXTURES[operation.service])
    validator(operation.response).validate(response.json())
    ((name, args, context),) = service.calls
    assert name == operation.service
    if operation.query:
        assert args[-1] == query
        assert HEX.fullmatch(context["scope"])
    if operation.request:
        assert args[-1] == body
        assert context["actor"] == "local-operator"
        assert context["key"] == WRITE["Idempotency-Key"]
        assert context["trace"] == response.headers["traceparent"][3:35]
    (request,) = forwarded
    assert str(request.url).startswith("http://paper-api:8012" + path)
    assert {"cookie", "origin", "authorization", "forwarded"}.isdisjoint(request.headers)


def test_public_origin_login_csrf_private_scope_and_forgery(connected):
    auth, service, private, _, public, forwarded = connected
    path = BASE + "experiments"
    body = fixture("experiment-request-strict")
    problem(public.get(path), 401, "UNAUTHORIZED")
    problem(public.post(path, json=body, headers=WRITE), 401, "UNAUTHORIZED")
    activate(public, auth, "0001")
    problem(public.post(path, json=body), 400, "INVALID_REQUEST")
    problem(
        public.post(path, json=body, headers={**WRITE, "Origin": "http://other"}), 403, "FORBIDDEN"
    )
    problem(public.post(path, json=body, headers={"Origin": ORIGIN}), 400, "INVALID_REQUEST")
    problem(private.get(path), 403, "FORBIDDEN")
    problem(
        private.get(path, headers=[(SCOPE_HEADER, CANDIDATE), (SCOPE_HEADER, CANDIDATE)]),
        403,
        "FORBIDDEN",
    )
    problem(
        private.get(path, headers={SCOPE_HEADER: CANDIDATE, "Origin": ORIGIN}), 403, "FORBIDDEN"
    )
    problem(
        private.get(path, headers={SCOPE_HEADER: CANDIDATE, "Authorization": "test-marker"}),
        403,
        "FORBIDDEN",
    )
    assert not forwarded and not service.calls
    response = public.get(path, headers={SCOPE_HEADER: CANDIDATE})
    assert response.status_code == 200
    assert forwarded[0].headers[SCOPE_HEADER] != CANDIDATE
    assert forwarded[0].headers[SCOPE_HEADER] != public.cookies["atl_session"]


def test_distinct_authenticated_sessions_cannot_replay_cursor(connected):
    auth, _, _, app, public, forwarded = connected
    activate(public, auth, "0001")
    path = BASE + "experiments"
    assert public.get(path).status_code == 200
    assert public.get(path, params={"cursor": CURSOR}).status_code == 200
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 46001)) as other:
        activate(other, auth, "0002")
        problem(other.get(path, params={"cursor": CURSOR}), 400, "CURSOR_INVALID")
    scopes = [request.headers[SCOPE_HEADER] for request in forwarded]
    assert scopes[0] == scopes[1] != scopes[2]


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("experiments?limit=201", "INVALID_REQUEST"),
        ("experiments?limit=1&limit=2", "INVALID_REQUEST"),
        ("experiments?cursor=x", "CURSOR_INVALID"),
        ("experiments?cursor=" + "x" * 1025, "CURSOR_INVALID"),
        ("experiments?offset=1", "INVALID_REQUEST"),
        ("experiments/123", "INVALID_REQUEST"),
        ("experiment-comparisons", "INVALID_REQUEST"),
        (f"experiment-comparisons?batchId={ID}&batchId={ID}", "INVALID_REQUEST"),
        (f"experiment-batches/{ID}/results/{CANDIDATE}/equity?phase=test", "INVALID_REQUEST"),
        (
            f"experiment-batches/{ID}/results/{CANDIDATE}/drawdown?phase=holdout&fold=0",
            "INVALID_REQUEST",
        ),
        (
            f"experiment-batches/{ID}/results/{CANDIDATE}/equity?phase=holdout&role=SEARCH",
            "INVALID_REQUEST",
        ),
        (f"experiment-batches/{ID}/results?role=REFERENCE_FLAT", "INVALID_REQUEST"),
        (
            f"experiment-batches/{ID}/reliability-reports/{SECOND_ID}?section=UNKNOWN",
            "INVALID_REQUEST",
        ),
    ],
)
def test_invalid_paths_and_query_stopped_before_relay(connected, path, code):
    auth, service, _, _, public, forwarded = connected
    activate(public, auth, "0001")
    problem(public.get(BASE + path), 400, code)
    assert not forwarded and not service.calls


def test_unknown_fields_duplicate_keys_and_body_limits(connected):
    auth, service, _, _, public, forwarded = connected
    activate(public, auth, "0001")
    path = BASE + "experiments"
    body = fixture("experiment-request-strict")
    problem(
        public.post(path, json={**body, "unknown": "marker"}, headers=WRITE), 400, "INVALID_REQUEST"
    )
    for raw in ('{"x":1,"x":2}', "[" + " " * 16384 + "]"):
        problem(
            public.post(path, content=raw, headers={**WRITE, "Content-Type": "application/json"}),
            400,
            "INVALID_REQUEST",
        )
    assert not forwarded and not service.calls


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "IDEMPOTENCY_CONFLICT"),
        (422, "INVALID_PROTOCOL"),
        (429, "ADMISSION_LIMIT"),
    ],
)
def test_closed_service_problems_cross_private_relay(connected, status, code):
    auth, service, _, _, public, _ = connected
    activate(public, auth, "0001")
    service.failure = BacktestError(status, code)
    problem(
        public.post(BASE + "experiments", json=fixture("experiment-request-strict"), headers=WRITE),
        status,
        code,
    )


def test_corrupt_service_response_and_unexpected_error_are_sanitized(connected):
    auth, service, _, app, public, _ = connected
    activate(public, auth, "0001")
    path = BASE + "strategies"
    marker = "private-test-error-marker"
    service.response = {**fixture("strategy-catalog"), "unexpected": marker}
    for failure in (None, RuntimeError(marker)):
        service.failure = failure
        response = public.get(path)
        problem(response, 503, "UNAVAILABLE")
        assert marker not in response.text
    audit = app.state.quant_lab_metrics.audit_snapshot()
    assert len(audit) == 2
    assert all(set(item) == {"operation", "code", "traceId"} for item in audit)
    assert {item["operation"] for item in audit} == {"quant_get_strategies"}
    assert {item["code"] for item in audit} == {"UNAVAILABLE"}
    assert marker not in json.dumps(audit)


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (302, {"Location": "http://not-an-authorized-destination"}),
        (200, {"Content-Type": "text/plain"}),
        (200, {"Content-Type": "application/json", "Content-Encoding": "gzip"}),
        (200, {"Content-Type": "application/json", "Content-Length": str(16 * 1024 * 1024 + 1)}),
    ],
)
def test_relay_rejects_unsafe_upstream_envelopes(status, headers):
    calls = []

    def upstream(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(b"{}"))

    client = BacktestClient(transport=httpx.MockTransport(upstream))
    operation = OPERATIONS["getQuantLabStrategies"]
    with pytest.raises(BacktestError) as failure:
        client.quant_lab(
            operation,
            {},
            {},
            None,
            actor="local-operator",
            key=None,
            trace="b" * 32,
            scope=CANDIDATE,
        )
    assert (failure.value.status, failure.value.code) == (503, "UNAVAILABLE")
    assert calls == ["http://paper-api:8012" + BASE + "strategies"]


def diagnostic_service(service):
    """Report closed failure locations for this synthetic fixture without echoing data."""
    from traceback import extract_tb

    from sqlalchemy import event

    sql_errors = []
    control = service.storage.engine

    @event.listens_for(control, "handle_error")
    def capture_error(context):
        error = context.original_exception
        sql_errors.append(
            (
                getattr(error, "sqlstate", None),
                getattr(getattr(error, "diag", None), "statement_position", None),
            )
        )

    class Diagnostics:
        def __getattr__(self, name):
            def invoke(*args, **kwargs):
                sql_errors.clear()
                try:
                    value = getattr(service, name)(*args, **kwargs)
                except Exception as error:
                    if isinstance(error, BacktestError) and error.status != 503:
                        raise
                    frames = extract_tb(error.__traceback__)
                    locations = [
                        f"{Path(frame.filename).name}:{frame.lineno}"
                        for frame in frames
                        if "quant_lab" in frame.filename or "experiment_storage" in frame.filename
                    ]
                    print(
                        "SERVICE", name, type(error).__name__, ",".join(locations[-4:]), sql_errors
                    )
                    raise
                operation = next(item for item in OPERATIONS.values() if item.service == name)
                for error in validator(operation.response).iter_errors(value):
                    print("WIRE", name, error.validator, ".".join(map(str, error.absolute_path)))
                    break
                return value

            return invoke

    return Diagnostics()


def assert_published_http(service, *, batch_id, definition_id, selected):
    """Read actual worker artifacts through both API surfaces, using one session."""
    from services.control_api.backtest_service import BacktestService

    batch_id, definition_id = str(batch_id), str(definition_id)
    base = BASE + f"experiment-batches/{batch_id}"
    legacy = BacktestService(service.storage.engine, code_version=service.code_version)
    with connect(diagnostic_service(service), legacy=legacy) as (auth, _, _, app, http, _):
        activate(http, auth, "0001")

        def get(path, **params):
            response = http.get(path, params=params)
            if response.status_code != 200:
                # Only contract route kind and status are useful here.
                path_part = path.rsplit("/", 1)[-1]
                stage = (
                    path_part
                    if path_part
                    in {
                        "candidates",
                        "results",
                        "comparison",
                        "experiment-comparisons",
                        "equity",
                        "drawdown",
                    }
                    else "resource"
                )
                print("HTTP_READ", stage, response.status_code)
                raise AssertionError("unexpected read status")
            return response.json()

        batch = get(base)
        assert (batch["id"], batch["definitionId"], batch["state"]) == (
            batch_id,
            definition_id,
            "SUCCEEDED",
        )
        definition = get(BASE + f"experiments/{definition_id}")
        assert definition["definitionHash"] == batch["definitionHash"]
        candidates = get(base + "/candidates")["items"]
        search = next(item for item in candidates if item["role"] == "SEARCH_CANDIDATE")
        candidate = search["candidateId"]
        assert {item["role"] for item in candidates if item["candidateId"] == candidate} == {
            "SEARCH_CANDIDATE",
            "REFERENCE_FLAT",
        }

        results = get(base + "/results", limit=20)["items"]
        assert len(results) == 12
        validation = get(base + "/results", phase="validation", fold=0, candidateId=candidate)
        search_validation = next(
            item for item in validation["items"] if item["role"] == search["role"]
        )
        assert search_validation["evaluationStatus"] == "COMPLETE"
        assert search_validation["selectionValues"] is not None
        holdout = get(base + "/results", phase="holdout", candidateId=candidate)["items"]
        expected_status = "COMPLETE" if selected else "NOT_EVALUATED_NO_SELECTION"
        assert {item["role"]: item["evaluationStatus"] for item in holdout} == {
            "SEARCH_CANDIDATE": expected_status,
            "REFERENCE_FLAT": "COMPLETE",
        }
        link = search_validation["reliability"]
        report_url = base + "/reliability-reports/" + link["reportId"]
        findings = get(report_url, section="FINDINGS", limit=1)
        report = findings["report"]
        assert report["batchId"] == batch_id and report["runId"] == link["runId"]
        assert report["reportHash"] == link["reportHash"] and report["role"] == search["role"]
        assert report["evidenceStatus"] == link["evidenceStatus"]
        assert findings["findings"] and findings["revisionReferences"] == []
        revisions = get(report_url, section="REVISION_REFERENCES", limit=1)
        assert revisions["findings"] == [] and revisions["revisionReferences"]
        revisions_cursor = revisions["nextCursor"]
        assert revisions_cursor
        next_revisions = get(
            report_url, section="REVISION_REFERENCES", limit=1, cursor=revisions_cursor
        )
        assert next_revisions["report"]["reportHash"] == link["reportHash"]
        assert next_revisions["revisionReferences"] != revisions["revisionReferences"]
        foreign_report = (
            BASE + f"experiment-batches/{SECOND_ID}/reliability-reports/{link['reportId']}"
        )
        problem(http.get(foreign_report), 404, "NOT_FOUND")
        problem(
            http.get(
                report_url, params={"section": "FINDINGS", "limit": 1, "cursor": revisions_cursor}
            ),
            400,
            "CURSOR_INVALID",
        )
        for path, identifier in (
            ("runs", link["runId"]),
            ("reliability-reports", link["reportId"]),
        ):
            response = http.get(BASE + f"backtests/{path}/{identifier}")
            assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"

        report = get(base + "/comparison")
        assert report["status"] == "COMPLETE" and report["reportHash"] == batch["reportHash"]
        assert report["foldOverview"]["activity"]
        comparisons = get(BASE + "experiment-comparisons", batchId=batch_id)
        assert comparisons["batchIds"] == [batch_id] and comparisons["crossBatchRanking"] is None

        curve_base = base + f"/results/{candidate}"
        filters = dict(phase="validation", fold=0, limit=1)
        for kind in ("equity", "drawdown"):
            curve_url = curve_base + "/" + kind
            curve = get(curve_url, **filters)
            cursor = curve["nextCursor"]
            assert curve["role"] == search["role"] and curve["reportHash"] == batch["reportHash"]
            assert curve["totalPointCount"] >= 2 and cursor
            later = get(curve_url, **filters, cursor=cursor)
            assert later["items"][0]["at"] > curve["items"][0]["at"]
            other_kind = "drawdown" if kind == "equity" else "equity"
            problem(
                http.get(curve_base + "/" + other_kind, params={**filters, "cursor": cursor}),
                400,
                "CURSOR_INVALID",
            )
            problem(
                http.get(curve_url, params={**filters, "cursor": cursor, "role": "REFERENCE_FLAT"}),
                400,
                "CURSOR_INVALID",
            )
            with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 46001)) as other:
                activate(other, auth, f"curve-{kind}")
                problem(
                    other.get(curve_url, params={**filters, "cursor": cursor}),
                    400,
                    "CURSOR_INVALID",
                )
            reference = get(curve_url, phase="holdout", role="REFERENCE_FLAT")
            assert reference["role"] == "REFERENCE_FLAT" and reference["items"]
            if selected:
                assert get(curve_url, phase="holdout")["role"] == "SEARCH_CANDIDATE"
            else:
                problem(http.get(curve_url, params={"phase": "holdout"}), 404, "NOT_FOUND")


def guest():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy import text

    from packages.research.registry import definitions, register
    from services.control_api.quant_lab_service import QuantLabService
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    with engine.begin() as connection:
        register(connection)
    service = QuantLabService(role_engine(engine, "atl_control_api"), code_version="a" * 40)

    payload = fixture("experiment-request-strict")
    payload["instrumentId"] = str(definitions()[0][0].instrument_id)
    payload["searchSpaces"] = [{"strategyId": "flat-v1", "version": "1", "parameterValues": {}}]
    phases = [datetime(2026, 1, day, 14, 30, tzinfo=UTC) for day in range(5, 9)]

    def window(start):
        return {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    payload["protocol"]["folds"] = [
        {
            "fold": 0,
            **dict(zip(("training", "validation", "test"), map(window, phases[:3]), strict=True)),
        }
    ]
    payload["protocol"]["holdout"] = window(phases[-1])
    definitions_path = BASE + "experiments"

    def post(http, path, body, key, status):
        response = http.post(path, json=body, headers={**WRITE, "Idempotency-Key": key})
        if response.status_code != status:
            code = response.json().get("code")
            if code in {
                "UNAVAILABLE",
                "INVALID_REQUEST",
                "INVALID_PARAMETERS",
                "INVALID_POLICY",
                "INVALID_PROTOCOL",
                "STATE_CONFLICT",
                "DEFINITION_CONFLICT",
            }:
                print("HTTP", key, response.status_code, code)
            raise AssertionError("unexpected write status")
        return response.json()

    with connect(diagnostic_service(service)) as (auth, _, _, app, http, _):
        activate(http, auth, "0001")
        catalog = http.get(BASE + "strategies")
        assert catalog.status_code == 200 and len(catalog.json()["items"]) == 5
        draft = post(http, definitions_path, payload, "quant-create-draft-001", 201)
        assert draft["state"] == "DRAFT" and draft["candidateCount"] == 1
        assert post(http, definitions_path, payload, "quant-create-draft-001", 201) == draft
        problem(
            http.post(
                definitions_path,
                json={**payload, "feeUsd": "1"},
                headers={
                    **WRITE,
                    "Idempotency-Key": "quant-create-draft-001",
                },
            ),
            409,
            "IDEMPOTENCY_CONFLICT",
        )
        invalid_policy = {**payload, "dataSelectionPolicyVersion": "retrospective-v1"}
        problem(
            http.post(definitions_path, json=invalid_policy, headers=WRITE), 422, "INVALID_POLICY"
        )
        post(http, definitions_path, payload, "quant-create-draft-002", 201)
        draft_path = f"{definitions_path}/{draft['id']}"
        definition_binding = {
            key: draft[key] for key in ("schemaVersion", "definitionVersion", "definitionHash")
        }
        assert http.get(draft_path).json()["id"] == draft["id"]
        problem(http.get(f"{definitions_path}/{uuid4()}"), 404, "NOT_FOUND")
        problem(
            http.post(
                draft_path + "/validations",
                json={
                    **definition_binding,
                    "definitionHash": "e" * 64,
                },
                headers=WRITE,
            ),
            409,
            "DEFINITION_CONFLICT",
        )

        validations = draft_path + "/validations"
        no_calendar = post(http, validations, definition_binding, "quant-validate-missing", 201)
        assert no_calendar["status"] == "INVALID"
        assert no_calendar["reasons"] == ["MISSING_CALENDAR"]
        assert not no_calendar["budgetEstimate"]["calendarConfirmed"]

        with engine.begin() as connection:
            # Synthetic historically observed schedule in this disposable test DB only.
            connection.execute(
                text("ALTER TABLE research.calendar_versions DISABLE TRIGGER a_stamp")
            )
            for start in phases:
                connection.execute(
                    text("""
                    INSERT INTO research.calendar_versions
                    (session_id,session_date,opens_at,closes_at,version,source,commit_version,ingested_at)
                    VALUES (:id,:day,:opens,:closes,:version,'alpaca-v2-calendar',
                            research.reserve_version(),:ingested)
                """),
                    {
                        "id": uuid4(),
                        "day": start.date(),
                        "opens": start,
                        "closes": start + timedelta(hours=1),
                        "version": "a" * 64,
                        "ingested": phases[0] - timedelta(days=1),
                    },
                )
            connection.execute(
                text("ALTER TABLE research.calendar_versions ENABLE TRIGGER a_stamp")
            )
        replay = post(http, validations, definition_binding, "quant-validate-missing", 201)
        assert replay == no_calendar
        validation = post(http, validations, definition_binding, "quant-validate-present", 201)
        assert validation["status"] == "VALIDATED"
        assert validation["budgetEstimate"]["simulationBarSteps"] == 48
        assert validation["budgetEstimate"]["reservedQueueSlots"] == 3
        assert (
            http.get(draft_path, params={"validationId": validation["id"]}).json()["state"]
            == "VALIDATED"
        )

        start_binding = {
            **definition_binding,
            "validationId": validation["id"],
            "validationHash": validation["validationHash"],
        }
        batches = draft_path + "/batches"
        problem(
            http.post(batches, json={**start_binding, "validationHash": "e" * 64}, headers=WRITE),
            409,
            "DEFINITION_CONFLICT",
        )
        batch = post(http, batches, start_binding, "quant-start-batch-001", 202)
        assert batch["state"] == "ACCEPTED"
        assert post(http, batches, start_binding, "quant-start-batch-001", 202)["id"] == batch["id"]
        batch_path = BASE + f"experiment-batches/{batch['id']}"
        assert http.get(batch_path).json()["id"] == batch["id"]
        assert [item["id"] for item in http.get(batches).json()["items"]] == [batch["id"]]
        assert len(http.get(batch_path + "/candidates").json()["items"]) == 3
        problem(http.get(batch_path + "/comparison"), 409, "REPORT_NOT_READY")

        first = http.get(definitions_path, params={"limit": 1}).json()
        cursor = first["nextCursor"]
        assert isinstance(cursor, str)
        second = http.get(definitions_path, params={"limit": 1, "cursor": cursor})
        assert second.status_code == 200
        assert second.json()["items"][0]["id"] != first["items"][0]["id"]
        problem(
            http.get(definitions_path, params={"limit": 2, "cursor": cursor}), 400, "CURSOR_INVALID"
        )
        problem(
            http.get(definitions_path, params={"limit": 1, "cursor": cursor[:-1]}),
            400,
            "CURSOR_INVALID",
        )
        with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 46001)) as other:
            activate(other, auth, "0002")
            problem(
                other.get(definitions_path, params={"limit": 1, "cursor": cursor}),
                400,
                "CURSOR_INVALID",
            )

        # C+2 weight is 3. Existing first batch and 32 further batches occupy 99.
        for index in range(32):
            queued = post(http, batches, start_binding, f"quant-fill-queue-{index:03}", 202)
            if index == 0:
                history = queued["researchHistory"]
                assert history["overlapCount"] > 0
                assert any(item["priorBatchId"] == batch["id"] for item in history["overlaps"])
        reloaded = http.get(batch_path).json()
        assert (
            reloaded["researchHistory"]["registryHash"] == batch["researchHistory"]["registryHash"]
        )
        assert reloaded["researchHistory"]["overlaps"] == batch["researchHistory"]["overlaps"]
        assert any(
            item["code"] == "LATER_REGISTERED_OVERLAP" for item in reloaded["currentWarnings"]
        )
        problem(
            http.post(
                batches,
                json=start_binding,
                headers={
                    **WRITE,
                    "Idempotency-Key": "quant-start-over-capacity",
                },
            ),
            429,
            "ADMISSION_LIMIT",
        )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.experiment_batches WHERE status='ACCEPTED'")
            ).scalar_one()
            == 33
        )
    print("PASS quant PG/HTTP/relay draft/validation/start/idempotency/cursor/weighted-admission")


def test_quant_lab_api_actual_postgresql():
    from tests.integration.test_backtest_dataset import host

    assert host("tests.integration.test_quant_lab_api").startswith("PASS quant PG/HTTP/relay")


if __name__ == "__main__" and sys.argv[1:] == ["guest"]:
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        location = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-4:])
        print("FAIL " + type(error).__name__ + " " + location)
        raise SystemExit(1) from None
