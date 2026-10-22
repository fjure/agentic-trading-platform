"""GET-only certification of immutable imported history exclusions."""

from copy import deepcopy
from datetime import timedelta
from uuid import UUID

import pytest

from packages.brokers.alpaca_paper import AlpacaPaperError
from packages.brokers.initial_anchors import (
    InitialHistoryProof,
    activity_fingerprint,
    order_fingerprint,
)
from tests.contracts.test_alpaca_activities import EvidenceServer, adapter, fill
from tests.contracts.test_alpaca_paper import ACCOUNT_ID, NOW, intent, payload

BASELINE = NOW - timedelta(minutes=5)
OLD_ID = "99999999-9999-4999-8999-999999999999"


def imported():
    server = EvidenceServer()
    old = payload(
        intent("foreign-initial"),
        status="filled",
        filled_qty="2",
        filled_avg_price="100",
        broker_id=OLD_ID,
    )
    old.update(
        symbol="AAPL",
        submitted_at=(BASELINE - timedelta(minutes=2)).isoformat(),
        created_at=(BASELINE - timedelta(minutes=2)).isoformat(),
        updated_at=(BASELINE - timedelta(minutes=1)).isoformat(),
    )
    activity = dict(
        fill(901),
        symbol="AAPL",
        order_id=OLD_ID,
        transaction_time=(BASELINE - timedelta(minutes=1)).isoformat(),
    )
    server.orders.insert(0, old)
    server.activities.insert(0, activity)
    proof = InitialHistoryProof(
        ACCOUNT_ID,
        BASELINE,
        ((OLD_ID, order_fingerprint(old)),),
        ((activity["id"], activity_fingerprint(activity)),),
    )
    return server, proof


def collect(server, proof):
    broker = adapter(server)
    return broker, broker.reconciliation_evidence(after=BASELINE, initial_history=proof)


def test_exact_old_foreign_order_and_same_day_fill_are_excluded_before_mapping():
    server, proof = imported()
    broker, evidence = collect(server, proof)
    assert len(evidence.orders) == len(evidence.activities) == 1
    assert evidence.activities[0].instrument_id == "SPY"
    assert broker.reports("client-1")
    assert not broker.reports("foreign-initial")
    assert all(request.method == "GET" for request in server.requests)
    orders = [r for r in server.requests if r.url.path == "/v2/orders"]
    assert orders and all(
        "after" not in r.url.params and "until" not in r.url.params for r in orders
    )


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("order", "limit_price", "900"),
        ("order", "status", "new"),
        ("order", "updated_at", NOW.isoformat()),
        ("activity", "price", "900"),
        ("activity", "transaction_time", NOW.isoformat()),
    ],
)
def test_changed_anchor_rejected_without_adapter_mutation(target, field, value):
    server, proof = imported()
    (server.orders if target == "order" else server.activities)[0][field] = value
    broker = adapter(server)
    with pytest.raises(AlpacaPaperError, match="CONFLICTING_RECONCILIATION_EVIDENCE"):
        broker.reconciliation_evidence(after=BASELINE, initial_history=proof)
    assert not broker.reports("client-1")


def test_missing_order_anchor_is_incomplete_but_filtered_activity_may_be_absent():
    server, proof = imported()
    server.orders.pop(0)
    with pytest.raises(AlpacaPaperError, match="INCOMPLETE_RECONCILIATION_EVIDENCE"):
        collect(server, proof)
    server, proof = imported()
    server.activities.pop(0)
    assert len(collect(server, proof)[1].activities) == 1


def test_new_foreign_order_cannot_borrow_initial_exclusion():
    server, proof = imported()
    new = deepcopy(server.orders[-1])
    new.update(id=str(UUID(int=51)), client_order_id="foreign-new")
    server.orders.append(new)
    with pytest.raises(AlpacaPaperError, match="UNBOUND_RECONCILIATION_ORDER"):
        collect(server, proof)


@pytest.mark.parametrize("target", ["orders", "activities"])
def test_even_exact_duplicate_imported_history_fails_closed(target):
    server, proof = imported()
    rows = getattr(server, target)
    rows.insert(1, deepcopy(rows[0]))
    with pytest.raises(AlpacaPaperError, match="CONFLICTING_RECONCILIATION_EVIDENCE"):
        collect(server, proof)


@pytest.mark.parametrize("mode", ["baseline", "account", "type"])
def test_proof_must_have_exact_type_baseline_and_account(mode):
    server, proof = imported()
    if mode == "baseline":
        proof = InitialHistoryProof(
            ACCOUNT_ID, BASELINE - timedelta(seconds=1), proof.orders, proof.activities
        )
    elif mode == "account":
        proof = InitialHistoryProof(str(UUID(int=52)), BASELINE, proof.orders, proof.activities)
    else:
        proof = object()
    with pytest.raises(AlpacaPaperError, match="INITIAL_HISTORY_INVALID"):
        collect(server, proof)
    if mode != "account":
        assert not server.requests


def test_whole_order_history_uses_exclusive_id_cursor_without_timestamp_filters():
    server, original = imported()
    old = server.orders[0]
    history = [dict(old, id=str(UUID(int=1000 + index))) for index in range(500)]
    proof = InitialHistoryProof(
        ACCOUNT_ID,
        BASELINE,
        tuple((row["id"], order_fingerprint(row)) for row in history),
        original.activities,
    )

    def pages(request):
        params = request.url.params
        assert "after" not in params and "until" not in params
        if "after_order_id" not in params:
            return history
        assert params["after_order_id"] == history[-1]["id"]
        return [server.orders[-1]]

    server.order_pages = pages
    assert len(collect(server, proof)[1].orders) == 1
    assert len([r for r in server.requests if r.url.path == "/v2/orders"]) == 2


@pytest.mark.parametrize("target", ["orders", "activities"])
def test_even_matching_hash_cannot_authorize_history_after_baseline(target):
    server, proof = imported()
    if target == "orders":
        server.orders[0]["updated_at"] = NOW.isoformat()
        proof = InitialHistoryProof(
            ACCOUNT_ID, BASELINE, ((OLD_ID, order_fingerprint(server.orders[0])),), proof.activities
        )
    else:
        server.activities[0]["transaction_time"] = NOW.isoformat()
        proof = InitialHistoryProof(
            ACCOUNT_ID,
            BASELINE,
            proof.orders,
            ((server.activities[0]["id"], activity_fingerprint(server.activities[0])),),
        )
    with pytest.raises(AlpacaPaperError, match="CONFLICTING_RECONCILIATION_EVIDENCE"):
        collect(server, proof)


def test_unanchored_activity_still_undergoes_normal_validation():
    server, proof = imported()
    new = dict(fill(902), symbol="AAPL")
    server.activities.append(new)
    with pytest.raises(AlpacaPaperError, match="UNSUPPORTED_RECONCILIATION_ASSET"):
        collect(server, proof)
