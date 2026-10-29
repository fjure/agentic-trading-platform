"""The actual public relay reaches all retrospective private HTTP endpoints."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.backtesting.jobs import BacktestError
from services.control_api.backtest_client import BacktestClient
from services.control_api.backtest_routes import CURRENT_TRACE
from services.runtime.paper_api import create_private_paper_app
from tests.contracts.test_retrospective_transport import (
    BASE,
    HEADERS,
    Spy,
    client,
    example,
)


@pytest.mark.parametrize(
    "operation,fixture,path,status",
    [
        ("create_assessment", "retrospective-assessment-accepted", "assessments", 202),
        ("read_assessment", "retrospective-assessment-succeeded", "assessments/", 200),
        (
            "read_reliability_report",
            "retrospective-report-findings-page",
            "reliability-reports/",
            200,
        ),
    ],
)
def test_fixed_relay_methods_use_expected_verbs_paths_and_sanitized_headers(
    operation, fixture, path, status
):
    response = example(fixture)
    identifier = response.get("assessmentId", response.get("report", {}).get("reportId"))
    query = {"section": "findings", "limit": "1", "category": "LICENSE", "foldIndex": "0"}

    def handler(request):
        assert request.url.host == "paper-api" and request.url.port == 8012
        assert request.url.path == BASE + "/" + path + (identifier if path.endswith("/") else "")
        assert request.method == ("POST" if status == 202 else "GET")
        assert not {"authorization", "cookie", "origin"} & set(request.headers)
        assert request.headers["traceparent"].split("-")[1] == "a" * 32
        if status == 202:
            assert request.headers["Idempotency-Key"] == HEADERS["Idempotency-Key"]
            assert json.loads(request.content) == example("retrospective-assessment-request")
        elif operation == "read_reliability_report":
            assert dict(request.url.params) == query
        return httpx.Response(status, stream=httpx.ByteStream(json.dumps(response).encode()))

    relay = BacktestClient(transport=httpx.MockTransport(handler))
    token = CURRENT_TRACE.set("a" * 32)
    try:
        if status == 202:
            actual = relay.create_assessment(
                example("retrospective-assessment-request"),
                "local-operator",
                HEADERS["Idempotency-Key"],
                "a" * 32,
            )
        elif operation == "read_assessment":
            actual = relay.read_assessment(identifier)
        else:
            actual = relay.read_reliability_report(identifier, query)
        assert actual == response
    finally:
        CURRENT_TRACE.reset(token)


@pytest.mark.parametrize("operation", ["read_assessment", "read_reliability_report"])
def test_relay_refuses_noncanonical_ids_before_any_private_request(operation):
    def handler(_request):
        raise AssertionError("invalid reference reached private transport")

    relay = BacktestClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        getattr(relay, operation)("../runs", *([{}] if operation.endswith("report") else []))


def test_assessment_relay_requires_local_actor_before_private_request():
    def handler(_request):
        raise AssertionError("foreign actor reached private transport")

    relay = BacktestClient(transport=httpx.MockTransport(handler))
    with pytest.raises(BacktestError) as error:
        relay.create_assessment({}, "foreign", HEADERS["Idempotency-Key"], "a" * 32)
    assert (error.value.status, error.value.code) == (403, "FORBIDDEN")


@pytest.mark.parametrize("status,patch", [(200, {}), (202, {"providerPayload": "forbidden"})])
def test_assessment_relay_rejects_wrong_status_and_noncanonical_response(status, patch):
    body = {**example("retrospective-assessment-accepted"), **patch}
    relay = BacktestClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                stream=httpx.ByteStream(json.dumps(body).encode()),
            )
        )
    )
    with pytest.raises(BacktestError) as error:
        relay.create_assessment(
            example("retrospective-assessment-request"),
            "local-operator",
            HEADERS["Idempotency-Key"],
            "a" * 32,
        )
    assert (error.value.status, error.value.code) == (503, "UNAVAILABLE")


def test_public_session_to_real_private_routes_through_backtest_client():
    service = Spy()
    relayed = []
    with TestClient(
        create_private_paper_app(None, backtesting=service), base_url="http://paper-api:8012"
    ) as private:

        def relay_request(request):
            relayed.append(request.url.path)
            assert not {"authorization", "cookie", "origin"} & set(request.headers)
            response = private.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code,
                headers=dict(response.headers),
                stream=httpx.ByteStream(response.content),
            )

        proxy = BacktestClient(transport=httpx.MockTransport(relay_request))
        unauthenticated = client(proxy, activate=False)
        try:
            assert (
                unauthenticated.post(
                    BASE + "/assessments",
                    json=example("retrospective-assessment-request"),
                    headers=HEADERS,
                ).status_code
                == 401
            )
            assert relayed == []
        finally:
            unauthenticated.close()
        public = client(proxy)
        try:
            assert (
                public.post(
                    BASE + "/assessments",
                    json=example("retrospective-assessment-request"),
                    headers={"Idempotency-Key": HEADERS["Idempotency-Key"]},
                ).status_code
                == 400
            )
            assert relayed == []
            receipt = public.post(
                BASE + "/assessments",
                json=example("retrospective-assessment-request"),
                headers=HEADERS,
            )
            assert receipt.status_code == 202
            identifier = receipt.json()["assessmentId"]
            assert public.get(BASE + "/assessments/" + identifier).status_code == 200
            report = example("retrospective-report-findings-page")["report"]["reportId"]
            assert (
                public.get(BASE + "/reliability-reports/" + report + "?limit=1").status_code == 200
            )
            assert [name for name, _ in service.calls] == [
                "create_assessment",
                "read_assessment",
                "read_reliability_report",
            ]
        finally:
            public.close()


def test_private_conflict_status_is_preserved_without_forwarding_error_details():
    problem = example("backtest-problem-409")
    relay = BacktestClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                409,
                stream=httpx.ByteStream(json.dumps(problem).encode()),
            )
        )
    )
    identifier = example("retrospective-assessment-accepted")["assessmentId"]
    with pytest.raises(BacktestError) as error:
        relay.read_assessment(identifier)
    assert (error.value.status, error.value.code) == (409, problem["code"])
