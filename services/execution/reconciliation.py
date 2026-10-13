"""Read-only FakeBroker recovery and atomic, fail-closed fill accounting."""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.brokers.fake import FakeBrokerAdapter
from packages.brokers.reports import event_hash, normalized_report
from packages.domain import models as m
from packages.observability.outbox import persist
from packages.persistence.ledger import PostgresLedger
from services.execution.gates import _now

_NS = uuid.UUID("155230b4-6361-496d-9acf-c61b833c2e57")


def _id(kind: str, seed: str) -> str:
    return str(uuid.uuid5(_NS, f"{kind}:{seed}"))


def _exact(value: Decimal) -> Decimal:
    raw = format(value, "f")
    if "." in raw:
        raw = raw.rstrip("0").rstrip(".")
    return m.decimal_value(raw)


def _audit(
    tx,
    *,
    entity_id: str,
    version: int,
    trace_id: str,
    action: str,
    reason: str,
    now,
    policy_version: int | None = None,
) -> None:
    tx.insert(
        m.AuditEvent(
            id=_id(f"audit:{action}:{version}", entity_id),
            occurred_at=now,
            actor_id="execution-reconciliation",
            action=f"execution.{action}",
            reason_codes=(reason,),
            entity_id=entity_id,
            entity_version=version,
            trace_id=trace_id,
            policy_version=policy_version,
            outcome=reason,
        )
    )


def _context(tx, account_id: str):
    return (
        tx.connection.execute(
            text("""
        SELECT * FROM public.risk_account_context WHERE account_id=:account FOR UPDATE
    """),
            {"account": account_id},
        )
        .mappings()
        .one_or_none()
    )


def _update_context(
    tx,
    context,
    *,
    account_id: str,
    trace_id: str,
    now,
    reason: str,
    snapshot: m.PortfolioSnapshot | None = None,
    policy_version: int | None = None,
) -> None:
    version = context["version"] + 1
    _audit(
        tx,
        entity_id=account_id,
        version=version,
        trace_id=trace_id,
        action="reconciliation_pause",
        reason=reason,
        now=now,
        policy_version=policy_version,
    )
    tx.connection.execute(
        text("""
        UPDATE public.risk_account_context
        SET version=:version,safety_state=:state,reconciliation_current=false,
            portfolio_snapshot_id=:snapshot,portfolio_version=:portfolio_version,
            actor_id='execution-reconciliation',trace_id=:trace
        WHERE account_id=:account
    """),
        {
            "version": version,
            "state": "HALTED" if context["safety_state"] == "HALTED" else "PAUSE_NEW",
            "snapshot": snapshot.id if snapshot else context["portfolio_snapshot_id"],
            "portfolio_version": snapshot.ledger_version
            if snapshot
            else context["portfolio_version"],
            "trace": trace_id,
            "account": account_id,
        },
    )


def _fail(
    tx,
    *,
    order: m.BrokerOrder,
    reservation: m.RiskReservation,
    context,
    account_id: str,
    reason: str,
    now,
) -> m.BrokerOrder:
    decision = tx.get(m.RiskDecision, reservation.risk_decision_id)
    policy_version = decision.policy_version if decision else None
    failure_id = _id("failure-report", f"{order.id}:{reason}")
    if tx.get(m.ExecutionReport, failure_id) is None:
        report = m.ExecutionReport(
            id=failure_id,
            order_id=order.id,
            status=m.OrderStatus.UNKNOWN,
            requested_quantity=order.requested_quantity,
            filled_quantity=order.filled_quantity,
            reconciliation=m.Reconciliation.MISMATCH,
            occurred_at=now,
            received_at=now,
            trace_id=order.trace_id,
            reason_code=reason,
        )
        _audit(
            tx,
            entity_id=report.id,
            version=1,
            trace_id=order.trace_id,
            action="reconciliation_failure",
            reason=reason,
            now=now,
            policy_version=policy_version,
        )
        tx.insert(report, adapter="fake")
        persist(
            tx.connection,
            m.EventEnvelope(
                event_id=_id("failure-outbox", report.id),
                event_type="execution.reported.v1",
                occurred_at=now,
                aggregate_id=report.id,
                aggregate_version=1,
                trace_id=order.trace_id,
                payload=report,
            ),
        )
    if order.status is not m.OrderStatus.UNKNOWN:
        new = replace(
            order, status=m.OrderStatus.UNKNOWN, version=order.version + 1, updated_at=now
        )
        _audit(
            tx,
            entity_id=order.id,
            version=new.version,
            trace_id=order.trace_id,
            action="reconciliation_unknown",
            reason=reason,
            now=now,
            policy_version=policy_version,
        )
        if not tx.replace(new, expected_version=order.version):
            raise RuntimeError("ORDER_VERSION_CONFLICT")
        order = new
    if reservation.state not in (
        m.ReservationState.HELD_UNKNOWN,
        m.ReservationState.RELEASED,
        m.ReservationState.CONSUMED,
    ):
        held = replace(
            reservation, state=m.ReservationState.HELD_UNKNOWN, version=reservation.version + 1
        )
        _audit(
            tx,
            entity_id=reservation.id,
            version=held.version,
            trace_id=order.trace_id,
            action="reconciliation_hold",
            reason=reason,
            now=now,
            policy_version=policy_version,
        )
        if not tx.replace(held, expected_version=reservation.version):
            raise RuntimeError("RESERVATION_VERSION_CONFLICT")
    if context is not None and (
        context["reconciliation_current"] or context["safety_state"] == "RUNNING"
    ):
        _update_context(
            tx,
            context,
            account_id=account_id,
            trace_id=order.trace_id,
            now=now,
            reason=reason,
            policy_version=policy_version,
        )
    return order


def _source(broker: FakeBrokerAdapter, order: m.BrokerOrder):
    """Resolve both identifiers through fixture read methods; never submit or cancel."""
    try:
        source = broker.reconcile(order.client_order_id, order.broker_order_id)
        by_client = broker.order_status(order.client_order_id)
        by_broker = broker.by_broker_id(order.broker_order_id) if order.broker_order_id else None
        reports = broker.reports(order.client_order_id)
    except Exception:
        return None, (), "BROKER_UNAVAILABLE_OR_MISMATCH"
    if (
        type(source) is not m.BrokerOrder
        or type(by_client) is not m.BrokerOrder
        or source != by_client
        or not isinstance(reports, tuple)
        or any(type(item) is not m.ExecutionReport for item in reports)
    ):
        return None, (), "BROKER_ORDER_MISSING_OR_AMBIGUOUS"
    if source.status is m.OrderStatus.UNKNOWN or source.broker_order_id is None:
        return None, (), "BROKER_STATUS_UNKNOWN"
    try:
        discovered = broker.by_broker_id(source.broker_order_id)
    except Exception:
        return None, (), "BROKER_ID_MISMATCH"
    if type(discovered) is not m.BrokerOrder or discovered != source:
        return None, (), "BROKER_ID_MISMATCH"
    if order.broker_order_id is not None and (
        type(by_broker) is not m.BrokerOrder or by_broker != source
    ):
        return None, (), "BROKER_ID_MISMATCH"
    return source, reports, None


def _snapshot(
    tx,
    *,
    account_id: str,
    context,
    intent: m.OrderIntent,
    position: m.Position,
    fill: Decimal,
    cash_delta: Decimal,
    trace_id: str,
    policy_version: int,
    now,
) -> m.PortfolioSnapshot:
    old = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
    state_id = tx.connection.execute(
        text("""
        SELECT market_state_id FROM public.trade_proposals WHERE id=:proposal
    """),
        {"proposal": intent.proposal_id},
    ).scalar_one()
    state = tx.get(m.MarketState, state_id)
    account = (
        tx.connection.execute(
            text("""
        SELECT currency,ledger_version FROM public.accounts WHERE id=:account FOR UPDATE
    """),
            {"account": account_id},
        )
        .mappings()
        .one()
    )
    if (
        old is None
        or state is None
        or old.currency != account["currency"]
        or position.currency != old.currency
        or state.instrument_id != intent.instrument_id
        or context["portfolio_version"] != old.ledger_version
        or account["ledger_version"] != old.ledger_version
    ):
        raise ValueError("PORTFOLIO_BASIS_MISMATCH")
    with localcontext() as ctx:
        ctx.prec = 80
        exposure_delta = _exact(
            fill * state.bar.close * (Decimal("1") if intent.side == "BUY" else Decimal("-1"))
        )
        next_version = old.ledger_version + 1
        snapshot = m.PortfolioSnapshot(
            id=_id("portfolio", f"{account_id}:{next_version}"),
            currency=old.currency,
            equity=_exact(old.equity + cash_delta + exposure_delta),
            cash=_exact(old.cash + cash_delta),
            exposure=_exact(old.exposure + exposure_delta),
            as_of=now,
            ledger_version=next_version,
            quality=m.Quality.STALE,
            reconciliation=m.Reconciliation.PENDING,
        )
    _audit(
        tx,
        entity_id=snapshot.id,
        version=next_version,
        trace_id=trace_id,
        action="portfolio_fill",
        reason="FILL_BOOKED",
        now=now,
        policy_version=policy_version,
    )
    tx.insert(snapshot)
    _audit(
        tx,
        entity_id=account_id,
        version=next_version,
        trace_id=trace_id,
        action="account_fill",
        reason="FILL_BOOKED",
        now=now,
        policy_version=policy_version,
    )
    tx.connection.execute(
        text("""
        UPDATE public.accounts SET ledger_version=:version WHERE id=:account
    """),
        {"version": next_version, "account": account_id},
    )
    return snapshot


def reconcile_order(
    *, ledger: PostgresLedger, account_id: str, order_id: str, broker: FakeBrokerAdapter
) -> m.BrokerOrder:
    """Reconcile one durable order against the exact local FakeBroker instance.

    UNKNOWN is a committed, auditable result. A fresh authoritative account
    snapshot and operator resume remain separate control operations.
    """
    if type(broker) is not FakeBrokerAdapter:
        raise ValueError("FAKE_BROKER_REQUIRED")
    with ledger.transaction() as tx:
        c = tx.connection
        if c.execute(text("SELECT current_user")).scalar_one() != "atl_execution":
            raise PermissionError("EXECUTION_ROLE_REQUIRED")
        tx.lock_account(account_id)
        order = tx.get(m.BrokerOrder, order_id)
        if order is None:
            raise LookupError("ORDER_MISSING")
        now = _now(c)
        intent = tx.get(m.OrderIntent, order.intent_id)
        decision = tx.get(m.RiskDecision, intent.risk_decision_id) if intent else None
        reservation = (
            tx.get(m.RiskReservation, decision.reservation_id)
            if decision and decision.reservation_id
            else None
        )
        context = _context(tx, account_id)
        owner = (
            c.execute(
                text("""
            SELECT i.account_id AS intent_owner,r.account_id AS reservation_owner,
                   b.adapter FROM public.broker_orders b
            JOIN public.order_intents i ON i.id=b.intent_id
            JOIN public.risk_reservations r ON r.risk_decision_id=i.risk_decision_id
            WHERE b.id=:order
        """),
                {"order": order_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            context is None
            or reservation is None
            or intent is None
            or decision is None
            or owner is None
        ):
            raise RuntimeError("RECONCILIATION_LINK_MISSING")
        if (
            c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() != 1
            or owner["intent_owner"] != account_id
            or owner["reservation_owner"] != account_id
            or owner["adapter"] != "fake"
            or order.client_order_id != intent.client_order_id
            or order.requested_quantity != intent.quantity
            or len({order.trace_id, intent.trace_id, decision.trace_id, reservation.trace_id}) != 1
        ):
            return _fail(
                tx,
                order=order,
                reservation=reservation,
                context=context,
                account_id=account_id,
                reason="OWNERSHIP_OR_IDENTITY_MISMATCH",
                now=now,
            )
        source, incoming, failure = _source(broker, order)
        if failure:
            return _fail(
                tx,
                order=order,
                reservation=reservation,
                context=context,
                account_id=account_id,
                reason=failure,
                now=now,
            )
        if (
            source.intent_id != intent.id
            or source.client_order_id != intent.client_order_id
            or source.requested_quantity != intent.quantity
            or source.trace_id != intent.trace_id
            or (
                order.broker_order_id is not None
                and source.broker_order_id != order.broker_order_id
            )
            or source.filled_quantity < order.filled_quantity
        ):
            return _fail(
                tx,
                order=order,
                reservation=reservation,
                context=context,
                account_id=account_id,
                reason="BROKER_ORDER_MISMATCH",
                now=now,
            )
        if (
            source.status
            not in (
                m.OrderStatus.ACKNOWLEDGED,
                m.OrderStatus.PARTIALLY_FILLED,
                m.OrderStatus.CANCEL_PENDING,
                m.OrderStatus.FILLED,
                m.OrderStatus.CANCELED,
                m.OrderStatus.REJECTED,
            )
            or (
                order.status
                in (m.OrderStatus.FILLED, m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
                and source.status
                in (
                    m.OrderStatus.ACKNOWLEDGED,
                    m.OrderStatus.PARTIALLY_FILLED,
                    m.OrderStatus.CANCEL_PENDING,
                )
            )
            or (
                reservation.state in (m.ReservationState.RELEASED, m.ReservationState.CONSUMED)
                and source.status
                in (
                    m.OrderStatus.ACKNOWLEDGED,
                    m.OrderStatus.PARTIALLY_FILLED,
                    m.OrderStatus.CANCEL_PENDING,
                )
            )
            or (
                source.status is m.OrderStatus.PARTIALLY_FILLED
                and not Decimal("0") < source.filled_quantity < intent.quantity
            )
            or (source.status is m.OrderStatus.FILLED and source.filled_quantity == 0)
            or (
                source.filled_quantity == intent.quantity
                and source.status is not m.OrderStatus.FILLED
            )
        ):
            return _fail(
                tx,
                order=order,
                reservation=reservation,
                context=context,
                account_id=account_id,
                reason="STATUS_REGRESSION_OR_MISMATCH",
                now=now,
            )
        try:
            normalized = [
                normalized_report(
                    item,
                    source_order_id=source.id,
                    local_order_id=order.id,
                    requested_quantity=intent.quantity,
                    trace_id=intent.trace_id,
                )
                for item in incoming
            ]
            if not normalized:
                raise ValueError("BROKER_REPORT_MISSING")
            if any(r.fee != 0 for r in normalized):
                raise ValueError("UNSUPPORTED_FAKE_FEE")
            seen = {}
            for item in normalized:
                digest = event_hash(item)
                prior = seen.setdefault(item.broker_event_id, digest)
                if prior != digest:
                    raise ValueError("BROKER_EVENT_CONFLICT")
                existing_id = c.execute(
                    text("""
                    SELECT id FROM public.execution_reports
                    WHERE adapter='fake' AND broker_event_id=:event
                """),
                    {"event": item.broker_event_id},
                ).scalar_one_or_none()
                if existing_id and event_hash(tx.get(m.ExecutionReport, existing_id)) != digest:
                    raise ValueError("BROKER_EVENT_CONFLICT")
            fill_reports = sorted(
                (
                    r
                    for r in normalized
                    if r.filled_quantity > 0 and r.average_fill_price is not None
                ),
                key=lambda r: (r.filled_quantity, r.occurred_at, r.broker_event_id),
            )
            averages = {}
            for report in fill_reports:
                previous_average = averages.setdefault(
                    report.filled_quantity, report.average_fill_price
                )
                if previous_average != report.average_fill_price:
                    raise ValueError("CUMULATIVE_COST_CONFLICT")
                durable_averages = (
                    c.execute(
                        text("""
                    SELECT average_fill_price FROM public.execution_reports
                    WHERE order_id=:order AND filled_quantity=:quantity
                      AND average_fill_price IS NOT NULL
                """),
                        {"order": order.id, "quantity": report.filled_quantity},
                    )
                    .scalars()
                    .all()
                )
                if any(value != report.average_fill_price for value in durable_averages):
                    raise ValueError("CUMULATIVE_COST_CONFLICT")
            # A previously booked fill has its own durable priced report. A
            # later cancel acknowledgement may omit the average price.
            proof = max(
                order.filled_quantity,
                max((r.filled_quantity for r in fill_reports), default=Decimal("0")),
            )
            reported_quantity = max(r.filled_quantity for r in normalized)
            if reported_quantity != source.filled_quantity or proof != source.filled_quantity:
                raise ValueError("FILL_PROOF_MISSING")
            if source.status in (
                m.OrderStatus.FILLED,
                m.OrderStatus.CANCELED,
                m.OrderStatus.REJECTED,
            ) and not any(
                r.status == source.status and r.filled_quantity == source.filled_quantity
                for r in normalized
            ):
                raise ValueError("TERMINAL_PROOF_MISSING")
            if source.status is m.OrderStatus.FILLED and proof != intent.quantity:
                raise ValueError("FILLED_QUANTITY_MISMATCH")
            if source.status is m.OrderStatus.ACKNOWLEDGED and proof > 0:
                raise ValueError("STATUS_FILL_MISMATCH")
            position_ids = (
                c.execute(
                    text("""
                SELECT id FROM public.positions
                WHERE account_id=:account AND instrument_id=:instrument
                ORDER BY id FOR UPDATE
            """),
                    {"account": account_id, "instrument": intent.instrument_id},
                )
                .scalars()
                .all()
            )
            if len(position_ids) != 1:
                raise ValueError("POSITION_AMBIGUOUS_OR_MISSING")
            position = tx.get(m.Position, position_ids[0])
            account_currency = c.execute(
                text("""
                SELECT currency FROM public.accounts WHERE id=:account
            """),
                {"account": account_id},
            ).scalar_one()
            if position.currency != account_currency:
                raise ValueError("POSITION_CURRENCY_MISMATCH")
            prior_fill = order.filled_quantity
            prior_report = c.execute(
                text("""
                SELECT average_fill_price FROM public.execution_reports
                WHERE order_id=:order AND filled_quantity=:filled
                  AND average_fill_price IS NOT NULL
                ORDER BY occurred_at DESC,id DESC LIMIT 1
            """),
                {"order": order.id, "filled": prior_fill},
            ).scalar_one_or_none()
            if prior_fill > 0 and prior_report is None:
                raise ValueError("BOOKED_COST_BASIS_MISSING")
            with localcontext() as ctx:
                ctx.prec = 80
                previous_total = _exact(prior_fill * prior_report) if prior_fill else Decimal("0")
            next_position = position
            fill_steps = []
            for report in fill_reports:
                if report.filled_quantity <= prior_fill:
                    continue
                with localcontext() as ctx:
                    ctx.prec = 80
                    new_total = _exact(report.filled_quantity * report.average_fill_price)
                    delta = _exact(report.filled_quantity - prior_fill)
                    cost = _exact(new_total - previous_total)
                    if cost <= 0:
                        raise ValueError("FILL_COST_INVALID")
                    if intent.side == "BUY":
                        new_qty = _exact(next_position.quantity + delta)
                        if next_position.quantity and next_position.average_price is None:
                            raise ValueError("POSITION_COST_BASIS_MISSING")
                        old_basis = (
                            _exact(next_position.quantity * next_position.average_price)
                            if next_position.quantity
                            else Decimal("0")
                        )
                        average = _exact((old_basis + cost) / new_qty)
                        next_position = replace(
                            next_position,
                            quantity=new_qty,
                            average_price=average,
                            ledger_version=next_position.ledger_version + 1,
                        )
                        cash_delta = _exact(-cost)
                    else:
                        if next_position.average_price is None or delta > next_position.quantity:
                            raise ValueError("SELL_BASIS_OR_QUANTITY_INVALID")
                        basis = _exact(delta * next_position.average_price)
                        new_qty = _exact(next_position.quantity - delta)
                        next_position = replace(
                            next_position,
                            quantity=new_qty,
                            average_price=next_position.average_price if new_qty else None,
                            realized_pnl=_exact(next_position.realized_pnl + cost - basis),
                            ledger_version=next_position.ledger_version + 1,
                        )
                        cash_delta = cost
                    fill_steps.append((report, next_position, delta, cash_delta))
                    prior_fill = report.filled_quantity
                    previous_total = new_total
            with localcontext() as ctx:
                ctx.prec = 80
                new_reserved = (
                    Decimal("0")
                    if source.status
                    in (m.OrderStatus.FILLED, m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
                    else _exact(
                        reservation.reserved_notional
                        * (intent.quantity - source.filled_quantity)
                        / (intent.quantity - order.filled_quantity)
                    )
                )
            if new_reserved < 0:
                raise ValueError("RESERVATION_INVALID")
            if fill_steps:
                old_snapshot = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
                market_state = tx.get(
                    m.MarketState,
                    c.execute(
                        text("""
                        SELECT market_state_id FROM public.trade_proposals WHERE id=:proposal
                    """),
                        {"proposal": intent.proposal_id},
                    ).scalar_one(),
                )
                account_row = (
                    c.execute(
                        text("""
                    SELECT currency,ledger_version FROM public.accounts WHERE id=:account
                """),
                        {"account": account_id},
                    )
                    .mappings()
                    .one()
                )
                if (
                    old_snapshot is None
                    or market_state is None
                    or market_state.instrument_id != intent.instrument_id
                    or old_snapshot.currency != account_row["currency"]
                    or old_snapshot.currency != position.currency
                    or old_snapshot.ledger_version != context["portfolio_version"]
                    or old_snapshot.ledger_version != account_row["ledger_version"]
                ):
                    raise ValueError("PORTFOLIO_BASIS_MISMATCH")
                with localcontext() as ctx:
                    ctx.prec = 80
                    preview = old_snapshot
                    for _, _, delta, cash_delta in fill_steps:
                        exposure_delta = _exact(
                            delta
                            * market_state.bar.close
                            * (Decimal("1") if intent.side == "BUY" else Decimal("-1"))
                        )
                        preview = m.PortfolioSnapshot(
                            id=_id("preview", str(preview.ledger_version + 1)),
                            currency=preview.currency,
                            equity=_exact(preview.equity + cash_delta + exposure_delta),
                            cash=_exact(preview.cash + cash_delta),
                            exposure=_exact(preview.exposure + exposure_delta),
                            as_of=now,
                            ledger_version=preview.ledger_version + 1,
                            quality=m.Quality.STALE,
                            reconciliation=m.Reconciliation.PENDING,
                        )
        except (ValueError, ArithmeticError, ZeroDivisionError) as exc:
            reason = str(exc) if str(exc).isupper() else "RECONCILIATION_MISMATCH"
            return _fail(
                tx,
                order=order,
                reservation=reservation,
                context=context,
                account_id=account_id,
                reason=reason,
                now=now,
            )
        # The account lock serializes these ledger effects with risk and submit.
        snapshot = None
        for _report, updated_position, delta, cash_delta in fill_steps:
            _audit(
                tx,
                entity_id=position.id,
                version=updated_position.ledger_version,
                trace_id=intent.trace_id,
                action="position_fill",
                reason="FILL_BOOKED",
                now=now,
                policy_version=decision.policy_version,
            )
            if not tx.replace(updated_position, expected_version=position.ledger_version):
                raise RuntimeError("POSITION_VERSION_CONFLICT")
            position = updated_position
            snapshot = _snapshot(
                tx,
                account_id=account_id,
                context=context,
                intent=intent,
                position=position,
                fill=delta,
                cash_delta=cash_delta,
                trace_id=intent.trace_id,
                policy_version=decision.policy_version,
                now=now,
            )
            context = {
                **context,
                "portfolio_snapshot_id": snapshot.id,
                "portfolio_version": snapshot.ledger_version,
            }
        for report in normalized:
            exists = c.execute(
                text("""
                SELECT id FROM public.execution_reports
                WHERE adapter='fake' AND broker_event_id=:event
            """),
                {"event": report.broker_event_id},
            ).scalar_one_or_none()
            if exists:
                continue
            _audit(
                tx,
                entity_id=report.id,
                version=1,
                trace_id=intent.trace_id,
                action="reconciliation_report",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            tx.insert(report, adapter="fake")
            persist(
                c,
                m.EventEnvelope(
                    event_id=_id("outbox", report.id),
                    event_type="execution.reported.v1",
                    occurred_at=now,
                    aggregate_id=report.id,
                    aggregate_version=1,
                    trace_id=intent.trace_id,
                    payload=report,
                ),
            )
        new_status = source.status
        new_order = replace(
            order,
            broker_order_id=source.broker_order_id,
            status=new_status,
            filled_quantity=source.filled_quantity,
            version=order.version + 1,
            updated_at=now,
        )
        if (
            new_order.status != order.status
            or new_order.filled_quantity != order.filled_quantity
            or new_order.broker_order_id != order.broker_order_id
        ):
            _audit(
                tx,
                entity_id=order.id,
                version=new_order.version,
                trace_id=intent.trace_id,
                action="reconciliation_order",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            if not tx.replace(new_order, expected_version=order.version):
                raise RuntimeError("ORDER_VERSION_CONFLICT")
        else:
            new_order = order
        desired = (
            (m.ReservationState.CONSUMED if source.filled_quantity else m.ReservationState.RELEASED)
            if source.status
            in (m.OrderStatus.FILLED, m.OrderStatus.CANCELED, m.OrderStatus.REJECTED)
            else m.ReservationState.ACTIVE
        )
        if new_reserved != reservation.reserved_notional or desired != reservation.state:
            next_reservation = replace(
                reservation,
                reserved_notional=new_reserved,
                state=desired,
                released_at=now
                if desired in (m.ReservationState.CONSUMED, m.ReservationState.RELEASED)
                else None,
                version=reservation.version + 1,
            )
            _audit(
                tx,
                entity_id=reservation.id,
                version=next_reservation.version,
                trace_id=intent.trace_id,
                action="reconciliation_reservation",
                reason="BROKER_EVIDENCE",
                now=now,
                policy_version=decision.policy_version,
            )
            if not tx.replace(next_reservation, expected_version=reservation.version):
                raise RuntimeError("RESERVATION_VERSION_CONFLICT")
        if snapshot is not None:
            _update_context(
                tx,
                context,
                account_id=account_id,
                trace_id=intent.trace_id,
                now=now,
                reason="FILL_BOOKED",
                snapshot=snapshot,
                policy_version=decision.policy_version,
            )
        return new_order
