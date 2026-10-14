"""Independent local safety commands and Execution-owned recovery proof.

No broker credentials or broker object crosses the Control/Watchdog boundary.
"""

from __future__ import annotations

import re

from sqlalchemy import text

from packages.domain import models as m
from packages.persistence.ledger import PostgresLedger

_REASON = re.compile(
    r"^(PAUSED_NEW|KILL_SWITCH_ACTIVE|HEALTH_CHECK_FAILED|"
    r"TEMPORARILY_UNAVAILABLE|VERSION_CONFLICT|OK|INVALID_REQUEST)$"
)


class ControlDenied(RuntimeError):
    """Stable non-sensitive reason for a rejected safety action."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ControlDenied(reason)


def _role(connection, *allowed: str) -> None:
    _require(connection.execute(text("SELECT current_user")).scalar_one() in allowed, "ROLE_DENIED")


def _now(connection):
    return m.utc_time(connection.execute(text("SELECT clock_timestamp()")).scalar_one())


def _control_transition(
    *,
    ledger: PostgresLedger,
    account_id: str,
    actor_id: str,
    trace_id: str,
    reason_code: str,
    state: str,
) -> int:
    _require(bool(_REASON.fullmatch(reason_code)), "REASON_INVALID")
    _require(bool(re.fullmatch(r"[0-9a-f]{32}", trace_id)), "TRACE_INVALID")
    _require(bool(actor_id) and len(actor_id) <= 255, "ACTOR_INVALID")
    with ledger.transaction() as tx:
        _role(tx.connection, "atl_control_api", "atl_watchdog", "atl_execution")
        return tx.connection.execute(
            text("SELECT public.safety_latch(:account,:state,:actor,:trace,:reason)"),
            {
                "account": account_id,
                "state": state,
                "actor": actor_id,
                "trace": trace_id,
                "reason": reason_code,
            },
        ).scalar_one()


def pause(
    *, ledger: PostgresLedger, account_id: str, actor_id: str, trace_id: str, reason_code: str
) -> int:
    """Durably block new entries, preserving an existing HALTED state."""
    return _control_transition(
        ledger=ledger,
        account_id=account_id,
        actor_id=actor_id,
        trace_id=trace_id,
        reason_code=reason_code,
        state="PAUSE_NEW",
    )


def kill(
    *, ledger: PostgresLedger, account_id: str, actor_id: str, trace_id: str, reason_code: str
) -> int:
    """Durably halt new submits. Cancel is a separate best-effort Execution action."""
    return _control_transition(
        ledger=ledger,
        account_id=account_id,
        actor_id=actor_id,
        trace_id=trace_id,
        reason_code=reason_code,
        state="HALTED",
    )


def request_resume(
    *,
    ledger: PostgresLedger,
    account_id: str,
    actor_id: str,
    trace_id: str,
    expected_policy_version: int,
) -> str:
    """Request a fresh recovery of an already PAPER account."""
    return _request_recovery(
        ledger=ledger,
        account_id=account_id,
        actor_id=actor_id,
        trace_id=trace_id,
        expected_policy_version=expected_policy_version,
        operation="RESUME",
    )


def request_paper_activation(
    *,
    ledger: PostgresLedger,
    account_id: str,
    actor_id: str,
    trace_id: str,
    expected_policy_version: int,
) -> str:
    """Explicitly request a checked transition into PAPER from a safe mode."""
    return _request_recovery(
        ledger=ledger,
        account_id=account_id,
        actor_id=actor_id,
        trace_id=trace_id,
        expected_policy_version=expected_policy_version,
        operation="ACTIVATE_PAPER",
    )


def _request_recovery(
    *,
    ledger: PostgresLedger,
    account_id: str,
    actor_id: str,
    trace_id: str,
    expected_policy_version: int,
    operation: str,
) -> str:
    """Record an explicit operator request; this cannot set RUNNING."""
    with ledger.transaction() as tx:
        _role(tx.connection, "atl_control_api")
        return tx.connection.execute(
            text("SELECT public.safety_request_resume(:account,:actor,:trace,:policy,:operation)"),
            {
                "account": account_id,
                "actor": actor_id,
                "trace": trace_id,
                "policy": expected_policy_version,
                "operation": operation,
            },
        ).scalar_one()
