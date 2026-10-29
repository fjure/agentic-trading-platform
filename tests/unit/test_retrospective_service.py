"""Public mode normalization and mutable warnings never rewrite historical facts."""

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.research.models import content_fingerprint
from services.control_api.retrospective_dto import COMPONENT_MODELS
from services.control_api.retrospective_service import RetrospectiveService, normalize_request
from tests.unit.test_backtest_request import NOW, valid_request


def test_missing_and_explicit_strict_mode_retain_legacy_idempotency_preimage():
    original = normalize_request(valid_request(), NOW)
    explicit = normalize_request({**valid_request(), "researchMode": "STRICT_PIT"}, NOW)
    assert explicit == original
    assert content_fingerprint(explicit) == content_fingerprint(original)
    assert "researchMode" not in explicit


@pytest.mark.parametrize(
    "field,value",
    [
        ("assessmentId", str(uuid4())),
        ("assessmentHash", "a" * 64),
        ("selectionPolicyVersion", "retrospective-v1"),
    ],
)
def test_strict_request_rejects_retrospective_binding_fields(field, value):
    with pytest.raises(ValueError):
        normalize_request({**valid_request(), "researchMode": "STRICT_PIT", field: value}, NOW)


def test_retrospective_normalization_retains_explicit_receipt_binding():
    patch = {
        "researchMode": "RETROSPECTIVE_EXPLORATORY",
        "selectionPolicyVersion": "retrospective-v1",
        "assessmentId": str(uuid4()),
        "assessmentHash": "b" * 64,
    }
    request = normalize_request({**valid_request(), **patch}, NOW)
    assert {key: request[key] for key in patch} == patch
    assert request["startCash"] == normalize_request(valid_request(), NOW)["startCash"]


@pytest.mark.parametrize(
    "code,status,blocks",
    [
        ("LICENSE_DENIED", "DENIED", True),
        ("TRUST_REVOKED", "UNKNOWN", True),
        ("STRUCTURAL_CORPORATE_ACTION", "DECLARED_CONFIRMED", True),
        ("CORPORATE_ACTION_CONFLICT", "DECLARED_CONFIRMED", True),
        (None, "UNKNOWN", False),
        (None, "DECLARED_CONFIRMED", None),
    ],
)
def test_current_warnings_use_real_check_time_and_independent_current_gate(code, status, blocks):
    checked = datetime(2026, 10, 28, 12, tzinfo=UTC)
    connection = SimpleNamespace(execute=lambda _sql: SimpleNamespace(scalar_one=lambda: checked))

    @contextmanager
    def transaction():
        yield connection

    service = RetrospectiveService(SimpleNamespace(engine=None))
    service.storage = SimpleNamespace(
        transaction=transaction,
        current_assessment_gate=lambda *_: code,
        current_license=lambda *_: {"status": status},
        has_retrospective_correction=lambda *_: False,
    )
    warnings = service.current_warnings(uuid4())
    if blocks is None:
        assert warnings == []
    else:
        assert warnings[0]["blocksNewExecution"] is blocks
        assert warnings[0]["checkedAt"] == "2026-10-28T12:00:00Z"
        assert warnings[0]["code"] == (code or "LICENSE_UNKNOWN")
        assert warnings[0]["category"] == (
            "CORPORATE_ACTIONS"
            if code in {"STRUCTURAL_CORPORATE_ACTION", "CORPORATE_ACTION_CONFLICT"}
            else "LICENSE"
            if code is None or code.startswith("LICENSE_")
            else "INTEGRITY"
        )


def test_current_port_failure_is_a_closed_warning_without_error_payload():
    @contextmanager
    def unavailable():
        raise RuntimeError("synthetic private diagnostic must never leave the port")
        yield  # pragma: no cover

    service = RetrospectiveService(SimpleNamespace(engine=None))
    service.storage = SimpleNamespace(transaction=unavailable)
    warning = service.current_warnings(uuid4())[0]
    assert warning["code"] == "TRUST_UNAVAILABLE"
    assert warning["blocksNewExecution"] is True
    assert "private" not in str(warning)


@pytest.mark.parametrize("gate", [None, "LICENSE_DENIED", "STRUCTURAL_CORPORATE_ACTION"])
def test_correction_warning_is_independent_and_does_not_replace_a_current_blocker(gate):
    checked = datetime(2026, 10, 29, 8, tzinfo=UTC)
    connection = SimpleNamespace(execute=lambda _sql: SimpleNamespace(scalar_one=lambda: checked))

    @contextmanager
    def transaction():
        yield connection

    service = RetrospectiveService(SimpleNamespace(engine=None))
    service.storage = SimpleNamespace(
        transaction=transaction,
        current_assessment_gate=lambda *_: gate,
        current_license=lambda *_: {"status": "DECLARED_CONFIRMED"},
        has_retrospective_correction=lambda *_: True,
    )
    warnings = service.current_warnings(uuid4())
    assert [warning["code"] for warning in warnings] == ([gate] if gate else []) + [
        "EX_POST_REVISIONS"
    ]
    correction = warnings[-1]
    assert correction == {
        "code": "EX_POST_REVISIONS",
        "category": "HISTORICAL_AVAILABILITY",
        "checkedAt": "2026-10-29T08:00:00Z",
        "blocksNewExecution": False,
    }
    for warning in warnings:
        COMPONENT_MODELS["BacktestCurrentReliabilityWarning"].model_validate(warning)


def test_correction_port_failure_preserves_known_blocker_and_fails_closed():
    checked = datetime(2026, 10, 29, 8, tzinfo=UTC)
    connection = SimpleNamespace(execute=lambda _sql: SimpleNamespace(scalar_one=lambda: checked))

    @contextmanager
    def transaction():
        yield connection

    def failed(*_):
        raise RuntimeError("synthetic private diagnostic")

    service = RetrospectiveService(SimpleNamespace(engine=None))
    service.storage = SimpleNamespace(
        transaction=transaction,
        current_assessment_gate=lambda *_: "LICENSE_DENIED",
        has_retrospective_correction=failed,
    )
    warnings = service.current_warnings(uuid4())
    assert [warning["code"] for warning in warnings] == ["LICENSE_DENIED", "TRUST_UNAVAILABLE"]
    assert all(warning["blocksNewExecution"] for warning in warnings)
    assert "private" not in str(warnings)


@pytest.mark.parametrize("mutation", ["fold", "parameters"])
def test_nested_run_request_preserves_cross_field_response_semantics(mutation):
    import json
    from pathlib import Path

    value = json.loads(
        (
            Path(__file__).resolve().parents[2] / "contracts/examples/v1/backtest-run.valid.json"
        ).read_text()
    )
    if mutation == "fold":
        fold = value["request"]["folds"][0]
        fold["trainingStart"] = fold["trainingEnd"]
    else:
        value["request"].update(strategyId="sma-cross-v1", parameters={"fast": 10, "slow": 5})
    with pytest.raises(ValueError):
        COMPONENT_MODELS["BacktestRun"].model_validate(value)
