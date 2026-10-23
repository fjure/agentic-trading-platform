"""Isolated Paper submit: network preflight and POST never hold a ledger lock.

The committed SUBMITTING row is the durable attempt marker. A crash after that
commit is recovered by client-ID lookup, never by sending another POST.
"""

from decimal import Decimal

from packages.domain import models as m
from packages.observability.outbox import persist
from packages.risk.paper_daily_sql import is_isolated_paper
from packages.risk.paper_lifecycle import read_activation
from services.execution.gates import SubmitDenied, _now, evaluate_submit_gate
from services.execution.intents import _adapter_id, _assert_owner, _audit, _finish, _id


class PaperAttemptExpired(SubmitDenied):
    """Committed attempt remains lookup-only when its deadline passed during commit."""


def _start_attempt(tx, *, account_id, intent, decision, reservation, binding, now):
    from services.execution.paper_reconciliation import (
        ensure_paper_baseline,
        ensure_paper_order_basis,
    )

    ensure_paper_baseline(tx, account_id, binding, now, intent.trace_id)
    order = m.BrokerOrder(
        id=_id("order", intent.id), intent_id=intent.id,
        client_order_id=intent.client_order_id, broker_order_id=None,
        status=m.OrderStatus.SUBMITTING, requested_quantity=intent.quantity,
        filled_quantity=Decimal(0), version=1, updated_at=now, trace_id=intent.trace_id,
    )
    _audit(tx, intent=intent, decision=decision, entity_id=order.id, version=1,
           phase="submitting", reason="GATE_APPROVED", now=now)
    tx.insert(order, adapter="alpaca-paper")
    ensure_paper_order_basis(tx, account_id, order, intent, reservation, now, intent.trace_id)
    report = m.ExecutionReport(
        id=_id("report:SUBMITTING", intent.id), order_id=order.id,
        status=m.OrderStatus.SUBMITTING, requested_quantity=intent.quantity,
        filled_quantity=Decimal(0), reconciliation=m.Reconciliation.PENDING,
        occurred_at=now, received_at=now, trace_id=intent.trace_id, reason_code="GATE_APPROVED",
    )
    _audit(tx, intent=intent, decision=decision, entity_id=report.id, version=1,
           phase="submitting_report", reason="GATE_APPROVED", now=now)
    tx.insert(report, adapter="alpaca-paper")
    persist(tx.connection, m.EventEnvelope(
        event_id=_id("event:SUBMITTING", intent.id), event_type="execution.reported.v1",
        occurred_at=now, aggregate_id=report.id, aggregate_version=1,
        trace_id=intent.trace_id, payload=report,
    ))


def submit_paper_once(*, ledger, account_id, intent_id, limits, owner_id, fencing_token,
                      broker, binding):
    """Attempt one fresh reserved intent; ambiguous completion remains reconcile-only."""
    if _adapter_id(ledger, account_id, broker, binding) != "alpaca-paper":
        raise SubmitDenied("BROKER_BOUNDARY")
    # Obtain only immutable local data here; no account lock spans provider I/O.
    with ledger.transaction() as tx:
        if not is_isolated_paper(tx.connection):
            raise SubmitDenied("PAPER_RUNTIME_REQUIRED")
        intent = tx.get(m.OrderIntent, intent_id)
        if intent is None:
            raise SubmitDenied("INTENT_MISSING")
        # Re-entering this public port is never a replay authority. Restoration
        # and reconciliation own all attempted intents, including missing replies.
        if tx.get(m.BrokerOrder, _id("order", intent.id)) is not None:
            raise SubmitDenied("RECONCILE_REQUIRED")
    proofs = binding.collect_submit_proofs(intent.instrument_id)
    attempt = None

    def before_io():
        nonlocal attempt
        if attempt is not None:
            raise SubmitDenied("RECONCILE_REQUIRED")
        with ledger.transaction() as tx:
            if not is_isolated_paper(tx.connection):
                raise SubmitDenied("PAPER_RUNTIME_REQUIRED")
            current, proposal, decision, reservation, capability, now = evaluate_submit_gate(
                tx=tx, account_id=account_id, intent_id=intent_id, limits=limits,
                owner_id=owner_id, fencing_token=fencing_token, expected_broker_id="alpaca-paper",
            )
            if current != intent:
                raise SubmitDenied("INTENT_CHANGED")
            binding.validate_submit(tx, intent, capability, now, collected_proofs=proofs)
            # SQL clock is read after all checks; expired approval cannot be
            # renewed by a fresh quote, heartbeat, or an older proof bundle.
            now = _now(tx.connection)
            activation = read_activation(tx.connection, account_id, proposal.id, now,
                                         symbol=intent.instrument_id, owner_id=owner_id,
                                         fencing_token=fencing_token)
            if activation is None:
                raise SubmitDenied("PAPER_ACTIVATION_INVALID")
            if now >= proposal.valid_until or now >= decision.valid_until:
                raise SubmitDenied("PROPOSAL_EXPIRED")
            _start_attempt(tx, account_id=account_id, intent=intent, decision=decision,
                           reservation=reservation, binding=binding, now=now)
        # Assigned only after successful commit. A commit exception escapes the
        # callback and prevents the adapter from marking/sending an attempt.
        attempt = (decision, reservation)
        # Committing can itself block. The execution-owned adapter clock is a
        # local UTC read, so expiry is checked again with no lock or network I/O.
        # Do not erase an attempted marker: a database commit may be ambiguous.
        if m.utc_time(broker._now()) >= min(
            proposal.valid_until, decision.valid_until, activation.expires_at
        ):
            raise PaperAttemptExpired("PAPER_ATTEMPT_EXPIRED")

    try:
        response = broker.submit(intent, before_io=before_io)
    except PaperAttemptExpired:
        raise
    except Exception:
        if attempt is None:
            raise
        response = None
    if attempt is None:
        # Broker found a pre-existing client ID during read-only preflight.
        raise SubmitDenied("RECONCILE_REQUIRED")
    decision, reservation = attempt
    accepted = (
        type(response) is m.BrokerOrder and response.intent_id == intent.id
        and response.client_order_id == intent.client_order_id
        and response.trace_id == intent.trace_id and response.requested_quantity == intent.quantity
        and response.filled_quantity == 0 and response.broker_order_id is not None
        and response.status is m.OrderStatus.ACKNOWLEDGED
    )
    with ledger.transaction() as tx:
        if not is_isolated_paper(tx.connection):
            raise SubmitDenied("PAPER_RUNTIME_REQUIRED")
        tx.lock_account(account_id)
        _assert_owner(tx, account_id, owner_id, fencing_token)
        return _finish(tx, intent=intent, decision=decision, reservation=reservation,
                       status=m.OrderStatus.ACKNOWLEDGED if accepted else m.OrderStatus.UNKNOWN,
                       broker_id=response.broker_order_id if accepted else None,
                       reason="ACKNOWLEDGED" if accepted else "SUBMIT_UNCERTAIN",
                       now=_now(tx.connection), adapter_id="alpaca-paper")
