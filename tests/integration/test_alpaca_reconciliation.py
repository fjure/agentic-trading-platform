"""PTV-25: real PostgreSQL accounting with entirely offline provider evidence."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    (
        "recovery",
        "activation",
        "partial-cancel",
        "cancel-fill-race",
        "terminal-regression",
        "owner-expiry-after-book",
        "restart-idempotent",
        "unknown-fees",
        "profile-change",
        "daily-fee",
        "delayed-fee",
        "missing-page",
        "crash-after",
        "ack-partial",
        "resume-denied-after-fill",
        "limit-violation",
        "missing-fill",
        "foreign-order",
        "position-mismatch",
        "cash-mismatch",
        "account-mismatch",
        "timeout",
        "private-audit",
    ),
)
def test_alpaca_reconciliation_postgres(scenario: str) -> None:
    assert f"PASS {scenario}" in host(
        guest_path=Path(__file__).with_name("alpaca_reconciliation_support.py"),
        guest_args=(scenario,),
    )
