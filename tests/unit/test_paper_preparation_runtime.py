"""Preparation routing, private proof handoff and fenced atomic completion."""

import json
from unittest.mock import Mock

import pytest

from services.control_api.paper_commands import PaperCommandError
from services.execution.paper_initial_evidence import InitialEvidenceUnavailable
from tests.contracts.test_paper_daily_evidence import collect
from tests.contracts.test_paper_initial_evidence import proof
from tests.unit.test_paper_verification_runtime import CLAIM, OWNER, Commands, runtime


def preparing():
    commands = Commands(CLAIM | {"operationType": "PREPARE_LEDGER"})
    instance, _, _, reader, _, verifier = runtime(commands=commands)
    prepared = collect()[0].collect(7, proof())
    source = Mock(collect=Mock(return_value=prepared))
    instance.preparation_factory = Mock(return_value=source)
    commands._call = Mock(return_value={"prepared": True})
    return instance, commands, source, reader, verifier


def test_claimed_prepare_uses_private_evidence_and_atomic_completion_not_generic_finish():
    instance, commands, source, reader, verifier = preparing()
    assert instance.tick() == "READY"
    source.collect.assert_called_once_with(7, verifier.verify.return_value.private)
    assert instance.preparation_factory.call_args.kwargs["credentials"] is reader
    statement, values = commands._call.call_args.args
    assert "public.paper_prepare" in statement
    assert values["id"] == CLAIM["operationId"]
    assert values["owner"] == OWNER and values["fence"] == CLAIM["fence"]
    data = json.loads(values["evidence"])
    assert data["positions"][0]["costBasis"] == "300"
    assert data["daily"]["openingLower"] == "100499.99"
    assert not commands.finishes


@pytest.mark.parametrize(
    "internal,public",
    [
        ("INITIAL_OPEN_ORDERS", "OPEN_ORDERS_PRESENT"),
        ("INITIAL_COST_BASIS_MISSING", "COST_BASIS_MISSING"),
        ("INITIAL_EVIDENCE_CHANGED", "SNAPSHOT_CHANGED"),
        ("DAILY_REFERENCE_UNPROVEN", "RECONCILIATION_REQUIRED"),
        ("EXTERNAL_FLOW_UNPROVEN", "RECONCILIATION_REQUIRED"),
    ],
)
def test_failed_source_never_invokes_initialization(internal, public):
    instance, commands, source, *_ = preparing()
    source.collect.side_effect = InitialEvidenceUnavailable(internal)
    instance.tick()
    commands._call.assert_not_called()
    assert commands.finishes[0][1]["error_code"] == public


def test_sql_fence_rejection_remains_a_failed_operation_without_local_success():
    instance, commands, *_ = preparing()
    commands._call.side_effect = PaperCommandError(409, "VERSION_CONFLICT")
    instance.tick()
    assert commands.finishes[0][1]["error_code"] == "VERSION_CONFLICT"


def test_verification_failure_prevents_financial_evidence_collection():
    instance, commands, source, _, verifier = preparing()
    verifier.verify.side_effect = RuntimeError("synthetic-private-provider-error")
    instance.tick()
    source.collect.assert_not_called()
    instance.preparation_factory.assert_not_called()
    commands._call.assert_not_called()
    assert commands.finishes[0][1]["error_code"] == "PROVIDER_UNAVAILABLE"
