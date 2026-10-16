"""Consistent paper supervisor reads and bounded historical pages.

The first history page records a PostgreSQL transaction snapshot in its opaque
cursor. Later pages keep the original creation-membership boundary and a descending
(capture time, id) seek. Mutable records expose their latest committed DTO,
while immutable ownership metadata keeps new commits and heartbeat updates
from changing membership or seek order. Cursors share the event retention policy.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import text

from packages.domain import models
from packages.persistence.ledger import LedgerUnit, PostgresLedger
from services.control_api.backend import ControlBackend
from services.control_api.cursors import CursorExpired, CursorStore
from services.control_api.projections import project_resource

_TRACE = re.compile(r"[0-9a-f]{32}\Z")

# Each SQL fragment is a fixed, reviewed resource mapping, never user SQL.
_SOURCES = {
    "market-states": ("public.market_states t", "t.as_of", "t.xmin", models.MarketState),
    "decisions": ("public.decisions t", "t.valid_until", "t.xmin", models.DecisionResult),
    "proposals": ("public.trade_proposals t", "t.created_at", "t.xmin", models.TradeProposal),
    "risk-decisions": ("public.risk_decisions t", "t.decided_at", "t.xmin", models.RiskDecision),
    "orders": (
        "public.broker_orders t JOIN public.order_intents i ON i.id=t.intent_id",
        "i.created_at",
        "i.xmin",
        None,
    ),
    "audit": ("public.audit_events t", "t.occurred_at", "t.xmin", models.AuditEvent),
    "runs": ("public.agent_runs t", "t.heartbeat_at", "t.xmin", models.AgentRun),
    "costs": (
        "public.decisions t LEFT JOIN LATERAL (SELECT occurred_at FROM public.audit_events a "
        "WHERE a.entity_id=t.id AND a.trace_id=t.trace_id "
        "ORDER BY occurred_at ASC,id ASC LIMIT 1) a ON true",
        "a.occurred_at",
        "t.xmin",
        None,
    ),
}
_OWNED_TABLES = {
    "market-states": "market_states",
    "decisions": "decisions",
    "proposals": "trade_proposals",
    "risk-decisions": "risk_decisions",
    "audit": "audit_events",
    "runs": "agent_runs",
    "costs": "decisions",
}


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class SnapshotReader:
    def __init__(
        self,
        ledger: PostgresLedger,
        account_id: str,
        *,
        account_alias: str = "paper",
        cost_currency: str | None = None,
    ):
        if not account_id:
            raise ValueError("account_id required")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", account_alias):
            raise ValueError("safe local account alias required")
        if cost_currency is not None and not re.fullmatch(r"[A-Z]{3}", cost_currency):
            raise ValueError("ISO 4217 cost currency required")
        self.ledger = ledger
        self.account_id = account_id
        self.account_alias = account_alias
        self.cost_currency = cost_currency
        self.cursors = CursorStore(account_id)
        self.control = ControlBackend(ledger, account_id)

    @contextmanager
    def _read(self):
        # mint() inserts an opaque cursor; all source reads stay on this one
        # REPEATABLE READ view and the cursor insert commits with the response.
        with self.ledger.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")
                accounts = (
                    connection.execute(text("SELECT id FROM public.accounts")).scalars().all()
                )
                if accounts != [self.account_id]:
                    raise LookupError("configured paper account unavailable")
                yield connection

    def _order(self, connection, order_id: str) -> dict:
        row = (
            connection.execute(
                text("""
            SELECT o.id,i.proposal_id,i.risk_decision_id,i.instrument_id,
                   o.status,o.requested_quantity,o.filled_quantity,o.updated_at,o.trace_id,
                   COALESCE(r.reconciliation,'PENDING') AS reconciliation,
                   r.average_fill_price,r.reconciliation AS report_reconciliation
            FROM public.broker_orders o
            JOIN public.order_intents i ON i.id=o.intent_id
            LEFT JOIN LATERAL (
              SELECT reconciliation,average_fill_price FROM public.execution_reports
              WHERE order_id=o.id ORDER BY occurred_at DESC,id DESC LIMIT 1
            ) r ON true
            WHERE o.id=:id AND i.account_id=:account
        """),
                {"id": order_id, "account": self.account_id},
            )
            .mappings()
            .one()
        )
        # Before the first report the order necessarily still awaits
        # reconciliation; PENDING is the explicit internal state.
        if row["report_reconciliation"] is None and row["status"] not in {
            "CREATED",
            "RISK_RESERVED",
            "SUBMITTING",
        }:
            raise LookupError("order reconciliation unavailable")
        return project_resource("orders", row)

    def _cost(self, connection, decision_id: str) -> dict:
        row = (
            connection.execute(
                text("""
            SELECT d.id,d.trace_id,d.estimated_cost AS amount,
                   (SELECT a.occurred_at FROM public.audit_events a
                    WHERE a.entity_id=d.id AND a.trace_id=d.trace_id
                      AND a.transaction_id=(SELECT own.created_txid
                        FROM public.supervisor_entity_owners own
                        WHERE own.entity_type='decisions' AND own.entity_id=d.id
                          AND own.account_id=:account)
                    ORDER BY a.occurred_at ASC,a.id ASC LIMIT 1) AS observed_at
            FROM public.decisions d WHERE d.id=:id AND d.input_tokens IS NOT NULL
        """),
                {"id": decision_id, "account": self.account_id},
            )
            .mappings()
            .one()
        )
        if row["observed_at"] is None:
            raise LookupError("cost observation unavailable")
        if self.cost_currency is None:
            raise LookupError("cost currency unavailable")
        # Token-counted decisions currently originate only in JevDecisionEngine.
        return project_resource("costs", {**row, "provider": "JEV", "currency": self.cost_currency})

    def _run(self, connection, run_id: str) -> dict:
        row = (
            connection.execute(
                text("""
            SELECT r.id,r.role,r.started_at,r.status,r.heartbeat_at,r.trace_id
            FROM public.agent_runs r
            JOIN public.supervisor_entity_owners own
              ON own.entity_type='agent_runs' AND own.entity_id=r.id
            WHERE r.id=:id AND own.account_id=:account
        """),
                {"id": run_id, "account": self.account_id},
            )
            .mappings()
            .one()
        )
        return project_resource("runs", row)

    def _project(self, connection, resource: str, entity_id: str) -> dict:
        if resource == "orders":
            return self._order(connection, entity_id)
        if resource == "costs":
            return self._cost(connection, entity_id)
        if resource == "runs":
            return self._run(connection, entity_id)
        model = _SOURCES[resource][3]
        entity = LedgerUnit(connection).get(model, entity_id)
        if entity is None:
            raise LookupError("history entity unavailable")
        return project_resource(resource, entity)

    def snapshot(self, trace_id: str) -> dict:
        if not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id):
            raise ValueError("invalid trace_id")
        with self._read() as connection:
            now = connection.execute(text("SELECT transaction_timestamp()")).scalar_one()
            mode = self.control._mode(connection, trace_id)
            equity = connection.execute(
                text("""
                SELECT p.equity FROM public.risk_account_context c
                JOIN public.portfolio_snapshots p ON p.id=c.portfolio_snapshot_id
                WHERE c.account_id=:account
            """),
                {"account": self.account_id},
            ).scalar_one_or_none()
            if equity is None:
                raise LookupError("portfolio unavailable")
            run_ids = (
                connection.execute(
                    text("""
                SELECT r.id FROM public.agent_runs r
                JOIN public.supervisor_entity_owners own
                  ON own.entity_type='agent_runs' AND own.entity_id=r.id
                WHERE own.account_id=:account
                ORDER BY r.heartbeat_at DESC,r.id DESC LIMIT 20
            """),
                    {"account": self.account_id},
                )
                .scalars()
                .all()
            )
            workers = [self._run(connection, run_id) for run_id in run_ids]
            order_ids = (
                connection.execute(
                    text("""
                SELECT o.id FROM public.broker_orders o
                JOIN public.order_intents i ON i.id=o.intent_id
                WHERE i.account_id=:account
                ORDER BY i.created_at DESC,o.id DESC LIMIT 20
            """),
                    {"account": self.account_id},
                )
                .scalars()
                .all()
            )
            orders = [self._order(connection, order_id) for order_id in order_ids]
            highwater = connection.execute(
                text("SELECT COALESCE(max(cursor),0) FROM public.publish_log")
            ).scalar_one()
            cursor = self.cursors.mint(connection, highwater, kind="events")
            return {
                "asOf": _utc(now),
                "cursor": cursor,
                "mode": mode,
                "paperAccount": self.account_alias,
                "connections": {"execution": mode["health"]},
                "portfolioEquity": models.decimal_string(equity),
                "workers": workers,
                "recentOrders": orders,
            }

    def page(
        self,
        resource: str,
        cursor: str | None = None,
        limit: int = 50,
        trace_id: str | None = None,
    ) -> dict:
        if resource not in _SOURCES:
            raise ValueError("unknown supervisor resource")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit outside 1..200")
        if trace_id is not None and (
            not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id)
        ):
            raise ValueError("invalid trace_id")
        source, _sort_at, _visibility, _model = _SOURCES[resource]
        with self._read() as connection:
            if cursor is None:
                boundary = connection.execute(
                    text("SELECT txid_current_snapshot()::text")
                ).scalar_one()
                last_time = last_id = None
            else:
                record = self.cursors.resolve(connection, cursor, kind="page")
                context = record.context
                if context.get("account") != self.account_id or context.get("resource") != resource:
                    raise CursorExpired("cursor scope differs")
                boundary = context["snapshot"]
                last_time, last_id = context["lastTime"], context["lastId"]
            extra = "AND t.input_tokens IS NOT NULL" if resource == "costs" else ""
            if resource == "orders":
                extra += " AND i.account_id=:account"
            else:
                entity_type = _OWNED_TABLES[resource]
                extra += (
                    " AND EXISTS (SELECT 1 FROM public.supervisor_entity_owners own "
                    "WHERE own.entity_type='" + entity_type + "' "
                    "AND own.entity_id=t.id AND own.account_id=:account)"
                )
            if resource == "audit" and trace_id is not None:
                extra += " AND t.trace_id=:trace"
            # The requested trace filter is part of the opaque page scope.
            if cursor is not None and context.get("trace") != (
                trace_id if resource == "audit" else None
            ):
                raise CursorExpired("cursor filter differs")
            owner_id = "i.id" if resource == "orders" else "t.id"
            statement = text(f"""
                SELECT t.id,vis.created_at AS sort_at FROM {source}
                JOIN public.supervisor_entity_owners vis
                  ON vis.entity_type=:owner_type AND vis.entity_id={owner_id}
                 AND vis.account_id=:account
                WHERE txid_visible_in_snapshot(vis.created_txid,
                                               CAST(:snapshot AS txid_snapshot))
                  AND (CAST(:last_time AS timestamptz) IS NULL OR (vis.created_at,t.id) <
                       (CAST(:last_time AS timestamptz),:last_id))
                  {extra}
                ORDER BY vis.created_at DESC,t.id DESC LIMIT :fetch
            """)
            rows = (
                connection.execute(
                    statement,
                    {
                        "snapshot": boundary,
                        "last_time": last_time,
                        "last_id": last_id,
                        "trace": trace_id,
                        "account": self.account_id,
                        "fetch": limit + 1,
                        "owner_type": "order_intents"
                        if resource == "orders"
                        else _OWNED_TABLES[resource],
                    },
                )
                .mappings()
                .all()
            )
            selected = rows[:limit]
            items = [self._project(connection, resource, row["id"]) for row in selected]
            next_cursor = None
            if len(rows) > limit:
                last = selected[-1]
                next_cursor = self.cursors.mint(
                    connection,
                    0,
                    kind="page",
                    context={
                        "account": self.account_id,
                        "resource": resource,
                        "snapshot": boundary,
                        "lastTime": _utc(last["sort_at"]),
                        "lastId": last["id"],
                        "trace": trace_id if resource == "audit" else None,
                    },
                )
            return {"items": items, "nextCursor": next_cursor}
