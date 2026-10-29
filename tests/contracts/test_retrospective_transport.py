"""RRB-06 real HTTP boundaries with test-only services (T-RRB-002/010/013/020).

These tests exercise the production FastAPI routes and canonical response shapes;
database admission, cursor signatures and worker execution have separate PG tests.
"""

import copy
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from scripts.contract_formats import contract_formats
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.backtest_dto import BacktestRequest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "contracts/examples/v1"
SCHEMA = json.loads((ROOT / "contracts/schemas/v1/backtesting.schema.json").read_text())
FIXTURES = json.loads((EXAMPLES / "retrospective-fixtures.json").read_text())
BASE = "/api/v1/research/backtests"
ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"Origin": ORIGIN, "Idempotency-Key": "retrospective-http-0001"}


def example(name):
    return json.loads((EXAMPLES / (name + ".valid.json")).read_text())


def validate(component, value):
    Draft202012Validator(
        {**SCHEMA, "$ref": "#/$defs/" + component},
        format_checker=contract_formats(),
    ).validate(value)


class Spy:
    """Only records transport effects; it has no simulation/provider methods."""

    def __init__(self):
        self.calls = []
        self.responses = {
            "create_assessment": "retrospective-assessment-accepted",
            "read_assessment": "retrospective-assessment-succeeded",
            "read_reliability_report": "retrospective-report-findings-page",
            "create_run": "retrospective-run-accepted",
        }

    def invoke(self, method, *args):
        self.calls.append((method, args))
        value = self.responses[method]
        return example(value) if isinstance(value, str) else copy.deepcopy(value)

    def create_assessment(self, *args):
        return self.invoke("create_assessment", *args)

    def read_assessment(self, *args):
        return self.invoke("read_assessment", *args)

    def read_reliability_report(self, *args):
        return self.invoke("read_reliability_report", *args)

    def create_run(self, *args):
        return self.invoke("create_run", *args)


def client(service=None, *, activate=True, readiness=None):
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, backtesting=service, readiness=readiness),
        base_url=ORIGIN,
        client=("127.0.0.1", 46124),
        raise_server_exceptions=False,
    )
    if activate:
        response = http.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": ORIGIN, "Idempotency-Key": "activate-retro-0001"},
        )
        assert response.status_code == 204
    return http


@pytest.mark.parametrize("filename,component", FIXTURES.items())
def test_all_canonical_fixtures_and_nested_closure(filename, component):
    value = json.loads((EXAMPLES / filename).read_text())
    validate(component, value)
    dto = getattr(importlib.import_module("services.control_api.retrospective_dto"), component)
    assert dto.model_validate(value).model_dump() == value

    def objects(item, path=()):
        if isinstance(item, dict):
            yield path
            for key, child in item.items():
                yield from objects(child, (*path, key))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                yield from objects(child, (*path, index))

    for path in objects(value):
        invalid = copy.deepcopy(value)
        target = invalid
        for key in path:
            target = target[key]
        target["transportPrivateCanary"] = "must-not-cross-boundary"
        with pytest.raises(ValueError):
            dto.model_validate(invalid)


@pytest.mark.parametrize("activate", [False, True])
def test_session_and_origin_guard_precede_every_new_operation(activate):
    spy = Spy()
    http = client(spy, activate=activate)
    headers = {"Origin": "https://foreign.invalid"} if activate else {}
    expected = 403 if activate else 401
    identifier = str(uuid4())
    requests = [
        http.post(
            BASE + "/assessments",
            json=example("retrospective-assessment-request"),
            headers={**HEADERS, **headers},
        ),
        http.get(BASE + "/assessments/" + identifier, headers=headers),
        http.get(BASE + "/reliability-reports/" + identifier, headers=headers),
    ]
    assert [response.status_code for response in requests] == [expected] * 3
    assert spy.calls == []
    for response in requests:
        validate("BacktestProblem", response.json())
        assert response.headers["cache-control"] == "no-store"


def test_assessment_and_run_are_separate_accepted_operations_with_bound_trace():
    spy = Spy()
    http = client(spy)
    for suffix, name, method in (
        ("assessments", "retrospective-assessment-request", "create_assessment"),
        ("runs", "retrospective-request", "create_run"),
    ):
        response = http.post(
            BASE + "/" + suffix,
            json=example(name),
            headers={**HEADERS, "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-00"},
        )
        assert response.status_code == 202
        validate(
            "BacktestAssessment" if suffix == "assessments" else "BacktestRun", response.json()
        )
        called, args = spy.calls[-1]
        assert called == method and args == (
            example(name),
            "local-operator",
            HEADERS["Idempotency-Key"],
            "a" * 32,
        )
    assert [method for method, _ in spy.calls] == ["create_assessment", "create_run"]


@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Idempotency-Key": HEADERS["Idempotency-Key"]}, 400),
        ({"Origin": ORIGIN}, 400),
        ({"Origin": ORIGIN, "Idempotency-Key": "short"}, 400),
    ],
)
def test_write_origin_and_idempotency_are_required_before_assessment_effects(headers, status):
    spy = Spy()
    response = client(spy).post(
        BASE + "/assessments",
        json=example("retrospective-assessment-request"),
        headers=headers,
    )
    assert response.status_code == status and spy.calls == []
    validate("BacktestProblem", response.json())


@pytest.mark.parametrize("suffix", ["assessments", "runs"])
@pytest.mark.parametrize(
    "patch",
    [
        {"researchMode": "LIVE"},
        {"selectionPolicyVersion": "retrospective-v2"},
        {"researchMode": "STRICT_PIT"},
        {"providerPayload": {"transportPrivateCanary": True}},
        {"parameters": {"fast": 3, "slow": 3}},
        {"startCash": 100},
    ],
)
def test_closed_invalid_admission_never_reaches_service(suffix, patch):
    spy = Spy()
    name = (
        "retrospective-assessment-request" if suffix == "assessments" else "retrospective-request"
    )
    response = client(spy).post(
        BASE + "/" + suffix, json={**example(name), **patch}, headers=HEADERS
    )
    assert response.status_code == 400
    validate("BacktestProblem", response.json())
    assert spy.calls == [] and "transportPrivateCanary" not in response.text


@pytest.mark.parametrize("missing", ["assessmentId", "assessmentHash", "selectionPolicyVersion"])
def test_retrospective_run_requires_complete_bound_preview(missing):
    spy = Spy()
    value = example("retrospective-request")
    value.pop(missing)
    response = client(spy).post(BASE + "/runs", json=value, headers=HEADERS)
    assert response.status_code == 400 and spy.calls == []


@pytest.mark.parametrize(
    "query",
    [
        "section=all",
        "category=LIVE",
        "foldIndex=-1",
        "foldIndex=12",
        "foldIndex=01",
        "limit=0",
        "limit=201",
        "limit=1&limit=2",
        "section=findings&section=revisions",
        "cursor=bad",
        "cursor=" + "a" * 2049,
        "source=anything",
        "apiKey=transportPrivateCanary",
    ],
)
def test_report_query_rejection_precedes_service(query):
    spy = Spy()
    response = client(spy).get(BASE + "/reliability-reports/" + str(uuid4()) + "?" + query)
    assert response.status_code == 400 and spy.calls == []
    validate("BacktestProblem", response.json())
    assert "transportPrivateCanary" not in response.text


@pytest.mark.parametrize("section", ["findings", "revisions"])
def test_report_pages_keep_canonical_section_and_filter_values(section):
    spy = Spy()
    spy.responses["read_reliability_report"] = "retrospective-report-" + section + "-page"
    report_id = example("retrospective-report")["reportId"]
    values = {"section": section, "category": "LICENSE", "foldIndex": "0", "limit": "1"}
    values["cursor"] = "a" * 64
    response = client(spy).get(BASE + "/reliability-reports/" + report_id, params=values)
    assert response.status_code == 200
    validate("BacktestReliabilityReportPage", response.json())
    assert response.json()["section"] == section
    assert spy.calls == [("read_reliability_report", (report_id, values))]


@pytest.mark.parametrize(
    "name",
    [
        "retrospective-assessment-accepted",
        "retrospective-assessment-succeeded",
        "retrospective-assessment-blocked",
    ],
)
def test_assessment_detail_is_real_read_operation(name):
    spy = Spy()
    spy.responses["read_assessment"] = name
    value = example(name)
    response = client(spy).get(BASE + "/assessments/" + value["assessmentId"])
    assert response.status_code == 200
    validate("BacktestAssessment", response.json())
    assert response.json() == value
    assert spy.calls == [("read_assessment", (value["assessmentId"],))]


@pytest.mark.parametrize(
    "method,suffix",
    [
        ("read_assessment", "/assessments/"),
        ("read_reliability_report", "/reliability-reports/"),
    ],
)
def test_absent_backend_or_invalid_response_is_redacted_fail_closed(method, suffix):
    identifier = str(uuid4())
    assert client().get(BASE + suffix + identifier).status_code == 503
    spy = Spy()
    spy.responses[method] = {"transportPrivateCanary": "internal-only"}
    response = client(spy).get(BASE + suffix + identifier)
    assert response.status_code == 503
    validate("BacktestProblem", response.json())
    assert "transportPrivateCanary" not in response.text and "internal-only" not in response.text


def test_schema_valid_fields_with_inconsistent_license_context_fail_closed_over_http():
    spy = Spy()
    value = example("retrospective-report-findings-page")
    value["report"]["licenseStatus"] = "DENIED"
    spy.responses["read_reliability_report"] = value
    response = client(spy).get(BASE + "/reliability-reports/" + value["report"]["reportId"])
    assert response.status_code == 503
    validate("BacktestProblem", response.json())
    assert "licenseStatus" not in response.text


def test_old_readiness_and_strict_request_dto_reject_retrospective_admission():
    request = example("retrospective-assessment-request")
    with pytest.raises(ValueError):
        BacktestRequest.model_validate(request)
    spy = Spy()
    response = client(readiness=spy).post(
        "/api/v1/research/data-readiness/assessments",
        json=request,
        headers=HEADERS,
    )
    assert response.status_code == 400 and spy.calls == []


def test_omitted_and_explicit_strict_modes_remain_valid_http_admissions():
    spy = Spy()
    spy.responses["create_run"] = "backtest-run"
    http = client(spy)
    request = example("backtest-request")
    for payload in (request, {**request, "researchMode": "STRICT_PIT"}):
        response = http.post(BASE + "/runs", json=payload, headers=HEADERS)
        assert response.status_code == 202
        validate("BacktestRun", response.json())
    assert [method for method, _ in spy.calls] == ["create_run", "create_run"]


@pytest.mark.parametrize(
    "name,component,path,replacement",
    [
        ("retrospective-report-final", "BacktestReliabilityReportV1", ("runId",), None),
        (
            "retrospective-report",
            "BacktestReliabilityReportV1",
            ("licenseStatus",),
            "DENIED",
        ),
        (
            "retrospective-report-findings-page",
            "BacktestReliabilityReportPage",
            ("section",),
            "revisions",
        ),
        (
            "retrospective-run-accepted",
            "BacktestRun",
            ("selectionPolicyVersion",),
            None,
        ),
        (
            "retrospective-assessment-succeeded",
            "BacktestAssessment",
            ("request", "researchMode"),
            "STRICT_PIT",
        ),
    ],
)
def test_conditional_mode_report_and_phase_contexts_are_not_interchangeable(
    name,
    component,
    path,
    replacement,
):
    value = example(name)
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    validator = Draft202012Validator(
        {**SCHEMA, "$ref": "#/$defs/" + component},
        format_checker=contract_formats(),
    )
    assert not validator.is_valid(value)
    dto = getattr(importlib.import_module("services.control_api.retrospective_dto"), component)
    with pytest.raises(ValueError):
        dto.model_validate(value)


@pytest.mark.parametrize("content", [b"NaN", b'{"researchMode":"a","researchMode":"b"}'])
def test_duplicate_or_nonfinite_body_is_rejected_before_admission(content):
    spy = Spy()
    response = client(spy).post(
        BASE + "/assessments",
        content=content,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 400 and spy.calls == []
    validate("BacktestProblem", response.json())
