"""Synthetic fixed-host GET-only activity/account evidence certification."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from packages.brokers.alpaca_activities import EQUITY_ZERO_FEE_PROFILE
from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter, AlpacaPaperError
from tests.contracts.test_alpaca_paper import (
    ACCOUNT_ID,
    ASSET_ID,
    BROKER_ID,
    NOW,
    FrozenClock,
    SeededIds,
    intent,
    payload,
)

AFTER = NOW - timedelta(days=2)


def fill(index=1):
    return {
        "id": f"20261014145900000::{index}",
        "activity_type": "FILL",
        "transaction_time": (NOW - timedelta(minutes=1)).isoformat(),
        "order_id": BROKER_ID,
        "symbol": "SPY",
        "side": "buy",
        "qty": "1",
        "price": "100",
        "cum_qty": str(index),
        "leaves_qty": "0",
        "type": "fill",
    }


class EvidenceServer:
    def __init__(self):
        self.requests = []
        self.account = {
            "id": ACCOUNT_ID,
            "currency": "USD",
            "cash": "800",
            "equity": "1000",
            "portfolio_value": "1000",
        }
        self.positions = [
            {
                "asset_id": ASSET_ID,
                "symbol": "SPY",
                "side": "long",
                "qty": "2",
                "avg_entry_price": "100",
                "market_value": "200",
            }
        ]
        self.orders = [payload(intent(), status="filled", filled_qty="2", filled_avg_price="100")]
        self.orders[0]["submitted_at"] = (NOW - timedelta(minutes=2)).isoformat()
        self.activities = [fill()]
        self.activity_pages = None
        self.order_pages = None
        self.mutate_account = False
        self.account_reads = 0

    def __call__(self, req):
        self.requests.append(req)
        assert req.method == "GET"
        assert req.url.host == "paper-api.alpaca.markets"
        path = req.url.path
        if path == "/v2/account":
            self.account_reads += 1
            body = dict(self.account)
            if self.mutate_account and self.account_reads >= 3:
                body["cash"] = "799"
            return httpx.Response(200, json=body)
        if path == "/v2/clock":
            return httpx.Response(200, json={"timestamp": NOW.isoformat()})
        if path == "/v2/positions":
            return httpx.Response(200, json=self.positions)
        if path == "/v2/orders":
            body = self.orders if self.order_pages is None else self.order_pages(req)
            return httpx.Response(200, json=body)
        if path == "/v2/account/activities":
            body = self.activities if self.activity_pages is None else self.activity_pages(req)
            return httpx.Response(200, json=body)
        raise AssertionError(path)


def adapter(server, profile=None):
    return AlpacaPaperBrokerAdapter(
        FrozenClock(),
        SeededIds(),
        api_key="test-key",
        api_secret="test-secret",
        paper_enabled=True,
        expected_account_id=ACCOUNT_ID,
        transport=httpx.MockTransport(server),
        restored_intents=(intent(),),
        reconciliation_fee_profile=profile,
    )


def test_normalized_complete_bundle_and_no_invented_fee():
    server = EvidenceServer()
    broker = adapter(server)
    evidence = broker.reconciliation_evidence(after=AFTER)
    assert evidence.cash == Decimal("800")
    assert evidence.positions[0].quantity == Decimal("2")
    assert evidence.activities[0].cumulative_quantity == Decimal("1")
    assert evidence.fee_authority_profile is None
    assert broker.reports("client-1")[-1].fee is None
    with pytest.raises(FrozenInstanceError):
        evidence.cash = Decimal("1")


def test_explicit_profile_only_and_no_auto_authority():
    assert (
        adapter(EvidenceServer(), EQUITY_ZERO_FEE_PROFILE)
        .reconciliation_evidence(after=AFTER)
        .fee_authority_profile
        == EQUITY_ZERO_FEE_PROFILE
    )
    with pytest.raises(ValueError, match="unknown reconciliation fee profile"):
        adapter(EvidenceServer(), "pretend-zero")


def test_activity_pagination_uses_exact_last_id_and_no_type_filter():
    server = EvidenceServer()
    first = [fill(i) for i in range(1, 101)]

    def pages(req):
        assert "activity_types" not in req.url.params and "date" not in req.url.params
        assert req.url.params["page_size"] == "100"
        if "page_token" not in req.url.params:
            return first
        assert req.url.params["page_token"] == first[-1]["id"]
        return [fill(101)]

    server.activity_pages = pages
    assert len(adapter(server).reconciliation_evidence(after=AFTER).activities) == 101


@pytest.mark.parametrize("mode", ["cycle", "conflict"])
def test_activity_cycles_and_conflicting_duplicate_fail_redacted(mode):
    server = EvidenceServer()
    first = [fill(i) for i in range(1, 101)]

    def pages(req):
        if mode == "conflict" and "page_token" in req.url.params:
            return [dict(first[0], price="900")]
        return first

    server.activity_pages = pages
    with pytest.raises(AlpacaPaperError) as error:
        adapter(server).reconciliation_evidence(after=AFTER)
    assert error.value.reason in {
        "INCOMPLETE_RECONCILIATION_EVIDENCE",
        "CONFLICTING_RECONCILIATION_EVIDENCE",
    }
    assert "test-secret" not in str(error.value)


def test_exact_duplicate_fill_deduplicates():
    server = EvidenceServer()
    server.activities *= 2
    assert len(adapter(server).reconciliation_evidence(after=AFTER).activities) == 1


def test_unknown_external_order_rejected_even_before_baseline():
    server = EvidenceServer()
    server.orders[0]["client_order_id"] = "external"
    server.orders[0]["submitted_at"] = (AFTER - timedelta(days=10)).isoformat()
    with pytest.raises(AlpacaPaperError, match="UNBOUND_RECONCILIATION_ORDER"):
        adapter(server).reconciliation_evidence(after=AFTER)
    req = next(r for r in server.requests if r.url.path == "/v2/orders")
    assert "after" not in req.url.params and req.url.params["status"] == "all"


def test_order_timestamp_saturation_rejected():
    server = EvidenceServer()
    server.orders *= 500
    with pytest.raises(AlpacaPaperError, match="INCOMPLETE_RECONCILIATION_EVIDENCE"):
        adapter(server).reconciliation_evidence(after=AFTER)


@pytest.mark.parametrize(
    "field,value", [("cash", "NaN"), ("equity", "1000.01"), ("currency", "EUR"), ("cash", 800.0)]
)
def test_account_invalid_and_inconsistent_fail_closed(field, value):
    server = EvidenceServer()
    server.account[field] = value
    # JSON numeric tokens become Decimal; exact integer is supported by shared parser.
    if field == "cash" and value == 800.0:
        server.account[field] = "8e2"
    with pytest.raises(AlpacaPaperError):
        adapter(server).reconciliation_evidence(after=AFTER)


def test_changed_account_fails_before_mapping_reports():
    server = EvidenceServer()
    server.mutate_account = True
    broker = adapter(server)
    with pytest.raises(AlpacaPaperError, match="ACCOUNT_EVIDENCE_CHANGED"):
        broker.reconciliation_evidence(after=AFTER)
    assert broker.reports("client-1") == ()


def test_signed_daily_fees_preserved_without_order_attribution():
    server = EvidenceServer()
    server.activities = [
        {
            "id": "20261013000000000::fee",
            "activity_type": "CFEE",
            "date": "2026-10-13",
            "net_amount": "0",
            "symbol": "BTCUSD",
            "qty": "-0.000195",
            "price": "1884.5",
        }
    ]
    activity = adapter(server).reconciliation_evidence(after=AFTER).activities[0]
    assert activity.quantity == Decimal("-0.000195")
    assert activity.order_id is None and activity.occurred_at is None
    assert activity.instrument_id == "BTC/USD"


@pytest.mark.parametrize(
    "change",
    [
        {"qty": "-1"},
        {"cum_qty": "0"},
        {"side": "secret-invalid"},
        {"order_id": "bad"},
        {"price": "1e2"},
        {"symbol": {"bad": 1}},
    ],
)
def test_invalid_fill_mapping_has_stable_reason(change):
    server = EvidenceServer()
    server.activities = [dict(fill(), **change)]
    with pytest.raises(AlpacaPaperError):
        adapter(server).reconciliation_evidence(after=AFTER)


def test_conflicting_order_duplicate_rejected():
    server = EvidenceServer()
    changed = deepcopy(server.orders[0])
    changed["filled_qty"] = "1"
    server.orders.append(changed)
    with pytest.raises(AlpacaPaperError, match="CONFLICTING_RECONCILIATION_EVIDENCE"):
        adapter(server).reconciliation_evidence(after=AFTER)


def test_order_pagination_overlaps_last_timestamp_without_losing_ties():
    server = EvidenceServer()
    broker = adapter(server)
    rows = []
    for index in range(501):
        order_intent = intent(f"paged-{index}")
        broker.bind_intent(order_intent)
        row = payload(order_intent, broker_id=f"30000000-0000-4000-8000-{index:012d}")
        row["submitted_at"] = (NOW - timedelta(seconds=1000 - index)).isoformat()
        rows.append(row)

    def pages(req):
        assert req.url.params["nested"] == "false"
        if "after" not in req.url.params:
            return rows[:500]
        assert req.url.params["after"] == (NOW - timedelta(seconds=501, microseconds=1)).isoformat()
        return rows[499:]

    server.order_pages = pages
    evidence = broker.reconciliation_evidence(after=AFTER)
    assert len(evidence.orders) == 501
    assert len({order.broker_order_id for order in evidence.orders}) == 501


@pytest.mark.parametrize("day", ["2026-10-12", "2026-10-14"])
def test_daily_fee_boundary_preserved_for_sql_fail_closed_decision(day):
    server = EvidenceServer()
    server.activities = [
        {"id": "20261014000000000::fee", "activity_type": "FEE", "date": day, "net_amount": "-0.15"}
    ]
    activity = adapter(server).reconciliation_evidence(after=AFTER).activities[0]
    assert activity.activity_date.isoformat() == day
    assert activity.occurred_at is None and activity.net_amount == Decimal("-0.15")


def test_activity_day_floor_does_not_drop_other_types_and_filters_old_fills():
    server = EvidenceServer()
    before = dict(fill(), transaction_time=(AFTER - timedelta(hours=1)).isoformat())
    server.activities = [before]
    evidence = adapter(server).reconciliation_evidence(after=AFTER)
    assert evidence.activities == ()
    req = next(r for r in server.requests if r.url.path == "/v2/account/activities")
    assert req.url.params["after"].endswith("T00:00:00+00:00")


def test_out_of_window_fill_is_not_accepted():
    server = EvidenceServer()
    server.activities = [dict(fill(), transaction_time=(NOW + timedelta(seconds=1)).isoformat())]
    with pytest.raises(AlpacaPaperError, match="INVALID_RECONCILIATION_EVIDENCE"):
        adapter(server).reconciliation_evidence(after=AFTER)


def test_disabled_adapter_sends_no_requests_and_bad_window_rejected():
    server = EvidenceServer()
    broker = AlpacaPaperBrokerAdapter(
        FrozenClock(), SeededIds(), transport=httpx.MockTransport(server)
    )
    with pytest.raises(AlpacaPaperError, match="PAPER_DISABLED"):
        broker.reconciliation_evidence(after=AFTER)
    assert server.requests == []
    with pytest.raises(AlpacaPaperError, match="INVALID_RECONCILIATION_WINDOW"):
        adapter(server).reconciliation_evidence(after=AFTER.replace(tzinfo=None))


def test_profile_selection_cannot_be_mutated_after_construction():
    broker = adapter(EvidenceServer())
    with pytest.raises(AttributeError):
        broker.reconciliation_fee_profile = EQUITY_ZERO_FEE_PROFILE


def test_failed_second_order_mapping_rolls_back_all_adapter_report_state():
    server = EvidenceServer()
    broker = adapter(server)
    second_intent = intent("client-2")
    broker.bind_intent(second_intent)
    second = payload(second_intent, broker_id="30000000-0000-4000-8000-000000000002")
    second["submitted_at"] = server.orders[0]["submitted_at"]
    second["qty"] = "999"
    server.orders.append(second)
    from packages.brokers.fake import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        broker.reconciliation_evidence(after=AFTER)
    assert broker.reports("client-1") == ()
    assert broker.reports("client-2") == ()
