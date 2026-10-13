"""Validate and identify immutable FakeBroker recovery evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace

from packages.domain import models as m

_NAMESPACE = uuid.UUID("15494835-9162-48ec-8bc3-52350215616c")


def _number(value) -> str:
    if value == 0:
        return "0"
    raw = m.decimal_string(value)
    return raw.rstrip("0").rstrip(".") if "." in raw else raw


def normalized_report(
    report: m.ExecutionReport,
    *,
    source_order_id: str,
    local_order_id: str,
    requested_quantity,
    trace_id: str,
) -> m.ExecutionReport:
    """Map the fixture's private order ID to the durable order without trusting it."""
    if (
        type(report) is not m.ExecutionReport
        or not report.broker_event_id
        or report.order_id != source_order_id
        or report.requested_quantity != requested_quantity
        or report.trace_id != trace_id
        or report.reconciliation is not m.Reconciliation.CURRENT
        or report.reason_code is not None
    ):
        raise ValueError("BROKER_REPORT_MISMATCH")
    return replace(
        report,
        id=str(uuid.uuid5(_NAMESPACE, f"fake:{report.broker_event_id}")),
        order_id=local_order_id,
        # The exact FakeBroker fixture has no fee subsystem. Its omission is zero.
        fee=report.fee if report.fee is not None else m.decimal_value("0"),
    )


def event_hash(report: m.ExecutionReport) -> str:
    """Compare stable broker evidence, excluding delivery and local storage IDs."""
    if not report.broker_event_id:
        raise ValueError("BROKER_EVENT_ID_MISSING")
    payload = {
        "adapter": "fake",
        "broker_event_id": report.broker_event_id,
        "order_id": report.order_id,
        "status": report.status.value,
        "requested_quantity": _number(report.requested_quantity),
        "filled_quantity": _number(report.filled_quantity),
        "occurred_at": m.utc_string(report.occurred_at),
        "trace_id": report.trace_id,
        "average_fill_price": (
            _number(report.average_fill_price) if report.average_fill_price is not None else None
        ),
        "fee": _number(report.fee) if report.fee is not None else None,
        "reason_code": report.reason_code,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
