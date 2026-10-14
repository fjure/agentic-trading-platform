"""Private PostgreSQL guest with exclusively synthetic process-local broker state."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext


class Crash(BaseException):
    pass


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace, seed=None):
        return f"{namespace}:{seed}"


def checks(ledger, execution_ledger, engine, now):
    from sqlalchemy import event, text

    from packages.brokers.fake import FakeBrokerAdapter, FakeFault
    from packages.domain import models as m
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.execution.intents import submit_once
    from services.execution.reconciliation import reconcile_order

    scenario = sys.argv[2]
    proposal_id = "p-0"
    if scenario == "sell":
        with ledger.transaction() as tx:
            position = tx.get(m.Position, "pos")
            tx.insert(
                m.AuditEvent(
                    "sell-pos", now, "fixture", "fixture", ("TEST",), position.id, 2, "a" * 32
                )
            )
            assert tx.replace(
                replace(
                    position, quantity=Decimal("1"), average_price=Decimal("100"), ledger_version=2
                ),
                expected_version=1,
            )
            portfolio = replace(
                tx.get(m.PortfolioSnapshot, "pf"), id="sell-pf", exposure=Decimal("100")
            )
            tx.insert(
                m.AuditEvent(
                    "sell-pf-audit", now, "fixture", "fixture", ("TEST",), portfolio.id, 1, "a" * 32
                )
            )
            tx.insert(portfolio)
            state = replace(
                tx.get(m.MarketState, "ms"),
                id="sell-ms",
                portfolio=portfolio,
                position=tx.get(m.Position, "pos"),
            )
            tx.insert(state)
            tx.insert(
                m.AuditEvent(
                    "sell-context", now, "fixture", "fixture", ("TEST",), "paper", 2, "a" * 32
                )
            )
            tx.connection.execute(
                text(
                    "UPDATE public.risk_account_context SET version=2,"
                    "portfolio_snapshot_id='sell-pf' WHERE account_id='paper'"
                )
            )
            decision = tx.get(m.DecisionResult, "dec")
            flat = replace(
                decision,
                id="flat-dec",
                market_state_id=state.id,
                choice=m.Choice.FLAT,
                distribution={
                    "LONG": Decimal("0.1"),
                    "FLAT": Decimal("0.8"),
                    "ABSTAIN": Decimal("0.1"),
                },
            )
            tx.insert(
                m.AuditEvent("sell-dec", now, "fixture", "fixture", ("TEST",), flat.id, 1, "a" * 32)
            )
            tx.insert(flat)
            proposal = m.TradeProposal(
                "sell-proposal",
                flat.id,
                state.id,
                "spy",
                m.Target.FLAT,
                Decimal("1"),
                now,
                now + timedelta(seconds=30),
                "a" * 32,
            )
            tx.insert(
                m.AuditEvent(
                    "sell-proposal-audit",
                    now,
                    "fixture",
                    "fixture",
                    ("TEST",),
                    proposal.id,
                    1,
                    "a" * 32,
                )
            )
            tx.insert(proposal)
            proposal_id = proposal.id
    risk = reserve(
        ledger=execution_ledger, account_id="paper", proposal_id=proposal_id, limits=RiskLimits()
    )
    assert risk.approved
    with ledger.transaction() as tx:
        intent_id = tx.connection.execute(
            text("SELECT id FROM public.order_intents WHERE proposal_id=:proposal"),
            {"proposal": proposal_id},
        ).scalar_one()
        intent = tx.get(m.OrderIntent, intent_id)
    cap = m.BrokerCapabilities(
        "fake",
        "spy",
        1,
        paper=m.Capability.CONFIRMED,
        regular_session=m.Capability.CONFIRMED,
        market_order=m.Capability.CONFIRMED,
        limit_order=m.Capability.CONFIRMED,
        fractional=m.Capability.UNKNOWN,
        quantity_step=Decimal("1"),
        price_tick=Decimal("0.01"),
    )
    broker = FakeBrokerAdapter(
        clock=Clock(),
        ids=Ids(),
        capabilities={"spy": cap},
        session_open=lambda _i, _t: True,
        holdings={"spy": Decimal("1")} if scenario == "sell" else None,
    )
    submit = broker.submit
    calls = []

    def counted(item):
        calls.append(item.client_order_id)
        if scenario == "unaccepted":
            raise Crash()
        return submit(item)

    broker.submit = counted
    if scenario == "timeout":
        broker.inject_once(FakeFault.TIMEOUT_AFTER_ACCEPT)
    try:
        order = submit_once(
            ledger=execution_ledger,
            account_id="paper",
            intent_id=intent_id,
            limits=RiskLimits(),
            broker=broker,
            paper_smoke_enabled=True,
            owner_id="fixture",
            fencing_token=1,
        )
    except Crash:
        with ledger.transaction() as tx:
            order_id = tx.connection.execute(
                text("SELECT id FROM public.broker_orders WHERE intent_id=:intent"),
                {"intent": intent_id},
            ).scalar_one()
            order = tx.get(m.BrokerOrder, order_id)
    assert len(calls) == 1

    def reconcile():
        result = reconcile_order(
            ledger=execution_ledger, account_id="paper", order_id=order.id, broker=broker
        )
        assert len(calls) == 1, "reconciliation called submit"
        return result

    def facts():
        with ledger.transaction() as tx:
            return (
                tx.get(m.Position, "pos"),
                tx.get(m.RiskReservation, risk.reservation_id),
                tx.get(m.BrokerOrder, order.id),
            )

    if scenario in ("timeout", "unaccepted"):
        broker.disconnect()
        reconcile()
        broker.reconnect()
        result = reconcile()
        position, reservation, _ = facts()
        assert position.quantity == 0 and reservation.reserved_notional == Decimal("100.01")
        assert reservation.state in (m.ReservationState.ACTIVE, m.ReservationState.HELD_UNKNOWN)
        if scenario == "unaccepted":
            assert result.status is m.OrderStatus.UNKNOWN
        else:
            assert result.status is m.OrderStatus.ACKNOWLEDGED
        print(f"PASS {scenario}: authoritative readback only; held budget; no retry")
        return

    if scenario == "sell":
        broker.fill(
            intent.client_order_id,
            cumulative_quantity=Decimal("1"),
            price=Decimal("110"),
            event_id="sell-fill",
        )
        reconcile()
        reconcile()
        position, reservation, result = facts()
        assert position.quantity == 0 and position.realized_pnl == Decimal("10")
        assert result.status is m.OrderStatus.FILLED and reservation.state in (
            m.ReservationState.RELEASED,
            m.ReservationState.CONSUMED,
        )
        print("PASS SELL: exact realized PNL; zero remaining position; idempotent")
        return

    if scenario == "broker-id-mismatch":
        actual_by_id = broker.by_broker_id
        broker.by_broker_id = lambda key: replace(
            actual_by_id(key), status=m.OrderStatus.PARTIALLY_FILLED, filled_quantity=Decimal("0.5")
        )
        assert reconcile().status is m.OrderStatus.UNKNOWN
        assert facts()[0].quantity == 0
        print("PASS broker-ID proof conflict: no economic mutation")
        return
    if scenario == "unpriced-extra-fill":
        broker.cancel(intent.client_order_id, event_id="empty-cancel", confirmed=True)
        reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: (
            *reports,
            replace(
                reports[-1],
                id="extra",
                broker_event_id="unpriced-extra",
                filled_quantity=Decimal("0.5"),
                average_fill_price=None,
            ),
        )
        assert reconcile().status is m.OrderStatus.UNKNOWN
        position, reservation, _ = facts()
        assert position.quantity == 0 and reservation.reserved_notional == Decimal("100.01")
        print("PASS unpriced extra fill: contradictory closure does not release budget")
        return
    if scenario == "negative-cash":
        broker.fill(
            intent.client_order_id,
            cumulative_quantity=Decimal("1"),
            price=Decimal("101"),
            event_id="expensive-fill",
        )
        result = reconcile()
        position, reservation, _ = facts()
        assert result.status is m.OrderStatus.UNKNOWN and position.quantity == 0
        assert reservation.reserved_notional == Decimal("100.01")
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text(
                        "SELECT safety_state FROM public.risk_account_context "
                        "WHERE account_id='paper'"
                    )
                ).scalar_one()
                == "PAUSE_NEW"
            )
        print("PASS negative cash: entire accounting rejected atomically; durable pause")
        return
    broker.fill(
        intent.client_order_id,
        cumulative_quantity=Decimal("0.5"),
        price=Decimal("100"),
        event_id="fill-half",
    )
    if scenario == "reordered":
        broker.fill(
            intent.client_order_id,
            cumulative_quantity=Decimal("1"),
            price=Decimal("100"),
            event_id="fill-full",
        )
        reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: tuple(reversed(reports))
    if scenario == "cancel-race":
        broker.cancel(intent.client_order_id, event_id="cancel-pending", confirmed=False)
        broker.cancel(intent.client_order_id, event_id="cancel-confirmed", confirmed=True)
        reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: tuple(reversed(reports))
        reconcile()
        reconcile()
        position, reservation, result = facts()
        assert position.quantity == Decimal("0.5") and position.average_price == 100
        assert result.status is m.OrderStatus.CANCELED and result.filled_quantity == Decimal("0.5")
        assert reservation.reserved_notional == 0
        print("PASS unordered CANCEL_PENDING/partial/confirmation: filled portion preserved once")
        return
    if scenario == "unknown-price":
        reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: tuple(
            replace(item, average_fill_price=None) if item.filled_quantity > 0 else item
            for item in reports
        )
        result = reconcile()
        position, reservation, _ = facts()
        assert result.status is m.OrderStatus.UNKNOWN and position.quantity == 0
        assert reservation.reserved_notional == Decimal("100.01")
        print("PASS unknown price: no invented cost; retained risk; paused")
        return
    getcontext().prec = 6
    if scenario == "rollback":
        with engine.connect() as observer:
            before = observer.execute(
                text("SELECT count(*) FROM public.outbox_events")
            ).scalar_one()

        def interrupt_commit(_connection):
            raise Crash()

        event.listen(execution_ledger.engine, "commit", interrupt_commit)
        try:
            try:
                reconcile()
            except Crash:
                pass
            else:
                raise AssertionError("commit interruption did not occur")
        finally:
            event.remove(execution_ledger.engine, "commit", interrupt_commit)
        position, reservation, persisted_order = facts()
        assert position.quantity == 0 and persisted_order.filled_quantity == 0
        assert reservation.reserved_notional == Decimal("100.01")
        with engine.connect() as observer:
            assert (
                observer.execute(text("SELECT count(*) FROM public.outbox_events")).scalar_one()
                == before
            )
            assert (
                observer.execute(
                    text(
                        "SELECT count(*) FROM public.execution_reports "
                        "WHERE broker_event_id='fill-half'"
                    )
                ).scalar_one()
                == 0
            )
    reconcile()
    reconcile()
    position, reservation, result = facts()
    if scenario == "reordered":
        assert (
            position.quantity == 1
            and position.average_price == 100
            and result.status is m.OrderStatus.FILLED
        )
        assert reservation.state in (m.ReservationState.RELEASED, m.ReservationState.CONSUMED)
        print("PASS reordered reports: full economic effect booked once")
        return
    assert position.quantity == Decimal("0.5") and position.average_price == 100
    assert reservation.reserved_notional == Decimal("50.005")
    assert result.status is m.OrderStatus.PARTIALLY_FILLED
    if scenario == "rollback":
        print("PASS commit interruption: positions, reports and outbox rollback; retry books once")
        return
    if scenario == "duplicate-delivery":
        actual_reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: tuple(
            replace(
                item,
                id=f"redelivered-{index}",
                received_at=item.received_at + timedelta(seconds=1),
                filled_quantity=Decimal(str(item.filled_quantity) + "0")
                if "." in str(item.filled_quantity)
                else item.filled_quantity,
            )
            for index, item in enumerate(actual_reports)
        )
        assert reconcile().status is m.OrderStatus.PARTIALLY_FILLED
        assert facts()[0].quantity == Decimal("0.5")
        print("PASS redelivery: transport IDs/times and Decimal scale do not duplicate money")
        return
    if scenario == "cost-amendment":
        report = broker.reports(intent.client_order_id)[-1]
        broker.reports = lambda _id: (
            replace(
                report,
                id="amended",
                broker_event_id="new-amendment",
                average_fill_price=Decimal("200"),
            ),
        )
        assert reconcile().status is m.OrderStatus.UNKNOWN
        assert facts()[0].quantity == Decimal("0.5")
        print("PASS new-event cost amendment: durable cumulative basis protected")
        return
    if scenario == "terminal-regression":
        broker.cancel(intent.client_order_id, event_id="terminal-cancel", confirmed=True)
        reconcile()
        authoritative = broker.order_status(intent.client_order_id)
        regressed = replace(authoritative, status=m.OrderStatus.PARTIALLY_FILLED)
        broker.reconcile = lambda *_args: regressed
        broker.order_status = lambda _id: regressed
        broker.by_broker_id = lambda _id: regressed
        assert reconcile().status is m.OrderStatus.UNKNOWN
        assert reconcile().status is m.OrderStatus.UNKNOWN
        assert facts()[1].state in (m.ReservationState.CONSUMED, m.ReservationState.RELEASED)
        print("PASS repeated terminal regression cannot resurrect order or budget")
        return
    with engine.connect() as observer:
        assert (
            observer.execute(
                text("SELECT account_id FROM public.positions WHERE id='pos'")
            ).scalar_one()
            == "paper"
        )
    if scenario == "conflict":
        reports = broker.reports(intent.client_order_id)
        broker.reports = lambda _id: tuple(
            replace(item, average_fill_price=Decimal("101"))
            if item.broker_event_id == "fill-half"
            else item
            for item in reports
        )
        result = reconcile()
        assert result.status is m.OrderStatus.UNKNOWN
        assert facts()[0].quantity == Decimal("0.5")
        print("PASS event conflict: no duplicate or amended money; retained risk")
        return
    if scenario == "cancel-late":
        broker.cancel(intent.client_order_id, event_id="cancel", confirmed=True)
        reconcile()
        reconcile()
        assert facts()[2].status is m.OrderStatus.CANCELED
    broker.fill(
        intent.client_order_id,
        cumulative_quantity=Decimal("1"),
        price=Decimal("100"),
        event_id="fill-full",
    )
    reconcile()
    reconcile()
    position, reservation, result = facts()
    assert position.quantity == 1 and position.average_price == 100
    assert result.filled_quantity == 1 and result.status is m.OrderStatus.FILLED
    assert reservation.state in (m.ReservationState.RELEASED, m.ReservationState.CONSUMED)
    with ledger.transaction() as tx:
        context = tx.connection.execute(
            text(
                "SELECT portfolio_snapshot_id FROM public.risk_account_context "
                "WHERE account_id='paper'"
            )
        ).scalar_one()
        portfolio = tx.get(m.PortfolioSnapshot, context)
        assert portfolio.cash == Decimal("0.01")
    print(f"PASS {scenario}: cumulative fills and late corrections booked once; cash exact")


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
