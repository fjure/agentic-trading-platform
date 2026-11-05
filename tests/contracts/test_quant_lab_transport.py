"""QSL-01 / T-QCO-003 contract transport matrix, not QSL-07 runtime evidence.

No fake Quant Lab service is installed to make the new routes appear available.
The existing real transport retains its independent runtime contract checks.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from scripts.contract_stages import pending_contract_operations
from services.control_api.app import create_app

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
BASE = "/api/v1/research"
EXPERIMENTS = BASE + "/experiments"
BATCHES = BASE + "/experiment-batches/{id}"
D7 = {
    "getQuantLabReliabilityReport": (BATCHES + "/reliability-reports/{reportId}", "get", "200"),
    "getQuantLabStrategies": (BASE + "/strategies", "get", "200"),
    "createQuantLabExperiment": (EXPERIMENTS, "post", "201"),
    "listQuantLabExperiments": (EXPERIMENTS, "get", "200"),
    "getQuantLabExperiment": (EXPERIMENTS + "/{id}", "get", "200"),
    "validateQuantLabExperiment": (EXPERIMENTS + "/{id}/validations", "post", "201"),
    "startQuantLabExperimentBatch": (EXPERIMENTS + "/{id}/batches", "post", "202"),
    "listQuantLabExperimentBatches": (EXPERIMENTS + "/{id}/batches", "get", "200"),
    "getQuantLabExperimentBatch": (BATCHES, "get", "200"),
    "listQuantLabCandidates": (BATCHES + "/candidates", "get", "200"),
    "listQuantLabResults": (BATCHES + "/results", "get", "200"),
    "getQuantLabComparison": (BATCHES + "/comparison", "get", "200"),
    "getQuantLabEquity": (BATCHES + "/results/{candidateId}/equity", "get", "200"),
    "getQuantLabDrawdown": (BATCHES + "/results/{candidateId}/drawdown", "get", "200"),
    "compareQuantLabExperiments": (BASE + "/experiment-comparisons", "get", "200"),
}
PAGED = {
    "listQuantLabExperiments", "listQuantLabExperimentBatches", "listQuantLabCandidates",
    "listQuantLabResults", "getQuantLabEquity", "getQuantLabDrawdown",
    "getQuantLabReliabilityReport",
}


def operations():
    return {
        operation["operationId"]: (path, method, operation)
        for path, item in OAS["paths"].items() for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
        and (path in {expected[0] for expected in D7.values()}
             or "QuantLab" in operation.get("operationId", ""))
    }


def resolve(value):
    if "$ref" not in value:
        return value
    resolved = OAS
    for part in value["$ref"].removeprefix("#/").split("/"):
        resolved = resolved[part]
    return resolved


def parameters(operation):
    return {value["name"]: value for item in operation["parameters"] if (value := resolve(item))}


def schema_validator(schema):
    return Draft202012Validator({
        **OAS, "$ref": "#/candidateParameter", "candidateParameter": schema,
    })


def test_d7_has_exact_methods_paths_and_success_codes():
    actual = operations()
    assert actual.keys() == D7.keys()
    for identifier, (path, method, success) in D7.items():
        declared_path, declared_method, operation = actual[identifier]
        assert (declared_path, declared_method) == (path, method), identifier
        assert {status for status in operation["responses"] if status.startswith("2")} == {success}
        assert "application/json" in operation["responses"][success]["content"]
        for key in ("id", "candidateId", "reportId"):
            if "{" + key + "}" in path:
                param = parameters(operation)[key]
                assert param["in"] == "path" and param["required"] is True


@pytest.mark.parametrize("identifier", D7)
def test_every_operation_declares_auth_and_stable_redacted_problem_statuses(identifier):
    _, method, operation = operations()[identifier]
    assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
    errors = {"400", "401", "403", "404", "409", "503"}
    if method == "post":
        errors |= {"422", "429"}
    assert errors <= operation["responses"].keys()
    for status, response in operation["responses"].items():
        if int(status) >= 400:
            assert response["content"]["application/problem+json"]["schema"] == {
                "$ref": "#/components/schemas/QuantLabProblem"
            }
    if method == "post":
        assert operation["requestBody"]["required"] is True
        assert "application/json" in operation["requestBody"]["content"]
        key = parameters(operation)["Idempotency-Key"]
        assert key["in"] == "header" and key["required"] is True
        description = operation.get("description", "").lower()
        assert "csrf" in description and "origin" in description


@pytest.mark.parametrize("identifier", sorted(PAGED))
def test_all_list_and_curve_pages_bound_page_size_and_cursor(identifier):
    params = parameters(operations()[identifier][2])
    limit = resolve(params["limit"]["schema"])
    assert limit["type"] == "integer" and limit["minimum"] == 1 and limit["maximum"] == 200
    assert 1 <= limit["default"] <= 200
    cursor = resolve(params["cursor"]["schema"])
    assert cursor["type"] == "string"
    assert cursor["minLength"] >= 1 and cursor["maxLength"] <= 2048
    assert cursor.get("pattern")
    assert params["limit"]["in"] == params["cursor"]["in"] == "query"
    assert not params["cursor"].get("required", False)


def test_cross_batch_compare_is_bounded_read_and_curves_bind_phase_and_fold():
    params = parameters(operations()["compareQuantLabExperiments"][2])
    batch_ids = params["batchId"]
    assert batch_ids["in"] == "query" and batch_ids["required"] is True
    assert batch_ids["schema"]["type"] == "array"
    assert batch_ids["schema"]["minItems"] >= 1
    assert batch_ids["schema"]["maxItems"] == 4
    check = schema_validator(batch_ids["schema"])
    identifiers = ["11111111-1111-4111-8111-111111111111",
                   "22222222-2222-4222-8222-222222222222"]
    assert check.is_valid(identifiers)
    assert not check.is_valid([identifiers[0], identifiers[0]])
    assert not check.is_valid(identifiers * 3)
    for identifier in ("getQuantLabEquity", "getQuantLabDrawdown"):
        curve = parameters(operations()[identifier][2])
        assert {"phase", "fold"} <= curve.keys()
        assert curve["phase"]["required"] is True
        assert curve["phase"]["in"] == curve["fold"]["in"] == "query"
        phase = schema_validator(curve["phase"]["schema"])
        for name in ("training", "validation", "test", "holdout"):
            assert phase.is_valid(name)
        assert not phase.is_valid("all")
        candidate = schema_validator(curve["candidateId"]["schema"])
        assert candidate.is_valid("a" * 64)
        for invalid in ("A" * 64, "a" * 63, identifiers[0], None):
            assert not candidate.is_valid(invalid)


def test_contract_stage_does_not_fabricate_runtime_routes():
    pending = pending_contract_operations()
    actual = {(route.path, method.lower(), route.operation_id)
              for route in create_app().routes for method in route.methods}
    actual_routes = {(path, method) for path, method, _ in actual}
    for identifier, (path, method, _) in D7.items():
        operation = operations()[identifier][2]
        assert operation["x-runtime-implementation-task"] == "QSL-07"
        if identifier in pending:
            assert operation["x-runtime-available"] is False
            assert (path, method) not in actual_routes
        else:
            assert operation["x-runtime-available"] is True
            assert (path, method, identifier) in actual


def test_existing_legacy_http_paths_are_unchanged():
    new_paths = {path for path, _, _ in D7.values()}
    legacy = {path: item for path, item in OAS["paths"].items() if path not in new_paths}
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert digest == "5f5f89b5f94554904a8fed004d419c6722a16582475b4e1f5ebd3c6a96a70301"


def test_quant_reliability_read_has_bound_section_and_report_parameters():
    operation = operations()["getQuantLabReliabilityReport"][2]
    params = parameters(operation)
    assert params["reportId"]["in"] == "path"
    assert params["reportId"]["required"] is True
    section = params["section"]["schema"]
    assert section["enum"] == ["FINDINGS", "REVISION_REFERENCES"]
    assert section["default"] == "FINDINGS"
    assert params["limit"]["schema"]["default"] == 20
    assert "batch/report/hash/section/session" in params["cursor"]["description"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QuantLabReliabilityReportPage"
    }
