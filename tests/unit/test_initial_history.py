"""Golden pre-refactor history hashes and immutable bounded import authority."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.brokers.initial_anchors import (
    InitialHistoryProof,
    activity_fingerprint,
    order_fingerprint,
)

ACCOUNT = "11111111-2222-4333-8444-555555555555"
NOW = datetime(2026, 10, 22, 15, tzinfo=UTC)
ORDER = {
    "id": ACCOUNT, "status": "filled", "submitted_at": "2026-10-21T14:30:00.000000Z",
    "updated_at": "2026-10-21T14:30:01Z", "created_at": "2026-10-21T14:29:59+00:00",
    "symbol": "SPY", "asset_class": "us_equity", "side": "buy", "type": "limit",
    "time_in_force": "day", "qty": "0.123456789", "notional": None,
    "filled_qty": "0.123456789", "filled_avg_price": "500.123456", "limit_price": "500.123456",
    "stop_price": None, "order_class": "simple", "replaces": None, "replaced_by": None,
}
FILL = {
    "id": "20261021143001000::abc123", "activity_type": "FILL",
    "transaction_time": "2026-10-21T14:30:01Z", "created_at": None, "symbol": "SPY",
    "order_id": ACCOUNT, "side": "buy", "type": "fill", "qty": "0.123456789",
    "price": "500.123456", "cum_qty": "0.123456789", "leaves_qty": "0",
}
NTA = {
    "id": "20261021120000000::cash123", "activity_type": "CSD", "date": "2026-10-21",
    "created_at": "2026-10-21T12:00:00Z", "net_amount": "1000.00", "per_share_amount": None,
}


@pytest.mark.parametrize("function,raw,expected", [
    (order_fingerprint, ORDER, "bfa6b0accf6e9fb597e0afed7d6a6c30fc5cf3f8a5b92ad6cc463f04b7aa787b"),
    (activity_fingerprint, FILL,
     "e1ce237cacf5115aa1a20cf8b72c7a286b2cb741b0f2f15f020d54fc53de556a"),
    (activity_fingerprint, NTA, "f6088fc2c804d4b4acd7cc91cc55c0551002ebbbfdcef18fd71dba4e39ebec11"),
])
def test_exact_pre_refactor_collector_hashes(function, raw, expected):
    assert function(raw) == expected
    assert function(dict(reversed(list(raw.items())))) == expected
    assert function(raw | {"client_order_id": "ignored", "description": "ignored",
                           "account_number": "ignored", "providerResponse": {"ignored": True}}) == (
        expected
    )


@pytest.mark.parametrize("function,raw,field,value", [
    (order_fingerprint, ORDER, "qty", "1"),
    (order_fingerprint, ORDER, "status", "canceled"),
    (order_fingerprint, ORDER, "created_at", "2026-10-21T14:29:59Z"),
    (activity_fingerprint, FILL, "price", "500.123457"),
    (activity_fingerprint, NTA, "net_amount", "1000.01"),
    (activity_fingerprint, NTA, "date", "2026-10-22"),
])
def test_changed_economic_terms_or_raw_timestamp_change_fingerprint(function, raw, field, value):
    assert function(raw | {field: value}) != function(raw)


def test_missing_and_null_financial_terms_remain_distinct():
    absent = {key: value for key, value in ORDER.items() if key != "notional"}
    assert order_fingerprint(absent) != order_fingerprint(ORDER)
    assert activity_fingerprint(NTA | {"qty": None}) != activity_fingerprint(NTA)


@pytest.mark.parametrize("function,raw", [(order_fingerprint, ORDER), (activity_fingerprint, FILL)])
@pytest.mark.parametrize("value", [False, 1, 0.1, {}, [], "x" * 257])
def test_financial_values_are_bounded_strings_or_none(function, raw, value):
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        function(raw | {"qty": value})


@pytest.mark.parametrize("changes", [
    {"id": "bad"}, {"status": "new"}, {"status": []},
    {"submitted_at": None, "created_at": None}, {"updated_at": "2026-10-21T14:29:00Z"},
    {"created_at": "2026-10-21T14:29:59"}, {"submitted_at": "x" * 41},
])
def test_order_identity_and_timestamps_are_bounded(changes):
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        order_fingerprint(ORDER | changes)


@pytest.mark.parametrize("changes", [
    {"id": "bad"}, {"id": "x" * 255 + "::"}, {"activity_type": "secret-value"},
    {"transaction_time": None}, {"created_at": {}},
])
def test_activity_identity_and_timestamps_are_bounded(changes):
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        activity_fingerprint(FILL | changes)


def proof(**changes):
    return InitialHistoryProof(**({
        "provider_account_id": ACCOUNT, "baseline_at": NOW,
        "orders": ((ACCOUNT, order_fingerprint(ORDER)),),
        "activities": ((FILL["id"], activity_fingerprint(FILL)),),
    } | changes))


def test_proof_is_frozen_redacted_and_accepts_empty_history():
    authority = proof()
    with pytest.raises(FrozenInstanceError):
        authority.orders = ()
    with pytest.raises(TypeError):
        authority.orders[0][1] = "b" * 64
    assert ACCOUNT not in repr(authority) and FILL["id"] not in repr(authority)
    assert proof(orders=(), activities=()).baseline_at == NOW


@pytest.mark.parametrize("changes", [
    {"provider_account_id": "bad"}, {"baseline_at": NOW.replace(tzinfo=None)},
    {"baseline_at": NOW.astimezone(timezone(timedelta(hours=2)))},
    {"orders": []}, {"orders": ([ACCOUNT, "a" * 64],)}, {"orders": ((ACCOUNT,),)},
    {"orders": ((ACCOUNT, "a" * 63),)}, {"orders": ((ACCOUNT, "A" * 64),)},
    {"orders": ((ACCOUNT, "a" * 64), (ACCOUNT, "b" * 64))},
    {"activities": (("bad-id", "a" * 64),)},
    {"activities": (("a::b", "a" * 64), ("a::b", "a" * 64))},
])
def test_invalid_or_mutable_proof_authority_is_rejected(changes):
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        proof(**changes)


def test_proof_count_limits_match_bounded_initial_collector():
    orders = tuple((f"00000000-0000-4000-8000-{index:012d}", "a" * 64)
                   for index in range(10000))
    activities = tuple((f"20261022::{index}", "b" * 64) for index in range(2000))
    maximum = proof(orders=orders, activities=activities)
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        replace(maximum, orders=orders + ((ACCOUNT, "c" * 64),))
    with pytest.raises(ValueError, match="^INITIAL_HISTORY_INVALID$"):
        replace(maximum, activities=activities + (("20261022::extra", "c" * 64),))
