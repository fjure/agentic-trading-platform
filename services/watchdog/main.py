"""Watchdog for Execution heartbeat loss, independent of Jev and the dashboard."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from time import sleep

from sqlalchemy import text

from packages.persistence.ledger import PostgresLedger


@dataclass(frozen=True, slots=True)
class WatchdogResult:
    healthy: bool
    paused: bool
    reason_code: str


def poll_once(*, ledger: PostgresLedger, account_id: str) -> WatchdogResult:
    """Latch a RUNNING account if its Execution owner heartbeat is absent or old.

    A database failure propagates: no watchdog-side success may be inferred. The
    Execution submit gate separately requires a live DB read and current fence.
    """
    with ledger.transaction() as tx:
        c = tx.connection
        if c.execute(text("SELECT current_user")).scalar_one() != "atl_watchdog":
            raise PermissionError("WATCHDOG_ROLE_REQUIRED")
        healthy = c.execute(text("""
            SELECT public.safety_watchdog_poll(:a,:t)
        """), {"a": account_id, "t": uuid.uuid4().hex}).scalar_one()
        if healthy:
            return WatchdogResult(True, False, "OK")
        state = c.execute(text("""
            SELECT safety_state FROM public.risk_account_context WHERE account_id=:a
        """), {"a": account_id}).scalar_one()
        return WatchdogResult(False, state == "PAUSE_NEW", "HEALTH_CHECK_FAILED")


def run_forever(*, ledger: PostgresLedger, account_id: str,
                interval_seconds: float = 5.0) -> None:
    """Minimal process loop; supervisor handles process restart and logging."""
    if not 0 < interval_seconds <= 30:
        raise ValueError("watchdog interval outside safe range")
    while True:
        poll_once(ledger=ledger, account_id=account_id)
        sleep(interval_seconds)
