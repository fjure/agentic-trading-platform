"""RRB-01 independent public schema evidence (T-RRB-002/008/010/011/013/020).

Schema checks do not prove snapshot resolution, hash identity, authorization at
runtime, cursor signatures or atomic publication; those belong to RRB-02..08.
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
EXAMPLES = ROOT / "contracts/examples/v1"
FIXTURES = json.loads((EXAMPLES / "retrospective-fixtures.json").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/retrospective-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)


def validator(component, standalone=False):
    if standalone:
        schema = {**MIRROR, "$ref": "#/$defs/" + component}
        return Draft202012Validator(schema, format_checker=contract_formats())
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY, format_checker=contract_formats(),
    )


def example(component):
    name = next(name for name, schema in FIXTURES.items() if schema == component)
    return json.loads((EXAMPLES / name).read_text("utf-8"))


def objects(value, path=()):
    if isinstance(value, dict):
        yield path
        for key, item in value.items():
            yield from objects(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from objects(item, (*path, index))


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_retrospective_fixtures_and_nested_closure(name, component, standalone):
    value = json.loads((EXAMPLES / name).read_text("utf-8"))
    check = validator(component, standalone)
    check.validate(value)
    for path in objects(value):
        bad = copy.deepcopy(value)
        target = bad
        for key in path:
            target = target[key]
        target["unverifiedProviderPayload"] = {"source": "untrusted"}
        assert not check.is_valid(bad), (name, path)


@pytest.mark.parametrize("standalone", [False, True])
def test_omitted_and_explicit_strict_keep_legacy_contracts_valid(standalone):
    request = json.loads((EXAMPLES / "backtest-request.valid.json").read_text("utf-8"))
    check = validator("BacktestRequest", standalone)
    check.validate(request)
    check.validate({**request, "researchMode": "STRICT_PIT"})
    manifest = json.loads((EXAMPLES / "backtest-manifest.valid.json").read_text("utf-8"))
    validator("BacktestManifest", standalone).validate(manifest)
    assert OAS["components"]["schemas"]["BacktestResearchMode"]["default"] == "STRICT_PIT"


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("mode", [None, "STRICT_PIT"])
@pytest.mark.parametrize("field,value", [
    ("selectionPolicyVersion", "retrospective-v1"),
    ("assessmentId", "11111111-1111-4111-8111-111111111111"),
    ("assessmentHash", "a" * 64),
])
def test_strict_refuses_every_retrospective_binding(mode, field, value, standalone):
    request = json.loads((EXAMPLES / "backtest-request.valid.json").read_text("utf-8"))
    if mode is not None:
        request["researchMode"] = mode
    request[field] = value
    assert not validator("BacktestRequest", standalone).is_valid(request)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field", ["selectionPolicyVersion", "assessmentId", "assessmentHash"])
def test_retrospective_run_requires_complete_preview_binding(field, standalone):
    request = example("BacktestRequest")
    assert request["researchMode"] == "RETROSPECTIVE_EXPLORATORY"
    request.pop(field)
    assert not validator("BacktestRequest", standalone).is_valid(request)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", ["BacktestRequest", "BacktestAssessmentRequest"])
@pytest.mark.parametrize("patch", [
    {"researchMode": "AUTO"},
    {"researchMode": "LIVE"},
    {"researchMode": None},
    {"selectionPolicyVersion": "retrospective-v2"},
    {"selectionPolicyVersion": "strict-pit-v1"},
    {"strategyId": "dynamic-python"},
    {"parameters": {"source": "import os"}},
    {"instrumentId": "SPY"},
    {"startCash": 10000},
    {"feeUsd": "NaN"},
    {"slippageBps": "1e2"},
])
def test_retrospective_input_refuses_unknown_versions_code_and_non_decimals(
    component, patch, standalone,
):
    assert not validator(component, standalone).is_valid({**example(component), **patch})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field,value", [
    ("assessmentId", "not-an-id"), ("assessmentHash", "a" * 63),
    ("assessmentHash", "G" * 64), ("assessmentHash", None),
])
def test_retrospective_run_refuses_malformed_binding(field, value, standalone):
    assert not validator("BacktestRequest", standalone).is_valid(
        {**example("BacktestRequest"), field: value}
    )


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("value", [
    "2026-10-22T00:00:00", "2026-10-22T00:00:00+00:00",
    "2026-10-22T02:00:00+02:00", "2026-02-30T00:00:00Z",
])
def test_assessment_preserves_strict_utc_format(value, standalone):
    request = example("BacktestAssessmentRequest")
    request["folds"][0]["testStart"] = value
    assert not validator("BacktestAssessmentRequest", standalone).is_valid(request)


def test_exact_authenticated_additive_operations_and_pagination():
    operations = {
        operation["operationId"]: operation
        for path, item in OAS["paths"].items() if "/backtests/" in path
        for method, operation in item.items() if method in {"get", "post"}
    }
    assert set(operations) == {
        "listBacktestStrategies", "createBacktestRun", "listBacktestRuns",
        "getBacktestRun", "getBacktestResults", "createBacktestAssessment",
        "getBacktestAssessment", "getBacktestReliabilityReport",
    }
    for operation in operations.values():
        assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
        assert {"400", "401", "403", "404", "409", "503"} <= operation["responses"].keys()
        for status, response in operation["responses"].items():
            if int(status) >= 400:
                assert response["content"]["application/problem+json"]["schema"] == {
                    "$ref": "#/components/schemas/BacktestProblem"
                }
    create = operations["createBacktestAssessment"]
    assert {"$ref": "#/components/parameters/IdempotencyKey"} in create["parameters"]
    assert "202" in create["responses"] and "200" not in create["responses"]
    assert create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BacktestAssessmentRequest"
    }
    report = operations["getBacktestReliabilityReport"]
    parameters = {parameter.get("name"): parameter for parameter in report["parameters"]}
    assert parameters["limit"]["schema"] == {
        "type": "integer", "minimum": 1, "maximum": 200, "default": 50,
    }
    assert "cursor" in parameters and "section" in parameters
    assert report["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BacktestReliabilityReportPage"
    }


def test_generated_client_exposes_additive_assessment_and_report_contracts():
    generated = ROOT / "apps/dashboard/src/app/api/generated/control-api"
    service = (generated / "api/backtesting.service.ts").read_text("utf-8")
    for operation in [
        "createBacktestAssessment", "getBacktestAssessment", "getBacktestReliabilityReport",
    ]:
        assert f"public {operation}(" in service
    for model in [
        "backtestResearchMode", "backtestSelectionPolicyVersion", "backtestAssessmentRequest",
        "backtestAssessment", "backtestSnapshotReference", "backtestRetrospectiveManifest",
        "backtestReliabilityReportV1", "backtestReliabilityReportPage",
    ]:
        assert (generated / f"model/{model}.ts").is_file()


@pytest.mark.parametrize("component", [
    "BacktestRun", "BacktestRunDetail", "BacktestSummaryPage",
    "BacktestEquityPage", "BacktestFillsPage",
])
def test_run_response_contracts_expose_typed_additive_experiment_context(component):
    properties = OAS["components"]["schemas"][component]["properties"]
    for field in [
        "researchMode", "selectionPolicyVersion", "experimentVersion", "snapshotReference",
        "reliabilityReportId", "resultLimitations",
    ]:
        assert field in properties, (component, field)
    assert properties["researchMode"] == {"$ref": "#/components/schemas/BacktestResearchMode"}


def test_mixed_run_list_keeps_each_items_typed_experiment_context():
    properties = OAS["components"]["schemas"]["BacktestRunPage"]["properties"]
    assert properties["items"]["items"] == {"$ref": "#/components/schemas/BacktestRun"}


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("patch", [
    {"overallStatus": "PASS"},
    {"overallStatus": "STRICT_PIT_CERTIFIED"},
    {"historicalAvailabilityStatus": "PROVEN"},
    {"reliabilityScore": "100"},
    {"resultLimitations": []},
    {"researchMode": "STRICT_PIT"},
    {"selectionPolicyVersion": "strict-pit-v1"},
])
def test_report_cannot_claim_strict_certification_or_erase_limitations(patch, standalone):
    report = example("BacktestReliabilityReportV1")
    assert not validator("BacktestReliabilityReportV1", standalone).is_valid({**report, **patch})


@pytest.mark.parametrize("standalone", [False, True])
def test_report_cannot_turn_unknown_actions_or_availability_into_positive_evidence(standalone):
    report = example("BacktestReliabilityReportV1")
    check = validator("BacktestReliabilityReportV1", standalone)
    report["corporateActionStatus"] = "UNKNOWN"
    report["dimensions"]["CORPORATE_ACTIONS"]["rating"] = "UNKNOWN"
    report["dimensions"]["HISTORICAL_AVAILABILITY"]["rating"] = "UNKNOWN"
    check.validate(report)
    bad = copy.deepcopy(report)
    bad["corporateActionStatus"] = "NO_ACTIONS"
    assert not check.is_valid(bad)
    bad = copy.deepcopy(report)
    bad["dimensions"]["HISTORICAL_AVAILABILITY"]["rating"] = "PASS"
    assert not check.is_valid(bad)


@pytest.mark.parametrize("standalone", [False, True])
def test_unknown_bar_denominator_never_becomes_zero_missing(standalone):
    check = validator("BacktestReliabilityBarCounts", standalone)
    value = {"expected": None, "present": 0, "missing": None, "unexpected": 0}
    check.validate(value)
    assert not check.is_valid({**value, "missing": 0})
    assert not check.is_valid({**value, "expected": 0})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("dimension", [
    "INTEGRITY", "CALENDAR", "CORPORATE_ACTIONS", "HISTORICAL_AVAILABILITY", "FEED", "LICENSE",
])
def test_each_independent_quality_dimension_is_mandatory(dimension, standalone):
    report = example("BacktestReliabilityReportV1")
    del report["dimensions"][dimension]
    assert not validator("BacktestReliabilityReportV1", standalone).is_valid(report)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("cursor", ["", "short", "x" * 2049, "a/" * 32, "a=" * 32])
def test_report_cursor_stays_bounded_and_opaque(cursor, standalone):
    page = example("BacktestReliabilityReportPage")
    page["nextCursor"] = cursor
    assert not validator("BacktestReliabilityReportPage", standalone).is_valid(page)


CONTEXT_FIELDS = [
    "researchMode", "selectionPolicyVersion", "experimentVersion", "snapshotReference",
    "reliabilityReportId", "resultLimitations",
]


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", ["BacktestRun", "BacktestRunDetail"])
@pytest.mark.parametrize("field", CONTEXT_FIELDS)
def test_nested_retrospective_request_requires_response_context(component, field, standalone):
    response = example(component)
    nested_run = response["run"] if component == "BacktestRunDetail" else response
    assert nested_run["request"]["researchMode"] == "RETROSPECTIVE_EXPLORATORY"
    validator(component, standalone).validate(response)
    del response[field]
    assert not validator(component, standalone).is_valid(response)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", [
    "BacktestRun", "BacktestRunDetail", "BacktestSummaryPage", "BacktestEquityPage",
    "BacktestFillsPage",
])
@pytest.mark.parametrize("patch", [
    {"selectionPolicyVersion": "strict-pit-v1"},
    {"experimentVersion": "backtest-experiment-v1"},
    {"resultLimitations": []},
])
def test_retrospective_response_cannot_mix_strict_policy_version_or_limits(
    component, patch, standalone,
):
    response = example(component)
    response.update(patch)
    assert not validator(component, standalone).is_valid(response)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", ["BacktestRun", "BacktestRunDetail"])
def test_nested_retrospective_request_cannot_be_labeled_strict(component, standalone):
    response = example(component)
    response["researchMode"] = "STRICT_PIT"
    assert not validator(component, standalone).is_valid(response)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", [
    "BacktestSummaryPage", "BacktestEquityPage", "BacktestFillsPage",
])
@pytest.mark.parametrize("field", CONTEXT_FIELDS[1:])
def test_explicit_retrospective_result_pages_require_complete_context(component, field, standalone):
    page = example(component)
    assert page["researchMode"] == "RETROSPECTIVE_EXPLORATORY"
    del page[field]
    assert not validator(component, standalone).is_valid(page)


def successful_retrospective_run():
    run = example("BacktestRun")
    run.update(
        status="SUCCEEDED", completedAt=run["updatedAt"], publicationComplete=True,
        manifestHash="a" * 64, resultHash="a" * 64,
    )
    return run


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component", [
    "BacktestRun", "BacktestRunDetail", "BacktestSummaryPage", "BacktestEquityPage",
    "BacktestFillsPage",
])
@pytest.mark.parametrize("field", ["snapshotReference", "reliabilityReportId"])
def test_successful_retrospective_publication_requires_snapshot_and_report(
    component, field, standalone,
):
    response = example(component)
    if component == "BacktestRun":
        response = successful_retrospective_run()
    elif component == "BacktestRunDetail":
        response["run"] = successful_retrospective_run()
    check = validator(component, standalone)
    check.validate(response)
    response[field] = None
    assert not check.is_valid(response)


@pytest.mark.parametrize("standalone", [False, True])
def test_explicit_retrospective_receipt_cannot_wrap_legacy_strict_request(standalone):
    run = example("BacktestRun")
    run["request"] = json.loads((EXAMPLES / "backtest-request.valid.json").read_text("utf-8"))
    assert not validator("BacktestRun", standalone).is_valid(run)


@pytest.mark.parametrize("standalone", [False, True])
def test_successful_retrospective_detail_requires_its_typed_manifest(standalone):
    detail = example("BacktestRunDetail")
    detail["run"] = successful_retrospective_run()
    check = validator("BacktestRunDetail", standalone)
    check.validate(detail)
    detail["retrospectiveManifest"] = None
    assert not check.is_valid(detail)


@pytest.mark.parametrize("standalone", [False, True])
def test_retrospective_summary_never_reuses_strict_certified_absent_items(standalone):
    page = example("BacktestSummaryPage")
    legacy = json.loads((EXAMPLES / "backtest-summary-page.valid.json").read_text("utf-8"))
    assert legacy["items"][0]["coverage"]["corporateActions"] == "CERTIFIED_ABSENT"
    assert page["items"] == []
    assert page["retrospectiveItems"]
    check = validator("BacktestSummaryPage", standalone)
    check.validate(page)
    page["items"] = legacy["items"]
    assert not check.is_valid(page)


@pytest.mark.parametrize("standalone", [False, True])
def test_retrospective_summary_requires_its_distinct_typed_items(standalone):
    page = example("BacktestSummaryPage")
    del page["retrospectiveItems"]
    assert not validator("BacktestSummaryPage", standalone).is_valid(page)


@pytest.mark.parametrize("standalone", [False, True])
def test_legacy_summary_item_type_stays_strict_and_accepts_its_original_fixture(standalone):
    properties = OAS["components"]["schemas"]["BacktestSummaryPage"]["properties"]
    assert properties["items"]["items"] == {"$ref": "#/components/schemas/BacktestFoldSummary"}
    legacy = json.loads((EXAMPLES / "backtest-summary-page.valid.json").read_text("utf-8"))
    check = validator("BacktestSummaryPage", standalone)
    check.validate(legacy)
    retrospective_item = example("BacktestSummaryPage")["retrospectiveItems"][0]
    bad = {**legacy, "items": [retrospective_item]}
    assert not check.is_valid(bad)
    bad = {**legacy, "retrospectiveItems": [retrospective_item]}
    assert not check.is_valid(bad)


@pytest.mark.parametrize("standalone", [False, True])
def test_retrospective_summary_keeps_unknown_actions_and_unproven_availability(standalone):
    page = example("BacktestSummaryPage")
    report = example("BacktestReliabilityReportV1")
    assert report["corporateActionStatus"] == "UNKNOWN"
    assert report["historicalAvailabilityStatus"] == "NOT_PROVEN"
    for item in page["retrospectiveItems"]:
        coverage = item["coverage"]
        assert coverage["corporateActionStatus"] == report["corporateActionStatus"]
        assert coverage["historicalAvailabilityStatus"] == report["historicalAvailabilityStatus"]
        assert "corporateActions" not in coverage
    check = validator("BacktestSummaryPage", standalone)
    check.validate(page)
    bad = copy.deepcopy(page)
    bad["retrospectiveItems"][0]["coverage"]["corporateActionStatus"] = "CERTIFIED_ABSENT"
    assert not check.is_valid(bad)
    bad = copy.deepcopy(page)
    bad["retrospectiveItems"][0]["coverage"]["historicalAvailabilityStatus"] = "PROVEN"
    assert not check.is_valid(bad)


def test_generated_retrospective_summary_models_keep_distinct_coverage():
    generated = ROOT / "apps/dashboard/src/app/api/generated/control-api/model"
    assert (generated / "backtestRetrospectiveFoldSummary.ts").is_file()
    assert (generated / "backtestRetrospectiveCoverage.ts").is_file()
    properties = OAS["components"]["schemas"]["BacktestSummaryPage"]["properties"]
    assert properties["retrospectiveItems"]["items"] == {
        "$ref": "#/components/schemas/BacktestRetrospectiveFoldSummary"
    }


@pytest.mark.parametrize("standalone", [False, True])
def test_retrospective_fills_never_claim_legacy_observation_cutoff(standalone):
    page = example("BacktestFillsPage")
    assert page["items"] == []
    assert page["retrospectiveItems"]
    check = validator("BacktestFillsPage", standalone)
    check.validate(page)
    for fill in page["retrospectiveItems"]:
        assert "simulatedMarketCutoff" in fill
        assert "observationCutoff" not in fill
    legacy = json.loads((EXAMPLES / "backtest-fills-page.valid.json").read_text("utf-8"))
    bad = {**page, "items": legacy["items"]}
    assert not check.is_valid(bad)
    bad = copy.deepcopy(page)
    fill = bad["retrospectiveItems"][0]
    fill["observationCutoff"] = fill["simulatedMarketCutoff"]
    assert not check.is_valid(bad)
    bad = copy.deepcopy(page)
    del bad["retrospectiveItems"][0]["simulatedMarketCutoff"]
    assert not check.is_valid(bad)


@pytest.mark.parametrize("standalone", [False, True])
def test_legacy_fills_retain_original_type_and_refuse_retrospective_payloads(standalone):
    properties = OAS["components"]["schemas"]["BacktestFillsPage"]["properties"]
    assert properties["items"]["items"] == {"$ref": "#/components/schemas/BacktestFill"}
    legacy = json.loads((EXAMPLES / "backtest-fills-page.valid.json").read_text("utf-8"))
    check = validator("BacktestFillsPage", standalone)
    check.validate(legacy)
    retro_items = example("BacktestFillsPage")["retrospectiveItems"]
    assert not check.is_valid({**legacy, "items": retro_items})
    assert not check.is_valid({**legacy, "retrospectiveItems": retro_items})


@pytest.mark.parametrize("standalone", [False, True])
def test_retrospective_fills_page_requires_its_typed_array(standalone):
    page = example("BacktestFillsPage")
    del page["retrospectiveItems"]
    assert not validator("BacktestFillsPage", standalone).is_valid(page)


def test_generated_retrospective_fill_model_names_simulated_time():
    generated = ROOT / "apps/dashboard/src/app/api/generated/control-api/model"
    model = (generated / "backtestRetrospectiveFill.ts").read_text("utf-8")
    assert "simulatedMarketCutoff: string;" in model
    assert "observationCutoff" not in model
    properties = OAS["components"]["schemas"]["BacktestFillsPage"]["properties"]
    assert properties["retrospectiveItems"]["items"] == {
        "$ref": "#/components/schemas/BacktestRetrospectiveFill"
    }
