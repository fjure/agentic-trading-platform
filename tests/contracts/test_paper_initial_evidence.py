"""APW-04 initial evidence: synthetic GET-only HTTP, no database or provider I/O."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from unittest.mock import Mock
from uuid import UUID

import httpx
import pytest

import services.execution.paper_initial_evidence as module
from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PrivatePaperCredentials,
)
from services.execution.paper_initial_evidence import (
    InitialEvidenceUnavailable,
    InitialPaperEvidenceCollector,
)
from services.execution.paper_verification import PrivateIdentityProof

NOW = datetime(2026, 10, 21, 10, tzinfo=UTC)
ACCOUNT = "11111111-2222-4333-8444-555555555555"
OTHER = "11111111-2222-4333-8444-555555555556"
ASSET = "21111111-2222-4333-8444-555555555555"
CANARY = "synthetic-only-canary-never-an-Alpaca-key"
ACCOUNT_DATA = {
    "id": ACCOUNT, "currency": "USD", "status": "ACTIVE", "cash": "100000",
    "equity": "100500", "portfolio_value": "100500", "last_equity": "100000",
    "short_market_value": "0", "account_blocked": False, "trading_blocked": False,
    "trade_suspended_by_user": False, "account_number": CANARY, "untrusted_extra": CANARY,
}
POSITION = {
    "asset_id": ASSET, "symbol": "SPY", "asset_class": "us_equity", "side": "long",
    "qty": "1", "cost_basis": "300", "avg_entry_price": "300", "market_value": "500",
    "untrusted_extra": CANARY,
}
ORDER = {
    "id": str(UUID(int=3)), "status": "filled", "symbol": "AAPL", "side": "buy",
    "submitted_at": "2026-10-20T10:00:00Z", "updated_at": "2026-10-20T10:01:00Z",
    "qty": "2", "filled_qty": "2", "client_order_id": CANARY, "untrusted_extra": CANARY,
}
ACTIVITY = {
    "id": "20261020::one", "activity_type": "FILL", "symbol": "AAPL", "qty": "2",
    "transaction_time": "2026-10-20T10:01:00Z", "order_id": ORDER["id"], "side": "buy",
    "type": "fill", "cum_qty": "2", "leaves_qty": "0", "price": "300",
    "description": CANARY,
}


def collector(handler=None, *, account=None, positions=None, orders=None, activities=None,
              pinned=ACCOUNT, clock=None):
    calls = []
    reader = Mock()
    reader.load.return_value = PrivatePaperCredentials(7, CANARY, CANARY + "-secret", ACCOUNT)

    def handle(request):
        calls.append(request)
        if handler is not None:
            return handler(request)
        data = {
            "/v2/clock": {"timestamp": NOW.isoformat()},
            "/v2/account": ACCOUNT_DATA if account is None else account,
            "/v2/positions": [POSITION] if positions is None else positions,
            "/v2/account/activities": [ACTIVITY] if activities is None else activities,
        }
        if request.url.path == "/v2/orders":
            value = [] if request.url.params.get("status") == "open" else (
                [ORDER] if orders is None else orders
            )
        else:
            value = data[request.url.path]
        return httpx.Response(200, json=deepcopy(value))

    instance = InitialPaperEvidenceCollector(
        credentials=reader, clock=clock or Mock(now=Mock(return_value=NOW)),
        expected_account_id=ACCOUNT, pinned_account_id=pinned,
        transport=httpx.MockTransport(handle),
    )
    return instance, reader, calls


def proof(**changes):
    return replace(PrivateIdentityProof(7, ACCOUNT, NOW), **changes)


def blocked(instance, code):
    with pytest.raises(InitialEvidenceUnavailable) as error:
        instance.collect(7, proof())
    assert str(error.value) == code
    assert CANARY not in str(error.value) and ACCOUNT not in str(error.value)


def test_two_independent_snapshots_preserve_raw_basis_and_historic_foreign_anchors(caplog):
    instance, reader, calls = collector()
    result = instance.collect(7, proof())
    assert result.cash == Decimal("100000") and result.equity == Decimal("100500")
    assert result.provider_previous_close_equity == Decimal("100000")
    assert result.provider_account_id == ACCOUNT and result.credential_generation == 7
    assert result.positions[0].cost_basis == Decimal("300")
    assert result.positions[0].average_only_ledger_compatible
    assert result.positions[0].quantity == Decimal("1")
    assert result.order_anchors[0].provider_order_id == ORDER["id"]
    assert result.activity_anchors[0].activity_id == ACTIVITY["id"]
    assert result.average_only_ledger_compatible
    assert result.first_read_at == result.confirmed_at == result.provider_time == NOW
    assert len(result.evidence_hash) == 64
    reader.load.assert_called_once_with(7)
    assert len(calls) == 18
    assert sum(request.url.path == "/v2/clock" for request in calls) == 2
    assert all(request.method == "GET" and not request.content for request in calls)
    assert all(request.url.host == "paper-api.alpaca.markets" for request in calls)
    assert all(request.headers["APCA-API-KEY-ID"] == CANARY for request in calls)
    assert CANARY not in str(asdict(result)) and CANARY not in caplog.text
    assert ACCOUNT not in repr(result) and ASSET not in repr(result.positions[0])
    with pytest.raises(FrozenInstanceError):
        result.cash = Decimal("1")
    with pytest.raises(FrozenInstanceError):
        result.positions[0].cost_basis = Decimal("1")
    assert result.evidence_hash == collector()[0].collect(7, proof()).evidence_hash


def test_cost_basis_is_never_rebuilt_from_rounded_average_and_no_day_reference_is_invented():
    positions = [POSITION | {"qty": "60", "cost_basis": "770", "avg_entry_price": "12.83"}]
    with localcontext() as context:
        context.prec = 3
        result = collector(positions=positions)[0].collect(7, proof())
    assert result.positions[0].cost_basis == Decimal("770")
    assert result.positions[0].average_entry_price == Decimal("12.83")
    assert not result.average_only_ledger_compatible
    assert not hasattr(result, "day_start_equity")
    no_previous = ACCOUNT_DATA.copy()
    del no_previous["last_equity"]
    without_previous = collector(account=no_previous)[0].collect(7, proof())
    assert without_previous.provider_previous_close_equity is None


def test_cash_only_account_does_not_need_fake_positions_or_seed_cash():
    result = collector(
        positions=[], orders=[], activities=[],
        account=ACCOUNT_DATA | {"cash": "100000.000001", "equity": "100000.000001",
                                "portfolio_value": "100000.000001"},
    )[0].collect(7, proof())
    assert result.cash == Decimal("100000.000001") and result.positions == ()
    assert result.order_anchors == result.activity_anchors == ()


@pytest.mark.parametrize("changes,code", [
    ({"symbol": "AAPL"}, "INITIAL_POSITION_UNSUPPORTED"),
    ({"side": "short"}, "INITIAL_POSITION_UNSUPPORTED"),
    ({"asset_class": "crypto"}, "INITIAL_POSITION_UNSUPPORTED"),
    ({"qty": "0"}, "INITIAL_EVIDENCE_INVALID"),
    ({"qty": "-1"}, "INITIAL_EVIDENCE_INVALID"),
    ({"qty": 1}, "INITIAL_EVIDENCE_INVALID"),
    ({"market_value": "0"}, "INITIAL_EVIDENCE_INVALID"),
    ({"cost_basis": None}, "INITIAL_COST_BASIS_MISSING"),
    ({"avg_entry_price": None}, "INITIAL_COST_BASIS_MISSING"),
])
def test_unsupported_or_incomplete_current_positions_block(changes, code):
    blocked(collector(positions=[POSITION | changes])[0], code)


@pytest.mark.parametrize("changes,code", [
    ({"id": OTHER}, "ACCOUNT_IDENTITY_MISMATCH"),
    ({"id": "not-a-uuid"}, "ACCOUNT_IDENTITY_MISMATCH"),
    ({"currency": "EUR"}, "CURRENCY_UNSUPPORTED"),
    ({"status": "INACTIVE"}, "ACCOUNT_BLOCKED"),
    ({"account_blocked": True}, "ACCOUNT_BLOCKED"),
    ({"trading_blocked": 0}, "ACCOUNT_BLOCKED"),
    ({"cash": "-1"}, "INITIAL_EVIDENCE_INVALID"),
    ({"cash": 100000}, "INITIAL_EVIDENCE_INVALID"),
    ({"equity": "100501", "portfolio_value": "100501"}, "INITIAL_EVIDENCE_INVALID"),
    ({"short_market_value": "-1"}, "INITIAL_POSITION_UNSUPPORTED"),
])
def test_account_and_financial_consistency_fail_closed(changes, code):
    blocked(collector(account=ACCOUNT_DATA | changes)[0], code)


def test_duplicate_position_symbol_or_asset_never_silently_merges():
    for positions in ([POSITION, POSITION], [POSITION, POSITION | {"symbol": "QQQ"}]):
        blocked(collector(positions=positions)[0], "INITIAL_POSITION_UNSUPPORTED")


@pytest.mark.parametrize("status", ["new", "partially_filled", "pending_cancel", "done_for_day"])
def test_any_active_historic_order_blocks_even_if_open_query_is_empty(status):
    blocked(collector(orders=[ORDER | {"status": status}])[0], "INITIAL_OPEN_ORDERS")


def test_open_order_never_triggers_cancel_or_other_mutation():
    def handle(request):
        if request.url.path == "/v2/orders" and request.url.params.get("status") == "open":
            return httpx.Response(200, json=[ORDER | {"status": "new"}])
        return httpx.Response(200, json={
            "/v2/clock": {"timestamp": NOW.isoformat()}, "/v2/account": ACCOUNT_DATA,
            "/v2/positions": [POSITION],
        }[request.url.path])
    instance, _, calls = collector(handle)
    blocked(instance, "INITIAL_OPEN_ORDERS")
    assert len(calls) == 4 and all(request.method == "GET" for request in calls)


@pytest.mark.parametrize("kind", ["cash", "position", "basis", "order", "activity"])
def test_drift_between_independent_reads_rejects_entire_evidence(kind):
    read = 0
    def handle(request):
        nonlocal read
        route = request.url.path
        if route == "/v2/clock":
            read += 1
            value = {"timestamp": NOW.isoformat()}
        elif route == "/v2/account":
            value = ACCOUNT_DATA | ({"cash": "100001", "equity": "100501",
                                     "portfolio_value": "100501"}
                                    if read == 2 and kind == "cash" else {})
        elif route == "/v2/positions":
            changes = ({"qty": "2"} if kind == "position" else {"cost_basis": "301"})
            value = [POSITION | (changes if read == 2 and kind in {"position", "basis"} else {})]
        elif route == "/v2/orders":
            value = [] if request.url.params.get("status") == "open" else (
                [ORDER | ({"qty": "3"} if read == 2 and kind == "order" else {})]
            )
        else:
            value = [ACTIVITY | ({"price": "301"} if read == 2 and kind == "activity" else {})]
        return httpx.Response(200, json=value)
    blocked(collector(handle)[0], "INITIAL_EVIDENCE_CHANGED")


def test_preimport_daily_fee_or_unknown_kind_is_preserved_as_anchor_without_replay():
    for kind in ("FEE", "CFEE", "DIV", "UNRECOGNIZED"):
        activity = {"id": "20261020::old", "activity_type": kind,
                    "date": "2026-10-20", "net_amount": "-7.001", "description": CANARY}
        result = collector(activities=[activity])[0].collect(7, proof())
        assert result.cash == Decimal("100000")
        assert result.activity_anchors[0].kind == kind
        assert result.activity_anchors[0].activity_date.isoformat() == "2026-10-20"
        assert result.activity_anchors[0].occurred_at is None


def test_creation_time_filter_does_not_mislabel_nontrade_settlement_date():
    activity = {"id": "20261021::fee", "activity_type": "FEE", "date": "2026-10-22T00:00:00Z",
                "created_at": "2026-10-21T09:00:00Z", "net_amount": "-1"}
    result = collector(activities=[activity])[0].collect(7, proof())
    assert result.activity_anchors[0].activity_date.isoformat() == "2026-10-22"
    assert result.activity_anchors[0].created_at == NOW - timedelta(hours=1)


def test_terminal_unsubmitted_rejection_can_be_anchored_without_fabricating_submission():
    order = ORDER | {"status": "rejected", "submitted_at": None, "updated_at": None,
                     "created_at": "2026-10-20T10:00:00Z"}
    result = collector(orders=[order])[0].collect(7, proof())
    assert result.order_anchors[0].submitted_at is None
    assert result.order_anchors[0].updated_at is None


@pytest.mark.parametrize("status", ["canceled", "expired", "rejected"])
@pytest.mark.parametrize("average", [{}, {"filled_avg_price": None},
                                    {"filled_avg_price": "0.000"}])
def test_only_explicit_zero_fill_terminal_attempt_is_proven_untraded(status, average):
    order = ORDER | {"status": status, "filled_qty": "0.000"} | average
    anchor = collector(orders=[order])[0].collect(7, proof()).order_anchors[0]
    assert anchor.untraded is True
    with pytest.raises(FrozenInstanceError):
        anchor.untraded = False


@pytest.mark.parametrize("change", [
    {"status": "filled"}, {"status": "replaced"}, {"filled_qty": None},
    {"filled_qty": "0.001"}, {"filled_qty": "-1"}, {"filled_qty": "bad"},
    {"filled_qty": "NaN"}, {"filled_qty": "0e0"},
    {"filled_avg_price": "0.001"}, {"filled_avg_price": "bad"},
])
def test_ambiguous_or_actual_fills_are_not_marked_untraded(change):
    order = ORDER | {"status": "canceled", "filled_qty": "0"} | change
    assert not collector(orders=[order])[0].collect(7, proof()).order_anchors[0].untraded


def test_missing_fill_quantity_does_not_prove_untraded_and_default_is_false():
    order = ORDER | {"status": "canceled"}
    del order["filled_qty"]
    anchor = collector(orders=[order])[0].collect(7, proof()).order_anchors[0]
    assert anchor.untraded is False
    assert module.InitialOrderAnchor(
        anchor.provider_order_id, anchor.status, anchor.submitted_at,
        anchor.updated_at, anchor.evidence_hash,
    ).untraded is False


@pytest.mark.parametrize("change", [{"filled_qty": 0}, {"filled_qty": False},
                                   {"filled_avg_price": 0}, {"filled_avg_price": False}])
def test_numeric_fill_fields_cannot_bypass_string_proof_requirement(change):
    order = ORDER | {"status": "canceled", "filled_qty": "0"} | change
    blocked(collector(orders=[order])[0], "INITIAL_EVIDENCE_INVALID")


def test_pagination_bound_and_deadline_are_not_optional(monkeypatch):
    monkeypatch.setattr(module, "_MAX_PAGES", 1)
    monkeypatch.setattr(module, "_ORDER_PAGE", 1)
    blocked(collector()[0], "INITIAL_HISTORY_INCOMPLETE")
    monotonic = Mock(side_effect=[0., 31.])
    monkeypatch.setattr(module, "monotonic", monotonic)
    instance, _, calls = collector()
    blocked(instance, "PROVIDER_UNAVAILABLE")
    assert calls == []


def test_missing_cost_basis_is_not_inferred_even_when_average_and_quantity_are_present():
    position = POSITION.copy()
    del position["cost_basis"]
    blocked(collector(positions=[position])[0], "INITIAL_COST_BASIS_MISSING")


def test_full_pages_are_exhausted_with_activity_and_order_identity_cursors(monkeypatch):
    monkeypatch.setattr(module, "_ORDER_PAGE", 2)
    monkeypatch.setattr(module, "_ACTIVITY_PAGE", 2)
    # More identities than a page share exactly the same submission timestamp.
    orders = [ORDER | {"id": str(UUID(int=i + 1))} for i in range(3)]
    activities = [ACTIVITY | {"id": f"20261020::{i}"} for i in range(3)]
    def handle(request):
        route, params = request.url.path, request.url.params
        if route == "/v2/clock":
            value = {"timestamp": NOW.isoformat()}
        elif route == "/v2/account":
            value = ACCOUNT_DATA
        elif route == "/v2/positions":
            value = [POSITION]
        elif route == "/v2/orders":
            assert "until" not in params and "after" not in params
            start = int(UUID(params["after_order_id"])) if "after_order_id" in params else 0
            value = [] if params.get("status") == "open" else orders[start:start + 2]
        else:
            start = int(params["page_token"].split("::")[1]) + 1 if "page_token" in params else 0
            value = activities[start:start + 2]
        return httpx.Response(200, json=value)
    result = collector(handle)[0].collect(7, proof())
    assert len(result.order_anchors) == len(result.activity_anchors) == 3


@pytest.mark.parametrize("kind", ["order", "activity"])
def test_stuck_or_unpageable_full_history_is_explicitly_incomplete(monkeypatch, kind):
    monkeypatch.setattr(module, "_ORDER_PAGE", 2)
    monkeypatch.setattr(module, "_ACTIVITY_PAGE", 2)
    orders = [ORDER, ORDER | {"id": str(UUID(int=4))}] if kind == "order" else [ORDER]
    activities = [ACTIVITY, ACTIVITY | {"id": "20261020::two"}]
    blocked(collector(orders=orders, activities=activities)[0], "INITIAL_HISTORY_INCOMPLETE")


@pytest.mark.parametrize("status,code", [
    (401, "CREDENTIALS_INVALID"), (403, "CREDENTIALS_INVALID"),
    (429, "PROVIDER_RATE_LIMITED"), (500, "PROVIDER_UNAVAILABLE"),
    (302, "PROVIDER_UNAVAILABLE"),
])
def test_http_failures_are_redacted_and_redirects_not_followed(status, code):
    instance, _, calls = collector(lambda _: httpx.Response(
        status, text=CANARY, headers={"Location": "https://invalid.example/"},
    ))
    blocked(instance, code)
    assert len(calls) == 1


@pytest.mark.parametrize("response,code", [
    (httpx.Response(200, content=b'{"timestamp":"a","timestamp":"b"}',
                    headers={"content-type": "application/json"}), "INITIAL_EVIDENCE_INVALID"),
    (httpx.Response(200, content=CANARY), "PROVIDER_UNAVAILABLE"),
    (httpx.Response(200, content=b'{}', headers={
        "content-type": "application/json", "content-length": "1048577",
    }), "PROVIDER_UNAVAILABLE"),
    (httpx.Response(200, content=b'x' * 1048577,
                    headers={"content-type": "application/json"}), "PROVIDER_UNAVAILABLE"),
])
def test_malformed_or_oversized_response_cannot_escape_bounds(response, code):
    blocked(collector(lambda _: response)[0], code)


def test_identity_generation_and_staleness_reject_before_io():
    for wrong in (proof(provider_account_id=OTHER), proof(credential_generation=8),
                  proof(observed_at=NOW - timedelta(seconds=120))):
        instance, reader, calls = collector()
        with pytest.raises(InitialEvidenceUnavailable, match="ACCOUNT_IDENTITY_MISMATCH"):
            instance.collect(7, wrong)
        reader.load.assert_not_called()
        assert calls == []
    instance, reader, calls = collector(pinned=OTHER)
    blocked(instance, "ACCOUNT_IDENTITY_MISMATCH")
    assert calls == []
    instance, reader, calls = collector()
    reader.load.side_effect = PaperCredentialsUnavailable()
    blocked(instance, "CREDENTIALS_MISSING")
    assert calls == []


def test_transport_exceptions_and_clock_drift_are_redacted(caplog):
    def failed(request):
        raise httpx.ReadTimeout(CANARY, request=request)
    blocked(collector(failed)[0], "PROVIDER_UNAVAILABLE")
    assert CANARY not in caplog.text
    clock = Mock(now=Mock(side_effect=[NOW, NOW + timedelta(seconds=30)]))
    blocked(collector(clock=clock)[0], "INITIAL_EVIDENCE_STALE")


def test_arbitrary_transport_and_invalid_fixed_identity_are_refused():
    with pytest.raises(ValueError, match="exact MockTransport"):
        InitialPaperEvidenceCollector(credentials=Mock(), clock=Mock(),
                                      expected_account_id=ACCOUNT, transport=Mock())
    with pytest.raises(InitialEvidenceUnavailable, match="ACCOUNT_IDENTITY_MISMATCH"):
        InitialPaperEvidenceCollector(credentials=Mock(), clock=Mock(), expected_account_id=CANARY)


def test_production_client_uses_only_fixed_paper_proxy_and_ignores_ambient_proxies(monkeypatch):
    mocked, reader, calls = collector()
    transport = mocked._transport
    real_client, options = httpx.Client, []
    def client_factory(**kwargs):
        options.append(kwargs.copy())
        # Code-only test replacement: the production collector receives no
        # transport/host/proxy override and cannot make a real network call.
        kwargs["proxy"] = None
        kwargs["transport"] = transport
        return real_client(**kwargs)
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid.example:9999")
    monkeypatch.setattr(module.httpx, "Client", client_factory)
    instance = InitialPaperEvidenceCollector(
        credentials=reader, clock=Mock(now=Mock(return_value=NOW)), expected_account_id=ACCOUNT,
    )
    assert instance.collect(7, proof()).cash == Decimal("100000")
    assert options[0]["proxy"] == "http://paper-execution-egress:8080"
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert all(request.url.host == "paper-api.alpaca.markets" for request in calls)
