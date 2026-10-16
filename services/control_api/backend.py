"""PostgreSQL-backed, fail-closed supervisor control port.

The API role can invoke only narrow SECURITY DEFINER state transitions. Receipts
are immutable and intentionally never expire, so an old intent cannot be retried
as a new one after an arbitrary 24-hour retention boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from packages.persistence.ledger import PostgresLedger

_TRACE = re.compile(r"^[0-9a-f]{32}$")
_FRACTION = re.compile(r"^(?:0|1)(?:\.\d{1,18})?$")
_OPERATIONS = {
    "setMode",
    "pauseNewEntries",
    "resumeNewEntries",
    "activateKillSwitch",
    "updateRiskPolicy",
}
_POLICY_FIELDS = {
    "maxPositionsPerInstrument",
    "maxOpenPositions",
    "maxPositionEquityFraction",
    "maxDailyLossFraction",
}


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _problem(status: int, code: str, trace_id: str) -> dict:
    names = {
        "INVALID_REQUEST": ("invalid-request", "Invalid request"),
        "IDEMPOTENCY_CONFLICT": (
            "idempotency-conflict",
            "Idempotency key reused with different payload",
        ),
        "VERSION_CONFLICT": ("version-conflict", "Policy version conflict"),
        "PRECONDITION_REQUIRED": ("precondition-required", "If-Match required"),
        "TEMPORARILY_UNAVAILABLE": ("temporarily-unavailable", "Dependency unavailable"),
        "HEALTH_CHECK_FAILED": ("control-refused", "Control action refused"),
        "KILL_SWITCH_ACTIVE": ("control-refused", "Control action refused"),
    }
    slug, title = names[code]
    return {
        "type": f"https://agentic-trading-lab.invalid/problems/{slug}",
        "title": title,
        "status": status,
        "code": code,
        "traceId": trace_id,
    }


def _error(status: int, code: str, trace_id: str) -> tuple[int, dict, dict]:
    headers = {"Content-Type": "application/problem+json"}
    if status == 503:
        headers["Retry-After"] = "1"
    return status, _problem(status, code, trace_id), headers


def _policy(row) -> dict:
    return {
        "maxPositionsPerInstrument": row["max_positions_per_instrument"],
        "maxOpenPositions": row["max_positions_total"],
        "maxPositionEquityFraction": str(row["max_position_fraction"]),
        "maxDailyLossFraction": str(row["max_daily_loss_fraction"]),
        "version": row["version"],
        "simulationOnly": True,
        "effectiveAt": _utc(row["effective_at"]),
        "changedAt": _utc(row["effective_at"]),
        "changedBy": row["actor_id"],
        "auditEventId": row["audit_event_id"],
    }


def _etag(version: int) -> str:
    return f'"risk-policy-v{version}"'


class ControlBackend:
    def __init__(self, ledger: PostgresLedger, account_id: str):
        if not account_id:
            raise ValueError("account_id required")
        self.ledger = ledger
        self.account_id = account_id

    def _mode(self, connection, trace_id: str) -> dict:
        row = (
            connection.execute(
                text("""
            SELECT c.mode,c.safety_state,c.reconciliation_current,c.valid_until,
                   c.version,c.portfolio_snapshot_id,p.reconciliation AS portfolio_reconciliation,
                   p.quality AS portfolio_quality,
                   o.heartbeat_at,
                   (SELECT a.occurred_at FROM public.audit_events a
                    WHERE a.entity_id=c.account_id AND a.entity_version=c.version
                    ORDER BY a.occurred_at DESC LIMIT 1) AS changed_at,
                   clock_timestamp() AS now_at
            FROM public.risk_account_context c
            LEFT JOIN public.portfolio_snapshots p ON p.id=c.portfolio_snapshot_id
            LEFT JOIN public.execution_owners o ON o.account_id=c.account_id
            WHERE c.account_id=:account
        """),
                {"account": self.account_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            now_at = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            return {
                "mode": "DISABLED",
                "pauseNew": True,
                "killSwitch": False,
                "health": "UNAVAILABLE",
                "reconciliation": "PENDING",
                "changedAt": _utc(now_at),
                "traceId": trace_id,
            }
        heartbeat = row["heartbeat_at"]
        fresh = (
            heartbeat is not None
            and row["now_at"] - timedelta(seconds=30) <= heartbeat <= row["now_at"]
            and row["valid_until"] > row["now_at"]
        )
        if not row["reconciliation_current"]:
            reconciliation = "PENDING"
        elif row["portfolio_reconciliation"] == "MISMATCH":
            reconciliation = "MISMATCH"
        elif row["portfolio_reconciliation"] == "CURRENT" and row["portfolio_quality"] == "VALID":
            reconciliation = "CURRENT"
        else:
            reconciliation = "PENDING"
        return {
            "mode": row["mode"],
            "pauseNew": row["safety_state"] != "RUNNING",
            "killSwitch": row["safety_state"] == "HALTED",
            "health": "HEALTHY" if fresh else "UNAVAILABLE",
            "reconciliation": reconciliation,
            "changedAt": _utc(row["changed_at"] or row["now_at"]),
            "traceId": trace_id,
        }

    def read_mode(self, trace_id: str) -> dict:
        if not _TRACE.fullmatch(trace_id):
            raise ValueError("invalid trace_id")
        with self.ledger.transaction() as tx:
            return self._mode(tx.connection, trace_id)

    @staticmethod
    def _current_policy(connection):
        return (
            connection.execute(
                text("""
            SELECT version,max_positions_per_instrument,max_positions_total,
                   max_position_fraction,max_daily_loss_fraction,effective_at,
                   actor_id,audit_event_id
            FROM public.risk_policies ORDER BY version DESC LIMIT 1
        """)
            )
            .mappings()
            .one_or_none()
        )

    def read_policy(self) -> tuple[dict, str]:
        with self.ledger.transaction() as tx:
            row = self._current_policy(tx.connection)
            if row is None:
                raise LookupError("risk policy unavailable")
            return _policy(row), _etag(row["version"])

    @staticmethod
    def _valid_payload(operation: str, payload: dict) -> bool:
        if not isinstance(payload, dict):
            return False
        if operation == "updateRiskPolicy":
            if set(payload) != _POLICY_FIELDS:
                return False
            for name in ("maxPositionsPerInstrument", "maxOpenPositions"):
                if (
                    isinstance(payload[name], bool)
                    or not isinstance(payload[name], int)
                    or payload[name] < 1
                ):
                    return False
            for name in ("maxPositionEquityFraction", "maxDailyLossFraction"):
                value = payload[name]
                if not isinstance(value, str) or not _FRACTION.fullmatch(value):
                    return False
                try:
                    if not Decimal("0") <= Decimal(value) <= Decimal("1"):
                        return False
                except InvalidOperation:
                    return False
            return True
        if operation == "setMode":
            if set(payload) != {"mode", "reason"} or payload["mode"] not in {
                "DISABLED",
                "REPLAY",
                "SHADOW",
                "PAPER",
            }:
                return False
        elif set(payload) != {"reason"}:
            return False
        return isinstance(payload["reason"], str) and 1 <= len(payload["reason"]) <= 500

    def mutate(
        self,
        operation: str,
        payload: dict,
        actor_id: str,
        key: str,
        trace_id: str,
        if_match: str | None = None,
    ) -> tuple[int, dict, dict]:
        if not _TRACE.fullmatch(trace_id):
            raise ValueError("invalid trace_id")
        if (
            operation not in _OPERATIONS
            or not actor_id
            or len(actor_id) > 255
            or not key
            or len(key) > 255
            or not self._valid_payload(operation, payload)
        ):
            return _error(400, "INVALID_REQUEST", trace_id)
        payload_hash = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        with self.ledger.transaction() as tx:
            connection = tx.connection
            # The account row serializes concurrent requests and policy versions.
            connection.execute(
                text("SELECT public.control_lock_account(:account)"), {"account": self.account_id}
            )
            existing = (
                connection.execute(
                    text("""
                SELECT account_id,payload_hash,status,response_body,response_headers
                FROM public.control_http_receipts
                WHERE actor_id=:actor AND operation=:operation AND idempotency_key=:key
            """),
                    {"actor": actor_id, "operation": operation, "key": key},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["account_id"] != self.account_id
                    or existing["payload_hash"] != payload_hash
                ):
                    return _error(409, "IDEMPOTENCY_CONFLICT", trace_id)
                return existing["status"], existing["response_body"], existing["response_headers"]
            result = self._perform(connection, operation, payload, actor_id, trace_id, if_match)
            status, body, headers = result
            connection.execute(
                text("""
                INSERT INTO public.control_http_receipts
                  (actor_id,operation,idempotency_key,account_id,payload_hash,
                   status,response_body,response_headers)
                VALUES (:actor,:operation,:key,:account,:hash,:status,
                        CAST(:body AS jsonb),CAST(:headers AS jsonb))
            """),
                {
                    "actor": actor_id,
                    "operation": operation,
                    "key": key,
                    "account": self.account_id,
                    "hash": payload_hash,
                    "status": status,
                    "body": json.dumps(body, separators=(",", ":")),
                    "headers": json.dumps(headers, separators=(",", ":")),
                },
            )
            return result

    def _perform(self, connection, operation, payload, actor_id, trace_id, if_match):
        if operation == "updateRiskPolicy":
            policy = self._current_policy(connection)
            if policy is None:
                return _error(503, "TEMPORARILY_UNAVAILABLE", trace_id)
            if if_match is None:
                return _error(428, "PRECONDITION_REQUIRED", trace_id)
            if if_match != _etag(policy["version"]):
                return _error(412, "VERSION_CONFLICT", trace_id)
            connection.execute(
                text("""
                SELECT public.control_replace_risk_policy(:account,:actor,:trace,
                  :per_instrument,:total,:position_fraction,:daily_loss_fraction)
            """),
                {
                    "account": self.account_id,
                    "actor": actor_id,
                    "trace": trace_id,
                    "per_instrument": payload["maxPositionsPerInstrument"],
                    "total": payload["maxOpenPositions"],
                    "position_fraction": Decimal(payload["maxPositionEquityFraction"]),
                    "daily_loss_fraction": Decimal(payload["maxDailyLossFraction"]),
                },
            )
            updated = self._current_policy(connection)
            assert updated is not None
            return 200, _policy(updated), {"ETag": _etag(updated["version"])}
        if operation == "pauseNewEntries":
            connection.execute(
                text("SELECT public.safety_latch(:account,'PAUSE_NEW',:actor,:trace,'PAUSED_NEW')"),
                {"account": self.account_id, "actor": actor_id, "trace": trace_id},
            )
            return 200, self._mode(connection, trace_id), {}
        if operation == "activateKillSwitch":
            connection.execute(
                text(
                    "SELECT public.safety_latch(:account,'HALTED',:actor,:trace,"
                    "'KILL_SWITCH_ACTIVE')"
                ),
                {"account": self.account_id, "actor": actor_id, "trace": trace_id},
            )
            return 200, self._mode(connection, trace_id), {}
        state = (
            connection.execute(
                text("""
            SELECT mode,safety_state FROM public.risk_account_context
            WHERE account_id=:account
        """),
                {"account": self.account_id},
            )
            .mappings()
            .one_or_none()
        )
        if state is None:
            return _error(503, "TEMPORARILY_UNAVAILABLE", trace_id)
        if state["safety_state"] == "HALTED":
            return _error(409, "KILL_SWITCH_ACTIVE", trace_id)
        if operation == "setMode" and payload["mode"] != "PAPER":
            if self._current_policy(connection) is None:
                return _error(503, "TEMPORARILY_UNAVAILABLE", trace_id)
            connection.execute(
                text("SELECT public.control_set_safe_mode(:account,:mode,:actor,:trace)"),
                {
                    "account": self.account_id,
                    "mode": payload["mode"],
                    "actor": actor_id,
                    "trace": trace_id,
                },
            )
            return 200, self._mode(connection, trace_id), {}
        if (
            operation == "setMode"
            and state["mode"] == "PAPER"
            and state["safety_state"] == "RUNNING"
        ):
            return 200, self._mode(connection, trace_id), {}
        if state["safety_state"] != "PAUSE_NEW":
            return _error(409, "HEALTH_CHECK_FAILED", trace_id)
        if operation == "resumeNewEntries" and state["mode"] != "PAPER":
            return _error(409, "HEALTH_CHECK_FAILED", trace_id)
        if operation == "setMode" and state["mode"] == "PAPER":
            return _error(409, "HEALTH_CHECK_FAILED", trace_id)
        policy = self._current_policy(connection)
        if policy is None:
            return _error(503, "TEMPORARILY_UNAVAILABLE", trace_id)
        request_operation = "RESUME" if operation == "resumeNewEntries" else "ACTIVATE_PAPER"
        connection.execute(
            text("""
            SELECT public.safety_request_resume(:account,:actor,:trace,:policy,:operation)
        """),
            {
                "account": self.account_id,
                "actor": actor_id,
                "trace": trace_id,
                "policy": policy["version"],
                "operation": request_operation,
            },
        )
        # Only Execution can turn fresh recovery evidence into RUNNING/PAPER.
        return _error(503, "HEALTH_CHECK_FAILED", trace_id)
