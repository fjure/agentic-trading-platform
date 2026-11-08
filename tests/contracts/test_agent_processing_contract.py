"""Contract oracles for APF-01 and the structure of NLP-05 and NLP-12."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
SCHEMA = json.loads((ROOT / "contracts/schemas/v1/agent-processing.schema.json").read_text("utf-8"))
EXAMPLES = ROOT / "contracts/examples/v1"
FIXTURES = json.loads((EXAMPLES / "agent-processing-fixtures.json").read_text("utf-8"))
FIXTURES["preview"] = json.loads((EXAMPLES / "agent-processing.valid.json").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/agent-processing-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OPENAPI, default_specification=DRAFT202012)
)
BASE = "/api/v1/research/agent/dialogues/{id}/turns/{turnId}/"


@pytest.fixture(params=["openapi", "standalone"])
def validator(request):
    def for_model(model):
        if request.param == "standalone":
            return Draft202012Validator(
                {**SCHEMA, "$ref": "#/$defs/" + model}, format_checker=contract_formats()
            )
        return Draft202012Validator(
            {"$ref": URI + "#/components/schemas/" + model},
            registry=REGISTRY,
            format_checker=contract_formats(),
        )

    return for_model


@pytest.mark.parametrize(
    ("fixture", "model"),
    [("preview", "ProcessingPreviewV1"), ("status", "ProcessingStatusV1")]
    + [(key, "ProcessingDecisionV1") for key in ("send", "decline", "revoke")],
)
def test_valid_examples(validator, fixture, model):
    validator(model).validate(FIXTURES[fixture])


@pytest.mark.parametrize(
    ("fixture", "changes"),
    [
        ("send", {"expectedDialogueVersion": 0}),
        ("send", {"projectionBinding": ""}),
        ("send", {"receiptId": "receipt-1"}),
        ("send", {"projection": {}}),
        ("decline", {"previewId": "preview-1"}),
        ("revoke", {"projectionBinding": "opaque-binding-fixture-1"}),
        ("send", {"action": "APPROVE_AND_START"}),
    ],
)
def test_decisions_require_exclusive_binding(validator, fixture, changes):
    assert not validator("ProcessingDecisionV1").is_valid(FIXTURES[fixture] | changes)


def test_projection_and_receipt_are_closed(validator):
    preview = FIXTURES["preview"]
    status = FIXTURES["status"]
    projection = preview["projection"] | {"conversationHistory": ["disallowed"]}
    receipt = status["receipt"] | {"projection": preview["projection"]}
    assert not validator("ProcessingPreviewV1").is_valid(preview | {"projection": projection})
    assert not validator("ProcessingStatusV1").is_valid(status | {"receipt": receipt})


def test_preview_cannot_understate_data_classes(validator):
    validate = validator("ProcessingPreviewV1")
    preview = FIXTURES["preview"]
    assert not validate.is_valid(preview | {"dataClasses": ["CONFIRMED_RESEARCH_FIELDS"]})
    excerpt_only = preview["projection"] | {"confirmedContext": {}}
    validate.validate(
        preview | {"projection": excerpt_only, "dataClasses": ["OPERATOR_RESEARCH_EXCERPT"]}
    )


def test_send_accepted_requires_durable_receipt(validator):
    validate = validator("AgentSendAcceptedV1")
    accepted = FIXTURES["status"]
    validate.validate(accepted)
    assert not validate.is_valid(accepted | {"delivery": "MAY_HAVE_BEEN_SENT"})
    missing_receipt = {key: value for key, value in accepted.items() if key != "receipt"}
    assert not validate.is_valid(missing_receipt)
    assert not validate.is_valid(accepted | {"receipt": accepted["receipt"] | {"state": "REVOKED"}})


def test_processing_operations_have_session_and_safe_response_contracts():
    assert OPENAPI["security"] == [{"LocalSession": []}]
    for suffix, method in (
        ("processing-previews", "post"),
        ("processing", "get"),
        ("processing-decisions", "post"),
    ):
        operation = OPENAPI["paths"][BASE + suffix][method]
        assert operation.get("security", OPENAPI["security"]) == OPENAPI["security"]
        for code in ("401", "403", "404", "409", "422", "429", "503"):
            response_ref = operation["responses"][code]["$ref"]
            assert response_ref == f"#/components/responses/AgentProcessing{code}"
        for code, response in operation["responses"].items():
            if code.startswith("2"):
                assert response["headers"]["Cache-Control"]["schema"]["enum"] == ["no-store"]
    preview = OPENAPI["paths"][BASE + "processing-previews"]["post"]
    assert {"$ref": "#/components/parameters/AgentPreviewIdempotencyKey"} in preview["parameters"]
