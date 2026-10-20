"""Durable transport journal around the actual FakeBrokerAdapter.

Replaying timestamped accepts/fills reconstructs exactly the same broker evidence;
the execution ledger is changed only by the existing reconciliation code.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from packages.brokers.fake import FakeBrokerAdapter
from packages.domain import models as m
from packages.persistence.ledger import _decode, _encode


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def __init__(self, run_id):
        self.run_id = run_id

    def new_id(self, namespace, seed=None):
        return hashlib.sha256(f"demo.v1:{self.run_id}:{namespace}:{seed}".encode()).hexdigest()


def journaled_broker(engine, run_id, *, fail_after_accept=False, transport_guard=nullcontext):
    clock = Clock()
    caps = {
        symbol: m.BrokerCapabilities(
            "fake",
            symbol,
            1,
            paper=m.Capability.CONFIRMED,
            regular_session=m.Capability.CONFIRMED,
            market_order=m.Capability.CONFIRMED,
            limit_order=m.Capability.CONFIRMED,
            fractional=m.Capability.UNKNOWN,
            quantity_step=Decimal("1"),
            price_tick=Decimal("0.01"),
        )
        for symbol in ("SPY", "QQQ")
    }
    broker = FakeBrokerAdapter(
        clock=clock, ids=Ids(run_id), capabilities=caps, session_open=lambda _instrument, _now: True
    )
    submit, fill = broker.submit, broker.fill
    with engine.connect() as c:
        rows = (
            c.execute(text("SELECT * FROM public.demo_transport_journal ORDER BY sequence"))
            .mappings()
            .all()
        )
    for row in rows:
        clock.now = lambda at=row["event_at"]: at
        payload = row["payload"]
        if row["kind"] == "accept":
            submit(_decode(m.OrderIntent, payload))
        else:
            fill(
                payload["client_order_id"],
                cumulative_quantity=Decimal(payload["quantity"]),
                price=Decimal(payload["price"]),
                event_id=payload["event_id"],
            )
    clock.now = lambda: datetime.now(UTC)

    def persist(key, kind, at, payload):
        with engine.begin() as c:
            c.execute(
                text("""INSERT INTO public.demo_transport_journal
              (event_key,event_at,kind,payload) VALUES (:key,:at,:kind,CAST(:payload AS jsonb))
              ON CONFLICT(event_key) DO NOTHING"""),
                {
                    "key": key,
                    "at": at,
                    "kind": kind,
                    "payload": json.dumps(payload, sort_keys=True),
                },
            )

    def accepted(intent):
        at = datetime.now(UTC)
        clock.now = lambda: at
        with transport_guard():
            result = submit(intent)
            persist(intent.client_order_id, "accept", at, _encode(intent))
        clock.now = lambda: datetime.now(UTC)
        if fail_after_accept:
            raise SimulatedCrash()
        return result

    def filled(client_order_id, *, cumulative_quantity, price, event_id):
        at = datetime.now(UTC)
        clock.now = lambda: at
        with transport_guard():
            result = fill(
                client_order_id,
                cumulative_quantity=cumulative_quantity,
                price=price,
                event_id=event_id,
            )
            persist(
                event_id,
                "fill",
                at,
                {
                    "client_order_id": client_order_id,
                    "quantity": str(cumulative_quantity),
                    "price": str(price),
                    "event_id": event_id,
                },
            )
        clock.now = lambda: datetime.now(UTC)
        return result

    # Preserve the exact Fake type required by the production execution boundary.
    broker.submit, broker.fill = accepted, filled
    return broker


class SimulatedCrash(BaseException):
    """Test-only process-death failpoint after durable Fake acceptance."""
