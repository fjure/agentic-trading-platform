"""Commit-ordered, account-scoped supervisor replay from the durable publish log."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.domain.models import (
    Choice,
    Mode,
    OrderStatus,
    Quality,
    Reconciliation,
    ReservationState,
    SafetyState,
    Target,
    decimal_string,
)
from packages.persistence.ledger import PostgresLedger
from services.control_api.cursors import CursorExpired, CursorStore

_RETENTION = timedelta(days=7)
_TRACE = re.compile(r"[0-9a-f]{32}\Z")
_SAFETY_ACTIONS = frozenset(
    {
        "PAUSE",
        "KILL",
        "RESUME_REQUESTED",
        "RESUMED",
        "PAPER_ACTIVATION_REQUESTED",
        "PAPER_ACTIVATED",
    }
)
_CONTROL_ACTIONS = frozenset({"MODE_CHANGED", "POLICY_CHANGED"})
_CODES = frozenset(
    {
        "OK",
        "APPROVED",
        "ABSTAIN_MISSING_EVIDENCE",
        "ABSTAIN_INVALID_RESPONSE",
        "ABSTAIN_UNAVAILABLE",
        "BROKER_CAPABILITY",
        "CAPABILITY_VERSION",
        "CASH_LIMIT",
        "DAILY_LOSS_LIMIT",
        "EXIT_QUANTITY",
        "INSTRUMENT_POSITION_LIMIT",
        "MARKET_STALE",
        "MODE_OR_SAFETY",
        "PORTFOLIO_VERSION",
        "POSITION_NOTIONAL_LIMIT",
        "PRICE_TICK",
        "PROPOSAL_EXPIRED",
        "PROPOSAL_TTL",
        "QUANTITY_STEP",
        "RECONCILIATION",
        "SESSION_CLOSED",
        "SPREAD",
        "TARGET_UNSUPPORTED",
        "TOTAL_POSITION_LIMIT",
        "RISK_CONTEXT_MISSING",
        "RISK_CONTEXT_STALE",
        "RISK_CONFIG_VERSION",
        "DAY_START_STALE",
        "SOURCE_MISSING",
        "PORTFOLIO_OR_SOURCE_STALE",
        "POLICY_LIMIT_MISMATCH",
        "ORPHAN_INTENT",
        "CURRENCY_MISMATCH",
        "POSITION_MARK_STALE",
        "BROKER_STATUS_UNKNOWN",
        "BROKER_UNAVAILABLE_OR_MISMATCH",
        "BROKER_ORDER_MISSING_OR_AMBIGUOUS",
        "BROKER_ID_MISMATCH",
        "BROKER_ORDER_MISMATCH",
        "STATUS_REGRESSION_OR_MISMATCH",
        "BROKER_REPORT_MISSING",
        "BROKER_EVENT_CONFLICT",
        "CUMULATIVE_COST_CONFLICT",
        "FILL_PROOF_MISSING",
        "TERMINAL_PROOF_MISSING",
        "FILLED_QUANTITY_MISMATCH",
        "STATUS_FILL_MISMATCH",
        "POSITION_AMBIGUOUS_OR_MISSING",
        "POSITION_CURRENCY_MISMATCH",
        "BOOKED_COST_BASIS_MISSING",
        "FILL_COST_INVALID",
        "POSITION_COST_BASIS_MISSING",
        "SELL_BASIS_OR_QUANTITY_INVALID",
        "RESERVATION_INVALID",
        "PORTFOLIO_BASIS_MISMATCH",
        "RECONCILIATION_MISMATCH",
        "BROKER_EVIDENCE",
        "FILL_BOOKED",
        "OWNERSHIP_OR_IDENTITY_MISMATCH",
        "PAUSED_NEW",
        "KILL_SWITCH_ACTIVE",
        "HEALTH_CHECK_FAILED",
        "TEMPORARILY_UNAVAILABLE",
        "VERSION_CONFLICT",
        "INVALID_REQUEST",
        "SUBMIT_UNCERTAIN",
        "PRE_IO_REJECTED",
        "RECONCILE_REQUIRED",
        "GATE_APPROVED",
        "ACCOUNT_CONTEXT_MISSING",
        "EXECUTION_HEARTBEAT_STALE",
        "EXECUTION_OWNER_FENCED",
        "INVALID_PRE_IO_PROOF",
        "LOCAL_EXECUTION_BLOCKED",
        "UNSAFE_RESTART",
        "RISK_STALE_MARKET",
        "RISK_SESSION_CLOSED",
        "RISK_POLICY_INCOMPLETE",
        "RISK_LIMIT_EXCEEDED",
        "RISK_CAPABILITY_UNCONFIRMED",
        "RISK_PROPOSAL_EXPIRED",
        "RISK_RECONCILIATION_REQUIRED",
        "MODE_NOT_PAPER",
        "PRECONDITION_REQUIRED",
        "IDEMPOTENCY_CONFLICT",
        "CURSOR_EXPIRED",
        "ABSTAIN_LOW_CONFIDENCE",
        "ABSTAIN_LOW_EVIDENCE_QUALITY",
        "ABSTAIN_SIGNAL_CONFLICT",
        "ABSTAIN_UNCERTAIN_REGIME",
        "BASELINE_SIGNAL",
        "JEV_SHADOW",
        "FEE_UNVERIFIED",
    }
)
_PUBLIC_TYPES = {
    "market.state.v1": "market.state.v1",
    "decision.completed.v1": "decision.completed.v1",
    "trade.proposed.v1": "trade.proposed.v1",
    "risk.decided.v1": "risk.decided.v1",
    "risk.reserved.v1": "risk.reserved.v1",
    "execution.reported.v1": "execution.reported.v1",
    "safety.changed.v1": "safety.changed.v1",
    "safety.resume_requested.v1": "safety.requested.v1",
    "control.mode.changed.v1": "mode.updated.v1",
    "control.policy.changed.v1": "policy.updated.v1",
}
_OWNERSHIP = {
    "market.state.v1": """
      SELECT account_id FROM public.supervisor_entity_owners
      WHERE entity_type='market_states' AND entity_id=:id""",
    "decision.completed.v1": """
      SELECT account_id FROM public.supervisor_entity_owners
      WHERE entity_type='decisions' AND entity_id=:id""",
    "trade.proposed.v1": """
      SELECT account_id FROM public.supervisor_entity_owners
      WHERE entity_type='trade_proposals' AND entity_id=:id""",
    "risk.decided.v1": """
      SELECT account_id FROM public.supervisor_entity_owners
      WHERE entity_type='risk_decisions' AND entity_id=:id""",
    "risk.reserved.v1": """
      SELECT account_id FROM public.risk_reservations WHERE id=:id""",
    "execution.reported.v1": """
      SELECT i.account_id FROM public.execution_reports r
      JOIN public.broker_orders o ON o.id=r.order_id
      JOIN public.order_intents i ON i.id=o.intent_id WHERE r.id=:id""",
}


class EventGap(CursorExpired):
    """Published replay is not continuous or cannot be safely projected."""


def _string(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise EventGap("unpublishable event")
    return value


def _number(value: object) -> str:
    if not isinstance(value, str):
        raise EventGap("unpublishable decimal")
    try:
        return decimal_string(value)
    except ValueError as exc:
        raise EventGap("unpublishable decimal") from exc


def _optional_number(value: object) -> str | None:
    return None if value is None else _number(value)


def _choice(value: object, choices) -> str:
    allowed = {member.value if hasattr(member, "value") else member for member in choices}
    if not isinstance(value, str) or value not in allowed:
        raise EventGap("unpublishable event state")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or value not in _CODES:
        raise EventGap("unpublishable reason code")
    return value


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EventGap("unpublishable event timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _payload(event_type: str, body: dict) -> dict:
    """Allowlist public fields; never copy a complete internal or vendor object."""
    if event_type == "market.state.v1":
        return {
            "marketStateId": _string(body["id"]),
            "instrumentId": _string(body["instrument_id"]),
            "quality": _choice(body["quality"], Quality),
        }
    if event_type == "decision.completed.v1":
        return {
            "decisionId": _string(body["id"]),
            "choice": _choice(body["choice"], Choice),
            "reasonCode": _reason(body["reason_code"]),
            "confidence": _optional_number(body.get("confidence")),
        }
    if event_type == "trade.proposed.v1":
        return {
            "proposalId": _string(body["id"]),
            "instrumentId": _string(body["instrument_id"]),
            "target": _choice(body["target"], Target),
            "quantity": _number(body["quantity"]),
        }
    if event_type == "risk.decided.v1":
        approved = body["approved"]
        version = body["policy_version"]
        if type(approved) is not bool or type(version) is not int or version < 1:
            raise EventGap("unpublishable risk decision")
        return {
            "riskDecisionId": _string(body["id"]),
            "proposalId": _string(body["proposal_id"]),
            "approved": approved,
            "reasonCode": _reason(body["reason_code"]),
            "policyVersion": version,
        }
    if event_type == "risk.reserved.v1":
        return {
            "reservationId": _string(body["id"]),
            "proposalId": _string(body["proposal_id"]),
            "state": _choice(body["state"], ReservationState),
            "reservedNotional": _number(body["reserved_notional"]),
        }
    if event_type == "execution.reported.v1":
        return {
            "reportId": _string(body["id"]),
            "orderId": _string(body["order_id"]),
            "status": _choice(body["status"], OrderStatus),
            "filledQuantity": _number(body["filled_quantity"]),
            "requestedQuantity": _number(body["requested_quantity"]),
            "reconciliation": _choice(body["reconciliation"], Reconciliation),
            "reasonCode": None if body.get("reason_code") is None else _reason(body["reason_code"]),
        }
    if event_type in {"safety.changed.v1", "safety.resume_requested.v1"}:
        action = _choice(body["action"], _SAFETY_ACTIONS)
        if (event_type == "safety.resume_requested.v1") != (
            action in {"RESUME_REQUESTED", "PAPER_ACTIVATION_REQUESTED"}
        ):
            raise EventGap("safety event action mismatch")
        version = body.get("policy_version")
        if version is not None and (type(version) is not int or version < 1):
            raise EventGap("unpublishable safety event")
        return {
            "action": action,
            "mode": _choice(body["mode"], Mode),
            "safetyState": _choice(body["safety_state"], SafetyState),
            "policyVersion": version,
            "reasonCode": _reason(body["reason_code"]),
            "requestId": None if body.get("request_id") is None else _string(body["request_id"]),
        }
    if event_type in {"control.mode.changed.v1", "control.policy.changed.v1"}:
        action = _choice(body["action"], _CONTROL_ACTIONS)
        if (event_type == "control.mode.changed.v1") != (action == "MODE_CHANGED"):
            raise EventGap("control event action mismatch")
        version = body["policy_version"]
        if type(version) is not int or version < 1:
            raise EventGap("unpublishable control event")
        return {
            "action": action,
            "mode": _choice(body["mode"], Mode),
            "safetyState": _choice(body["safety_state"], SafetyState),
            "policyVersion": version,
            "reasonCode": _reason(body["reason_code"]),
        }
    raise EventGap("unknown event type")


class EventFeed:
    def __init__(self, ledger: PostgresLedger, account_id: str):
        if not account_id:
            raise ValueError("account_id required")
        self.ledger = ledger
        self.account_id = account_id
        self.cursors = CursorStore(account_id)

    @staticmethod
    def _bounds(connection: Connection) -> None:
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")

    def _scope(self, connection: Connection) -> None:
        count = connection.execute(
            text("SELECT count(*) FROM public.accounts WHERE id=:account"),
            {"account": self.account_id},
        ).scalar_one()
        if count != 1:
            raise EventGap("account unavailable")

    def _rows(self, connection: Connection, position: int) -> list[dict]:
        state = (
            connection.execute(
                text(
                    "SELECT COALESCE(max(cursor),0) AS high_water, clock_timestamp() AS now_at "
                    "FROM public.publish_log"
                )
            )
            .mappings()
            .one()
        )
        high_water = state["high_water"]
        if position > high_water:
            raise EventGap("cursor ahead of publish log")
        rows = (
            connection.execute(
                text("""
                SELECT l.cursor,l.committed_at,e.event_id,e.event_type,e.occurred_at,
                       e.aggregate_id,e.aggregate_version,e.trace_id,e.schema_version,e.payload
                FROM public.publish_log l
                JOIN public.outbox_events e USING (event_id)
                WHERE l.cursor > :position ORDER BY l.cursor LIMIT 200
                """),
                {"position": position},
            )
            .mappings()
            .all()
        )
        if high_water > position and not rows:
            raise EventGap("missing published events")
        expected = position + 1
        cutoff = state["now_at"] - _RETENTION
        for row in rows:
            if row["cursor"] != expected:
                raise EventGap("publish log gap")
            if row["committed_at"] < cutoff:
                raise EventGap("event outside retention")
            expected += 1
        return [dict(row) for row in rows]

    def _project(self, connection: Connection, row: dict, cursor: str) -> dict:
        event_type = row["event_type"]
        public_type = _PUBLIC_TYPES.get(event_type)
        if public_type is None or row["schema_version"] != 1:
            raise EventGap("unknown event schema")
        body = row["payload"]
        if not isinstance(body, dict):
            raise EventGap("unpublishable event payload")
        if event_type in _OWNERSHIP:
            owner = connection.execute(
                text(_OWNERSHIP[event_type]), {"id": row["aggregate_id"]}
            ).scalar_one_or_none()
        else:
            owner = body.get("account_id")
        if owner != self.account_id:
            raise EventGap("event outside configured account")
        if event_type not in _OWNERSHIP:
            expected = self.account_id
            if event_type == "control.policy.changed.v1":
                expected = "risk-policy:" + self.account_id
            elif event_type == "safety.resume_requested.v1":
                expected = body.get("request_id")
                owner = connection.execute(
                    text("SELECT account_id FROM public.safety_resume_requests WHERE id=:id"),
                    {"id": expected},
                ).scalar_one_or_none()
                if owner != self.account_id:
                    raise EventGap("request outside configured account")
            if row["aggregate_id"] != expected:
                raise EventGap("event account identity mismatch")
        try:
            payload = _payload(event_type, body)
        except (KeyError, TypeError, ValueError) as exc:
            raise EventGap("unpublishable event payload") from exc
        version = row["aggregate_version"]
        trace = row["trace_id"]
        if (
            type(version) is not int
            or version < 1
            or not isinstance(trace, str)
            or not _TRACE.fullmatch(trace)
        ):
            raise EventGap("unpublishable event metadata")
        return {
            "eventId": _string(row["event_id"]),
            "eventType": public_type,
            "occurredAt": _utc(row["occurred_at"]),
            "aggregateId": _string(row["aggregate_id"]),
            "aggregateVersion": version,
            "traceId": trace,
            "cursor": cursor,
            "payload": payload,
        }

    def prepare(self, cursor: str | None) -> str:
        """Validate the first replay batch before opening an HTTP event stream."""
        with self.ledger.engine.begin() as connection:
            self._bounds(connection)
            self._scope(connection)
            position = self.cursors.resolve(connection, cursor).position
            for row in self._rows(connection, position):
                self._project(connection, row, cursor or "")
        assert cursor is not None
        return cursor

    def poll(self, cursor: str | None) -> tuple[list[dict], str]:
        """Replay at most 200 events, minting a durable token for each delivered id."""
        with self.ledger.engine.begin() as connection:
            self._bounds(connection)
            self._scope(connection)
            position = self.cursors.resolve(connection, cursor).position
            rows = self._rows(connection, position)
            result = []
            last = cursor
            for row in rows:
                last = self.cursors.mint(connection, row["cursor"])
                result.append(self._project(connection, row, last))
        assert last is not None
        return result, last
