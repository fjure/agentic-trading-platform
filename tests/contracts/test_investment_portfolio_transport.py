"""IEF-T-P01 through P04 at the authenticated canonical HTTP boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.control_api.app import create_app
from services.control_api.auth import AuthDenied, LocalAuth
from services.control_api.investment_portfolio_routes import PORTFOLIO_PATH, PortfolioPages
from tests.contracts.test_control_api import ORIGIN, validate, write

VALUED_AT = "2026-11-24T08:00:00Z"


def position(symbol, revision=2, currency="USD"):
    return {
        "instrument": symbol, "currency": currency, "revision": revision,
        "quantity": "1.000000000000000001", "average_price": "35.125",
        "cost_basis": None, "realized_pnl": "0",
    }


def source(*positions, cash="0", revision=3, currency="USD", status="CURRENT"):
    return {
        "snapshot_id": "source-snapshot", "revision": revision, "generation": None,
        "as_of": VALUED_AT, "quality": status, "reconciliation": "NOT_APPLICABLE",
        "currency": currency, "cash": cash, "equity": cash, "exposure": "0",
        "positions": list(positions),
    }


@pytest.fixture
def portfolio(monkeypatch):
    state = SimpleNamespace(
        fake=source(position("QQQ"), position("SPY")), demo=source(position("SYNTH"), cash="1000"),
        paper=source(cash="777") | {
            "generation": 1, "reconciliation": "CURRENT", "reconciliation_at": VALUED_AT,
        },
        calls=[],
    )

    def fake(_reader, _trace):
        state.calls.append("FAKE")
        return deepcopy(state.fake)

    def paper(_reader, _trace):
        state.calls.append("ALPACA_PAPER")
        return deepcopy(state.paper)

    def demo(run_id, actor, _trace):
        state.calls.append(("DEMO", run_id, actor))
        if run_id != "owned-demo" or actor != "local-operator":
            raise AuthDenied(404, "RESOURCE_NOT_FOUND")
        return deepcopy(state.demo)

    monkeypatch.setattr("services.control_api.investment_portfolio_local.read_fake_portfolio", fake)
    monkeypatch.setattr(
        "services.control_api.investment_portfolio_paper.read_paper_portfolio", paper,
    )
    auth = LocalAuth()
    app = create_app(
        auth=auth, fake_portfolio=object(), demo=SimpleNamespace(portfolio=demo), paper=object(),
    )
    client = TestClient(
        app, base_url=ORIGIN, client=("127.0.0.1", 46000), raise_server_exceptions=False,
    )
    return client, auth, state


def activate(http, auth):
    assert write(
        http, "/api/v1/session", {"activationCode": auth.issue_activation_code()},
        **{"Idempotency-Key": "portfolio-test-activation"},
    ).status_code == 204


def get(client, context, **query):
    result = client.get(PORTFOLIO_PATH, params={"context": context, **query})
    assert result.headers["Cache-Control"] == "no-store"
    schema = "InvestmentPortfolioViewV1" if result.is_success else "InvestmentProblemV1"
    validate(schema, result.json())
    return result


def test_scoped_sources_and_reconciliation_evidence(portfolio):
    client, auth, state = portfolio
    assert get(client, "FAKE").status_code == 401
    assert not state.calls
    activate(client, auth)
    rejected = client.get(PORTFOLIO_PATH, params={"context": "FAKE"}, headers={"Origin": "null"})
    assert rejected.status_code == 403
    validate("InvestmentProblemV1", rejected.json())
    assert not state.calls

    fake = get(client, "FAKE").json()
    demo = get(client, "DEMO", demoRunId="owned-demo").json()
    paper = get(client, "ALPACA_PAPER").json()
    cash_values = [view["metrics"]["cash"]["value"] for view in (fake, demo, paper)]
    assert cash_values == ["0", "1000", "777"]
    assert len({view["snapshotId"] for view in (fake, demo, paper)}) == 3
    assert demo["contextReference"]["demoRunId"] == "owned-demo"
    assert paper["reconciliationAt"] == VALUED_AT
    assert fake["reconciliationAt"] is None
    assert paper["positions"] == []
    assert get(client, "DEMO", demoRunId="foreign").status_code == 404
    assert state.calls == ["FAKE", ("DEMO", "owned-demo", "local-operator"), "ALPACA_PAPER",
                           ("DEMO", "foreign", "local-operator")]


@pytest.mark.parametrize("query", [
    "context=DEMO", "context=LIVE", "context=FAKE&demoRunId=foreign",
    "context=FAKE&context=ALPACA_PAPER", "context=FAKE&limit=101",
    "context=FAKE&limit=1e0", "context=FAKE&cursor=", "context=FAKE&actorId=foreign",
])
def test_invalid_queries_do_not_touch_sources(portfolio, query):
    client, auth, state = portfolio
    activate(client, auth)
    result = client.get(f"{PORTFOLIO_PATH}?{query}")
    assert result.status_code == 422
    validate("InvestmentProblemV1", result.json())
    assert state.calls == []


def test_page_replay_and_revision_change_require_new_snapshot(portfolio, monkeypatch):
    client, auth, state = portfolio
    activate(client, auth)
    first = get(client, "FAKE", limit=1).json()
    cursor = first["nextCursor"]
    assert [p["instrument"] for p in first["positions"]] == ["QQQ"]
    state.fake["observed_at"] = "2026-11-24T08:02:00Z"
    second = get(client, "FAKE", limit=1, cursor=cursor).json()
    assert second["snapshotId"] == first["snapshotId"]
    assert [p["instrument"] for p in second["positions"]] == ["SPY"]
    assert second["nextCursor"] is None
    assert get(client, "ALPACA_PAPER", limit=1, cursor=cursor).status_code == 409
    assert get(client, "FAKE", limit=2, cursor=cursor).status_code == 409
    suffix = "0" if cursor.endswith("1") else "1"
    assert get(client, "FAKE", limit=1, cursor=cursor[:-1] + suffix).status_code == 409
    with monkeypatch.context() as other:
        other.setattr(auth, "authenticate", lambda _token: "operator-2")
        assert get(client, "FAKE", limit=1, cursor=cursor).status_code == 409
    state.fake["positions"][1]["revision"] = 3
    state.fake["positions"][1]["quantity"] = "2"
    conflict = get(client, "FAKE", limit=1, cursor=cursor)
    assert conflict.status_code == 409 and conflict.json()["code"] == "CURSOR_EXPIRED"
    assert get(client, "FAKE", limit=1).json()["snapshotId"] != first["snapshotId"]


def test_unavailable_basis_differs_from_empty_zero_and_missing_fee(portfolio):
    client, auth, state = portfolio
    activate(client, auth)
    state.fake = source(position("EU-ETF", currency="EUR"), status="STALE")
    data = get(client, "FAKE").json()
    assert data["asOf"] == VALUED_AT
    assert data["metrics"]["cash"]["value"] == "0"
    assert data["metrics"]["cash"]["availability"] == "STALE"
    item = data["positions"][0]
    assert item["quantity"]["value"] == "1.000000000000000001"
    assert item["averagePrice"]["value"] is None
    assert item["averagePrice"]["nativeAmount"] == {
        "currency": "EUR", "value": "35.125", "asOf": VALUED_AT, "availability": "STALE",
    }
    assert item["marketValue"]["value"] is None
    assert item["costBasis"]["value"] is None
    assert item["realizedPnl"]["value"] is None
    assert item["realizedPnl"]["reasonCodes"] == ["FEE_EVIDENCE_MISSING"]
    for field in ("totalReturn", "dailyReturn", "drawdown"):
        assert data["metrics"][field]["value"] is None
        assert data["metrics"][field]["reasonCodes"] == ["PERFORMANCE_HISTORY_NOT_AVAILABLE"]
    state.fake.update(snapshot_id=None, revision=None, as_of=None, quality="UNAVAILABLE")
    data = get(client, "FAKE").json()
    assert data["positions"] is None
    assert data["metrics"]["cash"]["value"] is None
    assert data["metrics"]["cash"]["reasonCodes"] == ["LEDGER_NOT_PREPARED"]


def test_position_cursor_expires_without_reading_a_source(monkeypatch):
    clock = SimpleNamespace(time=lambda: 100)
    monkeypatch.setattr("services.control_api.investment_portfolio_routes.time", clock)
    pages = PortfolioPages()
    binding = ("local-operator", "DEMO", "owned-demo", 20)
    digest = "0" * 64
    token = pages.cursor(binding, 20, digest)
    assert pages.read(binding, token) == (20, digest)
    clock.time = lambda: 400
    with pytest.raises(AuthDenied) as expired:
        pages.read(binding, token)
    assert (expired.value.status, expired.value.code) == (409, "CURSOR_EXPIRED")
