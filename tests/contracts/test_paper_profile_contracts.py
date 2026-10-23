"""Versioned Paper profile contracts remain explicit, bounded and secret-free."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from scripts.contract_formats import contract_formats
from services.control_api import paper_dto as dto
from tests.contracts.test_paper_workflow_contracts import OAS, example, validator

ROOT = Path(__file__).resolve().parents[2]


def profile(configured=False):
    return example("paper-trading-profile.configured" if configured else "paper-trading-profile")


def update():
    return example("paper-trading-profile-update-request")


def reject(model, component, value):
    assert not validator(component).is_valid(value)
    with pytest.raises(ValidationError):
        model.model_validate(value)


@pytest.mark.parametrize("configured", [False, True])
def test_actual_profile_examples_agree_between_standalone_oas_and_transport_dto(configured):
    value = profile(configured)
    validator("PaperTradingProfile").validate(value)
    schema = json.loads(
        (ROOT / "contracts/schemas/v1/paper-trading-profile.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=contract_formats()).validate(value)
    assert dto.Profile.model_validate(value).model_dump(exclude_unset=True) == value
    if not configured:
        for key in ("riskBudget", "strategy", "strategyVersion", "feeAttestation"):
            assert key in dto.Profile.model_validate(value).model_dump(exclude_unset=True)
            assert value[key] is None
        assert value["policyVersion"] == value["profileVersion"] == value["budgetVersion"] == 0


@pytest.mark.parametrize("strategy", ["mock-neutral", "neutral-baseline", "jev"])
def test_all_stored_strategies_bind_actual_mapping_version_without_starting(strategy):
    value = profile(True)
    value.update(strategy=strategy, strategyVersion=strategy + ".v1")
    validator("PaperTradingProfile").validate(value)
    assert dto.Profile.model_validate(value).strategy == strategy
    request = update()
    request["strategy"] = strategy
    validator("PaperTradingProfileUpdateRequest").validate(request)
    assert dto.ProfileUpdate.model_validate(request).strategy == strategy


def test_create_update_example_is_standalone_valid_and_has_no_activation_fields():
    value = update()
    schema = json.loads(
        (ROOT / "contracts/schemas/v1/paper-trading-profile-update-request.schema.json").read_text()
    )
    Draft202012Validator(schema, format_checker=contract_formats()).validate(value)
    assert dto.ProfileUpdate.model_validate(value).model_dump(exclude_unset=True) == value
    assert value["expectedProfileVersion"] == 0
    assert not {"entryGateOpen", "operatingState", "providerAccountId"}.intersection(value)
    value["expectedProfileVersion"] = 9007199254740991
    validator("PaperTradingProfileUpdateRequest").validate(value)
    dto.ProfileUpdate.model_validate(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("riskBudget", "0"),
        ("riskBudget", "1000000.000000000000000001"),
        ("riskBudget", "100.0000000000000000001"),
        ("riskBudget", "1e2"),
        ("riskBudget", 100),
        ("riskBudget", True),
        ("riskBudget", None),
        ("currency", "EUR"),
        ("strategy", "unknown"),
        ("strategy", None),
        ("credentialGeneration", 0),
        ("accountGeneration", True),
        ("expectedProfileVersion", True),
        ("expectedProfileVersion", -1),
        ("expectedProfileVersion", "0"),
        ("expectedProfileVersion", 9007199254740992),
        ("confirmation", "START_PAPER"),
        ("confirmation", True),
        ("feeAttestation", None),
        ("api_key", "synthetic-secret-canary"),
        ("providerAccountId", "11111111-1111-4111-8111-111111111111"),
        ("entryGateOpen", True),
    ],
)
def test_update_refuses_invalid_scalars_private_fields_and_activation_claims(field, value):
    request = update()
    request[field] = value
    reject(dto.ProfileUpdate, "PaperTradingProfileUpdateRequest", request)


@pytest.mark.parametrize(
    "mode",
    [
        "missing-scope",
        "missing-confirmed",
        "false",
        "integer",
        "string",
        "other-scope",
        "extra",
        "missing-all",
    ],
)
def test_fee_attestation_is_explicit_exact_scope_and_strict_true(mode):
    request = update()
    attestation = request["feeAttestation"]
    if mode == "missing-scope":
        del attestation["scope"]
    elif mode == "missing-confirmed":
        del attestation["confirmed"]
    elif mode in {"false", "integer", "string"}:
        attestation["confirmed"] = {"false": False, "integer": 1, "string": "true"}[mode]
    elif mode == "other-scope":
        attestation["scope"] = "alpaca-live-or-institutional"
    elif mode == "extra":
        attestation["credential"] = "synthetic-secret-canary"
    else:
        del request["feeAttestation"]
    reject(dto.ProfileUpdate, "PaperTradingProfileUpdateRequest", request)


@pytest.mark.parametrize("field", list(update()))
def test_each_update_binding_is_required(field):
    request = update()
    del request[field]
    reject(dto.ProfileUpdate, "PaperTradingProfileUpdateRequest", request)


@pytest.mark.parametrize(
    "configured,field,value",
    [
        (False, "riskBudget", "100"),
        (False, "strategy", "mock-neutral"),
        (False, "strategyVersion", "mock-neutral.v1"),
        (False, "feeAttestation", update()["feeAttestation"]),
        (False, "profileVersion", 1),
        (False, "budgetVersion", 1),
        (False, "credentialGeneration", 1),
        (False, "accountGeneration", 1),
        (False, "limitPolicyVersion", 0),
        (True, "riskBudget", None),
        (True, "strategy", None),
        (True, "strategyVersion", None),
        (True, "feeAttestation", None),
        (True, "policyVersion", 0),
        (True, "profileVersion", 0),
        (True, "budgetVersion", 0),
        (True, "accountGeneration", 0),
        (True, "credentialGeneration", 0),
        (True, "strategyVersion", "jev.v1"),
        (True, "configured", "true"),
        (True, "policyVersion", True),
        (True, "budgetVersion", 9007199254740992),
        (True, "providerAccountId", "11111111-1111-4111-8111-111111111111"),
    ],
)
def test_profile_discriminator_and_versions_cannot_invent_saved_authority(configured, field, value):
    response = profile(configured)
    response[field] = deepcopy(value)
    reject(dto.Profile, "PaperTradingProfile", response)


@pytest.mark.parametrize("field", list(profile()))
def test_profile_preserves_required_nullable_and_version_fields(field):
    response = profile()
    del response[field]
    reject(dto.Profile, "PaperTradingProfile", response)


def test_profile_routes_use_paper_security_errors_and_synchronous_durable_save():
    path = OAS["paths"]["/api/v1/paper/trading-profile"]
    assert set(path) == {"get", "put"}
    for method, operation in path.items():
        assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
        assert set(operation["responses"]) == {"200", "400", "401", "403", "409", "503"}
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/PaperTradingProfile")
        for code in ("400", "401", "403", "409", "503"):
            assert operation["responses"][code]["content"]["application/problem+json"]["schema"][
                "$ref"
            ].endswith("/PaperProblem")
        if method == "put":
            assert {"$ref": "#/components/parameters/IdempotencyKey"} in operation["parameters"]
    assert OAS["paths"]["/api/v1/trading-profile"]["put"]["operationId"] == "updateTradingProfile"
    assert (
        OAS["components"]["schemas"]["PaperStartRequest"]["properties"]["profileVersion"]["minimum"]
        == 1
    )
