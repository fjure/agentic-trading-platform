"""Private PG guest. No Jev/UI dependency or real broker/credentials."""

from __future__ import annotations

import socket
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


def checks(admin, execution, engine, now):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.brokers.fake import FakeBrokerAdapter
    from packages.domain import models as m
    from packages.observability.outbox import _envelope
    from packages.persistence import PostgresLedger
    from services.control_api.control import (
        ControlDenied,
        kill,
        pause,
        request_paper_activation,
        request_resume,
    )
    from services.execution.control_socket import LocalControlServer, dispatcher, send_control
    from services.execution.recovery_health import (
        check_runtime,
        claim_execution_owner,
        execution_refresh_and_resume,
    )
    from services.watchdog.main import poll_once

    engines = []

    def role(name):
        item = create_engine(engine.url, hide_parameters=True)
        engines.append(item)

        @event.listens_for(item, "connect")
        def assign(connection, _record):
            connection.execute(f"SET ROLE {name}")
            connection.commit()

        return PostgresLedger(engine=item)

    control = role("atl_control_api")
    watchdog = role("atl_watchdog")
    worker = role("atl_worker")
    common = dict(account_id="paper", actor_id="local-test", trace_id="a" * 32)
    scenario = sys.argv[2]
    caps = {}
    for instrument in ("spy", "qqq"):
        caps[instrument] = m.BrokerCapabilities(
            "fake",
            instrument,
            1,
            paper=m.Capability.CONFIRMED,
            regular_session=m.Capability.CONFIRMED,
            market_order=m.Capability.CONFIRMED,
            limit_order=m.Capability.CONFIRMED,
            fractional=m.Capability.UNKNOWN,
            quantity_step=Decimal("1"),
            price_tick=Decimal("0.01"),
        )

    class Clock:
        def now(self):
            from datetime import UTC, datetime

            return datetime.now(UTC)

    class Ids:
        def new_id(self, namespace, seed=None):
            return f"{namespace}:{seed}"

    broker = FakeBrokerAdapter(
        clock=Clock(), ids=Ids(), capabilities=caps, session_open=lambda _i, _n: True
    )
    token = claim_execution_owner(
        ledger=execution, account_id="paper", owner_id="test-owner", trace_id="a" * 32
    )

    def state():
        with engine.connect() as c:
            return (
                c.execute(
                    text("SELECT * FROM public.risk_account_context WHERE account_id='paper'")
                )
                .mappings()
                .one()
            )

    def request():
        return request_resume(ledger=control, expected_policy_version=1, **common)

    def recover(request_id):
        return execution_refresh_and_resume(
            ledger=execution,
            account_id="paper",
            request_id=request_id,
            broker=broker,
            owner_id="test-owner",
            fencing_token=token,
        )

    def denied(call):
        try:
            call()
        except (ControlDenied, DBAPIError, PermissionError):
            return
        raise AssertionError("unsafe operation succeeded")

    def current_intent():
        from packages.risk.engine import RiskLimits
        from packages.risk.reservations import reserve

        with admin.transaction() as tx:
            current = state()
            snapshot = tx.get(m.PortfolioSnapshot, current["portfolio_snapshot_id"])
            source = tx.get(m.MarketState, "ms")
            fresh = replace(source, id="current-market", portfolio=snapshot)
            tx.insert(fresh)
            decision = replace(
                tx.get(m.DecisionResult, "dec"), id="current-decision", market_state_id=fresh.id
            )
            tx.insert(
                m.AuditEvent(
                    "current-decision-audit",
                    now,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    decision.id,
                    1,
                    "a" * 32,
                    1,
                )
            )
            tx.insert(decision)
            created = datetime.now(UTC)
            proposal = m.TradeProposal(
                "current-proposal",
                decision.id,
                fresh.id,
                "spy",
                m.Target.LONG,
                Decimal("1"),
                created,
                created + timedelta(seconds=30),
                "a" * 32,
            )
            tx.insert(
                m.AuditEvent(
                    "current-proposal-audit",
                    created,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    proposal.id,
                    1,
                    "a" * 32,
                    1,
                )
            )
            tx.insert(proposal)
        assert reserve(
            ledger=execution, account_id="paper", proposal_id=proposal.id, limits=RiskLimits()
        ).approved
        with admin.transaction() as tx:
            return tx.connection.execute(
                text("SELECT id FROM public.order_intents WHERE proposal_id='current-proposal'")
            ).scalar_one()

    if scenario in {
        "fenced-submit",
        "stale-submit",
        "db-down",
        "kill-cancel",
        "local-reject",
        "post-fill-resume",
        "session-closed-repair",
    }:
        from sqlalchemy.exc import OperationalError

        from packages.risk.engine import RiskLimits
        from services.execution.intents import ExecutionProcess, SubmitDenied, submit_once
        from services.execution.reconciliation import reconcile_order
        from services.execution.recovery_health import cancel_known_after_halt

        recover(request())
        process = None
        if scenario == "db-down":
            process = ExecutionProcess(
                ledger=execution,
                account_id="paper",
                limits=RiskLimits(),
                broker=broker,
                paper_smoke_enabled=True,
            )
            token = process.fencing_token
            execution_refresh_and_resume(
                ledger=execution,
                account_id="paper",
                request_id=request(),
                broker=broker,
                owner_id=process.owner_id,
                fencing_token=token,
            )
        intent = current_intent()
        counts = []
        original = broker.submit

        def counted(item):
            counts.append(item.id)
            return original(item)

        broker.submit = counted

        def submit():
            return submit_once(
                ledger=execution,
                account_id="paper",
                intent_id=intent,
                limits=RiskLimits(),
                broker=broker,
                paper_smoke_enabled=True,
                owner_id="test-owner",
                fencing_token=token,
            )

        if scenario == "kill-cancel":
            submit()
            response = dispatcher(
                ledger=control,
                account_id="paper",
                on_kill=lambda: cancel_known_after_halt(
                    ledger=execution,
                    account_id="paper",
                    broker=broker,
                    owner_id="test-owner",
                    fencing_token=token,
                ),
            )({"command": "kill"})
            assert response["status"] == "HALTED"
            with engine.connect() as c:
                assert (
                    c.execute(text("SELECT status FROM public.broker_orders")).scalar_one()
                    == "CANCEL_PENDING"
                )
            assert len(counts) == 1 and state()["safety_state"] == "HALTED"
        elif scenario == "local-reject":
            broker.capabilities = lambda _i: replace(caps["spy"], paper=m.Capability.UNAVAILABLE)
            try:
                submit()
            except SubmitDenied:
                pass
            else:
                raise AssertionError("unconfirmed capability submitted")
            assert not counts
            broker.capabilities = lambda i: caps[i]
            pause(ledger=control, reason_code="PAUSED_NEW", **common)
            recover(request())
            assert state()["safety_state"] == "RUNNING"
        elif scenario == "session-closed-repair":
            with admin.transaction() as tx:
                tx.insert(
                    m.AuditEvent(
                        "session-expiring",
                        now,
                        "fixture",
                        "fixture",
                        ("TEST",),
                        "paper:spy",
                        2,
                        "a" * 32,
                    )
                )
                tx.connection.execute(
                    text(
                        "UPDATE public.risk_instrument_context SET version=2,"
                        "session_valid_until=clock_timestamp()+interval '1 second' "
                        "WHERE account_id='paper' AND instrument_id='spy'"
                    )
                )
            original_capabilities = broker.capabilities

            def slow_capabilities(instrument):
                from time import sleep

                sleep(1.2)
                return original_capabilities(instrument)

            broker.capabilities = slow_capabilities
            try:
                submit()
            except SubmitDenied as exc:
                assert exc.reason_code == "SESSION_CLOSED"
            else:
                raise AssertionError("expired session submitted")
            finally:
                broker.capabilities = original_capabilities
            assert not counts
            with engine.connect() as c:
                proof = c.execute(
                    text(
                        "SELECT a.outcome,r.reason_code FROM public.broker_orders b "
                        "JOIN public.execution_reports r ON r.order_id=b.id "
                        "JOIN public.audit_events a ON a.entity_id=b.id "
                        "AND a.action='execution.submit_result' "
                        "WHERE r.status='REJECTED' AND r.reason_code='SESSION_CLOSED'"
                    )
                ).one()
                assert proof == ("PRE_IO_REJECTED", "SESSION_CLOSED")
            with admin.transaction() as tx:
                tx.insert(
                    m.AuditEvent(
                        "session-repaired",
                        now,
                        "fixture",
                        "fixture",
                        ("TEST",),
                        "paper:spy",
                        3,
                        "a" * 32,
                    )
                )
                tx.connection.execute(
                    text(
                        "UPDATE public.risk_instrument_context SET version=3,"
                        "session_valid_until=clock_timestamp()+interval '5 minutes' "
                        "WHERE account_id='paper' AND instrument_id='spy'"
                    )
                )
            pause(ledger=control, reason_code="PAUSED_NEW", **common)
            recover(request())
            assert state()["safety_state"] == "RUNNING"
        elif scenario == "post-fill-resume":
            order = submit()
            broker.fill(
                order.client_order_id,
                cumulative_quantity=order.requested_quantity,
                price=Decimal("100"),
                event_id="filled-after-submit",
            )
            reconciled = reconcile_order(
                ledger=execution, account_id="paper", order_id=order.id, broker=broker
            )
            assert reconciled.status is m.OrderStatus.FILLED
            pause(ledger=control, reason_code="PAUSED_NEW", **common)
            pending = request()
            recover(pending)
            assert state()["safety_state"] == "RUNNING"
            with engine.connect() as c:
                assert (
                    c.execute(
                        text(
                            "SELECT broker_order_count FROM public.safety_health_evidence "
                            "WHERE request_id=:request"
                        ),
                        {"request": pending},
                    ).scalar_one()
                    == 1
                )
        elif scenario == "db-down":

            def unavailable(*_args):
                raise OperationalError("offline", {}, RuntimeError("synthetic outage"))

            event.listen(execution.engine, "before_cursor_execute", unavailable)
            try:
                try:
                    process.submit(intent)
                except OperationalError:
                    pass
                else:
                    raise AssertionError("database outage submitted")
            finally:
                event.remove(execution.engine, "before_cursor_execute", unavailable)
            try:
                process.submit(intent)
            except SubmitDenied as exc:
                assert exc.reason_code == "LOCAL_EXECUTION_BLOCKED"
            else:
                raise AssertionError("database recovery silently resumed")
            assert not counts
        else:
            if scenario == "fenced-submit":
                claim_execution_owner(
                    ledger=execution, account_id="paper", owner_id="replacement", trace_id="b" * 32
                )
            else:
                with engine.begin() as c:
                    c.execute(
                        text(
                            "UPDATE public.execution_owners SET "
                            "heartbeat_at=clock_timestamp()-interval '31 seconds'"
                        )
                    )
            try:
                submit()
            except SubmitDenied:
                pass
            else:
                raise AssertionError("lost runtime lease submitted")
            assert not counts
        print(f"PASS {scenario}: zero unsafe submits; persistent latch and process fencing")
        return

    if scenario == "slow-health":
        from time import sleep

        with admin.transaction() as tx:
            current = state()
            version = current["version"] + 1
            tx.insert(
                m.AuditEvent(
                    "short-context",
                    now,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    "paper",
                    version,
                    "a" * 32,
                    1,
                )
            )
            tx.connection.execute(
                text(
                    "UPDATE public.risk_account_context SET version=:v,"
                    "valid_until=clock_timestamp()+interval '0.5 seconds',"
                    "actor_id='fixture',trace_id=:t WHERE account_id='paper'"
                ),
                {"v": version, "t": "a" * 32},
            )
        original = broker.all_orders

        def slow():
            sleep(0.8)
            return original()

        broker.all_orders = slow
        denied(lambda: recover(request()))
        assert state()["safety_state"] == "PAUSE_NEW"
        with engine.connect() as c:
            assert (
                c.execute(
                    text(
                        "SELECT count(*) FROM public.audit_events "
                        "WHERE action='safety.resume_denied'"
                    )
                ).scalar_one()
                > 0
            )
        print("PASS late health expiry rejected with durable denial audit")
        return
    if scenario == "broker-extra":
        broker.all_holdings = lambda: {"unknown-instrument": Decimal("1")}
        denied(lambda: recover(request()))
        assert state()["safety_state"] == "PAUSE_NEW"
        print("PASS unknown broker-only position blocks resume")
        return
    if scenario == "socket-negative":
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o755)
            try:
                LocalControlServer(path / "control.sock", lambda _r: None).__enter__()
            except PermissionError:
                pass
            else:
                raise AssertionError("unsafe endpoint permissions accepted")
            path.chmod(0o700)
            foreign = path / "foreign"
            foreign.write_text("fixture")
            try:
                LocalControlServer(foreign, lambda _r: None).__enter__()
            except FileExistsError:
                pass
            else:
                raise AssertionError("existing foreign endpoint replaced")
            assert foreign.is_file()
            called = []
            with LocalControlServer(path / "control.sock", lambda r: called.append(r)) as server:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(server.serve_once)
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(2)
                        client.connect(str(path / "control.sock"))
                        client.sendall(b'{"command":"pause","actor_id":"spoof"}\n')
                        assert b"DENIED" in client.recv(4096)
                    future.result(timeout=5)
            assert not called
        print("PASS private socket: rejects unsafe paths, spoofed identity and foreign overwrite")
        return

    if scenario == "rights":
        for candidate in (control, watchdog, worker):

            def mutate(candidate=candidate):
                with candidate.transaction() as tx:
                    tx.connection.execute(
                        text("UPDATE public.risk_account_context SET safety_state='RUNNING'")
                    )

            denied(mutate)
        denied(lambda: pause(ledger=worker, reason_code="PAUSED_NEW", **common))
        denied(lambda: request_resume(ledger=watchdog, expected_policy_version=1, **common))
        print("PASS actual-role grants: only narrow pause/escalation; no unsafe table writes")
        return
    if scenario == "pause-kill":
        pause(ledger=control, reason_code="PAUSED_NEW", **common)
        kill(ledger=control, reason_code="KILL_SWITCH_ACTIVE", **common)
        pause(ledger=watchdog, reason_code="PAUSED_NEW", **common)
        denied(request)
        assert state()["safety_state"] == "HALTED"
        print("PASS independent pause/kill preserve HALTED and deny automatic resume")
        return
    if scenario == "socket":
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with LocalControlServer(
                path / "control.sock", dispatcher(ledger=control, account_id="paper")
            ) as server:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(server.serve_once)
                    response = send_control(path / "control.sock", {"command": "pause"})
                    future.result(timeout=5)
            assert response["status"] == "PAUSE_NEW" and state()["safety_state"] == "PAUSE_NEW"
            with LocalControlServer(
                path / "control.sock", dispatcher(ledger=control, account_id="paper")
            ) as server:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(server.serve_once)
                    response = send_control(path / "control.sock", {"command": "kill"})
                    future.result(timeout=5)
            assert response["status"] == "HALTED"
            assert response["cancellation"] == "NOT_CONFIGURED"
            assert state()["safety_state"] == "HALTED"
        print("PASS local private control socket without Jev or browser")
        return
    if scenario == "fencing":
        new = claim_execution_owner(
            ledger=execution, account_id="paper", owner_id="replacement", trace_id="b" * 32
        )
        assert new > token

        def old():
            with execution.transaction() as tx:
                check_runtime(tx.connection, "paper", owner_id="test-owner", fencing_token=token)

        denied(old)
        print("PASS superseded Execution process cannot pass fresh runtime gate")
        return
    if scenario == "watchdog":
        assert poll_once(ledger=watchdog, account_id="paper").healthy
        recover(request())
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE public.execution_owners SET "
                    "heartbeat_at=clock_timestamp()-interval '31 seconds'"
                )
            )
        result = poll_once(ledger=watchdog, account_id="paper")
        assert not result.healthy and state()["safety_state"] == "PAUSE_NEW"
        denied(lambda: recover(request()))
        print("PASS actual-role watchdog pauses heartbeat loss and recovery gate stays closed")
        return
    if scenario == "future-heartbeat":
        recover(request())
        with engine.begin() as c:
            c.execute(
                text(
                    "UPDATE public.execution_owners SET "
                    "heartbeat_at=clock_timestamp()+interval '30 seconds'"
                )
            )
        result = poll_once(ledger=watchdog, account_id="paper")
        assert not result.healthy and state()["safety_state"] == "PAUSE_NEW"
        pending = request()
        try:
            recover(pending)
        except ControlDenied as exc:
            assert exc.reason_code == "EXECUTION_HEARTBEAT_STALE"
        else:
            raise AssertionError("future heartbeat allowed resume")
        print("PASS future heartbeat denied by watchdog and recovery")
        return
    if scenario == "recover-db-sticky":
        from sqlalchemy.exc import OperationalError

        from packages.risk.engine import RiskLimits
        from services.execution.intents import ExecutionProcess, SubmitDenied

        process = ExecutionProcess(
            ledger=execution,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
        )
        process.recover(request())
        intent = current_intent()
        pause(ledger=control, reason_code="PAUSED_NEW", **common)
        pending = request()

        def unavailable(*_args):
            raise OperationalError("offline", {}, RuntimeError("synthetic outage"))

        event.listen(execution.engine, "before_cursor_execute", unavailable)
        try:
            try:
                process.recover(pending)
            except OperationalError:
                pass
            else:
                raise AssertionError("database outage allowed recovery")
        finally:
            event.remove(execution.engine, "before_cursor_execute", unavailable)
        try:
            process.submit(intent)
        except SubmitDenied as exc:
            assert exc.reason_code == "LOCAL_EXECUTION_BLOCKED"
        else:
            raise AssertionError("database failure did not latch process")
        process.recover(pending)
        assert state()["safety_state"] == "RUNNING" and not process._faulted
        try:
            process.submit(intent)
        except SubmitDenied as exc:
            assert exc.reason_code == "PORTFOLIO_OR_SOURCE_STALE"
        else:
            raise AssertionError("old portfolio intent remained valid after recovery")
        print("PASS recovery DB outage latches process until verified repair")
        return
    if scenario == "stale-data":
        with admin.transaction() as tx:
            tx.insert(
                replace(
                    tx.get(m.MarketBar, "bar"), id="invalid-latest-bar", quality=m.Quality.INVALID
                )
            )
        denied(lambda: recover(request()))
        print("PASS stale data even with zero holdings blocks recovery")
        return
    if scenario == "pause-wins":
        pending = request()
        pause(ledger=control, reason_code="PAUSED_NEW", **common)
        denied(lambda: recover(pending))
        assert state()["safety_state"] == "PAUSE_NEW"
        print("PASS later repeated pause invalidates previously queued resume")
        return
    if scenario == "activation":
        with admin.transaction() as tx:
            current = state()
            version = current["version"] + 1
            tx.insert(
                m.AuditEvent(
                    "activate-mode",
                    now,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    "paper",
                    version,
                    "a" * 32,
                    1,
                )
            )
            tx.connection.execute(
                text(
                    "UPDATE public.risk_account_context SET mode='SHADOW',version=:version,"
                    "actor_id='fixture',trace_id=:trace WHERE account_id='paper'"
                ),
                {"version": version, "trace": "a" * 32},
            )
        denied(request)
        pending = request_paper_activation(ledger=control, expected_policy_version=1, **common)
        recover(pending)
        assert state()["mode"] == "PAPER" and state()["safety_state"] == "RUNNING"
        print("PASS explicit paper activation distinct from generic resume")
        return
    identifier = recover(request())
    current = state()
    assert current["safety_state"] == "RUNNING" and current["reconciliation_current"]
    assert current["portfolio_snapshot_id"] == identifier
    with engine.connect() as c:
        rows = (
            c.execute(text("SELECT * FROM public.outbox_events WHERE event_type LIKE 'safety.%'"))
            .mappings()
            .all()
        )
        assert rows
        for row in rows:
            assert isinstance(_envelope(row).payload, m.SafetyEvent)
    print("PASS explicit resume: fresh full inventory/data proof, portfolio and typed outbox")


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
