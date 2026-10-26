"""RDR-01 Vertragsabnahme; keine Zertifizierung der späteren Runtimepfade.

T-RDR-004/009/010/013..018 und T-HMD-016/018..022: öffentliche Struktur,
geschlossene DTOs, Status-/Zeitdarstellung und generierter Client.
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
EXAMPLES = ROOT / "contracts/examples/v1"
FIXTURES = json.loads((EXAMPLES / "readiness-fixtures.json").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/readiness-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)


def validator(component):
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY, format_checker=contract_formats(),
    )


def example(filename):
    return json.loads((EXAMPLES / filename).read_text("utf-8"))


def walk(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, (*path, index))


def replace(value, path, replacement):
    if not path:
        return replacement
    result = copy.deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return result


@pytest.mark.parametrize("filename,component", FIXTURES.items())
def test_all_public_examples(filename, component):
    validator(component).validate(example(filename))


@pytest.mark.parametrize("filename,component", [
    ("readiness-assessment.partial-eligible.invalid.json", "ReadinessAssessment"),
    ("collection-update.override.invalid.json", "CollectionPlanUpdate"),
])
def test_persisted_negative_examples(filename, component):
    assert not validator(component).is_valid(example(filename))


@pytest.mark.parametrize("filename,component", FIXTURES.items())
def test_every_nested_object_rejects_unknown_fields(filename, component):
    value = example(filename)
    check = validator(component)
    for path, node in walk(value):
        if isinstance(node, dict):
            bad = replace(value, path, {**node, "unknownProviderField": "denied"})
            assert not check.is_valid(bad), (filename, path)


@pytest.mark.parametrize("filename,component", FIXTURES.items())
def test_timestamp_fields_reject_noncanonical_and_impossible_utc(filename, component):
    value = example(filename)
    check = validator(component)
    for path, node in walk(value):
        if isinstance(node, str) and node.startswith("2026-") and node.endswith("Z"):
            for invalid in [
                "2026-10-26T00:00:00+00:00", "2026-10-26T00:00:00",
                "2026-02-30T00:00:00Z", "2026-10-26T00:00:00.1234567Z",
            ]:
                assert not check.is_valid(replace(value, path, invalid)), (filename, path)


def test_exact_five_authenticated_operations_reuse_backtest_protocol():
    paths = {
        path: methods for path, methods in OAS["paths"].items()
        if "/data-readiness/" in path or "/collection/plans" in path
    }
    assert set(paths) == {
        "/api/v1/research/data-readiness/assessments",
        "/api/v1/research/data-readiness/assessments/{assessmentId}",
        "/api/v1/research/data-readiness/assessments/{assessmentId}/findings",
        "/api/v1/research/collection/plans",
        "/api/v1/research/collection/plans/{instrumentId}",
    }
    operations = [
        (method, op) for methods in paths.values() for method, op in methods.items()
        if method in {"get", "post", "put"}
    ]
    assert len(operations) == 5
    for method, op in operations:
        assert op.get("security", OAS["security"]) == [{"LocalSession": []}]
        assert {"400", "401", "403", "503"} <= op["responses"].keys()
        if method in {"post", "put"}:
            assert {"$ref": "#/components/parameters/IdempotencyKey"} in op["parameters"]
    post = paths["/api/v1/research/data-readiness/assessments"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/BacktestRequest"
    }
    assert {"202", "409", "429"} <= post["responses"].keys()


def test_generated_client_exposes_all_five_operations():
    generated = ROOT / "apps/dashboard/src/app/api/generated/control-api"
    services = "\n".join(path.read_text("utf-8") for path in (generated / "api").glob("*.ts"))
    for path, methods in OAS["paths"].items():
        if "/data-readiness/" in path or "/collection/plans" in path:
            for op in methods.values():
                assert f"public {op['operationId']}(" in services
    for component in set(FIXTURES.values()):
        if OAS["components"]["schemas"][component].get("type") == "string":
            continue  # Primitive Aliasnamen erzeugen absichtlich keine eigene Modeldatei.
        model = component[0].lower() + component[1:]
        assert (generated / f"model/{model}.ts").is_file()


@pytest.mark.parametrize("eligibility", ["ELIGIBLE", "BLOCKED", "UNKNOWN"])
def test_unstarted_assessment_cannot_claim_an_evaluation(eligibility):
    value = example("readiness-assessment.accepted.valid.json")
    value["eligibility"] = eligibility
    assert not validator("ReadinessAssessment").is_valid(value)


def test_budget_failure_cannot_publish_positive_or_partial_report():
    value = example("readiness-assessment.budget-exhausted.valid.json")
    for field, invalid in [
        ("eligibility", "ELIGIBLE"), ("status", "SUCCEEDED"),
        ("publicationComplete", True),
        ("report", example("readiness-assessment.eligible.valid.json")["report"]),
    ]:
        assert not validator("ReadinessAssessment").is_valid({**value, field: invalid})


@pytest.mark.parametrize("dimension", [
    "integrity", "sourceAuthenticity", "temporalAuthenticity", "completeness", "domainQuality",
])
@pytest.mark.parametrize("status", ["REJECTED", "UNKNOWN", "NOT_EVALUATED"])
def test_eligible_report_requires_five_positive_dimensions(dimension, status):
    value = example("readiness-assessment.eligible.valid.json")
    value["report"]["trust"][dimension]["status"] = status
    assert not validator("ReadinessAssessment").is_valid(value)


def test_null_denominator_is_na_and_never_complete_success():
    value = {
        "status": "N/A", "numerator": 0, "denominator": 0,
        "denominatorBasis": "EXPECTED_BAR_IDENTITIES", "unknownCount": 0,
    }
    check = validator("ReadinessRatio")
    check.validate(value)
    assert not check.is_valid({**value, "status": "MEASURED"})
    assert not check.is_valid({**value, "numerator": 1})


def test_ten_bar_fixture_keeps_receipt_and_usable_deadlines_separate():
    value = example("readiness-metrics.ten-bars.valid.json")
    assert value["expectedBars"] == 10
    assert value["receivedBySignalDeadline"]["numerator"] == 8
    assert value["usableBySignalDeadline"]["numerator"] == 6
    assert value["usableByOutcomeDeadline"]["numerator"] == 8
    assert value["missingBars"]["numerator"] == 2
    assert value["lateForSignal"]["numerator"] == 2
    assert value["lateForOutcome"]["numerator"] == 0
    for field in ["usableBySignalDeadline", "usableByOutcomeDeadline", "missingBars"]:
        assert value[field]["denominator"] == 10


@pytest.mark.parametrize("cursor", ["", "short", "a/" * 32, "a=" * 32, "a b" * 32, "x" * 2049])
def test_invalid_cursor_syntax_rejected(cursor):
    value = example("readiness-findings.first-page.valid.json")
    assert not validator("ReadinessFindingPage").is_valid({**value, "nextCursor": cursor})


def test_collection_update_cannot_override_provider_or_global_gate():
    value = example("collection-update.enable.valid.json")
    check = validator("CollectionPlanUpdate")
    for field, invalid in [
        ("provider", "other"), ("feed", "sip"), ("interval", "1Min"),
        ("researchIngestionEnabled", True), ("credential", "redacted"),
        ("expectedVersion", -1), ("enabled", "true"),
    ]:
        assert not check.is_valid({**value, field: invalid})


def test_public_error_examples_are_closed_and_status_bound():
    for filename, component in FIXTURES.items():
        if component != "ReadinessProblem":
            continue
        value = example(filename)
        check = validator(component)
        assert not check.is_valid({**value, "detail": "internal provider response"})
        assert not check.is_valid({**value, "status": 200})
