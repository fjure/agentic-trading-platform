"""BKT-01: REQ-BKT-001/006/007, T-BKT-002/011/014 public contract evidence.

These checks prove schema constraints, not runtime PIT, signed cursor verification,
idempotent persistence or chronological cross-field validation (BKT-02/07).
"""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
MIRROR = json.loads((ROOT / "contracts/schemas/v1/backtesting.schema.json").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/backtest-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)
FIXTURES = {
    "request": "BacktestRequest",
    "strategy-catalog": "BacktestStrategyCatalog",
    "run": "BacktestRun",
    "run-blocked": "BacktestRun",
    "run-detail": "BacktestRunDetail",
    "run-page": "BacktestRunPage",
    "manifest": "BacktestManifest",
    "summary-page": "BacktestSummaryPage",
    "equity-page": "BacktestEquityPage",
    "fills-page": "BacktestFillsPage",
    "results-pending": "BacktestResults",
    **{f"problem-{status}": "BacktestProblem" for status in [400, 401, 403, 404, 409, 503]},
}


def example(name):
    path = ROOT / f"contracts/examples/v1/backtest-{name}.valid.json"
    return json.loads(path.read_text("utf-8"))


def validator(component, standalone=False):
    if standalone:
        return Draft202012Validator(
            {**MIRROR, "$ref": "#/$defs/" + component}, format_checker=contract_formats()
        )
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY,
        format_checker=contract_formats(),
    )


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_examples_match_public_and_standalone_schemas(name, component, standalone):
    validator(component, standalone).validate(example(name))


def test_every_component_is_mirrored_without_drift():
    def rewrite(value):
        if isinstance(value, dict):
            return {
                key: (
                    item.replace("#/components/schemas/", "#/$defs/")
                    if key == "$ref"
                    else rewrite(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    expected = {
        name: rewrite(schema)
        for name, schema in OAS["components"]["schemas"].items()
        if name.startswith("Backtest")
    }
    assert MIRROR["$defs"] == expected


def test_research_metric_conditional_compatibility_keeps_dto_closed():
    metric = {
        "status": "NOT_COMPUTABLE", "value": None,
        "algorithmVersion": "decimal-population-v1", "evidenceIds": [],
        "reason": "INSUFFICIENT_BARS",
    }
    check = validator("ResearchMetric")
    check.validate(metric)
    assert not check.is_valid({**metric, "unknownProviderField": "denied"})
    metric.update(status="COMPUTABLE", value="0.1", reason=None,
                  evidenceIds=["11111111-1111-4111-8111-111111111111"] * 3)
    check.validate(metric)
    assert not check.is_valid({**metric, "unknownProviderField": "denied"})


def object_paths(value, path=()):
    if isinstance(value, dict):
        yield path
        for key, item in value.items():
            yield from object_paths(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from object_paths(item, (*path, index))


@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_unknown_fields_rejected_at_every_nested_object(name, component):
    value = example(name)
    for path in object_paths(value):
        bad = copy.deepcopy(value)
        target = bad
        for key in path:
            target = target[key]
        target["unverifiedManifestUrl"] = "https://invalid.example/manifest"
        assert not validator(component).is_valid(bad), (name, path)
        assert not validator(component, True).is_valid(bad), (name, path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("strategyId", "arbitrary-python"),
        ("version", "2"),
        ("parameters", {"fast": 1, "slow": 3}),
        ("parameters", {"fast": 2, "slow": 101}),
        ("parameters", {"fast": 2.5, "slow": 3}),
        ("parameters", {}),
        ("parameters", {"fast": 2, "slow": 3, "source": "print('x')"}),
        ("startCash", "0"),
        ("startCash", "0.00000000"),
        ("startCash", "-1"),
        ("startCash", "1000000000.00000001"),
        ("startCash", "1000000001"),
        ("feeUsd", "1000.00000001"),
        ("feeUsd", "-0"),
        ("slippageBps", "100.00000001"),
        ("quantityStep", "0.01"),
        ("folds", []),
        ("folds", [example("request")["folds"][0]] * 13),
        ("instrumentId", "SPY"),
    ],
)
def test_request_rejects_disallowed_input(field, value):
    bad = example("request")
    bad[field] = value
    assert not validator("BacktestRequest").is_valid(bad)


@pytest.mark.parametrize("value", [1, True, "NaN", "Infinity", "1e3", ".1", "01", "1.", " 1"])
@pytest.mark.parametrize("field", ["startCash", "feeUsd", "slippageBps"])
def test_non_decimal_input_is_rejected(field, value):
    bad = example("request")
    bad[field] = value
    assert not validator("BacktestRequest").is_valid(bad)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-22T00:00:00",
        "2026-10-22T00:00:00+00:00",
        "2026-10-22T02:00:00+02:00",
        "2026-02-30T00:00:00Z",
        "2026-10-22T00:00:00.1234567Z",
        "2026-10-22",
    ],
)
def test_invalid_utc_is_rejected(value):
    bad = example("request")
    bad["folds"][0]["testStart"] = value
    assert not validator("BacktestRequest").is_valid(bad)


@pytest.mark.parametrize("strategy", ["flat-v1", "buy-hold-v1"])
def test_empty_parameters_for_non_sma_strategies(strategy):
    value = example("request")
    value.update(strategyId=strategy, parameters={})
    validator("BacktestRequest").validate(value)
    value["parameters"] = {"fast": 2, "slow": 3}
    assert not validator("BacktestRequest").is_valid(value)


@pytest.mark.parametrize("cursor", ["", "short", "x" * 2049, "a/" * 32, "a=" * 32, "a b" * 32])
def test_cursor_syntax_is_bounded(cursor):
    value = example("run-page")
    value["nextCursor"] = cursor
    assert not validator("BacktestRunPage").is_valid(value)


@pytest.mark.parametrize("status", ["ACCEPTED", "RUNNING", "BLOCKED", "FAILED"])
def test_unpublished_status_cannot_claim_complete_results(status):
    page = example("summary-page")
    page["status"] = status
    assert not validator("BacktestResults").is_valid(page)
    page.update(items=[], nextCursor=None, resultHash=None, publicationComplete=False)
    validator("BacktestResults").validate(page)


def test_problem_status_and_code_are_consistent_and_sanitized():
    value = example("problem-400")
    value["code"] = "IDEMPOTENCY_CONFLICT"
    assert not validator("BacktestProblem").is_valid(value)
    value = example("problem-503")
    value["detail"] = "provider stack"
    assert not validator("BacktestProblem").is_valid(value)


def test_five_additive_authenticated_operations_and_page_bounds():
    paths = {key: item for key, item in OAS["paths"].items() if "/backtests/" in key}
    operations = [operation for item in paths.values() for operation in item.values()]
    assert {op["operationId"] for op in operations} == {
        "listBacktestStrategies", "createBacktestRun", "listBacktestRuns",
        "getBacktestRun", "getBacktestResults",
    }
    for operation in operations:
        assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
        assert {"400", "401", "403", "404", "409", "503"} <= operation["responses"].keys()
        for status, response in operation["responses"].items():
            if int(status) >= 400:
                assert response["content"]["application/problem+json"]["schema"] == {
                    "$ref": "#/components/schemas/BacktestProblem"
                }
        for param in operation["parameters"]:
            if param.get("name") == "limit":
                assert param["schema"] == {
                    "type": "integer", "minimum": 1, "maximum": 200, "default": 50
                }
    post = paths["/api/v1/research/backtests/runs"]["post"]
    assert {"$ref": "#/components/parameters/IdempotencyKey"} in post["parameters"]
    assert "202" in post["responses"] and "200" not in post["responses"]


def test_generated_client_contains_all_backtest_operations():
    generated = ROOT / "apps/dashboard/src/app/api/generated/control-api"
    service = (generated / "api/backtesting.service.ts").read_text("utf-8")
    for name in [
        "listBacktestStrategies", "createBacktestRun", "listBacktestRuns",
        "getBacktestRun", "getBacktestResults",
    ]:
        assert f"public {name}(" in service
    assert (generated / "model/backtestManifest.ts").is_file()
    assert (generated / "model/backtestResults.ts").is_file()
    receipt = (generated / "model/backtestRun.ts").read_text("utf-8")
    for field in ["completedAt", "manifestHash", "resultHash"]:
        assert f"{field}: string | null;" in receipt
    for model in ["Summary", "Equity", "Fills", "Run"]:
        page = (generated / f"model/backtest{model}Page.ts").read_text("utf-8")
        assert "nextCursor: string | null;" in page
