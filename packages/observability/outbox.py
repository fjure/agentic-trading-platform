"""Transactional typed outbox, at-least-once publishing and consumer receipts."""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from packages.domain.models import EventEnvelope
from packages.persistence.ledger import _decode, _encode
from packages.persistence.transaction import ScopedConnection


def persist(connection: Connection, event: EventEnvelope) -> None:
    """Insert an event in the same transaction as its ledger effect.

    A repeated identity is accepted only if every immutable field matches.
    """
    payload = _encode(event.payload)
    values = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at,
        "aggregate_id": event.aggregate_id,
        "aggregate_version": event.aggregate_version,
        "trace_id": event.trace_id,
        "schema_version": event.schema_version,
        "payload": payload,
    }
    inserted = connection.execute(
        text("""
        INSERT INTO public.outbox_events
          (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
           trace_id,schema_version,payload)
        VALUES (:event_id,:event_type,:occurred_at,:aggregate_id,:aggregate_version,
                :trace_id,:schema_version,CAST(:payload AS jsonb))
        ON CONFLICT (event_id) DO NOTHING RETURNING event_id
    """),
        {**values, "payload": json.dumps(payload)},
    ).scalar_one_or_none()
    if inserted:
        return
    row = (
        connection.execute(
            text("""
        SELECT event_type,occurred_at,aggregate_id,aggregate_version,trace_id,
               schema_version,payload FROM public.outbox_events WHERE event_id=:event_id
        FOR UPDATE
    """),
            {"event_id": event.event_id},
        )
        .mappings()
        .one()
    )
    for field in (
        "event_type",
        "occurred_at",
        "aggregate_id",
        "aggregate_version",
        "trace_id",
        "schema_version",
        "payload",
    ):
        if row[field] != values[field]:
            raise ValueError("event identity reused with different payload or metadata")


def _envelope(row: dict) -> EventEnvelope:
    from packages.domain import models

    payload_types = {
        "market.state.v1": models.MarketState,
        "decision.completed.v1": models.DecisionResult,
        "trade.proposed.v1": models.TradeProposal,
        "risk.decided.v1": models.RiskDecision,
        "risk.reserved.v1": models.RiskReservation,
        "execution.reported.v1": models.ExecutionReport,
        "safety.changed.v1": models.SafetyEvent,
        "safety.resume_requested.v1": models.SafetyEvent,
    }
    return EventEnvelope(
        event_id=row["event_id"],
        event_type=row["event_type"],
        occurred_at=row["occurred_at"],
        aggregate_id=row["aggregate_id"],
        aggregate_version=row["aggregate_version"],
        trace_id=row["trace_id"],
        schema_version=row["schema_version"],
        payload=_decode(payload_types[row["event_type"]], row["payload"]),
    )


class Outbox:
    def __init__(self, engine: Engine):
        self.engine = engine

    def publish_one(self, deliver: Callable[[EventEnvelope], None]) -> int | None:
        """Deliver before acknowledging. Crashes can redeliver, never lose an event."""
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT event_id,event_type,occurred_at,aggregate_id,aggregate_version,
                       trace_id,schema_version,payload
                FROM public.outbox_events WHERE published_at IS NULL
                ORDER BY created_at,event_id LIMIT 1
            """)
                )
                .mappings()
                .first()
            )
            connection.rollback()
        if row is None:
            return None
        event = _envelope(row)
        deliver(event)
        with self.engine.begin() as connection:
            locked = (
                connection.execute(
                    text("""
                SELECT published_at FROM public.outbox_events
                WHERE event_id=:id FOR UPDATE
            """),
                    {"id": event.event_id},
                )
                .mappings()
                .one()
            )
            if locked["published_at"] is not None:
                return connection.execute(
                    text("""
                    SELECT cursor FROM public.publish_log WHERE event_id=:id
                """),
                    {"id": event.event_id},
                ).scalar_one()
            # The singleton row serializes allocation *and* commit. A rollback
            # rolls its increment back; later transactions cannot overtake it.
            cursor = connection.execute(
                text("""
                UPDATE public.publish_cursor SET next_cursor=next_cursor+1
                WHERE singleton=true RETURNING next_cursor-1
            """)
            ).scalar_one()
            connection.execute(
                text("""
                INSERT INTO public.publish_log (cursor,event_id) VALUES (:cursor,:id)
            """),
                {"cursor": cursor, "id": event.event_id},
            )
            connection.execute(
                text("""
                UPDATE public.outbox_events SET published_at=clock_timestamp()
                WHERE event_id=:id
            """),
                {"id": event.event_id},
            )
            return cursor

    def consume(
        self, consumer: str, event_id: str, effect: Callable[[ScopedConnection], None]
    ) -> bool:
        """Receipt and economic effect commit together on the same connection."""
        if not consumer:
            raise ValueError("consumer identity required")
        with self.engine.begin() as connection:
            inserted = connection.execute(
                text("""
                INSERT INTO public.consumer_receipts (consumer,event_id)
                VALUES (:consumer,:event_id)
                ON CONFLICT (consumer,event_id) DO NOTHING RETURNING event_id
            """),
                {"consumer": consumer, "event_id": event_id},
            ).scalar_one_or_none()
            if inserted is None:
                return False
            effect(ScopedConnection(connection))
            return True

    def snapshot_and_events(
        self, snapshot: Callable[[ScopedConnection], object], after_cursor: int = 0
    ) -> tuple[object, int, list[tuple[int, EventEnvelope]]]:
        """Read state, high-water cursor and events under one DB snapshot."""
        if after_cursor < 0:
            raise ValueError("cursor must be nonnegative")
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            state = snapshot(ScopedConnection(connection))
            high_water = connection.execute(
                text("SELECT COALESCE(max(cursor), 0) FROM public.publish_log")
            ).scalar_one()
            rows = (
                connection.execute(
                    text("""
                SELECT l.cursor,e.event_id,e.event_type,e.occurred_at,e.aggregate_id,
                       e.aggregate_version,e.trace_id,e.schema_version,e.payload
                FROM public.publish_log l JOIN public.outbox_events e USING (event_id)
                WHERE l.cursor > :cursor ORDER BY l.cursor
            """),
                    {"cursor": after_cursor},
                )
                .mappings()
                .all()
            )
            result = [(row["cursor"], _envelope(row)) for row in rows]
            connection.rollback()
            return state, high_water, result
