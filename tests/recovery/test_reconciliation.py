"""Authoritative Fake reconciliation against real isolated PostgreSQL ledgers."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.brokers.reports import event_hash, normalized_report
from packages.domain import models as m
from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    [
        "partial",
        "cancel-late",
        "timeout",
        "unaccepted",
        "sell",
        "conflict",
        "unknown-price",
        "reordered",
        "duplicate-delivery",
        "cost-amendment",
        "terminal-regression",
        "broker-id-mismatch",
        "negative-cash",
        "unpriced-extra-fill",
        "rollback",
        "cancel-race",
    ],
)
def test_reconciliation_books_economic_effect_once(scenario):
    assert "PASS" in host(
        guest_path=Path(__file__).with_name("reconciliation_support.py"), guest_args=(scenario,)
    )


def _report(**changes):
    report = m.ExecutionReport(
        "source",
        "broker-order",
        m.OrderStatus.PARTIALLY_FILLED,
        Decimal("1"),
        Decimal("0.5"),
        m.Reconciliation.CURRENT,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        "a" * 32,
        broker_event_id="event",
        average_fill_price=Decimal("100"),
        fee=Decimal("0"),
    )
    return replace(report, **changes)


def test_event_fingerprint_ignores_delivery_identity_and_numeric_scale():
    original = _report()
    delivered = replace(
        original,
        id="new-delivery",
        received_at=original.received_at + timedelta(seconds=1),
        requested_quantity=Decimal("1.000000000000000000"),
        filled_quantity=Decimal("0.50"),
        average_fill_price=Decimal("100.000"),
        fee=Decimal("-0.00"),
    )
    assert event_hash(original) == event_hash(delivered)
    assert event_hash(original) != event_hash(replace(original, average_fill_price=Decimal("101")))


def test_untrusted_reason_text_is_not_persisted_as_a_broker_code():
    with pytest.raises(ValueError) as error:
        normalized_report(
            _report(reason_code="SENTINEL_SECRET_DO_NOT_LEAK"),
            source_order_id="broker-order",
            local_order_id="durable-order",
            requested_quantity=Decimal("1"),
            trace_id="a" * 32,
        )
    assert "SENTINEL_SECRET" not in str(error.value)
