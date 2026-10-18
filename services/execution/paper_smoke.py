"""Human-only, bounded Paper operator entry point; ordinary CI never invokes it.

The Execution and Worker terminals remain separate roles. This module never
starts a worker, requests activation, drains a queue, retries or flattens.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import select
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text

from packages.domain import models as m
from packages.domain.paper_smoke import AUTH_TTL_SECONDS, SmokeRequest

_ACTOR = "paper-smoke-execution"
_NAMESPACE = uuid.UUID("a4f3578f-9580-4d85-942b-d22cfe49949f")
_TERMINAL = {m.OrderStatus.FILLED, m.OrderStatus.CANCELED, m.OrderStatus.REJECTED}


class SmokeDenied(RuntimeError):
    """Only a fixed public outcome, never a provider exception or payload."""


def _require(value):
    if not value:
        raise SmokeDenied("PAPER_SMOKE_DENIED")


@dataclass(frozen=True, slots=True)
class OperatorOptions:
    run_id: str
    account_fingerprint: str
    instrument_id: str
    limit_price: Decimal
    max_notional: Decimal

    def request(self, market_state_id):
        return SmokeRequest(
            run_id=self.run_id,
            account_id="paper",
            account_fingerprint=self.account_fingerprint,
            instrument_id=self.instrument_id,
            limit_price=self.limit_price,
            max_notional=self.max_notional,
            market_state_id=market_state_id,
        )


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SmokeDenied("PAPER_SMOKE_DENIED")


def parse_options(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--paper-only", action="store_true")
    parser.add_argument("--operator-confirmed", action="store_true")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--instrument", required=True, choices=("SPY", "QQQ"))
    parser.add_argument("--limit-price", required=True)
    parser.add_argument("--max-notional", required=True)
    args = parser.parse_args(argv)
    _require(args.paper_only and args.operator_confirmed)
    options = OperatorOptions(
        args.run_id,
        args.account_fingerprint,
        args.instrument,
        Decimal(args.limit_price),
        Decimal(args.max_notional),
    )
    # Validate public bounds before any runtime configuration, private file or
    # I/O is opened. This placeholder is never persisted or authorized.
    options.request("unbound")
    return options


def require_operator_terminal():
    _require(sys.platform == "linux" and os.name == "posix")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        _require(stream.isatty())
        fd = stream.fileno()
        info = os.fstat(fd)
        _require(stat.S_ISCHR(info.st_mode) and os.isatty(fd))
        _require(os.tcgetpgrp(fd) == os.getpgrp())


def operator_input(prompt, process=None, *, timeout=300):
    """Bound human waiting and keep the existing owner lease alive while idle."""
    print(prompt, flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None:
            process.heartbeat()
        ready, _, _ = select.select([sys.stdin], [], [], min(5, deadline - time.monotonic()))
        if ready:
            value = sys.stdin.readline(513)
            _require(value.endswith("\n") and len(value) <= 512)
            return value.strip()
    raise SmokeDenied("PAPER_SMOKE_DENIED")


def _now(tx):
    return m.utc_time(tx.connection.execute(text("SELECT clock_timestamp()")).scalar_one())


def _audit(tx, request, phase, reasons, outcome, *, entity_id=None, version=1, trace_id=None):
    event = m.AuditEvent(
        id=str(uuid.uuid5(_NAMESPACE, request.run_id + ":" + phase)),
        occurred_at=_now(tx),
        actor_id=_ACTOR,
        action="paper_smoke." + phase,
        reason_codes=tuple(reasons),
        entity_id=entity_id or request.entity_id,
        entity_version=version,
        trace_id=trace_id or request.trace_id,
        outcome=outcome,
    )
    tx.insert(event)
    return event


@contextmanager
def _run_lock(ledger):
    # Separate lock namespace from normal per-transaction account row locking.
    with ledger.engine.connect() as connection:
        locked = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext('paper-smoke:paper'))")
        ).scalar_one()
        connection.commit()
        _require(locked)
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext('paper-smoke:paper'))"))
            connection.commit()


def begin_run(ledger, options):
    request = options.request("unbound")
    with ledger.transaction() as tx:
        tx.lock_account("paper")
        c = tx.connection
        _require(c.execute(text("SELECT current_user")).scalar_one() == "atl_execution")
        _require(c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 1)
        context = (
            c.execute(
                text("""SELECT mode,safety_state FROM public.risk_account_context
            WHERE account_id='paper'""")
            )
            .mappings()
            .one()
        )
        _require(context["mode"] == "DISABLED" and context["safety_state"] == "PAUSE_NEW")
        _require(
            not c.execute(
                text("""SELECT
            EXISTS(SELECT 1 FROM public.positions WHERE quantity<>0)
            OR EXISTS(SELECT 1 FROM public.jobs WHERE status<>'DONE')
            OR EXISTS(SELECT 1 FROM public.risk_reservations
                      WHERE state IN ('ACTIVE','HELD_UNKNOWN'))
            OR EXISTS(SELECT 1 FROM public.broker_orders
                      WHERE status NOT IN ('FILLED','CANCELED','REJECTED'))
            OR EXISTS(SELECT 1 FROM public.order_intents i WHERE NOT EXISTS
                      (SELECT 1 FROM public.broker_orders b WHERE b.intent_id=i.id))
            """)
            ).scalar_one()
        )
        _require(
            not c.execute(
                text("SELECT EXISTS(SELECT 1 FROM public.audit_events WHERE entity_id=:entity)"),
                {"entity": request.entity_id},
            ).scalar_one()
        )
        return _audit(tx, request, "started", ("HUMAN_PAPER_ONLY",), "STARTED").occurred_at


def _fingerprint(runtime, options):
    # Construction and durable attempt restoration perform no provider I/O.
    # Compare the configured private account identity before the first request.
    account = runtime.process.broker._account_id
    _require(type(account) is str)
    actual = hashlib.sha256(account.encode("utf-8")).hexdigest()
    _require(hmac.compare_digest(actual, options.account_fingerprint))


def _activation(runtime, request_id, started_at):
    _require(str(uuid.UUID(request_id)) == request_id)
    with runtime.ledger.transaction() as tx:
        request = (
            tx.connection.execute(
                text("""SELECT operation,requested_at,expires_at
            FROM public.safety_resume_requests WHERE id=:id AND account_id='paper'"""),
                {"id": request_id},
            )
            .mappings()
            .one()
        )
        _require(request["operation"] == "ACTIVATE_PAPER")
        _require(started_at <= request["requested_at"] <= _now(tx) < request["expires_at"])
    runtime.process.heartbeat()
    runtime.process.recover(request_id)


def authorize(runtime, options):
    with runtime.ledger.transaction() as tx:
        row = tx.connection.execute(
            text("""SELECT s.id FROM public.market_states s
            JOIN public.risk_account_context c ON c.portfolio_snapshot_id=s.portfolio_id
            WHERE c.account_id='paper' AND c.mode='PAPER' AND c.safety_state='RUNNING'
              AND c.reconciliation_current AND s.instrument_id=:instrument
            ORDER BY s.available_at DESC,s.id DESC LIMIT 1"""),
            {"instrument": options.instrument_id},
        ).scalar_one()
        request = options.request(row)
        _audit(tx, request, "authorize", ("REQUEST_SHA256:" + request.digest(),), "AUTHORIZED")
        return request


def validated_intent(runtime, request):
    from packages.risk.reservations import reserve

    with runtime.ledger.transaction() as tx:
        tx.lock_account(request.account_id)
        auth = (
            tx.connection.execute(
                text("""SELECT occurred_at,reason_codes,trace_id
            FROM public.audit_events WHERE entity_id=:entity
              AND action='paper_smoke.authorize' AND actor_id=:actor AND outcome='AUTHORIZED'
            """),
                {"entity": request.entity_id, "actor": _ACTOR},
            )
            .mappings()
            .one()
        )
        _require(
            timedelta(0) <= _now(tx) - auth["occurred_at"] <= timedelta(seconds=AUTH_TTL_SECONDS)
        )
        _require(auth["reason_codes"] == ["REQUEST_SHA256:" + request.digest()])
        _require(auth["trace_id"] == request.trace_id)
        proposal = tx.get(m.TradeProposal, request.proposal_id)
        decision = tx.get(m.DecisionResult, request.decision_id)
        state = tx.get(m.MarketState, request.market_state_id)
        _require(proposal is not None and decision is not None and state is not None)
        _require(proposal.decision_id == decision.id and proposal.market_state_id == state.id)
        _require(decision.market_state_id == state.id)
        _require(proposal.trace_id == decision.trace_id == state.trace_id)
        _require(proposal.instrument_id == request.instrument_id == state.instrument_id)
        _require(proposal.target is m.Target.LONG and decision.choice is m.Choice.LONG)
        _require(proposal.quantity == Decimal(1) and proposal.limit_price == request.limit_price)
    risk = reserve(
        ledger=runtime.ledger,
        account_id=request.account_id,
        proposal_id=request.proposal_id,
        limits=runtime.limits,
    )
    _require(risk.approved)
    with runtime.ledger.transaction() as tx:
        intent_id = tx.connection.execute(
            text("""SELECT id FROM public.order_intents
            WHERE account_id=:account AND proposal_id=:proposal AND risk_decision_id=:risk"""),
            {"account": request.account_id, "proposal": request.proposal_id, "risk": risk.id},
        ).scalar_one()
        intent = tx.get(m.OrderIntent, intent_id)
        _require(intent.order_type == "LIMIT" and intent.side == "BUY")
        _require(intent.quantity == Decimal(1) and intent.limit_price == request.limit_price)
        _require(intent.limit_price * Decimal("1.01") <= request.max_notional)
        return intent


def _order(runtime, intent):
    with runtime.ledger.transaction() as tx:
        row = tx.connection.execute(
            text("""SELECT id FROM public.broker_orders
            WHERE intent_id=:intent AND adapter='alpaca-paper'"""),
            {"intent": intent.id},
        ).scalar_one_or_none()
        return tx.get(m.BrokerOrder, row) if row else None


def cancel_once(runtime, request, intent):
    from packages.observability.outbox import persist
    from services.execution.intents import _assert_owner

    with runtime.ledger.transaction() as tx:
        tx.lock_account(request.account_id)
        _assert_owner(
            tx, request.account_id, runtime.process.owner_id, runtime.process.fencing_token
        )
        order_id = tx.connection.execute(
            text("SELECT id FROM public.broker_orders WHERE intent_id=:i"),
            {"i": intent.id},
        ).scalar_one()
        order = tx.get(m.BrokerOrder, order_id)
        if order.status not in {m.OrderStatus.ACKNOWLEDGED, m.OrderStatus.PARTIALLY_FILLED}:
            return
        pending = replace(
            order,
            status=m.OrderStatus.CANCEL_PENDING,
            version=order.version + 1,
            updated_at=_now(tx),
        )
        _audit(
            tx,
            request,
            "cancel_requested",
            ("CANCEL_REQUESTED",),
            "REQUESTED",
            entity_id=order.id,
            version=pending.version,
            trace_id=order.trace_id,
        )
        _require(tx.replace(pending, expected_version=order.version))
        report = m.ExecutionReport(
            str(uuid.uuid5(_NAMESPACE, request.run_id + ":cancel-report")),
            order.id,
            pending.status,
            intent.quantity,
            order.filled_quantity,
            m.Reconciliation.PENDING,
            pending.updated_at,
            pending.updated_at,
            order.trace_id,
            reason_code="PAPER_SMOKE_CANCEL_REQUESTED",
        )
        tx.insert(
            m.AuditEvent(
                str(uuid.uuid5(_NAMESPACE, request.run_id + ":cancel-report-audit")),
                pending.updated_at,
                _ACTOR,
                "paper_smoke.cancel_report",
                ("CANCEL_REQUESTED",),
                report.id,
                1,
                order.trace_id,
            )
        )
        tx.insert(report, adapter="alpaca-paper")
        persist(
            tx.connection,
            m.EventEnvelope(
                str(uuid.uuid5(_NAMESPACE, request.run_id + ":cancel-event")),
                "execution.reported.v1",
                pending.updated_at,
                report.id,
                1,
                order.trace_id,
                report,
            ),
        )
    # Durable CANCEL_PENDING precedes I/O; a failed/ambiguous DELETE is never retried.
    try:
        response = runtime.process.broker.cancel(intent.client_order_id)
        status = (
            response.status.value
            if response is not None and type(response.status) is m.OrderStatus
            else "UNKNOWN"
        )
    except Exception:
        status = "UNKNOWN"
    with runtime.ledger.transaction() as tx:
        _audit(tx, request, "cancel_response", (status,), "OBSERVED")


def _unknown(runtime, request, intent):
    from services.execution.intents import _assert_owner
    from services.execution.reconciliation import _context, _fail

    with runtime.ledger.transaction() as tx:
        tx.lock_account(request.account_id)
        _assert_owner(
            tx, request.account_id, runtime.process.owner_id, runtime.process.fencing_token
        )
        order_id = tx.connection.execute(
            text("SELECT id FROM public.broker_orders WHERE intent_id=:i"),
            {"i": intent.id},
        ).scalar_one()
        order = tx.get(m.BrokerOrder, order_id)
        if order.status in _TERMINAL:
            return order
        risk = tx.get(m.RiskDecision, intent.risk_decision_id)
        reservation = tx.get(m.RiskReservation, risk.reservation_id)
        return _fail(
            tx,
            order=order,
            reservation=reservation,
            context=_context(tx, request.account_id),
            account_id=request.account_id,
            reason="PAPER_SMOKE_STATUS_UNRESOLVED",
            now=_now(tx),
            adapter="alpaca-paper",
        )


def settle(runtime, request, intent, *, wait=time.sleep, polls=6):
    """Read-only reconciliation plus at most one cancel; never a second submit."""
    order = _order(runtime, intent)
    if order is None:
        return "NO_ORDER"
    cancel_attempted = False
    ready = False
    try:
        for index in range(polls):
            runtime.process.heartbeat()
            receipt = runtime.process.reconcile()
            ready = receipt.ready
            order = _order(runtime, intent)
            if order.status in _TERMINAL:
                break
            if order.status is m.OrderStatus.UNKNOWN:
                break
            if not cancel_attempted:
                cancel_attempted = True
                cancel_once(runtime, request, intent)
            if index + 1 < polls:
                wait(1)
    except (Exception, KeyboardInterrupt):
        # A failed read never authorizes a cancel or another submit. Preserve
        # an explicit incident if SQL/owner authority is still available.
        ready = False
    if order.status not in _TERMINAL:
        order = _unknown(runtime, request, intent)
    status = order.status.value if ready and order.status in _TERMINAL else "UNKNOWN"
    with runtime.ledger.transaction() as tx:
        _audit(tx, request, "completed", (status,), status)
    return status


def publish_outbox(ledger, *, limit=1000):
    """Publish durable public events for SSE without executing other work."""
    from packages.observability.outbox import Outbox

    outbox = Outbox(ledger.engine)
    for _ in range(limit):
        if outbox.publish_one(lambda _event: None) is None:
            return
    with ledger.transaction() as tx:
        pending = tx.connection.execute(
            text("""SELECT EXISTS(
            SELECT 1 FROM public.outbox_events WHERE published_at IS NULL)""")
        ).scalar_one()
    if pending:
        raise SmokeDenied("PAPER_SMOKE_OUTBOX_UNRESOLVED")


def run(runtime, options, started_at, *, ask=operator_input, output=print):
    request = intent = None
    try:
        # Warm raw bars/bindings while paused; this cannot create a proposal.
        runtime.process.heartbeat()
        runtime.publish_market()
        _require(runtime.process.reconcile().ready)
        activation_id = ask(
            "Request fresh activate-paper in the separate Control-API terminal; "
            "enter its request_id:",
            runtime.process,
        )
        _activation(runtime, activation_id, started_at)
        _require(runtime.publish_market())
        request = authorize(runtime, options)
        output("market_state_id=" + request.market_state_id)
        output("request_sha256=" + request.digest())
        expected = ask(
            "Run the separately confirmed Worker one-shot for this state; enter its proposal_id:",
            runtime.process,
            timeout=AUTH_TTL_SECONDS,
        )
        _require(expected == request.proposal_id)
        runtime.process.heartbeat()
        intent = validated_intent(runtime, request)
        # This is the only submit invocation in the operator entry point.
        runtime.process.submit(intent.id)
    finally:
        runtime.pause_if_running(_ACTOR)
        if intent is not None:
            result = settle(runtime, request, intent)
            output("paper_smoke_status=" + result)
            _require(result in {state.value for state in _TERMINAL})


def execute(options):
    from packages.persistence.ledger import PostgresLedger
    from services.control_api.control import pause
    from services.runtime.config import RuntimeConfig, database_engine
    from services.runtime.execution import ExecutionRuntime

    config = RuntimeConfig.load()
    _require(config.mode == "DISABLED" and config.broker_provider == "alpaca-paper")
    _require(config.paper_enabled is True and config.decision_provider == "mock")
    _require(config.reconciliation_fee_profile == "alpaca-paper-us-retail-equity-zero-v1")
    _require(options.instrument_id in config.instruments)
    engine = database_engine("execution")
    ledger = PostgresLedger(engine=engine)
    runtime = None
    try:
        with _run_lock(ledger):
            started_at = begin_run(ledger, options)
            try:
                runtime = ExecutionRuntime(config, ledger)
                _fingerprint(runtime, options)
                run(runtime, options, started_at)
            finally:
                # Also covers construction failure and operator Ctrl-C/EOF.
                try:
                    pause(
                        ledger=ledger,
                        account_id="paper",
                        actor_id=_ACTOR,
                        trace_id=uuid.UUID(options.run_id).hex,
                        reason_code="PAUSED_NEW",
                    )
                finally:
                    publish_outbox(ledger)
    finally:
        if runtime is not None:
            runtime.process.broker.close()
            if runtime.market_provider is not None:
                runtime.market_provider.close()
        engine.dispose()


def main(argv=None):
    try:
        options = parse_options(argv)
        require_operator_terminal()
        confirmation = operator_input("Type PAPER " + options.run_id + " to authorize this run:")
        _require(confirmation == "PAPER " + options.run_id)
        execute(options)
    except SmokeDenied as exc:
        code = (
            "PAPER_SMOKE_OUTBOX_UNRESOLVED"
            if exc.args == ("PAPER_SMOKE_OUTBOX_UNRESOLVED",)
            else "PAPER_SMOKE_DENIED_OR_UNRESOLVED"
        )
        print(code, file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt):
        print("PAPER_SMOKE_DENIED_OR_UNRESOLVED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
