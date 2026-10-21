"""Compare the real transport validators with the canonical bounded requests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from services.control_api import paper_dto as d
from tests.contracts.test_paper_workflow_contracts import example, validator


def requests():
    limit = example("paper-limit-order-request")
    start = {
        key: limit[key]
        for key in (
            "credentialGeneration",
            "accountGeneration",
            "policyVersion",
            "profileVersion",
            "limitPolicyVersion",
            "budgetVersion",
            "riskBudget",
            "currency",
            "expiresAt",
        )
    }
    start.update(symbols=["SPY", "QQQ"], strategy="mock-neutral", confirmation="START_PAPER")
    return [
        (d.Verify, "PaperVerifyRequest", {"credentialGeneration": 1}),
        (
            d.Prepare,
            "PaperPrepareRequest",
            {
                "credentialGeneration": 1,
                "accountGeneration": 1,
                "verificationOperationId": "12345678-1234-4234-8234-123456789012",
            },
        ),
        (d.Start, "PaperStartRequest", start),
        (d.Control, "PaperControlRequest", {"action": "KILL"}),
        (d.Limit, "PaperLimitOrderRequest", limit),
        (
            d.Cancel,
            "PaperCancelRequest",
            {
                "accountGeneration": 1,
                "confirmation": "CANCEL_PAPER_ORDER",
            },
        ),
    ]


@pytest.mark.parametrize("model,component,payload", requests())
def test_request_dtos_accept_exact_canonical_examples_and_reject_missing_extra(
    model, component, payload
):
    validator(component).validate(payload)
    assert model.model_validate(payload).model_dump() == payload
    for field in payload:
        missing = deepcopy(payload)
        del missing[field]
        with pytest.raises(ValidationError):
            model.model_validate(missing)
    with pytest.raises(ValidationError):
        model.model_validate(dict(payload, apiKey="synthetic-canary"))


@pytest.mark.parametrize(
    "field,values",
    [
        ("quantity", [0.1, "NaN", "1e2", "0", "-1", "0.0000000001", "1" * 21]),
        ("limitPrice", [True, "Infinity", "0", "0.0000001", "-0.1", "1" * 21]),
        ("riskBudget", ["0", "1000000.1", "1e2", True]),
        ("credentialGeneration", [True, 0, "1", 9007199254740992]),
        ("expiresAt", ["2026-10-21T10:00:00+02:00", "2026-99-21T10:00:00Z", "nonsenseZ"]),
        ("orderType", ["MARKET"]),
        ("session", ["EXTENDED"]),
    ],
)
def test_negative_values_fail_in_dto_and_canonical_contract(field, values):
    for value in values:
        payload = dict(example("paper-limit-order-request"), **{field: value})
        assert not validator("PaperLimitOrderRequest").is_valid(payload)
        with pytest.raises(ValidationError):
            d.Limit.model_validate(payload)


def test_request_decimal_boundaries_remain_exact():
    value = dict(
        example("paper-limit-order-request"),
        quantity="99999999999999999999.123456789",
        limitPrice="99999999999999999999.123456",
        riskBudget="1000000.000000000000000000",
    )
    validator("PaperLimitOrderRequest").validate(value)
    assert d.Limit.model_validate(value).model_dump() == value


@pytest.mark.parametrize(
    "model,component,fixture",
    [
        (d.Operation, "PaperOperation", "paper-operation"),
        (d.Status, "PaperStatus", "paper-workflow"),
    ],
)
def test_public_projection_keeps_required_nulls_and_rejects_private_fields(
    model, component, fixture
):
    payload = example(fixture)
    value = model.model_validate(payload).model_dump(exclude_unset=True)
    assert value == payload
    validator(component).validate(value)
    for extra in ("private_account_id", "providerResponse", "api_key"):
        with pytest.raises(ValidationError):
            model.model_validate(dict(payload, **{extra: "synthetic-canary"}))


def test_operation_error_and_status_latch_are_honest():
    operation = example("paper-operation")
    for changes in (
        {"status": "FAILED"},
        {"errorCode": None},
        {"errorCode": "PROVIDER_UNAVAILABLE"},
    ):
        with pytest.raises(ValidationError):
            d.Operation.model_validate(dict(operation, **changes))
    d.Operation.model_validate(dict(operation, status="BLOCKED", errorCode="CREDENTIALS_MISSING"))
    for changes in (
        {"entryGateOpen": True},
        {"cash": None},
        {"riskBudget": None},
        {"blockers": ["ENTRY_PAUSED", "ENTRY_PAUSED"]},
    ):
        with pytest.raises(ValidationError):
            d.Status.model_validate(dict(example("paper-workflow"), **changes))
