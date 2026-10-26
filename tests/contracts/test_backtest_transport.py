"""BKT-07 strict public transport/client DTO regression (T-BKT-011/014)."""

import copy
import json
from pathlib import Path

import httpx
import pytest

from packages.backtesting.jobs import BacktestError
from services.control_api.backtest_client import BacktestClient
from services.control_api.backtest_dto import RESPONSE_MODELS, BacktestRequest
from services.control_api.backtest_routes import CURRENT_TRACE

ROOT = Path(__file__).resolve().parents[2]


def example(name):
    return json.loads((ROOT / f"contracts/examples/v1/backtest-{name}.valid.json").read_text())


@pytest.mark.parametrize(
    "method,name",
    [
        ("read_strategies", "strategy-catalog"),
        ("create_run", "run"),
        ("read_runs", "run-page"),
        ("read_run", "run-detail"),
        ("read_results", "summary-page"),
        ("read_results", "equity-page"),
        ("read_results", "fills-page"),
        ("read_results", "results-pending"),
    ],
)
def test_response_dtos_accept_examples_and_reject_extra_fields(method, name):
    value = example(name)
    assert RESPONSE_MODELS[method].model_validate(value).model_dump() == value
    value["providerResponse"] = {"not": "public"}
    with pytest.raises(ValueError):
        RESPONSE_MODELS[method].model_validate(value)


@pytest.mark.parametrize(
    "patch",
    [
        {"parameters": {"fast": 3, "slow": 3}},
        {"strategyId": "free-python"},
        {"version": "2"},
        {"startCash": 100},
        {"startCash": "0"},
        {"feeUsd": "1001"},
        {"slippageBps": "101"},
        {"quantityStep": "1"},
        {"manifestHash": "a" * 64},
        {"parameters": {"fast": True, "slow": 3}},
    ],
)
def test_strict_request_rejects_disallowed_fields(patch):
    with pytest.raises(ValueError):
        BacktestRequest.model_validate({**example("request"), **patch})


def test_nested_manifest_unknown_fields_and_unpublished_rows_are_refused():
    value = example("run-detail")
    value["manifest"]["outcomeInputs"][0]["revision"]["rawProvider"] = "denied"
    with pytest.raises(ValueError):
        RESPONSE_MODELS["read_run"].model_validate(value)
    page = example("summary-page")
    page["status"] = "BLOCKED"
    with pytest.raises(ValueError):
        RESPONSE_MODELS["read_results"].model_validate(page)


def test_fixed_private_client_has_no_ambient_auth_and_copies_trace():
    def handler(request):
        assert str(request.url) == "http://paper-api:8012/api/v1/research/backtests/strategies"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert request.headers["traceparent"].split("-")[1] == "a" * 32
        return httpx.Response(200, stream=httpx.ByteStream(
            json.dumps(example("strategy-catalog")).encode()
        ))

    token = CURRENT_TRACE.set("a" * 32)
    try:
        client = BacktestClient(transport=httpx.MockTransport(handler))
        assert client.read_strategies() == example("strategy-catalog")
    finally:
        CURRENT_TRACE.reset(token)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://invalid.example"}),
        httpx.Response(200, content=b'{"items": [], "items": []}'),
        httpx.Response(200, content=b"NaN"),
        httpx.Response(503, json={"code": "UNAVAILABLE", "provider": "secret"}),
        httpx.Response(503, headers={"content-length": str(16 * 1024 * 1024 + 1)}),
    ],
)
def test_client_refuses_redirects_malformed_and_unbounded_responses(response):
    client = BacktestClient(transport=httpx.MockTransport(lambda _: copy.copy(response)))
    with pytest.raises(BacktestError) as error:
        client.read_strategies()
    assert (error.value.status, error.value.code) == (503, "UNAVAILABLE")
