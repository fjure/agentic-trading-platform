"""One-shot, Fake-only Paper submit with a durable pre-I/O state."""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import text

from packages.brokers.fake import FakeBrokerAdapter
from packages.domain import models as m
from packages.observability.outbox import persist
from packages.persistence.ledger import PostgresLedger
from packages.risk.engine import RiskLimits
from services.execution.gates import SubmitDenied, _now, evaluate_submit_gate

_NAMESPACE = uuid.UUID("96e94135-3853-4f6f-90eb-99e38802bb28")
_ADAPTER = "fake"


def _id(kind: str, intent_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{intent_id}"))


def _audit(
    tx,
    *,
    intent: m.OrderIntent,
    decision: m.RiskDecision,
    entity_id: str,
    version: int,
    phase: str,
    reason: str,
    now,
) -> None:
    tx.insert(
        m.AuditEvent(
            id=_id(f"audit:{phase}:{version}", intent.id),
            occurred_at=now,
            actor_id="execution-submit",
            action=f"execution.{phase}",
            reason_codes=(reason,),
            entity_id=entity_id,
            entity_version=version,
            trace_id=intent.trace_id,
            policy_version=decision.policy_version,
            outcome=phase.upper(),
        )
    )


def _finish(
    tx,
    *,
    intent: m.OrderIntent,
    decision: m.RiskDecision,
    reservation: m.RiskReservation,
    status: m.OrderStatus,
    broker_id: str | None,
    reason: str,
    now,
) -> m.BrokerOrder:
    order = tx.get(m.BrokerOrder, _id("order", intent.id))
    if order is None or order.status is not m.OrderStatus.SUBMITTING:
        raise SubmitDenied("RECONCILE_REQUIRED")
    updated = replace(
        order,
        broker_order_id=broker_id,
        status=status,
        version=order.version + 1,
        updated_at=now,
    )
    _audit(
        tx,
        intent=intent,
        decision=decision,
        entity_id=order.id,
        version=updated.version,
        phase="submit_result",
        reason=reason,
        now=now,
    )
    if not tx.replace(updated, expected_version=order.version):
        raise SubmitDenied("ORDER_VERSION_CONFLICT")
    if status in (m.OrderStatus.UNKNOWN, m.OrderStatus.REJECTED):
        reservation_state = (
            m.ReservationState.HELD_UNKNOWN
            if status is m.OrderStatus.UNKNOWN
            else m.ReservationState.RELEASED
        )
        next_reservation = replace(
            reservation,
            state=reservation_state,
            version=reservation.version + 1,
            released_at=now if status is m.OrderStatus.REJECTED else None,
        )
        _audit(
            tx,
            intent=intent,
            decision=decision,
            entity_id=reservation.id,
            version=next_reservation.version,
            phase="reservation_result",
            reason=reason,
            now=now,
        )
        if not tx.replace(next_reservation, expected_version=reservation.version):
            raise SubmitDenied("RESERVATION_VERSION_CONFLICT")
    if status is m.OrderStatus.UNKNOWN:
        context = (
            tx.connection.execute(
                text("""
                SELECT version FROM public.risk_account_context
                WHERE account_id=(SELECT account_id FROM public.order_intents WHERE id=:intent)
                FOR UPDATE
            """),
                {"intent": intent.id},
            )
            .mappings()
            .one()
        )
        next_version = context["version"] + 1
        _audit(
            tx,
            intent=intent,
            decision=decision,
            entity_id=tx.connection.execute(
                text("SELECT account_id FROM public.order_intents WHERE id=:intent"),
                {"intent": intent.id},
            ).scalar_one(),
            version=next_version,
            phase="pause_unknown",
            reason=reason,
            now=now,
        )
        tx.connection.execute(
            text("""
                UPDATE public.risk_account_context
                SET version=:version,safety_state='PAUSE_NEW',
                    reconciliation_current=false,actor_id='execution-submit',trace_id=:trace
                WHERE account_id=(SELECT account_id FROM public.order_intents WHERE id=:intent)
            """),
            {"version": next_version, "trace": intent.trace_id, "intent": intent.id},
        )
    report = m.ExecutionReport(
        id=_id(f"report:{status.value}", intent.id),
        order_id=order.id,
        status=status,
        requested_quantity=intent.quantity,
        filled_quantity=Decimal("0"),
        reconciliation=(
            m.Reconciliation.PENDING
            if status is m.OrderStatus.UNKNOWN
            else m.Reconciliation.CURRENT
        ),
        occurred_at=now,
        received_at=now,
        trace_id=intent.trace_id,
        reason_code=reason,
    )
    _audit(
        tx,
        intent=intent,
        decision=decision,
        entity_id=report.id,
        version=1,
        phase="report",
        reason=reason,
        now=now,
    )
    tx.insert(report, adapter=_ADAPTER)
    persist(
        tx.connection,
        m.EventEnvelope(
            event_id=_id(f"outbox:{status.value}", intent.id),
            event_type="execution.reported.v1",
            occurred_at=now,
            aggregate_id=report.id,
            aggregate_version=1,
            trace_id=intent.trace_id,
            payload=report,
        ),
    )
    return updated


def _capability_matches(
    broker: FakeBrokerAdapter, intent: m.OrderIntent, expected: m.BrokerCapabilities
) -> bool:
    try:
        actual = broker.capabilities(intent.instrument_id)
    except Exception:
        return False
    if not isinstance(actual, m.BrokerCapabilities):
        return False
    return (
        actual.broker_id == expected.broker_id == _ADAPTER
        and actual.instrument_id == intent.instrument_id
        and actual.version == expected.version == intent.capability_version
        and actual.paper is m.Capability.CONFIRMED
        and actual.permits(
            order_type=intent.order_type,
            fractional=intent.quantity != intent.quantity.to_integral_value(),
            regular_session=True,
        )
        and actual.quantity_step == expected.quantity_step
        and actual.price_tick == expected.price_tick
        and actual.market_order == expected.market_order
        and actual.limit_order == expected.limit_order
        and actual.regular_session == expected.regular_session
        and actual.fractional == expected.fractional
    )


def submit_once(
    *,
    ledger: PostgresLedger,
    account_id: str,
    intent_id: str,
    limits: RiskLimits,
    broker: FakeBrokerAdapter,
    paper_smoke_enabled: bool = False,
) -> m.BrokerOrder:
    """Submit one durable intent at most once, with explicit process-local Paper consent.

    SUBMITTING is committed before any broker call. A crash or other BaseException
    leaves it durable and every later call requires read-only reconciliation.
    The second transaction holds the account lock across the synchronous call,
    serializing policy and pause mutations with the last gate and I/O.
    """
    if paper_smoke_enabled is not True:
        raise SubmitDenied("PAPER_SMOKE_DISABLED")
    if type(broker) is not FakeBrokerAdapter:
        raise SubmitDenied("BROKER_BOUNDARY")
    with ledger.transaction() as tx:
        intent, _proposal, decision, _reservation, _capability, now = evaluate_submit_gate(
            tx=tx,
            account_id=account_id,
            intent_id=intent_id,
            limits=limits,
        )
        order = m.BrokerOrder(
            id=_id("order", intent.id),
            intent_id=intent.id,
            client_order_id=intent.client_order_id,
            broker_order_id=None,
            status=m.OrderStatus.SUBMITTING,
            requested_quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            version=1,
            updated_at=now,
            trace_id=intent.trace_id,
        )
        _audit(
            tx,
            intent=intent,
            decision=decision,
            entity_id=order.id,
            version=1,
            phase="submitting",
            reason="GATE_APPROVED",
            now=now,
        )
        tx.insert(order, adapter=_ADAPTER)

    # A pause, policy change, expiry, or other mutation may win the interval
    # between transactions. Recheck everything after acquiring every lock.
    with ledger.transaction() as tx:
        try:
            intent, _proposal, decision, reservation, capability, _now_checked = (
                evaluate_submit_gate(
                    tx=tx,
                    account_id=account_id,
                    intent_id=intent_id,
                    limits=limits,
                    allow_submitting_order=True,
                )
            )
        except SubmitDenied as exc:
            # An invalid role cannot write an outcome. For a legitimate Execution
            # caller, no broker I/O has occurred, so rejection can release budget.
            if exc.reason_code == "EXECUTION_ROLE_REQUIRED":
                raise
            persisted_intent = tx.get(m.OrderIntent, intent_id)
            persisted_order = tx.get(m.BrokerOrder, _id("order", intent_id))
            if persisted_intent is None or persisted_order is None:
                raise
            persisted_decision = tx.get(m.RiskDecision, persisted_intent.risk_decision_id)
            persisted_reservation = tx.get(m.RiskReservation, persisted_decision.reservation_id)
            _finish(
                tx,
                intent=persisted_intent,
                decision=persisted_decision,
                reservation=persisted_reservation,
                status=m.OrderStatus.REJECTED,
                broker_id=None,
                reason=exc.reason_code,
                now=_now(tx.connection),
            )
            raise_after_commit = exc
        else:
            raise_after_commit = None
            if not _capability_matches(broker, intent, capability):
                result = _finish(
                    tx,
                    intent=intent,
                    decision=decision,
                    reservation=reservation,
                    status=m.OrderStatus.REJECTED,
                    broker_id=None,
                    reason="BROKER_CAPABILITY",
                    now=_now(tx.connection),
                )
                raise_after_commit = SubmitDenied("BROKER_CAPABILITY")
            else:
                # The read-only adapter capability lookup may itself block.
                # Refresh DB time and every gate again immediately before submit.
                try:
                    intent, _proposal, decision, reservation, _capability, _now_checked = (
                        evaluate_submit_gate(
                            tx=tx,
                            account_id=account_id,
                            intent_id=intent_id,
                            limits=limits,
                            allow_submitting_order=True,
                        )
                    )
                except SubmitDenied as exc:
                    result = _finish(
                        tx,
                        intent=intent,
                        decision=decision,
                        reservation=reservation,
                        status=m.OrderStatus.REJECTED,
                        broker_id=None,
                        reason=exc.reason_code,
                        now=_now(tx.connection),
                    )
                    raise_after_commit = exc
                else:
                    try:
                        response = broker.submit(intent)
                    except Exception:
                        # Any ordinary error may have happened after acceptance.
                        result = _finish(
                            tx,
                            intent=intent,
                            decision=decision,
                            reservation=reservation,
                            status=m.OrderStatus.UNKNOWN,
                            broker_id=None,
                            reason="SUBMIT_UNCERTAIN",
                            now=_now(tx.connection),
                        )
                    else:
                        accepted = (
                            isinstance(response, m.BrokerOrder)
                            and response.intent_id == intent.id
                            and response.client_order_id == intent.client_order_id
                            and response.trace_id == intent.trace_id
                            and response.requested_quantity == intent.quantity
                            and response.filled_quantity == 0
                            and response.broker_order_id is not None
                            and response.status is m.OrderStatus.ACKNOWLEDGED
                        )
                        result = _finish(
                            tx,
                            intent=intent,
                            decision=decision,
                            reservation=reservation,
                            status=(
                                m.OrderStatus.ACKNOWLEDGED if accepted else m.OrderStatus.UNKNOWN
                            ),
                            broker_id=response.broker_order_id if accepted else None,
                            reason="ACKNOWLEDGED" if accepted else "SUBMIT_UNCERTAIN",
                            now=_now(tx.connection),
                        )
    if raise_after_commit is not None:
        raise raise_after_commit
    return result


def pause_after_restart(*, ledger: PostgresLedger, account_id: str) -> None:
    """Latch new Paper submits off when an Execution process starts or restarts."""
    with ledger.transaction() as tx:
        connection = tx.connection
        if connection.execute(text("SELECT current_user")).scalar_one() != "atl_execution":
            raise SubmitDenied("EXECUTION_ROLE_REQUIRED")
        tx.lock_account(account_id)
        context = (
            connection.execute(
                text("""
                    SELECT version,safety_state,reconciliation_current,trace_id
                    FROM public.risk_account_context WHERE account_id=:account FOR UPDATE
                """),
                {"account": account_id},
            )
            .mappings()
            .one_or_none()
        )
        if context is None:
            raise SubmitDenied("ACCOUNT_CONTEXT_MISSING")
        if (
            context["safety_state"] in ("PAUSE_NEW", "HALTED")
            and not context["reconciliation_current"]
        ):
            return
        next_version = context["version"] + 1
        next_state = "HALTED" if context["safety_state"] == "HALTED" else "PAUSE_NEW"
        now = _now(connection)
        tx.insert(
            m.AuditEvent(
                id=str(uuid.uuid4()),
                occurred_at=now,
                actor_id="execution-startup",
                action="execution.unsafe_restart",
                reason_codes=("UNSAFE_RESTART",),
                entity_id=account_id,
                entity_version=next_version,
                trace_id=context["trace_id"],
                outcome=next_state,
            )
        )
        connection.execute(
            text("""
                UPDATE public.risk_account_context
                SET version=:version,safety_state=:state,reconciliation_current=false,
                    actor_id='execution-startup',trace_id=:trace
                WHERE account_id=:account
            """),
            {
                "version": next_version,
                "state": next_state,
                "trace": context["trace_id"],
                "account": account_id,
            },
        )


class ExecutionProcess:
    """Local Execution entry point: every fresh process latches Paper submits off."""

    def __init__(
        self,
        *,
        ledger: PostgresLedger,
        account_id: str,
        limits: RiskLimits,
        broker: FakeBrokerAdapter,
        paper_smoke_enabled: bool = False,
    ) -> None:
        pause_after_restart(ledger=ledger, account_id=account_id)
        self.ledger = ledger
        self.account_id = account_id
        self.limits = limits
        self.broker = broker
        self.paper_smoke_enabled = paper_smoke_enabled

    def submit(self, intent_id: str) -> m.BrokerOrder:
        return submit_once(
            ledger=self.ledger,
            account_id=self.account_id,
            intent_id=intent_id,
            limits=self.limits,
            broker=self.broker,
            paper_smoke_enabled=self.paper_smoke_enabled,
        )
