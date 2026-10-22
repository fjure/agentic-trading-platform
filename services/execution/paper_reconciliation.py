"""Execution-only, account-wide Paper evidence replay and atomic accounting.

Provider activity identities stay in private tables. Recovery authority is a
fresh durable receipt, never an order acknowledgement or an empty fee window.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.domain import models as m
from packages.observability.outbox import persist
from packages.persistence.execution_identity import is_execution
from packages.persistence.ledger import PostgresLedger, _encode
from services.execution.broker_binding import AlpacaExecutionBinding
from services.execution.gates import SubmitDenied, _now
from services.execution.paper_cost_basis import costed_fill
from services.execution.reconciliation import _audit, _context, _exact, _fail, _id, _update_context

_ACTOR = "execution-reconciliation"
ZERO_FEE_PROFILE = "alpaca-paper-us-retail-equity-zero-v1"
_TERMINAL = (m.OrderStatus.FILLED, m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
_ALLOWED = (
    *_TERMINAL,
    m.OrderStatus.ACKNOWLEDGED,
    m.OrderStatus.PARTIALLY_FILLED,
    m.OrderStatus.CANCEL_PENDING,
)


@dataclass(frozen=True, slots=True)
class PaperReconciliationReceipt:
    ready: bool
    reason_code: str
    account_id: str
    owner_id: str
    fencing_token: int
    observed_at: datetime
    portfolio_snapshot_id: str
    portfolio_version: int
    receipt_id: str
    context_version: int


class _Mismatch(ValueError):
    """Only fixed local reason codes may be persisted as reconciliation outcomes."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise _Mismatch(reason)


def _boundary(ledger, account_id, broker, binding) -> None:
    if not (
        type(ledger) is PostgresLedger
        and type(broker) is AlpacaPaperBrokerAdapter
        and type(binding) is AlpacaExecutionBinding
        and binding.ledger is ledger
        and binding.account_id == account_id
        and binding.broker is broker
        and binding.restored
    ):
        raise SubmitDenied("BROKER_BOUNDARY")


def ensure_paper_baseline(tx, account_id, binding, now, trace_id) -> None:
    """Freeze the initial portfolio before the first possibly sent attempt.

    Caller already holds the account lock. Never manufacture a baseline after
    an attempt: a narrowed activity window cannot prove historic accounting.
    """
    c = tx.connection
    if not is_execution(c):
        raise SubmitDenied("EXECUTION_ROLE_REQUIRED")
    _boundary(binding.ledger, account_id, binding.broker, binding)
    tx.lock_account(account_id)
    if c.execute(
        text("SELECT 1 FROM public.paper_reconciliation_baselines WHERE account_id=:a"),
        {"a": account_id},
    ).scalar_one_or_none():
        return
    if c.execute(
        text("""SELECT EXISTS(SELECT 1 FROM public.broker_orders b
      JOIN public.order_intents i ON i.id=b.intent_id
      WHERE i.account_id=:a AND b.adapter='alpaca-paper')"""),
        {"a": account_id},
    ).scalar_one():
        raise SubmitDenied("PAPER_BASELINE_MISSING")
    context = _context(tx, account_id)
    if context is None:
        raise SubmitDenied("PAPER_BASELINE_MISSING")
    old = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
    account = (
        c.execute(
            text("SELECT currency,ledger_version FROM public.accounts WHERE id=:a"),
            {"a": account_id},
        )
        .mappings()
        .one()
    )
    version = account["ledger_version"]
    if (
        old is None
        or old.currency != account["currency"]
        or old.currency != "USD"
        or old.ledger_version != version
        or version != context["portfolio_version"]
    ):
        raise SubmitDenied("PORTFOLIO_BASIS_MISMATCH")
    provider = c.execute(
        text("SELECT provider_account_id FROM public.broker_account_bindings WHERE account_id=:a"),
        {"a": account_id},
    ).scalar_one_or_none()
    if provider is None:
        raise SubmitDenied("BINDING_PIN_MISSING")
    rows = (
        c.execute(
            text("SELECT id FROM public.positions WHERE account_id=:a ORDER BY id FOR UPDATE"),
            {"a": account_id},
        )
        .scalars()
        .all()
    )
    positions = [tx.get(m.Position, row) for row in rows]
    if any(p.currency != "USD" for p in positions):
        raise SubmitDenied("POSITION_CURRENCY_MISMATCH")
    if len({p.instrument_id for p in positions}) != len(positions):
        raise SubmitDenied("POSITION_AMBIGUOUS_OR_MISSING")
    identity = _id("paper-baseline", account_id)
    _audit(
        tx,
        entity_id=identity,
        version=1,
        trace_id=trace_id,
        action="paper_baseline",
        reason="PAPER_BASELINE_FROZEN",
        now=now,
    )
    c.execute(
        text("""INSERT INTO public.paper_reconciliation_baselines
      (id,account_id,provider_account_id,portfolio_snapshot_id,portfolio_version,
       baseline_at,fee_authority_profile,positions,actor_id,trace_id)
      VALUES (:id,:a,:provider,:snapshot,:version,:at,:profile,CAST(:positions AS jsonb),
              :actor,:trace)"""),
        {
            "id": identity,
            "a": account_id,
            "provider": provider,
            "snapshot": old.id,
            "version": version,
            "at": now,
            "profile": binding.broker.reconciliation_fee_profile,
            "positions": json.dumps(_encode(positions)),
            "actor": _ACTOR,
            "trace": trace_id,
        },
    )


def ensure_paper_order_basis(tx, account_id, order, intent, reservation, now, trace_id):
    """Pin the original risk budget in the durable SUBMITTING transaction, before I/O."""
    c = tx.connection
    if not is_execution(c):
        raise SubmitDenied("EXECUTION_ROLE_REQUIRED")
    tx.lock_account(account_id)
    owner = (
        c.execute(
            text("""SELECT b.adapter,i.account_id,r.account_id AS reservation_account
      FROM public.broker_orders b JOIN public.order_intents i ON i.id=b.intent_id
      JOIN public.risk_reservations r ON r.id=:r WHERE b.id=:o"""),
            {"o": order.id, "r": reservation.id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        owner is None
        or owner["adapter"] != "alpaca-paper"
        or owner["account_id"] != account_id
        or owner["reservation_account"] != account_id
        or order.intent_id != intent.id
        or order.requested_quantity != intent.quantity
        or reservation.risk_decision_id != intent.risk_decision_id
    ):
        raise SubmitDenied("RESERVATION_ORIGINAL_BASIS_MISMATCH")
    prior = (
        c.execute(
            text("SELECT * FROM public.paper_order_accounting_bases WHERE order_id=:o"),
            {"o": order.id},
        )
        .mappings()
        .one_or_none()
    )
    if prior is not None:
        if (
            prior["account_id"] != account_id
            or prior["reservation_id"] != reservation.id
            or prior["requested_quantity"] != intent.quantity
        ):
            raise SubmitDenied("RESERVATION_ORIGINAL_BASIS_MISMATCH")
        return
    if order.filled_quantity != 0 or reservation.currency != "USD":
        raise SubmitDenied("RESERVATION_ORIGINAL_BASIS_MISSING")
    identity = _id("paper-order-basis", order.id)
    _audit(
        tx,
        entity_id=identity,
        version=1,
        trace_id=trace_id,
        action="paper_order_basis",
        reason="PAPER_ORIGINAL_BASIS_FROZEN",
        now=now,
    )
    c.execute(
        text("""INSERT INTO public.paper_order_accounting_bases
      (id,account_id,order_id,reservation_id,original_notional,requested_quantity,actor_id,trace_id)
      VALUES (:id,:a,:o,:r,:n,:q,:actor,:trace)"""),
        {
            "id": identity,
            "a": account_id,
            "o": order.id,
            "r": reservation.id,
            "n": reservation.reserved_notional,
            "q": intent.quantity,
            "actor": _ACTOR,
            "trace": trace_id,
        },
    )


def _activity_payload(activity) -> dict:
    payload = _encode(activity)
    for key in ("quantity", "price", "cumulative_quantity", "leaves_quantity", "net_amount"):
        value = getattr(activity, key)
        if value is not None:
            raw = format(value, "f")
            payload[key] = (
                "0" if value == 0 else (raw.rstrip("0").rstrip(".") if "." in raw else raw)
            )
    # Non-trade activities legitimately provide only a calendar date.
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in payload.items()}


def _payload_digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _digest(activity) -> str:
    return _payload_digest(_activity_payload(activity))


def _receipt(tx, *, account_id, owner_id, fencing_token, ready, reason, observed_at, trace_id):
    from services.execution.intents import _assert_owner

    _assert_owner(tx, account_id, owner_id, fencing_token)
    context = _context(tx, account_id)
    identity = str(uuid.uuid4())
    result = PaperReconciliationReceipt(
        ready,
        reason,
        account_id,
        owner_id,
        fencing_token,
        observed_at,
        context["portfolio_snapshot_id"],
        context["portfolio_version"],
        identity,
        context["version"],
    )
    _audit(
        tx,
        entity_id=identity,
        version=1,
        trace_id=trace_id,
        action="paper_receipt",
        reason=reason,
        now=_now(tx.connection),
    )
    tx.connection.execute(
        text("""INSERT INTO public.paper_reconciliation_receipts
      (id,account_id,owner_id,fencing_token,portfolio_snapshot_id,portfolio_version,
       context_version,ready,reason_code,observed_at,actor_id,trace_id)
      VALUES (:id,:a,:owner,:fence,:snapshot,:version,:context,:ready,:reason,:at,
              :actor,:trace)"""),
        {
            "id": identity,
            "a": account_id,
            "owner": owner_id,
            "fence": fencing_token,
            "snapshot": result.portfolio_snapshot_id,
            "version": result.portfolio_version,
            "context": result.context_version,
            "ready": ready,
            "reason": reason,
            "at": observed_at,
            "actor": _ACTOR,
            "trace": trace_id,
        },
    )
    _assert_owner(tx, account_id, owner_id, fencing_token)
    return result


def _proven_pre_io_rejection(tx, order, intent):
    if (
        order.status is not m.OrderStatus.REJECTED
        or order.broker_order_id is not None
        or order.filled_quantity != 0
    ):
        return False
    return tx.connection.execute(
        text("""SELECT EXISTS (
      SELECT 1 FROM public.execution_reports r
      JOIN public.audit_events a ON a.entity_id=:order
      WHERE r.order_id=:order AND r.adapter='alpaca-paper'
        AND r.status='REJECTED' AND r.filled_quantity=0
        AND r.requested_quantity=:quantity AND r.trace_id=:trace
        AND r.reconciliation='CURRENT' AND r.broker_event_id IS NULL
        AND a.actor_id='execution-submit' AND a.action='execution.submit_result'
        AND a.outcome='PRE_IO_REJECTED' AND a.reason_codes ? r.reason_code
        AND a.trace_id=:trace
    )"""),
        {"order": order.id, "quantity": intent.quantity, "trace": intent.trace_id},
    ).scalar_one()


def _pause(tx, account_id, orders, reason, now, trace_id):
    context = _context(tx, account_id)
    for order, _intent, _decision, reservation in orders:
        if _proven_pre_io_rejection(tx, order, _intent):
            continue
        _fail(
            tx,
            order=order,
            reservation=reservation,
            context=context,
            account_id=account_id,
            reason=reason,
            now=now,
            adapter="alpaca-paper",
        )
        context = _context(tx, account_id)
    # Also pause an empty account; the latch retains HALTED and emits its outbox.
    tx.connection.execute(
        text("SELECT public.safety_latch(:a,'PAUSE_NEW',:actor,:trace,'HEALTH_CHECK_FAILED')"),
        {"a": account_id, "actor": _ACTOR, "trace": trace_id},
    )


def _orders(tx, account_id):
    rows = (
        tx.connection.execute(
            text("""SELECT b.id FROM public.broker_orders b
      JOIN public.order_intents i ON i.id=b.intent_id
      WHERE i.account_id=:a ORDER BY b.id FOR UPDATE OF b"""),
            {"a": account_id},
        )
        .scalars()
        .all()
    )
    result = []
    for identity in rows:
        adapter = tx.connection.execute(
            text("SELECT adapter FROM public.broker_orders WHERE id=:id"), {"id": identity}
        ).scalar_one()
        _require(adapter == "alpaca-paper", "PAPER_ORDER_ADAPTER_MISMATCH")
        order = tx.get(m.BrokerOrder, identity)
        intent = tx.get(m.OrderIntent, order.intent_id)
        decision = tx.get(m.RiskDecision, intent.risk_decision_id)
        reservation = tx.get(m.RiskReservation, decision.reservation_id) if decision else None
        _require(decision is not None and reservation is not None, "RECONCILIATION_LINK_MISSING")
        _require(
            order.client_order_id == intent.client_order_id
            and order.requested_quantity == intent.quantity
            and len({order.trace_id, intent.trace_id, decision.trace_id, reservation.trace_id})
            == 1,
            "OWNERSHIP_OR_IDENTITY_MISMATCH",
        )
        ownership = tx.connection.execute(
            text("SELECT account_id FROM public.risk_reservations WHERE id=:id"),
            {"id": reservation.id},
        ).scalar_one()
        _require(
            ownership == account_id and reservation.currency == "USD",
            "OWNERSHIP_OR_IDENTITY_MISMATCH",
        )
        result.append((order, intent, decision, reservation))
    return result


def reconcile_paper_account(*, ledger, account_id, broker, binding, owner_id, fencing_token):
    _boundary(ledger, account_id, broker, binding)
    with ledger.transaction() as tx:
        return reconcile_paper_account_tx(
            tx,
            ledger=ledger,
            account_id=account_id,
            broker=broker,
            binding=binding,
            owner_id=owner_id,
            fencing_token=fencing_token,
        )


def reconcile_paper_account_tx(
    tx, *, ledger, account_id, broker, binding, owner_id, fencing_token, now=None, trace_id=None
):
    """Reconcile and issue a receipt under the same owner/account lock as recovery."""
    _boundary(ledger, account_id, broker, binding)
    from services.execution.intents import _assert_owner

    c = tx.connection
    if not is_execution(c):
        raise SubmitDenied("EXECUTION_ROLE_REQUIRED")
    tx.lock_account(account_id)
    _assert_owner(tx, account_id, owner_id, fencing_token)
    now = _now(c)  # Caller cannot supply a stale authority time.
    trace_id = trace_id or uuid.uuid4().hex
    ensure_paper_baseline(tx, account_id, binding, now, trace_id)
    baseline = (
        c.execute(
            text("SELECT * FROM public.paper_reconciliation_baselines WHERE account_id=:a"),
            {"a": account_id},
        )
        .mappings()
        .one()
    )
    orders = []
    try:
        _require(
            c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 1,
            "ACCOUNT_AMBIGUOUS",
        )
        orders = _orders(tx, account_id)
        from packages.risk.paper_daily_sql import is_isolated_paper

        if is_isolated_paper(c):
            from services.execution.paper_initialization import read_initial_history

            history = read_initial_history(c, account_id)
            _require(
                history.baseline_at == baseline["baseline_at"]
                and history.provider_account_id == str(baseline["provider_account_id"]),
                "INITIAL_HISTORY_MISMATCH",
            )
            evidence = broker.reconciliation_evidence(
                after=baseline["baseline_at"], initial_history=history
            )
        else:
            evidence = broker.reconciliation_evidence(after=baseline["baseline_at"])
        _assert_owner(tx, account_id, owner_id, fencing_token)
        final_now = _now(c)
        _require(
            str(baseline["provider_account_id"]) == evidence.account_id
            and evidence.currency == "USD",
            "PAPER_ACCOUNT_MISMATCH",
        )
        _require(
            evidence.after == baseline["baseline_at"]
            and evidence.until >= baseline["baseline_at"]
            and evidence.until <= final_now + timedelta(seconds=5)
            and timedelta(0) <= final_now - evidence.observed_at < timedelta(seconds=120)
            and -timedelta(seconds=5)
            <= final_now - evidence.provider_time
            < timedelta(seconds=120),
            "PAPER_EVIDENCE_STALE_OR_INCOMPLETE",
        )
        with localcontext() as arithmetic:
            arithmetic.prec = 80
            with c.begin_nested():
                plan = _plan(tx, account_id, baseline, orders, evidence)
                _book(tx, account_id, baseline, orders, evidence, plan, final_now, trace_id)
        ready, reason = plan["ready"], plan["reason"]
        _assert_owner(tx, account_id, owner_id, fencing_token)
        _require(
            timedelta(0) <= _now(c) - evidence.observed_at < timedelta(seconds=120),
            "PAPER_EVIDENCE_STALE_OR_INCOMPLETE",
        )
        if not ready:
            _pause(tx, account_id, _orders(tx, account_id), reason, final_now, trace_id)
        return _receipt(
            tx,
            account_id=account_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            ready=ready,
            reason=reason,
            observed_at=evidence.observed_at,
            trace_id=trace_id,
        )
    except SubmitDenied:
        raise
    except Exception as exc:
        # No provider errors or payloads (which can contain private IDs) escape.
        reason = str(exc) if type(exc) is _Mismatch else "PAPER_EVIDENCE_UNAVAILABLE"
        _assert_owner(tx, account_id, owner_id, fencing_token)
        _pause(tx, account_id, orders, reason, _now(c), trace_id)
        return _receipt(
            tx,
            account_id=account_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            ready=False,
            reason=reason,
            observed_at=_now(c),
            trace_id=trace_id,
        )


def _fill_position(position, side, quantity, price):
    cost = _exact(quantity * price)
    if side == "BUY":
        _require(
            not position.quantity or position.average_price is not None,
            "POSITION_COST_BASIS_MISSING",
        )
        qty = _exact(position.quantity + quantity)
        basis = position.quantity * position.average_price if position.quantity else Decimal(0)
        return replace(position, quantity=qty, average_price=_exact((basis + cost) / qty))
    _require(
        side == "SELL" and position.average_price is not None and position.quantity >= quantity,
        "SELL_BASIS_OR_QUANTITY_INVALID",
    )
    qty = _exact(position.quantity - quantity)
    return replace(
        position,
        quantity=qty,
        average_price=position.average_price if qty else None,
        realized_pnl=_exact(position.realized_pnl + cost - quantity * position.average_price),
    )


def _plan(tx, account_id, baseline, orders, evidence):
    c = tx.connection
    _require(
        baseline["fee_authority_profile"] == evidence.fee_authority_profile,
        "PAPER_FEE_PROFILE_CHANGED",
    )
    baseline_snapshot = tx.get(m.PortfolioSnapshot, baseline["portfolio_snapshot_id"])
    _require(
        baseline_snapshot is not None and baseline_snapshot.currency == "USD",
        "PORTFOLIO_BASIS_MISMATCH",
    )
    positions = {}
    raw_bases = {}
    for raw in baseline["positions"]:
        position = m.Position(
            raw["id"],
            raw["instrument_id"],
            raw["currency"],
            Decimal(raw["quantity"]),
            Decimal(raw["average_price"]) if raw["average_price"] else None,
            Decimal(raw["realized_pnl"]),
            raw["ledger_version"],
        )
        positions[position.instrument_id] = position
        if "cost_basis" in raw:
            basis = m.decimal_value(raw["cost_basis"])
            _require(
                basis >= 0 and (position.quantity > 0 or basis == 0), "POSITION_COST_BASIS_MISSING"
            )
            raw_bases[position.instrument_id] = basis
    _require(not raw_bases or set(raw_bases) == set(positions), "POSITION_COST_BASIS_MISSING")
    local_positions = {
        p.instrument_id: p
        for p in (
            tx.get(m.Position, identity)
            for identity in c.execute(
                text("SELECT id FROM public.positions WHERE account_id=:a"), {"a": account_id}
            )
            .scalars()
            .all()
        )
    }
    _require(
        set(positions) == set(local_positions)
        and all(p.currency == "USD" for p in positions.values())
        and all(p.currency == "USD" for p in local_positions.values()),
        "POSITION_BASELINE_MISMATCH",
    )
    bases = {}
    for order, intent, _decision, reservation in orders:
        basis = (
            c.execute(
                text("SELECT * FROM public.paper_order_accounting_bases WHERE order_id=:o"),
                {"o": order.id},
            )
            .mappings()
            .one_or_none()
        )
        if basis is None:
            _require(order.filled_quantity == 0, "RESERVATION_ORIGINAL_BASIS_MISSING")
            bases[order.id] = (reservation.reserved_notional, False)
        else:
            _require(
                basis["account_id"] == account_id
                and basis["reservation_id"] == reservation.id
                and basis["requested_quantity"] == intent.quantity,
                "RESERVATION_ORIGINAL_BASIS_MISMATCH",
            )
            bases[order.id] = (basis["original_notional"], True)
    sources = {s.client_order_id: s for s in evidence.orders}
    _require(len(sources) == len(evidence.orders), "BROKER_ORDER_AMBIGUOUS")
    by_provider = {}
    for order, intent, _, _ in orders:
        source = sources.pop(order.client_order_id, None)
        if source is None and _proven_pre_io_rejection(tx, order, intent):
            # Only immutable matched submit audit/report proves non-transmission.
            continue
        _require(source is not None, "BROKER_ORDER_MISSING_OR_AMBIGUOUS")
        _require(
            source.intent_id == intent.id
            and source.trace_id == intent.trace_id
            and source.requested_quantity == intent.quantity
            and source.client_order_id == intent.client_order_id
            and source.broker_order_id is not None
            and (order.broker_order_id is None or order.broker_order_id == source.broker_order_id)
            and source.filled_quantity >= order.filled_quantity,
            "BROKER_ORDER_MISMATCH",
        )
        _require(
            source.status in _ALLOWED
            and not (order.status in _TERMINAL and source.status not in _TERMINAL),
            "STATUS_REGRESSION_OR_MISMATCH",
        )
        historic = (
            c.execute(
                text("""SELECT status,filled_quantity FROM public.execution_reports
          WHERE order_id=:o AND adapter='alpaca-paper'
            AND status IN ('FILLED','CANCELED','REJECTED')"""),
                {"o": order.id},
            )
            .mappings()
            .all()
        )
        for proof in historic:
            terminal = m.OrderStatus(proof["status"])
            _require(
                source.status == terminal
                or (
                    terminal in (m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
                    and source.status is m.OrderStatus.FILLED
                    and source.filled_quantity > proof["filled_quantity"]
                ),
                "TERMINAL_STATUS_CORRECTION",
            )
        _require(source.broker_order_id not in by_provider, "BROKER_ORDER_AMBIGUOUS")
        by_provider[source.broker_order_id] = (order, intent, source)
    _require(not sources, "EXTERNAL_ORDER_MISMATCH")
    prior = {
        row["activity_id"]: row
        for row in c.execute(
            text("SELECT * FROM public.paper_activity_evidence WHERE account_id=:a"),
            {"a": account_id},
        )
        .mappings()
        .all()
    }
    for row in prior.values():
        _require(
            _payload_digest(row["evidence"]) == row["evidence_hash"],
            "BROKER_ACTIVITY_IDENTITY_DRIFT",
        )
    activities = {}
    for activity in evidence.activities:
        digest = _digest(activity)
        if activity.id in activities:
            _require(activities[activity.id][1] == digest, "BROKER_ACTIVITY_IDENTITY_DRIFT")
            continue
        _require(
            activity.id not in prior or prior[activity.id]["evidence_hash"] == digest,
            "BROKER_ACTIVITY_IDENTITY_DRIFT",
        )
        if activity.id in prior:
            expected_order_id = (
                by_provider[activity.order_id][0].id
                if activity.kind == "FILL" and activity.order_id in by_provider
                else None
            )
            _require(
                prior[activity.id]["order_id"] == expected_order_id,
                "BROKER_ACTIVITY_IDENTITY_DRIFT",
            )
        activities[activity.id] = (activity, digest)
    _require(set(prior) <= set(activities), "BROKER_ACTIVITY_HISTORY_INCOMPLETE")
    prior_qty = {key: Decimal(0) for key in positions}
    prior_cash = Decimal(0)
    for row in prior.values():
        prior_cash += row["cash_delta"]
        if row["instrument_id"]:
            _require(row["instrument_id"] in prior_qty, "POSITION_BASELINE_MISMATCH")
            prior_qty[row["instrument_id"]] += row["quantity_delta"]
    for key, p in positions.items():
        _require(
            local_positions[key].quantity == _exact(p.quantity + prior_qty[key]),
            "POSITION_LEDGER_DRIFT",
        )
    context = _context(tx, account_id)
    current = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
    account_version = c.execute(
        text("SELECT ledger_version FROM public.accounts WHERE id=:a"), {"a": account_id}
    ).scalar_one()
    _require(
        current.currency == "USD"
        and current.cash == _exact(baseline_snapshot.cash + prior_cash)
        and current.ledger_version == account_version == context["portfolio_version"],
        "PORTFOLIO_LEDGER_DRIFT",
    )
    totals = {key: Decimal(0) for key in by_provider}
    costs = {key: Decimal(0) for key in by_provider}
    cash = baseline_snapshot.cash
    movements = []
    ready = evidence.fee_authority_profile == ZERO_FEE_PROFILE
    reason = "PAPER_RECONCILED" if ready else "PAPER_FEE_AUTHORITY_UNKNOWN"
    for p in positions:
        market = c.execute(
            text("SELECT market FROM public.instruments WHERE id=:id"), {"id": p}
        ).scalar_one()
        if market != m.Market.US_EQUITY.value:
            ready, reason = False, "PAPER_FEE_AUTHORITY_UNKNOWN"
    # FILL timestamps define chronology. Daily fees have no intraday timestamp:
    # apply their confirmed signed asset/cash deltas after fill replay, and keep
    # fee authority closed. Never invent midnight execution before a first BUY.
    ordered = sorted(
        activities.values(),
        key=lambda pair: (
            0 if pair[0].kind == "FILL" else 1,
            pair[0].occurred_at or evidence.after,
            pair[0].order_id or "",
            pair[0].cumulative_quantity or Decimal(0),
            pair[0].id,
        ),
    )
    prior_positions = dict(positions)
    prior_bases, updated_bases = dict(raw_bases), dict(raw_bases)
    for activity, _digest_value in ordered:
        if activity.id not in prior:
            continue
        instrument = activity.instrument_id
        if activity.kind == "FILL":
            _require(instrument in prior_positions, "POSITION_BASELINE_MISMATCH")
            if raw_bases:
                prior_positions[instrument], prior_bases[instrument] = costed_fill(
                    prior_positions[instrument],
                    prior_bases[instrument],
                    activity.side,
                    activity.quantity,
                    activity.price,
                )
            else:
                prior_positions[instrument] = _fill_position(
                    prior_positions[instrument], activity.side, activity.quantity, activity.price
                )
        elif activity.kind == "CFEE":
            _require(not raw_bases, "POSITION_COST_BASIS_UNPROVEN")
            _require(instrument in prior_positions, "POSITION_BASELINE_MISMATCH")
            p = prior_positions[instrument]
            qty = _exact(p.quantity + activity.quantity)
            prior_positions[instrument] = replace(
                p, quantity=qty, average_price=p.average_price if qty else None
            )
    for instrument, p in prior_positions.items():
        current_position = local_positions[instrument]
        _require(
            (p.quantity, None if raw_bases else p.average_price, p.realized_pnl)
            == (
                current_position.quantity,
                None if raw_bases else current_position.average_price,
                current_position.realized_pnl,
            ),
            "POSITION_LEDGER_DRIFT",
        )
        if raw_bases:
            locked_basis = c.execute(
                text("SELECT public.paper_cost_basis_read(:id,:version)"),
                {"id": current_position.id, "version": current_position.ledger_version},
            ).scalar_one_or_none()
            _require(locked_basis == prior_bases[instrument], "POSITION_COST_BASIS_MISMATCH")
    for activity, digest in ordered:
        quantity_delta, cash_delta = Decimal(0), Decimal(0)
        instrument = activity.instrument_id
        if activity.kind == "FILL":
            _require(activity.order_id in by_provider, "EXTERNAL_FILL_MISMATCH")
            _, intent, source = by_provider[activity.order_id]
            _require(
                instrument == intent.instrument_id
                and activity.side == intent.side
                and instrument in positions
                and activity.quantity > 0
                and activity.price > 0,
                "FILL_IDENTITY_MISMATCH",
            )
            totals[activity.order_id] = _exact(totals[activity.order_id] + activity.quantity)
            costs[activity.order_id] = _exact(
                costs[activity.order_id] + activity.quantity * activity.price
            )
            _require(
                activity.cumulative_quantity == totals[activity.order_id]
                and activity.leaves_quantity == _exact(intent.quantity - totals[activity.order_id]),
                "FILL_HISTORY_INCOMPLETE",
            )
            cost = _exact(activity.quantity * activity.price)
            if intent.limit_price is not None and (
                (intent.side == "BUY" and activity.price > intent.limit_price)
                or (intent.side == "SELL" and activity.price < intent.limit_price)
            ):
                ready, reason = False, "FILL_LIMIT_PRICE_MISMATCH"
            if raw_bases:
                positions[instrument], updated_bases[instrument] = costed_fill(
                    positions[instrument],
                    updated_bases[instrument],
                    intent.side,
                    activity.quantity,
                    activity.price,
                )
            else:
                positions[instrument] = _fill_position(
                    positions[instrument], intent.side, activity.quantity, activity.price
                )
            quantity_delta = activity.quantity if intent.side == "BUY" else -activity.quantity
            cash_delta = -cost if intent.side == "BUY" else cost
        elif activity.kind == "FEE":
            _require(
                activity.net_amount is not None
                and activity.net_amount <= 0
                and activity.quantity in (None, Decimal(0)),
                "FEE_EVIDENCE_INVALID",
            )
            cash_delta = activity.net_amount
            ready, reason = False, "PAPER_FEE_AUTHORITY_UNKNOWN"
        elif activity.kind == "CFEE":
            _require(not raw_bases, "POSITION_COST_BASIS_UNPROVEN")
            _require(
                instrument in positions
                and activity.quantity is not None
                and activity.quantity < 0
                and activity.net_amount == 0,
                "CRYPTO_FEE_EVIDENCE_INVALID",
            )
            quantity_delta = activity.quantity
            p = positions[instrument]
            qty = _exact(p.quantity + quantity_delta)
            _require(qty >= 0, "CRYPTO_FEE_QUANTITY_MISMATCH")
            positions[instrument] = replace(
                p, quantity=qty, average_price=p.average_price if qty else None
            )
            ready, reason = False, "PAPER_FEE_AUTHORITY_UNKNOWN"
        else:
            raise _Mismatch("UNSUPPORTED_ACCOUNT_ACTIVITY")
        cash = _exact(cash + cash_delta)
        movements.append((activity, digest, _exact(cash_delta), instrument, _exact(quantity_delta)))
    for provider, (order, intent, source) in by_provider.items():
        _require(totals[provider] == source.filled_quantity, "FILL_PROOF_MISSING")
        _require(
            not (source.status is m.OrderStatus.FILLED and totals[provider] != intent.quantity)
            and not (source.status is m.OrderStatus.ACKNOWLEDGED and totals[provider] != 0)
            and not (
                source.status is m.OrderStatus.PARTIALLY_FILLED
                and not Decimal(0) < totals[provider] < intent.quantity
            )
            and not (
                totals[provider] == intent.quantity and source.status is not m.OrderStatus.FILLED
            ),
            "STATUS_FILL_MISMATCH",
        )
        # Cancellation/fill races may advance CANCELED to FILLED only with full fill proof.
        _require(
            order.status not in _TERMINAL
            or source.status == order.status
            or (
                order.status in (m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
                and source.status is m.OrderStatus.FILLED
                and source.filled_quantity > order.filled_quantity
            ),
            "TERMINAL_STATUS_CORRECTION",
        )
        # A fully filled order cannot become canceled or rejected later.
        _require(
            not (
                order.status is m.OrderStatus.FILLED and source.status is not m.OrderStatus.FILLED
            ),
            "TERMINAL_STATUS_CORRECTION",
        )
    broker_positions = {p.instrument_id: p for p in evidence.positions}
    _require(len(broker_positions) == len(evidence.positions), "POSITION_AMBIGUOUS_OR_MISSING")
    _require(set(broker_positions) <= set(positions), "EXTERNAL_POSITION_MISMATCH")
    for key, p in positions.items():
        source = broker_positions.get(key)
        _require(
            (source.quantity if source else Decimal(0)) == p.quantity, "POSITION_ACCOUNT_MISMATCH"
        )
        if source:
            pin = c.execute(
                text(
                    "SELECT provider_asset_id FROM public.broker_asset_bindings "
                    "WHERE account_id=:a AND instrument_id=:i"
                ),
                {"a": account_id, "i": key},
            ).scalar_one_or_none()
            _require(pin is not None and str(pin) == source.asset_id, "PROVIDER_ASSET_CHANGED")
            if raw_bases:
                _require(
                    source.cost_basis is not None and source.cost_basis == updated_bases[key],
                    "POSITION_COST_BASIS_MISMATCH",
                )
                # Accept the provider's rounded presentation only after the
                # independent total basis and exact quantity were reconciled.
                positions[key] = replace(p, average_price=source.avg_entry_price)
            else:
                _require(source.avg_entry_price == p.average_price, "POSITION_COST_BASIS_MISMATCH")
    _require(cash == evidence.cash, "CASH_ACCOUNT_MISMATCH")
    exposure = _exact(sum((p.market_value for p in evidence.positions), Decimal(0)))
    _require(_exact(cash + exposure) == evidence.equity, "EQUITY_ACCOUNT_MISMATCH")
    # Persisting evidence must exactly correspond to financial deltas already booked.
    return {
        "bases": bases,
        "positions": positions,
        "local_positions": local_positions,
        "movements": movements,
        "prior": prior,
        "by_provider": by_provider,
        "totals": totals,
        "costs": costs,
        "ready": ready,
        "reason": reason,
        "exposure": exposure,
        "cost_bases": updated_bases,
    }


def _book(tx, account_id, baseline, orders, evidence, plan, now, trace_id):
    c = tx.connection
    context = _context(tx, account_id)
    for instrument, p in plan["positions"].items():
        old = plan["local_positions"][instrument]
        if (p.quantity, p.average_price, p.realized_pnl) == (
            old.quantity,
            old.average_price,
            old.realized_pnl,
        ):
            continue
        new = replace(p, ledger_version=old.ledger_version + 1)
        _audit(
            tx,
            entity_id=old.id,
            version=new.ledger_version,
            trace_id=trace_id,
            action="paper_position",
            reason="PAPER_ACTIVITY_BOOKED",
            now=now,
        )
        if not tx.replace(new, expected_version=old.ledger_version):
            raise RuntimeError("POSITION_VERSION_CONFLICT")
        if plan["cost_bases"]:
            identity = new.id + ":cost:" + str(new.ledger_version)
            _audit(
                tx,
                entity_id=identity,
                version=1,
                trace_id=trace_id,
                action="paper_position_cost",
                reason="PAPER_ACTIVITY_BOOKED",
                now=now,
            )
            digest = _payload_digest(
                {
                    "basis": str(plan["cost_bases"][instrument]),
                    "quantity": str(new.quantity),
                    "observed_at": evidence.observed_at.isoformat(),
                    "activities": sorted(d for _, d, *_ in plan["movements"]),
                }
            )
            recorded = c.execute(
                text(
                    "SELECT public.paper_cost_basis_append("
                    ":id,:version,:quantity,:basis,:hash,:trace)"
                ),
                {
                    "id": new.id,
                    "version": new.ledger_version,
                    "quantity": new.quantity,
                    "basis": plan["cost_bases"][instrument],
                    "hash": digest,
                    "trace": trace_id,
                },
            ).scalar_one()
            _require(recorded, "POSITION_COST_BASIS_MISMATCH")
    changed = any(activity.id not in plan["prior"] for activity, *_ in plan["movements"])
    refresh_authority = plan["ready"] and (
        tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"]).quality is not m.Quality.VALID
        or tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"]).reconciliation
        is not m.Reconciliation.CURRENT
    )
    old_snapshot = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
    if (
        changed
        or refresh_authority
        or (old_snapshot.cash, old_snapshot.equity, old_snapshot.exposure)
        != (
            evidence.cash,
            evidence.equity,
            plan["exposure"],
        )
    ):
        version = old_snapshot.ledger_version + 1
        snapshot = m.PortfolioSnapshot(
            _id("paper-portfolio", f"{account_id}:{version}"),
            "USD",
            evidence.equity,
            evidence.cash,
            plan["exposure"],
            now,
            version,
            m.Quality.VALID,
            m.Reconciliation.CURRENT if plan["ready"] else m.Reconciliation.PENDING,
        )
        _audit(
            tx,
            entity_id=snapshot.id,
            version=version,
            trace_id=trace_id,
            action="paper_portfolio",
            reason="PAPER_ACTIVITY_BOOKED",
            now=now,
        )
        tx.insert(snapshot)
        _audit(
            tx,
            entity_id=account_id,
            version=version,
            trace_id=trace_id,
            action="paper_account",
            reason="PAPER_ACTIVITY_BOOKED",
            now=now,
        )
        c.execute(
            text("UPDATE public.accounts SET ledger_version=:v WHERE id=:a"),
            {"v": version, "a": account_id},
        )
        _update_context(
            tx,
            context,
            account_id=account_id,
            trace_id=trace_id,
            now=now,
            reason="PAPER_ACTIVITY_BOOKED",
            snapshot=snapshot,
        )
    for activity, digest, cash_delta, instrument, qty_delta in plan["movements"]:
        if activity.id in plan["prior"]:
            continue
        identity = _id("paper-activity", f"{account_id}:{activity.id}")
        linked = plan["by_provider"][activity.order_id] if activity.kind == "FILL" else None
        local_order_id = linked[0].id if linked else None
        activity_trace = linked[1].trace_id if linked else trace_id
        _audit(
            tx,
            entity_id=identity,
            version=1,
            trace_id=activity_trace,
            action="paper_activity",
            reason="PAPER_ACTIVITY_BOOKED",
            now=now,
        )
        c.execute(
            text("""INSERT INTO public.paper_activity_evidence
          (id,account_id,activity_id,activity_kind,evidence_hash,evidence,order_id,
           occurred_at,activity_date,cash_delta,instrument_id,quantity_delta,actor_id,trace_id)
          VALUES (:id,:a,:activity,:kind,:hash,CAST(:evidence AS jsonb),:order,
                  :at,:day,:cash,:instrument,:qty,:actor,:trace)"""),
            {
                "id": identity,
                "a": account_id,
                "activity": activity.id,
                "kind": activity.kind,
                "hash": digest,
                "evidence": json.dumps(_activity_payload(activity)),
                "order": local_order_id,
                "at": activity.occurred_at,
                "day": activity.activity_date,
                "cash": cash_delta,
                "instrument": instrument,
                "qty": qty_delta,
                "actor": _ACTOR,
                "trace": activity_trace,
            },
        )
    for provider, (order, intent, source) in plan["by_provider"].items():
        decision = tx.get(m.RiskDecision, intent.risk_decision_id)
        reservation = tx.get(m.RiskReservation, decision.reservation_id)
        original_notional, basis_exists = plan["bases"][order.id]
        if not basis_exists:
            ensure_paper_order_basis(
                tx, account_id, order, intent, reservation, now, intent.trace_id
            )
        new = replace(
            order,
            broker_order_id=provider,
            status=source.status,
            filled_quantity=source.filled_quantity,
            version=order.version + 1,
            updated_at=now,
        )
        # Fee uncertainty does not erase confirmed monotonic order evidence.
        if (new.broker_order_id, new.status, new.filled_quantity) != (
            order.broker_order_id,
            order.status,
            order.filled_quantity,
        ):
            _audit(
                tx,
                entity_id=order.id,
                version=new.version,
                trace_id=intent.trace_id,
                action="paper_order",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            if not tx.replace(new, expected_version=order.version):
                raise RuntimeError("ORDER_VERSION_CONFLICT")
        seed = (
            f"{order.id}:{source.status}:{source.filled_quantity}:"
            f"{plan['costs'][provider]}:{plan['ready']}"
        )
        report_id = _id("paper-report", seed)
        if tx.get(m.ExecutionReport, report_id) is None:
            quantity = source.filled_quantity
            with localcontext() as arithmetic:
                arithmetic.prec = 80
                average = _exact(plan["costs"][provider] / quantity) if quantity else None
            report = m.ExecutionReport(
                report_id,
                order.id,
                source.status,
                intent.quantity,
                quantity,
                m.Reconciliation.CURRENT if plan["ready"] else m.Reconciliation.PENDING,
                now,
                now,
                intent.trace_id,
                average_fill_price=average,
                fee=Decimal(0) if plan["ready"] else None,
                reason_code=None if plan["ready"] else "PAPER_FEE_AUTHORITY_UNKNOWN",
            )
            _audit(
                tx,
                entity_id=report.id,
                version=1,
                trace_id=intent.trace_id,
                action="paper_report",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            tx.insert(report, adapter="alpaca-paper")
            persist(
                c,
                m.EventEnvelope(
                    _id("paper-report-outbox", report.id),
                    "execution.reported.v1",
                    now,
                    report.id,
                    1,
                    intent.trace_id,
                    report,
                ),
            )
        if not plan["ready"]:
            desired = m.ReservationState.HELD_UNKNOWN
            remaining = reservation.reserved_notional
        elif source.status in _TERMINAL:
            desired = (
                m.ReservationState.CONSUMED
                if source.filled_quantity
                else m.ReservationState.RELEASED
            )
            remaining = Decimal(0)
        else:
            desired = m.ReservationState.ACTIVE
            with localcontext() as arithmetic:
                arithmetic.prec = 80
                remaining = _exact(
                    original_notional * (intent.quantity - source.filled_quantity) / intent.quantity
                )
        if (reservation.state, reservation.reserved_notional) != (desired, remaining):
            next_reservation = replace(
                reservation,
                state=desired,
                reserved_notional=remaining,
                version=reservation.version + 1,
                released_at=now
                if desired in (m.ReservationState.CONSUMED, m.ReservationState.RELEASED)
                else None,
            )
            _audit(
                tx,
                entity_id=reservation.id,
                version=next_reservation.version,
                trace_id=intent.trace_id,
                action="paper_reservation",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            if not tx.replace(next_reservation, expected_version=reservation.version):
                raise RuntimeError("RESERVATION_VERSION_CONFLICT")
