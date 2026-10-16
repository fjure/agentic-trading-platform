"""Nur synthetische Daten; DB-Zugang privat über den bestehenden Testhost."""

from __future__ import annotations

import json
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError

from packages.domain import models as m
from packages.observability.outbox import Outbox
from packages.persistence import PostgresLedger
from services.control_api.backend import ControlBackend
from services.control_api.cursors import CursorExpired
from services.control_api.events import EventFeed, EventGap
from services.control_api.snapshot import SnapshotReader

TRACE = "c" * 32


def checks(admin, _execution, engine, now):
    api_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(api_engine, "connect")
    def role(connection, _record):
        connection.execute("SET ROLE atl_control_api")
        connection.commit()

    ledger = PostgresLedger(engine=api_engine)
    reader = SnapshotReader(ledger, "paper")
    feed = EventFeed(ledger, "paper")
    control = ControlBackend(ledger, "paper")
    publisher = Outbox(engine)

    def publish_all():
        while publisher.publish_one(lambda _item: None) is not None:
            pass

    def pause(key):
        assert (
            control.mutate("pauseNewEntries", {"reason": "fixture"}, "local-operator", key, TRACE)[
                0
            ]
            == 200
        )

    scenario = sys.argv[2]
    result = {"passed": scenario}
    if scenario == "snapshot-race":
        called = False

        def concurrent_commit(_connection, _cursor, statement, _params, _context, _many):
            nonlocal called
            if not called and "SELECT p.equity" in statement:
                called = True
                pause("parallel-pause")
                publish_all()

        event.listen(api_engine, "before_cursor_execute", concurrent_commit)
        snapshot = reader.snapshot(TRACE)
        event.remove(api_engine, "before_cursor_execute", concurrent_commit)
        assert called and not snapshot["mode"]["pauseNew"]
        rows, last = feed.poll(snapshot["cursor"])
        assert len(rows) == 1 and rows[0]["payload"]["action"] == "PAUSE"
        assert (
            rows[0]["eventId"]
            == EventFeed(ledger, "paper").poll(snapshot["cursor"])[0][0]["eventId"]
        )
        assert feed.poll(last)[0] == []
        assert reader.snapshot(TRACE)["mode"]["pauseNew"]
        # New mode/policy/request aggregates must all be renderable.
        control.mutate(
            "updateRiskPolicy",
            {
                "maxPositionsPerInstrument": 1,
                "maxOpenPositions": 2,
                "maxPositionEquityFraction": "0.10",
                "maxDailyLossFraction": "0.01",
            },
            "local-operator",
            "policy-after-snapshot",
            TRACE,
            '"risk-policy-v1"',
        )
        control.mutate(
            "resumeNewEntries",
            {"reason": "fixture"},
            "local-operator",
            "resume-after-snapshot",
            TRACE,
        )
        publish_all()
        extra, _ = feed.poll(last)
        assert {item["eventType"] for item in extra} == {"policy.updated.v1", "safety.requested.v1"}
        result.update(snapshot=snapshot, events=rows + extra)
    elif scenario == "pagination":
        first = reader.page("proposals", limit=7)
        collected = [item["id"] for item in first["items"]]
        with admin.transaction() as tx:
            original = tx.get(m.TradeProposal, "p-0")
            fresh = replace(original, id="concurrently-inserted-proposal")
            tx.insert(
                m.AuditEvent(
                    "audit-later", now, "fixture", "proposal.persist", ("OK",), fresh.id, 1, TRACE
                )
            )
            tx.insert(fresh)
            for index in range(3):
                tx.insert(
                    m.AgentRun(
                        f"run-{index}",
                        "a" * 64,
                        "fixture",
                        None,
                        "RUNNING",
                        datetime.now(UTC),
                        None,
                        TRACE,
                    )
                )
        cursor = first["nextCursor"]
        while cursor is not None:
            page = reader.page("proposals", cursor, 7)
            collected.extend(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
        assert len(collected) == len(set(collected)) == 100
        assert "concurrently-inserted-proposal" not in collected
        assert reader.page("proposals", limit=1)["items"][0]["id"] == fresh.id
        # Mutable heartbeat updates cannot make an original run vanish from a walk.
        runs = reader.page("runs", limit=1)
        remaining = {"run-0", "run-1", "run-2"} - {runs["items"][0]["id"]}
        with engine.begin() as c:
            c.execute(
                text("UPDATE public.agent_runs SET heartbeat_at=clock_timestamp() WHERE id=:id"),
                {"id": min(remaining)},
            )
        got = {runs["items"][0]["id"]}
        cursor = runs["nextCursor"]
        while cursor:
            page = reader.page("runs", cursor, 1)
            got.update(item["id"] for item in page["items"])
            cursor = page["nextCursor"]
        assert got == {"run-0", "run-1", "run-2"}
        for method in (
            lambda: reader.page("decisions", first["nextCursor"]),
            lambda: feed.prepare(first["nextCursor"]),
        ):
            try:
                method()
            except CursorExpired:
                pass
            else:
                raise AssertionError("cursor crossed resource scope")
        # Normal economic rows before their first execution report still render.
        from packages.risk.engine import RiskLimits
        from packages.risk.reservations import reserve

        approved = reserve(
            ledger=_execution, account_id="paper", proposal_id="p-0", limits=RiskLimits()
        )
        assert approved.approved
        with admin.transaction() as tx:
            intent = (
                tx.connection.execute(
                    text(
                        "SELECT id,client_order_id,quantity FROM public.order_intents "
                        "WHERE proposal_id='p-0'"
                    )
                )
                .mappings()
                .one()
            )
            tx.insert(
                m.AuditEvent(
                    "audit-pending-order",
                    now,
                    "fixture",
                    "execution.submitting",
                    ("GATE_APPROVED",),
                    "pending-order",
                    1,
                    TRACE,
                    outcome="SUBMITTING",
                )
            )
            tx.insert(
                m.BrokerOrder(
                    "pending-order",
                    intent["id"],
                    intent["client_order_id"],
                    None,
                    m.OrderStatus.SUBMITTING,
                    intent["quantity"],
                    Decimal("0"),
                    1,
                    datetime.now(UTC),
                    TRACE,
                ),
                adapter="fake",
            )
        assert reader.page("orders")["items"][0]["reconciliation"] == "PENDING"
        pause("audit-example")
        pages = {}
        for resource, schema in [
            ("market-states", "MarketStatePage"),
            ("decisions", "DecisionPage"),
            ("proposals", "ProposalPage"),
            ("risk-decisions", "RiskDecisionPage"),
            ("orders", "OrderPage"),
            ("audit", "AuditPage"),
            ("runs", "RunPage"),
            ("costs", "CostPage"),
        ]:
            pages[schema] = reader.page(resource, trace_id=TRACE if resource == "audit" else None)
        result.update(snapshot=reader.snapshot(TRACE), pages=pages)
        with engine.begin() as c:
            try:
                c.execute(
                    text("UPDATE public.agent_runs SET role='MARKET_INTELLIGENCE' WHERE id='run-0'")
                )
            except DBAPIError:
                pass
            else:
                raise AssertionError("durable run provenance changed")
        with admin.transaction() as tx:
            usage = replace(
                tx.get(m.DecisionResult, "dec"),
                id="usage-observed",
                input_tokens=10,
                output_tokens=20,
                estimated_cost=None,
                trace_id=TRACE,
            )
            tx.insert(
                m.AuditEvent(
                    "usage-audit",
                    datetime.now(UTC),
                    "worker-planner",
                    "decision.persist",
                    ("OK",),
                    usage.id,
                    1,
                    TRACE,
                )
            )
            tx.insert(usage)
        try:
            reader.page("costs")
        except LookupError:
            pass
        else:
            raise AssertionError("unknown billing currency invented")
        cost_page = SnapshotReader(ledger, "paper", cost_currency="EUR").page("costs")
        assert cost_page["items"][0]["currency"] == "EUR"
        assert cost_page["items"][0]["amount"] is None
        result["pages"]["CostPage"] = cost_page
        # API reads metadata but cannot overwrite provenance.
        with api_engine.begin() as c:
            try:
                c.execute(text("UPDATE public.supervisor_entity_owners SET account_id='other'"))
            except DBAPIError:
                pass
            else:
                raise AssertionError("API can change history ownership")
    elif scenario == "retention":
        start = reader.snapshot(TRACE)["cursor"]
        for token in (None, "unknown", "c_" + "A" * 43):
            try:
                feed.prepare(token)
            except CursorExpired:
                pass
            else:
                raise AssertionError("unknown cursor accepted")
        old = "c_" + secrets.token_urlsafe(32)
        with engine.begin() as c:
            c.execute(
                text("""
                INSERT INTO public.supervisor_cursors
                  (token,account_id,kind,position,created_at,expires_at)
                VALUES (:token,'paper','events',0,clock_timestamp()-interval '8 days',
                        clock_timestamp()-interval '1 day')
            """),
                {"token": old},
            )
        try:
            feed.prepare(old)
        except CursorExpired:
            pass
        else:
            raise AssertionError("expired cursor accepted")
        pause("event-one")
        publish_all()
        rows, last = feed.poll(start)
        assert len(rows) == 1
        pause("event-three")
        # Inspect a synthetic gap before deferred publication guards reject commit.
        # Roll back the fixture: no deletion or relaxed product trigger is needed.
        with engine.begin() as c:
            identity = c.execute(
                text("SELECT event_id FROM public.outbox_events WHERE published_at IS NULL")
            ).scalar_one()
            c.execute(
                text("INSERT INTO public.publish_log(cursor,event_id) VALUES(3,:id)"),
                {"id": identity},
            )
            c.execute(
                text(
                    "UPDATE public.outbox_events SET published_at=clock_timestamp() "
                    "WHERE event_id=:id"
                ),
                {"id": identity},
            )
            try:
                feed._rows(c, 1)
            except EventGap:
                pass
            else:
                raise AssertionError("publication gap silently skipped")
            c.rollback()
    elif scenario == "publish-race":
        start = reader.snapshot(TRACE)["cursor"]
        for index in range(3):
            pause(f"publish-{index}")
        entered, release = threading.Event(), threading.Event()

        def delayed(_event):
            entered.set()
            assert release.wait(5)

        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(publisher.publish_one, delayed)
            assert entered.wait(5)
            publish_all()
            release.set()
            assert future.result(5) == 1
        rows, last = feed.poll(start)
        assert len(rows) == len({row["eventId"] for row in rows}) == 3
        assert [row["aggregateVersion"] for row in rows] == sorted(
            row["aggregateVersion"] for row in rows
        )
        assert feed.poll(last)[0] == []
        result["events"] = rows
    else:
        raise AssertionError("unknown fixture scenario")
    api_engine.dispose()
    print(json.dumps(result))


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
