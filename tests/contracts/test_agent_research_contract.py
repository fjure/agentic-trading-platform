"""APF-01 request and response fixtures and public contract boundaries."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats
from scripts.contract_stages import AGENT_RESEARCH_OPERATIONS, pending_contract_operations

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
STANDALONE = json.loads(
    (ROOT / "contracts/schemas/v1/agent-research.schema.json").read_text("utf-8")
)
FIXTURES = json.loads(
    (ROOT / "contracts/examples/v1/agent-research-fixtures.json").read_text("utf-8")
)
URI = "https://agentic-trading-lab.invalid/agent-research-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)
BASE = "/api/v1/research/agent"
OPERATIONS = {
    "/ideas": {"get", "post"},
    "/ideas/{id}": {"get"},
    "/ideas/{id}/revisions": {"post"},
    "/dialogues": {"post"},
    "/dialogues/{id}": {"get"},
    "/dialogues/{id}/turns": {"post"},
    "/dialogues/{id}/decisions": {"post"},
    "/registry": {"get"},
    "/runs": {"get"},
    "/runs/{id}": {"get"},
    "/runs/{id}/events": {"get"},
    "/runs/{id}/cancel": {"post"},
    "/schedule": {"get", "put"},
    "/schedule/commands": {"post"},
    "/approvals": {"get"},
    "/approvals/{id}": {"get"},
    "/approvals/{id}/decision": {"post"},
    "/reports": {"get"},
    "/reports/{id}": {"get"},
    "/providers": {"get"},
    "/providers/{provider}/probe": {"post"},
}


def validate_oas(component, value):
    Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY,
        format_checker=contract_formats(),
    ).validate(value)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["case"])
def test_apf_wire_fixture(fixture):
    component, value = fixture["schema"], fixture["value"]
    validate_oas(component, value)
    Draft202012Validator(
        {**STANDALONE, "$ref": "#/$defs/" + component}, format_checker=contract_formats()
    ).validate(value)


def test_apf_standalone_definitions_match_openapi():
    assert STANDALONE["$defs"]
    for name, definition in STANDALONE["$defs"].items():
        normalized = json.loads(json.dumps(definition).replace("#/$defs/", "#/components/schemas/"))
        assert normalized == OAS["components"]["schemas"][name]


def test_apf_contract_stage_matches_public_routes():
    prefixes = (BASE + "/", "/api/v1/integrations/research-providers/")
    public_ids = {
        operation["operationId"]
        for path, item in OAS["paths"].items()
        if path.startswith(prefixes)
        for method, operation in item.items()
        if method in {"get", "post", "put"}
    }
    assert public_ids == AGENT_RESEARCH_OPERATIONS.keys()
    task = "8.1 [APF-08 Control API]"
    pending = pending_contract_operations(agent_research_tasks_text=f"- [ ] {task}")
    done = pending_contract_operations(agent_research_tasks_text=f"- [x] {task}")
    assert pending - done == public_ids


@pytest.mark.parametrize("suffix, methods", OPERATIONS.items())
def test_apf_operations_require_session_and_safe_errors(suffix, methods):
    item = OAS["paths"][BASE + suffix]
    for method in methods:
        operation = item[method]
        assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
        assert {"401", "403", "404", "409", "422", "429", "503"} <= operation["responses"].keys()
        if method == "get":
            continue
        params = [*item.get("parameters", []), *operation.get("parameters", [])]
        assert {"$ref": "#/components/parameters/IdempotencyKey"} in params
        assert operation["requestBody"]["required"] is True


def request_example(suffix):
    media = OAS["paths"][BASE + suffix]["post"]["requestBody"]["content"]["application/json"]
    return media["example"]


def test_hypothesis_confirmation_cannot_dispatch_or_start():
    confirmation = request_example("/dialogues/{id}/decisions")
    for forbidden in ("previewId", "projectionBinding", "approvalId", "batchId", "tool"):
        with pytest.raises(ValidationError):
            validate_oas("AgentDialogueDecisionV1", confirmation | {forbidden: "foreign"})
    binding_fields = (
        "proposalId", "proposalHash", "expectedIdeaVersion", "expectedDialogueVersion"
    )
    for required in binding_fields:
        invalid = {name: value for name, value in confirmation.items() if name != required}
        with pytest.raises(ValidationError):
            validate_oas("AgentDialogueDecisionV1", invalid)


def test_quant_approval_accepts_only_existing_versioned_binding():
    approval = request_example("/approvals/{id}/decision")
    for forbidden in ("operatorId", "definition", "validationId", "resourceBudget", "batchId"):
        with pytest.raises(ValidationError):
            validate_oas("AgentQuantApprovalDecisionV1", approval | {forbidden: "foreign"})
    with pytest.raises(ValidationError):
        validate_oas("AgentQuantApprovalDecisionV1", approval | {"bindingHash": "changed"})


def test_provider_credentials_are_write_only_and_setup_scoped():
    schemas = OAS["components"]["schemas"]
    writes = {"AgentCredentialSaveRequestV1", "AgentCredentialRotateRequestV1"}
    scope = "/api/v1/integrations/research-providers/{provider}"
    seen = set()
    for path, item in OAS["paths"].items():
        for method in ("post", "put"):
            op = item.get(method, {})
            request = op.get("requestBody", {}).get("content", {}).get("application/json", {})
            name = request.get("schema", {}).get("$ref", "").rsplit("/", 1)[-1]
            if name not in writes:
                continue
            seen.add(name)
            assert path == scope or path.startswith(scope + "/")
            assert op["tags"] == ["Agent provider setup"]
            assert schemas[name]["properties"]["credential"]["writeOnly"] is True
            assert "example" not in request and "examples" not in request
            assert all(
                name not in json.dumps(response) for response in op["responses"].values()
            )
    assert seen == writes


def test_local_answer_has_no_free_text_dispatch_or_experiment_fields():
    local = next(
        fixture["value"] for fixture in FIXTURES
        if fixture["case"] == "createAgentDialogueTurn-request-localAnswer"
    )
    for field in ("text", "previewId", "batchId"):
        with pytest.raises(ValidationError):
            validate_oas("AgentCreateDialogueTurnV1", local | {field: "foreign"})
    with pytest.raises(ValidationError):
        validate_oas("AgentCreateDialogueTurnV1", local | {"expectedIdeaVersion": 0})


def test_report_requires_distinct_phases_and_disallows_unavailable_metrics():
    unavailable = {"status": "UNAVAILABLE", "metrics": [], "limitations": []}
    phases = dict.fromkeys(("inSample", "validation", "walkForwardOos", "holdout"), unavailable)
    validate_oas("AgentReportPhasesV1", phases)
    with pytest.raises(ValidationError):
        validate_oas("AgentReportPhasesV1", phases | {"holdout": unavailable | {"metrics": [{}]}})
    phases.pop("holdout")
    with pytest.raises(ValidationError):
        validate_oas("AgentReportPhasesV1", phases)
