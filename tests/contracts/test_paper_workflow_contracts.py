"""APW-01 strict public paper workflow contracts; offline and synthetic only."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text(encoding="utf-8"))
URI = "https://agentic-trading-lab.invalid/control-api"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)


def validator(name):
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + name},
        registry=REGISTRY,
        format_checker=contract_formats(),
    )


def example(name):
    return json.loads(
        (ROOT / "contracts/examples/v1" / (name + ".valid.json")).read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    "filename,component",
    [
        ("paper-workflow", "PaperStatus"),
        ("paper-operation", "PaperOperation"),
        ("paper-limit-order-request", "PaperLimitOrderRequest"),
        ("paper-trade-proposal", "PaperTradeProposal"),
    ],
)
def test_public_and_versioned_examples_are_equivalent(filename, component):
    schema = json.loads(
        (ROOT / "contracts/schemas/v1" / (filename + ".schema.json")).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=contract_formats()).validate(example(filename))
    validator(component).validate(example(filename))


def test_all_paper_components_are_mirrored_in_versioned_workflow_schema():
    schema = json.loads(
        (ROOT / "contracts/schemas/v1/paper-workflow.schema.json").read_text(encoding="utf-8")
    )

    def normalize(value):
        if isinstance(value, dict):
            return {
                key: (
                    item.replace("#/components/schemas/", "#/$defs/")
                    if key == "$ref"
                    else normalize(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    for name, component in OAS["components"]["schemas"].items():
        if name.startswith("Paper"):
            assert schema["$defs"][name] == normalize(component)


def test_async_routes_are_authenticated_idempotent_and_have_stable_errors():
    routes = {
        path: item for path, item in OAS["paths"].items() if path.startswith("/api/v1/paper/")
    }
    assert len(routes) == 9
    for _path, item in routes.items():
        for method, op in item.items():
            assert op.get("security", OAS["security"]) == [{"LocalSession": []}]
            assert {"400", "401", "403", "503"} <= op["responses"].keys()
            for status, response in op["responses"].items():
                if int(status) >= 400:
                    assert response["content"]["application/problem+json"]["schema"][
                        "$ref"
                    ].endswith("/PaperProblem")
            if method == "post":
                assert "202" in op["responses"] and "200" not in op["responses"]
                assert {"$ref": "#/components/parameters/IdempotencyKey"} in op["parameters"]
                assert "409" in op["responses"]
    assert OAS["components"]["schemas"]["PaperControlRequest"]["required"] == ["action"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "0.1234567891"),
        ("quantity", True),
        ("quantity", 1),
        ("quantity", "1e3"),
        ("quantity", "NaN"),
        ("limitPrice", "0"),
        ("limitPrice", "1.1234567"),
        ("limitPrice", "Infinity"),
        ("symbol", "AAPL"),
        ("side", "SHORT"),
        ("orderType", "MARKET"),
        ("timeInForce", "GTC"),
        ("session", "EXTENDED"),
        ("currency", "EUR"),
        ("notional", "10"),
        ("apiKey", "synthetic-canary"),
        ("credentialGeneration", 0),
        ("accountGeneration", False),
        ("policyVersion", 9007199254740992),
        ("profileVersion", "v1"),
        ("budgetVersion", 0),
        ("riskBudget", "0"),
        ("expiresAt", "2026-10-20T12:00:00+02:00"),
        ("confirmation", "PLACE_LIVE_ORDER"),
    ],
)
def test_invalid_or_unbounded_manual_orders_are_rejected(field, value):
    request = example("paper-limit-order-request")
    request[field] = value
    assert not validator("PaperLimitOrderRequest").is_valid(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", "fake"),
        ("environment", "LIVE"),
        ("apiSecret", "synthetic-canary"),
        ("accountLabel", "12345678-1234-4234-8234-123456789012"),
        ("blockers", ["raw provider exception"]),
        ("cash", 100000),
        ("providerVerification", "ASSUMED"),
        ("observedAt", "2026-10-20T12:00:00+02:00"),
        ("entryGateOpen", True),
    ],
)
def test_status_cannot_leak_private_data_or_invent_permission(field, value):
    state = example("paper-workflow")
    state[field] = value
    assert not validator("PaperStatus").is_valid(state)


def test_operation_acceptance_is_not_success_and_failures_require_allowlisted_codes():
    accepted = example("paper-operation")
    assert accepted["status"] == "ACCEPTED"
    for state in ["BLOCKED", "FAILED"]:
        failed = dict(accepted, status=state)
        assert not validator("PaperOperation").is_valid(failed)
        failed["errorCode"] = "ACCOUNT_IDENTITY_MISMATCH"
        validator("PaperOperation").validate(failed)
        failed["errorCode"] = "private provider response"
        assert not validator("PaperOperation").is_valid(failed)
    assert not validator("PaperOperation").is_valid(dict(accepted, errorCode="PROVIDER_REJECTED"))
    for extra in ["apiKey", "apiSecret", "providerResponse", "detail"]:
        assert not validator("PaperOperation").is_valid(
            dict(accepted, **{extra: "synthetic-canary"})
        )


def test_human_provenance_cannot_fabricate_or_mix_model_decisions():
    human = example("paper-trade-proposal")
    validator("PaperTradeProposal").validate(human)
    decision = "12345678-1234-4234-8234-123456789019"
    assert not validator("PaperTradeProposal").is_valid(dict(human, decisionId=decision))
    assert not validator("PaperTradeProposal").is_valid(dict(human, humanRequestId=None))
    model = dict(human, proposalSource="MODEL_DECISION", decisionId=decision, humanRequestId=None)
    validator("PaperTradeProposal").validate(model)
    assert not validator("PaperTradeProposal").is_valid(dict(model, humanRequestId=decision))
    assert not validator("PaperTradeProposal").is_valid(dict(model, decisionId=None))


def test_start_requires_all_fences_budget_expiry_and_human_confirmation():
    order = example("paper-limit-order-request")
    start = {
        key: order[key]
        for key in [
            "credentialGeneration",
            "accountGeneration",
            "policyVersion",
            "profileVersion",
            "limitPolicyVersion",
            "budgetVersion",
            "riskBudget",
            "currency",
            "expiresAt",
        ]
    }
    start.update(symbols=["SPY", "QQQ"], strategy="mock-neutral", confirmation="START_PAPER")
    validator("PaperStartRequest").validate(start)
    for field in start:
        missing = deepcopy(start)
        del missing[field]
        assert not validator("PaperStartRequest").is_valid(missing)
    for field, value in [
        ("symbols", []),
        ("symbols", ["SPY", "SPY"]),
        ("symbols", ["SPY", "QQQ", "AAPL"]),
        ("confirmation", "START_LIVE"),
        ("strategy", "fallback-market"),
    ]:
        assert not validator("PaperStartRequest").is_valid(dict(start, **{field: value}))


def test_control_commands_need_no_setup_fence_and_cancel_is_bounded():
    for action in ["PAUSE", "STOP", "KILL"]:
        validator("PaperControlRequest").validate({"action": action})
    for action in ["LIVE", "LIQUIDATE", "START"]:
        assert not validator("PaperControlRequest").is_valid({"action": action})
    validator("PaperCancelRequest").validate(
        {"accountGeneration": 1, "confirmation": "CANCEL_PAPER_ORDER"}
    )
    assert not validator("PaperCancelRequest").is_valid(
        {
            "accountGeneration": 1,
            "confirmation": "CANCEL_PAPER_ORDER",
            "brokerOrderId": "unowned-provider-id",
        }
    )


def test_public_errors_have_no_free_text_escape_hatch():
    problem = {
        "type": "urn:agentic-trading-lab:paper-problem",
        "title": "Paper workflow request failed",
        "status": 409,
        "code": "BROKER_STATUS_UNKNOWN",
        "traceId": example("paper-operation")["traceId"],
    }
    validator("PaperProblem").validate(problem)
    for key in ["detail", "providerResponse", "stackTrace", "accountId"]:
        assert not validator("PaperProblem").is_valid(dict(problem, **{key: "synthetic-canary"}))


@pytest.mark.parametrize(
    "filename,component",
    [
        ("paper-workflow", "PaperStatus"),
        ("paper-operation", "PaperOperation"),
        ("paper-limit-order-request", "PaperLimitOrderRequest"),
        ("paper-trade-proposal", "PaperTradeProposal"),
    ],
)
def test_materialized_negative_examples_are_rejected(filename, component):
    fixture = json.loads(
        (ROOT / "contracts/examples/v1" / (filename + ".invalid.json")).read_text()
    )
    assert not validator(component).is_valid(fixture)
    schema = json.loads((ROOT / "contracts/schemas/v1" / (filename + ".schema.json")).read_text())
    assert not Draft202012Validator(schema, format_checker=contract_formats()).is_valid(fixture)


@pytest.mark.parametrize(
    "component,payload",
    [
        ("PaperVerifyRequest", {"credentialGeneration": 1}),
        (
            "PaperPrepareRequest",
            {
                "credentialGeneration": 1,
                "accountGeneration": 1,
                "verificationOperationId": "12345678-1234-4234-8234-123456789012",
            },
        ),
    ],
)
def test_explicit_verification_and_preparation_are_generation_bound(component, payload):
    validator(component).validate(payload)
    for field in payload:
        incomplete = dict(payload)
        del incomplete[field]
        assert not validator(component).is_valid(incomplete)
    assert not validator(component).is_valid(dict(payload, apiKey="synthetic-canary"))
    assert not validator(component).is_valid(dict(payload, credentialGeneration=False))


def test_acceptance_examples_match_the_requested_operation():
    expected = {
        "verifyPaperConnection": "VERIFY_CONNECTION",
        "preparePaperLedger": "PREPARE_LEDGER",
        "startPaperWorkflow": "START",
        "controlPaperWorkflow": "PAUSE",
        "createPaperLimitOrderRequest": "PLACE_LIMIT_ORDER",
        "cancelPaperOrder": "CANCEL_ORDER",
    }
    for path, item in OAS["paths"].items():
        if path.startswith("/api/v1/paper/") and "post" in item:
            operation = item["post"]
            response = operation["responses"]["202"]["content"]["application/json"]["example"]
            assert response["operationType"] == expected[operation["operationId"]]
            assert response["status"] == "ACCEPTED"


def test_duplicate_public_blockers_remain_invalid_with_array_wire_type():
    state = example("paper-workflow")
    state["blockers"] = ["CONNECTION_NOT_VERIFIED", "CONNECTION_NOT_VERIFIED"]
    assert not validator("PaperStatus").is_valid(state)
    schema = json.loads((ROOT / "contracts/schemas/v1/paper-workflow.schema.json").read_text())
    assert not Draft202012Validator(schema, format_checker=contract_formats()).is_valid(state)
