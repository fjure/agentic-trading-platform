"""Offline process restart and broker-port scenarios; no external requests."""

import json
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from packages.brokers.fake import FakeBrokerAdapter, IdempotencyConflict
from packages.domain.models import OrderIntent, OrderStatus, Reconciliation
from packages.domain.ports import BrokerAdapter
from tests.contracts.test_alpaca_paper import (
    BROKER_ID,
    FrozenClock,
    PaperServer,
    SeededIds,
    make_adapter,
    payload,
    prepared,
)


class ProcessCrash(BaseException):
    """Deliberately bypass ordinary transport error handling, like process death."""


@pytest.mark.parametrize("accepted", [False, True])
def test_restart_with_durable_attempt_never_reposts(tmp_path, accepted):
    class CrashServer(PaperServer):
        def __call__(self, request):
            if request.method == "POST":
                if accepted:
                    super().__call__(request)
                else:
                    self.requests.append(request)
                raise ProcessCrash()
            return super().__call__(request)

    server = CrashServer()
    original = make_adapter(server)
    order_intent = prepared(original)
    # Synthetic durable record represents Execution's required pre-I/O commit.
    journal = tmp_path / "attempt.json"
    journal.write_text(json.dumps({
        "intent": asdict(order_intent), "attempted": [order_intent.client_order_id],
    }, default=str), encoding="utf-8")
    with pytest.raises(ProcessCrash):
        original.submit(order_intent)
    original.close()
    del original
    saved = json.loads(journal.read_text(encoding="utf-8"))
    fields = saved["intent"]
    fields["quantity"] = Decimal(fields["quantity"])
    fields["limit_price"] = Decimal(fields["limit_price"])
    fields["created_at"] = datetime.fromisoformat(fields["created_at"])
    restored_intent = OrderIntent(**fields)
    restarted = make_adapter(
        server, restored_intents=(restored_intent,),
        attempted_client_ids=frozenset(saved["attempted"]),
    )
    result = restarted.submit(restored_intent)
    assert result.status is (OrderStatus.ACKNOWLEDGED if accepted else OrderStatus.UNKNOWN)
    assert server.count("POST", "/v2/orders") == 1
    assert server.count("GET", "/v2/orders:by_client_order_id") >= 2


def test_partial_cancel_late_fill_and_duplicate_snapshots():
    server = PaperServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter, side="SELL")
    adapter.submit(order_intent)
    server.orders["client-1"] = payload(
        order_intent, status="partially_filled", filled_qty="1", filled_avg_price="100.01"
    )
    partial = adapter.order_status("client-1")
    assert partial.filled_quantity == Decimal("1")
    assert adapter.cancel("client-1").status is OrderStatus.CANCEL_PENDING
    server.orders["client-1"] = payload(
        order_intent, status="canceled", filled_qty="1", filled_avg_price="100.01"
    )
    assert adapter.reconcile("client-1", BROKER_ID).status is OrderStatus.CANCELED
    count = len(adapter.reports("client-1"))
    assert adapter.reconcile("client-1", BROKER_ID).filled_quantity == Decimal("1")
    assert len(adapter.reports("client-1")) == count
    server.orders["client-1"] = payload(
        order_intent, status="canceled", filled_qty="1.5", filled_avg_price="100.02"
    )
    late = adapter.reconcile("client-1", BROKER_ID)
    assert late.status is OrderStatus.CANCELED and late.filled_quantity == Decimal("1.5")
    reports = adapter.reports("client-1")
    assert len({report.broker_event_id for report in reports}) == len(reports)
    assert all(report.fee is None and report.reconciliation is Reconciliation.PENDING
               for report in reports)
    server.orders["client-1"] = payload(
        order_intent, status="partially_filled", filled_qty="0.5", filled_avg_price="100.01"
    )
    with pytest.raises(IdempotencyConflict, match="regressed"):
        adapter.reconcile("client-1", BROKER_ID)
    assert server.count("POST", "/v2/orders") == 1


def test_reconnect_is_read_only_and_foreign_broker_binding_fails():
    class DisconnectServer(PaperServer):
        disconnected = False

        def __call__(self, request):
            if self.disconnected:
                self.requests.append(request)
                raise httpx.ConnectError("synthetic disconnect", request=request)
            return super().__call__(request)

    server = DisconnectServer()
    adapter = make_adapter(server)
    order_intent = prepared(adapter)
    adapter.submit(order_intent)
    server.disconnected = True
    with pytest.raises(ValueError, match="UNAVAILABLE"):
        adapter.order_status("client-1")
    server.disconnected = False
    restarted = make_adapter(
        server, restored_intents=(order_intent,), attempted_client_ids=frozenset({"client-1"})
    )
    assert restarted.reconcile("client-1", BROKER_ID).broker_order_id == BROKER_ID
    server.orders["client-1"]["client_order_id"] = "foreign"
    with pytest.raises(IdempotencyConflict):
        restarted.by_broker_id(BROKER_ID)
    assert server.count("POST", "/v2/orders") == 1


@pytest.mark.parametrize("kind", ["fake", "alpaca-offline"])
def test_shared_broker_port_preserves_intent_and_no_duplicate_submit(kind):
    server = PaperServer()
    paper = make_adapter(server)
    order_intent = prepared(paper)
    if kind == "fake":
        cap = replace(paper.capabilities("SPY"), broker_id="fake")
        port: BrokerAdapter = FakeBrokerAdapter(
            clock=FrozenClock(), ids=SeededIds(), capabilities={"SPY": cap},
            session_open=lambda *_: True,
        )
    else:
        port = paper
    first = port.submit(order_intent)
    again = port.submit(order_intent)
    assert first.client_order_id == again.client_order_id == order_intent.client_order_id
    assert first.intent_id == order_intent.id
    assert first.status is again.status is OrderStatus.ACKNOWLEDGED
    assert port.order_status(order_intent.client_order_id).filled_quantity == 0
    assert port.reports(order_intent.client_order_id)
    with pytest.raises(IdempotencyConflict):
        port.submit(replace(order_intent, quantity=Decimal("3")))
