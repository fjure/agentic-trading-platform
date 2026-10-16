"""PTV-18 mode/policy outbox payload and schema contract checks."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from packages.domain.models import ControlEvent, EventEnvelope, Mode, SafetyEvent, SafetyState
from packages.observability.outbox import _envelope, persist

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts/schemas/v1"
EXAMPLES = ROOT / "contracts/examples/v1"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def _validator(name: str) -> Draft202012Validator:
    documents = [
        json.loads(path.read_text(encoding="utf-8")) for path in SCHEMAS.glob("*.schema.json")
    ]
    registry = Registry().with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents
    )
    selected = next(
        document for document in documents if document["$id"].endswith(f"/{name}.schema.json")
    )
    return Draft202012Validator(selected, registry=registry, format_checker=FormatChecker())


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("event_type", "action"),
    [
        ("control.mode.changed.v1", "MODE_CHANGED"),
        ("control.policy.changed.v1", "POLICY_CHANGED"),
    ],
)
def test_control_schema_and_action_bound_envelope(event_type: str, action: str) -> None:
    payload = _example("control-event.valid")
    payload["action"] = action
    assert _validator("control-event").is_valid(payload)
    envelope = _example("event-envelope.valid")
    envelope.update(eventType=event_type, payload=payload)
    assert _validator("event-envelope").is_valid(envelope)
    wrong = "POLICY_CHANGED" if action == "MODE_CHANGED" else "MODE_CHANGED"
    payload["action"] = wrong
    assert not _validator("event-envelope").is_valid(envelope)


@pytest.mark.parametrize(
    "mutation", ["live", "extra", "invalid-action", "zero-version", "bool-version", "bad-reason"]
)
def test_control_schema_rejects_invalid_payloads(mutation: str) -> None:
    payload = _example("control-event.valid")
    if mutation == "live":
        payload["mode"] = "LIVE"
    elif mutation == "extra":
        payload["brokerOrder"] = "send"
    elif mutation == "invalid-action":
        payload["action"] = "PAUSE"
    elif mutation == "zero-version":
        payload["policyVersion"] = 0
    elif mutation == "bool-version":
        payload["policyVersion"] = True
    else:
        payload["reasonCode"] = "UNKNOWN"
    assert not _validator("control-event").is_valid(payload)


def test_invalid_example_rejects_live() -> None:
    assert not _validator("control-event").is_valid(_example("control-event.invalid"))


@pytest.mark.parametrize(
    ("event_type", "action"),
    [
        ("control.mode.changed.v1", "MODE_CHANGED"),
        ("control.policy.changed.v1", "POLICY_CHANGED"),
    ],
)
def test_control_event_persists_and_decodes_without_poisoning_outbox(
    event_type: str, action: str
) -> None:
    payload = ControlEvent("paper", action, Mode.SHADOW, SafetyState.PAUSE_NEW, 2)
    event = EventEnvelope(
        "evt_control", event_type, datetime(2026, 10, 15, tzinfo=UTC), "paper", 2, TRACE, payload
    )
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = "evt_control"
    persist(connection, event)
    inserted = connection.execute.call_args.args[1]
    stored_payload = json.loads(inserted["payload"])
    assert stored_payload == {
        "account_id": "paper",
        "action": action,
        "mode": "SHADOW",
        "safety_state": "PAUSE_NEW",
        "policy_version": 2,
        "reason_code": "OK",
    }
    row = copy.deepcopy({key: value for key, value in inserted.items() if key != "payload"})
    row["payload"] = stored_payload
    restored = _envelope(row)
    assert restored == event
    assert restored.payload == payload


def test_control_event_rejects_wrong_action_and_untyped_state() -> None:
    with pytest.raises(ValueError, match="control event type/action mismatch"):
        EventEnvelope(
            "evt_control",
            "control.mode.changed.v1",
            datetime(2026, 10, 15, tzinfo=UTC),
            "paper",
            2,
            TRACE,
            ControlEvent("paper", "POLICY_CHANGED", Mode.SHADOW, SafetyState.PAUSE_NEW, 2),
        )
    with pytest.raises(ValueError, match="invalid control state"):
        ControlEvent("paper", "MODE_CHANGED", "LIVE", SafetyState.PAUSE_NEW, 2)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="version must be a positive integer"):
        ControlEvent("paper", "MODE_CHANGED", Mode.SHADOW, SafetyState.PAUSE_NEW, True)  # type: ignore[arg-type]


def test_emergency_latch_can_report_absent_policy_without_authorizing_recovery():
    payload = _example("safety-event.valid")
    payload.update(action="PAUSE", safetyState="PAUSE_NEW", policyVersion=None, requestId=None)
    assert _validator("safety-event").is_valid(payload)
    event = SafetyEvent("paper", "PAUSE", Mode.DISABLED, SafetyState.PAUSE_NEW, None, "PAUSED_NEW")
    assert event.policy_version is None
    payload.update(action="RESUMED", safetyState="RUNNING", mode="PAPER", requestId="request")
    assert not _validator("safety-event").is_valid(payload)
    with pytest.raises(ValueError, match="requires policy version"):
        SafetyEvent("paper", "RESUMED", Mode.PAPER, SafetyState.RUNNING, None, "OK", "request")
