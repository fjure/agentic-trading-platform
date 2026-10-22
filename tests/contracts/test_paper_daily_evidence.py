"""Synthetic provider proof of UTC window ends and the approved first-day exception."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest

from packages.risk.paper_daily import assess_daily_loss
from services.execution.paper_credentials import PrivatePaperCredentials
from services.execution.paper_daily_evidence import PaperPreparationCollector
from services.execution.paper_initial_evidence import InitialEvidenceUnavailable
from tests.contracts.test_paper_initial_evidence import (
    ACCOUNT,
    ACCOUNT_DATA,
    ACTIVITY,
    CANARY,
    NOW,
    ORDER,
    POSITION,
    proof,
)

MIDNIGHT = NOW.replace(hour=0)


def collect(*, fresh=False, history=None, flows=None, activities=None, account_changes=None,
            orders=None, prior_activities=None, mutate=None):
    calls, snapshot = [], 0
    account = ACCOUNT_DATA | (dict(cash="100000", equity="100000", portfolio_value="100000",
                                  created_at=(NOW-timedelta(hours=1)).isoformat()) if fresh else {})
    account.update(account_changes or {})
    raw_history = {"timeframe": "1Min", "timestamp": [int((MIDNIGHT-timedelta(minutes=1))
                                                        .timestamp())], "equity": [100500]}
    raw_history.update(history or {})

    def handler(request):
        nonlocal snapshot
        calls.append(request)
        if request.url.path == "/v2/clock":
            snapshot += 1
            value = {"timestamp": NOW.isoformat()}
        elif request.url.path == "/v2/account":
            value = account
        elif request.url.path == "/v2/positions":
            value = [] if fresh else [POSITION]
        elif request.url.path == "/v2/orders":
            value = [] if request.url.params.get("status") == "open" else (
                orders if orders is not None else ([] if fresh else [ORDER]))
        elif request.url.path == "/v2/account/portfolio/history":
            value = raw_history
        elif request.url.params.get("until") == MIDNIGHT.isoformat():
            assert "after" not in request.url.params
            value = prior_activities if prior_activities is not None else (
                [] if fresh or activities is not None else [ACTIVITY])
        else:
            value = (flows or []) if "after" in request.url.params else (
                activities if activities is not None else (
                    ([] if fresh else [ACTIVITY]) + (flows or [])))
        value = deepcopy(value)
        if mutate is not None:
            value = mutate(request, value, snapshot)
        return httpx.Response(200, json=value)

    reader = Mock(load=Mock(return_value=PrivatePaperCredentials(7, CANARY, CANARY, ACCOUNT)))
    collector = PaperPreparationCollector(credentials=reader, clock=Mock(now=lambda: NOW),
                                         expected_account_id=ACCOUNT, pinned_account_id=ACCOUNT,
                                         transport=httpx.MockTransport(handler))
    return collector, calls


def test_prior_minute_end_is_the_utc_reference_with_cent_uncertainty():
    collector, calls = collect()
    result = collector.collect(7, proof())
    ref = result.daily_reference
    assert ref.kind == "UTC_DAY" and ref.starts_at == MIDNIGHT
    assert ref.opening_equity.lower == Decimal("100499.99")
    assert ref.opening_equity.upper == Decimal("100500.01")
    assert ref.capital_lower == Decimal("100499.99")
    history = [r for r in calls if r.url.path.endswith("/history")]
    assert len(history) == 2
    for request in history:
        assert request.url.params["start"].startswith("2026-10-20T23:59:00")
        assert request.url.params["end"].startswith("2026-10-21T00:00:00")
        assert request.url.params["timeframe"] == "1Min"
        assert request.url.params["intraday_reporting"] == "continuous"
    assert CANARY not in repr(result) and ACCOUNT not in repr(result)
    assert all(r.method == "GET" and r.url.host == "paper-api.alpaca.markets" for r in calls)


@pytest.mark.parametrize("change", [
    {"timestamp": [int(MIDNIGHT.timestamp())]}, {"timestamp": []}, {"equity": []},
    {"timeframe": "1H"}, {"equity": [None]}, {"equity": [True]},
    {"equity": [100500.001]}, {"equity": [0]},
])
def test_missing_or_wrong_window_never_uses_last_equity(change):
    collector, _ = collect(history=change)
    with pytest.raises(InitialEvidenceUnavailable):
        collector.collect(7, proof())


def test_same_day_untraded_account_uses_explicit_first_day_without_history_fallback():
    collector, calls = collect(fresh=True)
    result = collector.collect(7, proof())
    ref = result.daily_reference
    assert ref.kind == "FIRST_DAY" and ref.starts_at == NOW
    assert ref.expires_at == MIDNIGHT + timedelta(days=1)
    assert ref.opening_equity.lower == ref.capital_lower == Decimal("100000")
    assert not any(r.url.path.endswith("/history") for r in calls)
    assert not assess_daily_loss(ref, result.daily_observation, now=NOW,
                                max_loss_fraction=Decimal("0.01")).reasons


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected"])
def test_zero_fill_terminal_attempts_allow_first_day_reference(status):
    order = ORDER | {"status": status, "filled_qty": "0", "filled_avg_price": None,
                     "submitted_at": (NOW-timedelta(minutes=30)).isoformat(),
                     "updated_at": (NOW-timedelta(minutes=29)).isoformat()}
    collector, calls = collect(fresh=True, orders=[order])
    result = collector.collect(7, proof())
    assert result.daily_reference.kind == "FIRST_DAY"
    assert result.initial.order_anchors[0].untraded
    assert not any(request.url.path.endswith("/history") for request in calls)


@pytest.mark.parametrize("fields", [
    {}, {"filled_qty": None}, {"filled_qty": 0}, {"filled_qty": "0.001"},
    {"filled_qty": "bad"}, {"filled_qty": "0", "filled_avg_price": "1"},
    {"filled_qty": "0", "filled_avg_price": 0},
    {"filled_qty": "0", "status": "filled"},
    {"filled_qty": "0", "status": "replaced"},
])
def test_unproven_zero_fill_attempts_cannot_qualify_for_first_day(fields):
    order = {key: value for key, value in ORDER.items() if key != "filled_qty"}
    order.update({"status": "canceled"} | fields)
    collector, _ = collect(fresh=True, orders=[order])
    with pytest.raises(InitialEvidenceUnavailable):
        collector.collect(7, proof())


@pytest.mark.parametrize("activity", [ACTIVITY, {
    "id": "20261021::fee", "activity_type": "FEE", "date": "2026-10-21", "net_amount": "-1",
}])
def test_zero_fill_orders_do_not_override_trade_or_return_history(activity):
    order = ORDER | {"status": "rejected", "filled_qty": "0"}
    collector, _ = collect(fresh=True, orders=[order], activities=[activity])
    with pytest.raises(InitialEvidenceUnavailable, match="DAILY_REFERENCE_UNPROVEN"):
        collector.collect(7, proof())


def test_prior_day_or_missing_creation_never_qualifies_for_first_day():
    for changes in ({"created_at": None}, {"created_at": MIDNIGHT.isoformat()},
                    {"created_at": (MIDNIGHT-timedelta(seconds=1)).isoformat()}):
        collector, _ = collect(fresh=True, account_changes=changes)
        assert collector.collect(7, proof()).daily_reference.kind == "UTC_DAY"


def test_trade_or_return_history_excludes_first_day_exception():
    for activities in ([ACTIVITY], [{"id": "20261021::fee", "activity_type": "FEE",
                                     "date": "2026-10-21", "net_amount": "-1"}]):
        collector, _ = collect(fresh=True, activities=activities)
        with pytest.raises(InitialEvidenceUnavailable, match="DAILY_REFERENCE_UNPROVEN"):
            collector.collect(7, proof())


def test_exact_external_flows_are_distinct_from_fees_and_dividends():
    flows = [{"id": "20261021::"+kind, "activity_type": kind, "date": "2026-10-22",
              "net_amount": amount} for kind, amount in
             (("CSD", "1000.001"), ("CSW", "-50.002"), ("FEE", "-1"), ("DIV", "2"))]
    collector, _ = collect(flows=flows)
    result = collector.collect(7, proof())
    assert result.daily_observation.net_external_funding.upper == Decimal("949.999")


@pytest.mark.parametrize("kind,amount", [("JNLC", "1"), ("JNLS", "1"),
                                         ("UNKNOWN", "1"), ("CSD", "-1"), ("CSW", "1")])
def test_unclassified_or_wrong_sign_funding_is_unproven(kind, amount):
    collector, _ = collect(flows=[{"id": "20261021::bad", "activity_type": kind,
                                  "date": "2026-10-21", "net_amount": amount}])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_daily_source_drift_between_snapshots_rejects_financial_basis():
    def mutate(request, value, snapshot):
        if request.url.path.endswith("/history") and snapshot == 2:
            value["equity"] = [100501]
        return value
    collector, _ = collect(mutate=mutate)
    with pytest.raises(InitialEvidenceUnavailable, match="INITIAL_EVIDENCE_CHANGED"):
        collector.collect(7, proof())


@pytest.mark.parametrize("kind", ["CSD", "JNLC"])
def test_filtered_history_cannot_omit_known_current_deposit_or_unknown_transfer(kind):
    activity = {"id": "20261021::omitted", "activity_type": kind,
                "date": "2026-10-20", "created_at": (NOW-timedelta(hours=1)).isoformat(),
                "net_amount": "2000"}
    collector, _ = collect(activities=[activity], flows=[])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


@pytest.mark.parametrize("change", [{"net_amount": "1999"}, {"activity_type": "DIV"},
                                   {"created_at": (NOW-timedelta(hours=2)).isoformat()}])
def test_filtered_activity_must_match_complete_history_source_hash(change):
    activity = {"id": "20261021::changed", "activity_type": "CSD",
                "date": "2026-10-21", "created_at": (NOW-timedelta(hours=1)).isoformat(),
                "net_amount": "2000"}
    collector, _ = collect(activities=[activity], flows=[activity | change])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_filtered_activity_missing_from_complete_history_is_unproven():
    activity = {"id": "20261021::extra", "activity_type": "CSW",
                "date": "2026-10-21", "net_amount": "-2000"}
    collector, _ = collect(activities=[], flows=[activity])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_funding_crosscheck_exhausts_both_paginated_sources():
    flows = [{"id": f"20261021::deposit-{index:03}", "activity_type": "CSD",
              "date": "2026-10-21", "created_at": (NOW-timedelta(hours=1)).isoformat(),
              "net_amount": "0.001"} for index in range(101)]

    def paginate(request, value, snapshot):
        if request.url.path != "/v2/account/activities":
            return value
        cursor = request.url.params.get("page_token")
        start = 0 if cursor is None else next(
            index + 1 for index, item in enumerate(value) if item["id"] == cursor
        )
        return value[start:start+100]

    collector, calls = collect(flows=flows, mutate=paginate)
    result = collector.collect(7, proof())
    assert result.daily_observation.net_external_funding.upper == Decimal("0.101")
    pages = [request for request in calls if request.url.path == "/v2/account/activities"]
    assert len(pages) == 10
    assert sum("page_token" in request.url.params for request in pages) == 4


@pytest.mark.parametrize("kind,amount", [("CSD", "2000"), ("CSW", "-2000"), ("JNLC", "2000")])
def test_missing_creation_cash_activity_requires_current_or_affirmative_prior_proof(kind, amount):
    activity = {"id": "20261021::unpartitioned", "activity_type": kind,
                "date": "2026-10-21", "net_amount": amount}
    collector, _ = collect(activities=[activity], flows=[], prior_activities=[])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_current_execution_time_cannot_substitute_for_missing_creation_membership():
    activity = ACTIVITY | {"transaction_time": (NOW-timedelta(hours=1)).isoformat()}
    collector, _ = collect(activities=[activity], flows=[], prior_activities=[])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_old_nontrade_without_creation_is_proven_prior_despite_future_settlement_date():
    activity = {"id": "20261020::prior", "activity_type": "JNLC",
                "date": "2026-10-22", "net_amount": "2000"}
    collector, calls = collect(activities=[activity], flows=[], prior_activities=[activity])
    result = collector.collect(7, proof())
    assert result.daily_observation.funding_complete
    assert result.daily_observation.net_external_funding.upper == Decimal(0)
    prior = [r for r in calls if r.url.params.get("until") == MIDNIGHT.isoformat()]
    assert len(prior) == 2
    assert all("after" not in request.url.params for request in prior)


@pytest.mark.parametrize("change", [{"net_amount": "1999"}, {"id": "20261020::unknown"},
                                   {"created_at": MIDNIGHT.isoformat()}])
def test_prior_partition_rejects_changed_unknown_or_boundary_anchors(change):
    activity = {"id": "20261020::prior", "activity_type": "CSD",
                "date": "2026-10-20", "net_amount": "2000"}
    collector, _ = collect(activities=[activity], flows=[], prior_activities=[activity | change])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_prior_partition_rejects_duplicate_anchors():
    collector, _ = collect(activities=[ACTIVITY], prior_activities=[ACTIVITY, ACTIVITY])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_unknown_creation_cannot_belong_to_both_current_and_prior_periods():
    activity = {"id": "20261021::overlap", "activity_type": "CSD",
                "date": "2026-10-21", "net_amount": "2000"}
    collector, _ = collect(flows=[activity], prior_activities=[ACTIVITY, activity])
    with pytest.raises(InitialEvidenceUnavailable, match="EXTERNAL_FLOW_UNPROVEN"):
        collector.collect(7, proof())


def test_prior_partition_is_exhausted_with_id_pagination():
    prior = [ACTIVITY | {"id": f"20261020::prior-{i:03}"} for i in range(101)]

    def paginate(request, value, snapshot):
        if request.url.path != "/v2/account/activities":
            return value
        token = request.url.params.get("page_token")
        start = 0 if token is None else next(
            i + 1 for i, row in enumerate(value) if row["id"] == token
        )
        return value[start:start+100]

    collector, calls = collect(activities=prior, prior_activities=prior, mutate=paginate)
    assert collector.collect(7, proof()).daily_observation.funding_complete
    pages = [r for r in calls if r.url.params.get("until") == MIDNIGHT.isoformat()]
    assert len(pages) == 4 and sum("page_token" in r.url.params for r in pages) == 2


@pytest.mark.parametrize("kind", ["DIVFEE", "DIVNRA", "CGD", "DIVCGL", "DIVCGS", "DIVFT",
                                 "DIVROC", "DIVTW", "DIVTXEX", "INT", "INTNRA", "INTTW",
                                 "PTC", "PTR"])
def test_documented_distribution_tax_and_charge_remain_returns(kind):
    flows = [{"id": "20261021::return", "activity_type": kind, "date": "2026-10-22",
              "created_at": (NOW-timedelta(hours=1)).isoformat(), "net_amount": "-10"}]
    collector, _ = collect(flows=flows, account_changes={
        "cash": "99990", "equity": "100490", "portfolio_value": "100490",
    })
    result = collector.collect(7, proof())
    assert result.daily_observation.funding_complete
    assert result.daily_observation.net_external_funding.upper == Decimal(0)
    assessment = assess_daily_loss(result.daily_reference, result.daily_observation, now=NOW,
                                   max_loss_fraction=Decimal("0.01"))
    assert assessment.loss_upper == Decimal("10.01")
