"""Public and private HTTP read boundaries reject private data and bad queries."""

from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from services.control_api import paper_dto as dto
from services.control_api.paper_client import PaperClient
from services.control_api.paper_commands import PaperCommandError
from services.runtime.paper_api import create_private_paper_app
from tests.contracts.test_paper_transport import client
from tests.contracts.test_paper_workflow_contracts import example, validator

TRACE = "a" * 32
CASES = (
    ("snapshot", "read_snapshot", "paper-snapshot", "PaperSnapshot", dto.Snapshot),
    ("orders", "read_orders", "paper-order-page", "PaperOrderPage", dto.OrderPage),
    ("fills", "read_fills", "paper-fill-page", "PaperFillPage", dto.FillPage),
    ("operations", "read_operations", "paper-operation-page", "PaperOperationPage",
     dto.OperationPage),
    ("traces/" + TRACE, "read_trace", "paper-trace", "PaperTrace", dto.TraceView),
)


class Reads:
    def __init__(self, method, value):
        self.method, self.value, self.calls = method, value, []

    def __getattr__(self, name):
        assert name == self.method

        def read(*args):
            self.calls.append(args)
            return deepcopy(self.value)

        return read


@pytest.mark.parametrize("path,method,fixture,schema,model", CASES)
def test_strict_read_response_matches_schema_and_relay(path, method, fixture, schema, model):
    value = example(fixture)
    model.model_validate(value)
    reads = Reads(method, value)
    response = client(reads).get("/api/v1/paper/" + path)
    assert response.status_code == 200
    validator(schema).validate(response.json())
    assert response.json() == value
    assert response.headers["Cache-Control"] == "no-store"
    calls = []

    def relay(request):
        calls.append(request)
        return httpx.Response(200, json=value)

    paper = PaperClient(transport=httpx.MockTransport(relay))
    args = (25, None, TRACE) if method in {
        "read_orders", "read_fills", "read_operations",
    } else (TRACE,)
    assert getattr(paper, method)(*args) == value
    assert calls[0].url.host == "paper-api" and calls[0].method == "GET"


@pytest.mark.parametrize("path,method,fixture,schema,model", CASES)
def test_every_read_requires_session_and_redacts_invalid_private_response(
    path, method, fixture, schema, model,
):
    value = example(fixture) | {"apiSecret": "synthetic-read-private-canary"}
    reads = Reads(method, value)
    response = client(reads, authenticated=False).get("/api/v1/paper/" + path)
    assert response.status_code == 401 and not reads.calls
    response = client(reads).get("/api/v1/paper/" + path)
    assert response.status_code == 503
    assert "synthetic-read-private-canary" not in response.text
    paper = PaperClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=value),
    ))
    args = (25, None, TRACE) if method in {
        "read_orders", "read_fills", "read_operations",
    } else (TRACE,)
    with pytest.raises(PaperCommandError, match="TEMPORARILY_UNAVAILABLE"):
        getattr(paper, method)(*args)


@pytest.mark.parametrize("query", (
    "limit=0", "limit=51", "limit=-1", "limit=1.0", "limit=01", "limit=true",
    "limit=1&limit=2", "cursor=x&cursor=y", "cursor=", "cursor=a%2Fb",
    "cursor=" + "a" * 1025, "accountId=fake", "trace=" + TRACE,
))
def test_invalid_public_pagination_does_not_reach_relay(query):
    reads = Reads("read_orders", example("paper-order-page"))
    response = client(reads).get("/api/v1/paper/orders?" + query)
    assert response.status_code == 400 and not reads.calls


@pytest.mark.parametrize("path", (
    "snapshot?accountId=fake", "traces/not-a-trace", "traces/" + TRACE + "?cursor=a",
))
def test_nonpaged_reads_reject_foreign_query_or_bad_trace(path):
    reads = Reads("not_called", {})
    response = client(reads).get("/api/v1/paper/" + path)
    assert response.status_code == 400 and not reads.calls


def test_private_reads_reject_bad_host_forwarding_and_query_multiplicity():
    reads = Reads("read_orders", example("paper-order-page"))
    private = create_private_paper_app(reads)
    with TestClient(private, base_url="http://paper-api:8012") as http:
        for path, headers in (
            ("/orders?trace=" + TRACE + "&limit=1&limit=2", {}),
            ("/orders?trace=" + TRACE + "&trace=" + TRACE, {}),
            ("/orders?trace=" + TRACE, {"Host": "evil.invalid"}),
            ("/orders?trace=" + TRACE, {"Origin": "http://paper-api:8012"}),
            ("/orders?trace=" + TRACE, {"X-Forwarded-Host": "paper-api:8012"}),
        ):
            response = http.get(path, headers=headers)
            assert response.status_code == 400
        assert not reads.calls


def test_nested_bad_timestamp_money_source_and_oversized_trace_are_rejected():
    for field, bad in (
        ("cash", 100), ("cash", "NaN"), ("asOf", "2026-02-30T00:00:00Z"),
        ("asOf", "2026-10-24 12:00:00Z"),
    ):
        value = example("paper-snapshot")
        value["portfolio"][field] = bad
        with pytest.raises(ValueError):
            dto.Snapshot.model_validate(value)
    value = example("paper-snapshot")
    value["status"]["verifiedAt"] = "2026-10-24 12:00:00Z"
    with pytest.raises(ValueError):
        dto.Snapshot.model_validate(value)
    value = example("paper-operation-page")
    value["items"][0]["acceptedAt"] = "2026-10-24 12:00:00Z"
    with pytest.raises(ValueError):
        dto.OperationPage.model_validate(value)
    order = example("paper-order-page")
    order["items"][0]["decisionId"] = "invented-model-source"
    with pytest.raises(ValueError):
        dto.OrderPage.model_validate(order)
    trace = example("paper-trace")
    trace["items"] *= 33
    with pytest.raises(ValueError):
        dto.TraceView.model_validate(trace)
