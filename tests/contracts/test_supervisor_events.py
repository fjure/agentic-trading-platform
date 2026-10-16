"""Public supervisor events expose only explicit, schema-compatible fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from services.control_api.cursors import CursorExpired, CursorStore
from services.control_api.events import EventFeed, EventGap

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text())
URI = "https://agentic-trading-lab.invalid/control-api"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)
VALIDATOR = Draft202012Validator(
    {"$ref": URI + "#/components/schemas/SupervisorEvent"},
    registry=REGISTRY,
    format_checker=FormatChecker(),
)
TRACE = "a" * 32


def _row(event_type: str, payload: dict) -> dict:
    return {
        "cursor": 7,
        "event_id": "evt_7",
        "event_type": event_type,
        "occurred_at": datetime(2026, 10, 16, tzinfo=UTC),
        "aggregate_id": "agg_7",
        "aggregate_version": 2,
        "trace_id": TRACE,
        "schema_version": 1,
        "payload": payload,
    }


@pytest.mark.parametrize(
    ("event_type", "payload", "owner"),
    [
        (
            "control.mode.changed.v1",
            {
                "account_id": "paper",
                "action": "MODE_CHANGED",
                "mode": "SHADOW",
                "safety_state": "PAUSE_NEW",
                "policy_version": 2,
                "reason_code": "OK",
                "broker_secret": "never-publish",
            },
            None,
        ),
        (
            "execution.reported.v1",
            {
                "id": "report_7",
                "order_id": "order_7",
                "status": "UNKNOWN",
                "filled_quantity": "0.25",
                "requested_quantity": "1.0",
                "reconciliation": "PENDING",
                "reason_code": "BROKER_STATUS_UNKNOWN",
                "broker_event_id": "private-provider-id",
            },
            "paper",
        ),
        (
            "risk.decided.v1",
            {
                "id": "risk_7",
                "proposal_id": "proposal_7",
                "approved": False,
                "reason_code": "RISK_STALE_MARKET",
                "policy_version": 2,
                "exposure_snapshot": {"private": "never-publish"},
            },
            "paper",
        ),
    ],
)
def test_sanitized_event_validates_oas_and_never_copies_unknown_fields(
    event_type: str, payload: dict, owner: str | None
) -> None:
    feed = EventFeed(Mock(), "paper")
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = owner
    row = _row(event_type, payload)
    if event_type.startswith("control"):
        row["aggregate_id"] = "paper"
    event = feed._project(connection, row, "c_token")
    VALIDATOR.validate(event)
    assert "never-publish" not in json.dumps(event)
    assert "private-provider-id" not in json.dumps(event)
    assert event["cursor"] == "c_token"
    if event_type.startswith("control"):
        assert event["eventType"] == "mode.updated.v1"


def test_unknown_and_cross_account_events_require_reset() -> None:
    feed = EventFeed(Mock(), "paper")
    connection = Mock()
    with pytest.raises(EventGap):
        feed._project(connection, _row("vendor.raw.v1", {"anything": "unsafe"}), "c_token")
    connection.execute.return_value.scalar_one_or_none.return_value = "other"
    with pytest.raises(EventGap):
        feed._project(
            connection,
            _row("risk.decided.v1", {"id": "risk_7", "approved": True}),
            "c_token",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "raw vendor status"),
        ("filled_quantity", "1e500"),
        ("reason_code", "PRIVATE BROKER MESSAGE"),
    ],
)
def test_malformed_public_fields_force_reset(field: str, value: str) -> None:
    payload = {
        "id": "report_7",
        "order_id": "order_7",
        "status": "UNKNOWN",
        "filled_quantity": "0.25",
        "requested_quantity": "1.0",
        "reconciliation": "PENDING",
        "reason_code": "BROKER_STATUS_UNKNOWN",
    }
    payload[field] = value
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = "paper"
    with pytest.raises(EventGap):
        EventFeed(Mock(), "paper")._project(
            connection, _row("execution.reported.v1", payload), "c_token"
        )


def test_cursor_receipt_is_opaque_and_resolves_only_for_account_and_kind() -> None:
    connection = Mock()
    token = CursorStore("paper").mint(connection, 7, context={"resource": "orders"})
    assert token.startswith("c_") and len(token) == 45
    inserted = connection.execute.call_args.args[1]
    assert inserted["account"] == "paper"
    assert inserted["kind"] == "events"
    assert inserted["position"] == 7
    assert inserted["context"] == '{"resource":"orders"}'
    row = {
        "position": 7,
        "context": {"resource": "orders"},
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "is_current": True,
    }
    connection.execute.return_value.mappings.return_value.first.return_value = row
    # A fresh store instance uses the same persisted receipt after a process restart.
    assert CursorStore("paper").resolve(connection, token).position == 7
    params = connection.execute.call_args.args[1]
    assert params == {"token": token, "account": "paper", "kind": "events"}
    connection.execute.return_value.mappings.return_value.first.return_value = None
    with pytest.raises(CursorExpired):
        CursorStore("other").resolve(connection, token)
    with pytest.raises(CursorExpired):
        CursorStore("paper").resolve(connection, "c_7")
