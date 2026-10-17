"""Best-effort numeric observations from a bounded, read-only ledger snapshot.

This observer cannot grant admission or alter ledger records. Missing evidence is
represented by an UNKNOWN label with a numeric placeholder, never healthy zero.
Outbox age is publication backlog; SSE age is measured separately at server yield.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from packages.observability.telemetry import Component, Metric, Status

# One row, no entity identifiers, JSON/provider payloads, URLs or reason strings.
# The lab schema enforces one account; the explicit scope guard prevents a global
# outbox/job count from being attributed to an absent or ambiguous account.
_SAMPLE = """
SELECT clock_timestamp() AS now_at,
  (SELECT count(*) FROM public.accounts) AS account_count,
  EXISTS(SELECT 1 FROM public.accounts WHERE id=:account) AS account_exists,
  (SELECT CASE WHEN count(*) FILTER (WHERE latest_at IS NULL)>0 THEN NULL
     ELSE min(latest_at) END FROM (
       SELECT max(s.as_of) AS latest_at FROM public.positions p
       LEFT JOIN public.market_states s ON s.position_id=p.id
       WHERE p.account_id=:account GROUP BY p.instrument_id
     ) AS latest_market) AS market_at,
  (SELECT heartbeat_at FROM public.execution_owners WHERE account_id=:account) AS owner_at,
  (SELECT min(heartbeat) FROM public.jobs WHERE status='LEASED') AS job_at,
  (SELECT count(*) FROM public.jobs WHERE status='LEASED') AS leased_count,
  (SELECT count(*) FROM public.jobs WHERE status='LEASED' AND heartbeat IS NULL) AS missing_beat,
  (SELECT min(created_at) FROM public.outbox_events WHERE published_at IS NULL) AS pending_at,
  (SELECT count(*) FROM public.outbox_events WHERE published_at IS NULL) AS pending_count,
  (SELECT count(*) FROM public.broker_orders o JOIN public.order_intents i ON i.id=o.intent_id
    WHERE i.account_id=:account AND o.status='UNKNOWN') AS unknown_count,
  (SELECT COALESCE(sum(reserved_notional),0) FROM public.risk_reservations
    WHERE account_id=:account AND state IN ('ACTIVE','HELD_UNKNOWN')) AS reserved_risk,
  (SELECT p.reconciliation FROM public.risk_account_context c
    JOIN public.portfolio_snapshots p ON p.id=c.portfolio_snapshot_id
    WHERE c.account_id=:account) AS reconciliation,
  (SELECT reconciliation_current FROM public.risk_account_context
    WHERE account_id=:account) AS reconciliation_current,
  (SELECT valid_until FROM public.risk_account_context
    WHERE account_id=:account) AS context_until
"""


def _age(now: datetime, stamp: datetime | None) -> float | None:
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        return None
    value = (now - stamp).total_seconds()
    return value if value >= 0 else None


class RuntimeMetrics:
    def __init__(self, ledger, telemetry, account_id: str):
        self.ledger, self.telemetry, self.account_id = ledger, telemetry, account_id

    def _observe(self, metric, value, component, status=Status.OK):
        try:
            self.telemetry.observe(metric, value, component, status)
        except Exception:
            # No logging of telemetry/export/transport exceptions, and no effect
            # on independent reconciliation, pause or execution scheduling.
            pass

    def _unknown(self):
        for metric, component in (
            (Metric.DATA_AGE_SECONDS, Component.MARKET),
            (Metric.LEASE_AGE_SECONDS, Component.EXECUTION),
            (Metric.LEASE_AGE_SECONDS, Component.DECISION),
            (Metric.OUTBOX_LAG_SECONDS, Component.CONTROL_API),
            (Metric.RECONCILIATION_MISMATCH_COUNT, Component.BROKER),
            (Metric.UNKNOWN_COUNT, Component.EXECUTION),
            (Metric.RESERVED_RISK, Component.EXECUTION),
        ):
            self._observe(metric, 0, component, Status.UNKNOWN)

    def sample(self) -> bool:
        """Observe current DB facts; False means evidence could not be sampled."""
        try:
            with self.ledger.engine.connect() as connection:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '100ms'")
                row = (
                    connection.execute(text(_SAMPLE), {"account": self.account_id})
                    .mappings().one()
                )
                connection.rollback()
            if row["account_count"] != 1 or row["account_exists"] is not True:
                self._unknown()
                return False
            now = row["now_at"]
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise ValueError("INVALID_OBSERVATION_TIME")
            for metric, stamp, component, maximum in (
                (Metric.DATA_AGE_SECONDS, row["market_at"], Component.MARKET, 120),
                (Metric.LEASE_AGE_SECONDS, row["owner_at"], Component.EXECUTION, 30),
            ):
                age = _age(now, stamp)
                status = Status.UNKNOWN if age is None else (
                    Status.DEGRADED if age > maximum else Status.OK
                )
                self._observe(metric, 0 if age is None else age, component, status)
            job_age = _age(now, row["job_at"])
            job_unknown = row["missing_beat"] > 0 or (row["leased_count"] > 0 and job_age is None)
            self._observe(
                Metric.LEASE_AGE_SECONDS, job_age or 0, Component.DECISION,
                Status.UNKNOWN if job_unknown else Status.OK,
            )
            pending_age = _age(now, row["pending_at"])
            self._observe(
                Metric.OUTBOX_LAG_SECONDS, pending_age or 0, Component.CONTROL_API,
                Status.UNKNOWN if row["pending_count"] > 0 and pending_age is None else Status.OK,
            )
            for metric, key in ((Metric.UNKNOWN_COUNT, "unknown_count"),
                                (Metric.RESERVED_RISK, "reserved_risk")):
                self._observe(
                    metric, row[key], Component.EXECUTION,
                    Status.DEGRADED if key == "unknown_count" and row[key] > 0 else Status.OK,
                )
            mismatch = row["reconciliation"] == "MISMATCH"
            current = (
                row["reconciliation_current"] is True
                and row["reconciliation"] == "CURRENT"
                and isinstance(row["context_until"], datetime)
                and row["context_until"].tzinfo is not None
                and now < row["context_until"]
            )
            self._observe(
                Metric.RECONCILIATION_MISMATCH_COUNT, 1 if mismatch else 0, Component.BROKER,
                Status.DEGRADED if mismatch else Status.OK if current else Status.UNKNOWN,
            )
            return True
        except Exception:
            self._unknown()
            return False

    def delivered(self, occurred_at: datetime) -> None:
        """Event time to server SSE yield; does not claim a browser acknowledgement."""
        try:
            age = _age(datetime.now(UTC), occurred_at)
            self._observe(
                Metric.SSE_LAG_SECONDS, 0 if age is None else age, Component.CONTROL_API,
                Status.UNKNOWN if age is None else Status.OK,
            )
        except Exception:
            self._observe(Metric.SSE_LAG_SECONDS, Decimal(0), Component.CONTROL_API, Status.UNKNOWN)
