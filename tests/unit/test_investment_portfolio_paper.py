"""Stored Paper projection transport and strict boundary checks."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from services.control_api.investment_portfolio_paper import read_paper_portfolio
from services.control_api.paper_client import PaperClient
from services.control_api.paper_commands import PaperCommandError
from services.runtime.paper_api import create_private_paper_app

TRACE = "a" * 32
AT = "2026-11-24T12:00:00.000000Z"


def projection():
    return {
        "snapshot_id": "b" * 64,
        "revision": 4,
        "generation": 1,
        "observed_at": AT,
        "as_of": AT,
        "reconciliation_at": None,
        "quality": "CURRENT",
        "reconciliation": "REQUIRED",
        "currency": "USD",
        "cash": "10000.000000000000000001",
        "equity": "10770.000000000000000001",
        "exposure": "770",
        "positions": [{
            "instrument": "SPY",
            "revision": 2,
            "quantity": "60",
            "average_price": "12.83",
            "cost_basis": None,
            "realized_pnl": None,
        }],
        "blockers": ["RECONCILIATION_REQUIRED"],
    }


def test_investment_paper_portfolio_uses_one_fixed_read_without_broker():
    stored = projection()
    calls = []

    def read(trace):
        calls.append(trace)
        return stored

    app = create_private_paper_app(SimpleNamespace(read_investment_portfolio=read))
    with TestClient(app, base_url="http://paper-api:8012") as private:
        def relay(request):
            assert request.method == "GET"
            assert str(request.url) == f"http://paper-api:8012/investment-portfolio?trace={TRACE}"
            assert not request.content
            response = private.get(request.url.path, params=request.url.params)
            assert response.headers["Cache-Control"] == "no-store"
            return httpx.Response(response.status_code, json=response.json())

        paper = PaperClient(transport=httpx.MockTransport(relay))
        assert read_paper_portfolio(paper, TRACE) == stored
        assert calls == [TRACE]
        for params in ([], [("trace", TRACE), ("trace", TRACE)],
                       [("trace", TRACE), ("account", "other")]):
            assert private.get("/investment-portfolio", params=params).status_code == 400
        assert private.get("/investment-portfolio", params={"trace": TRACE},
                           headers={"Origin": "http://example.test"}).status_code == 400
        assert calls == [TRACE]


@pytest.mark.parametrize("change", [
    {"snapshot_id": "raw-unverified-id"},
    {"cash": 100.25},
    {"observed_at": "2026-11-24T13:00:00+01:00"},
    {"quote": "137"},
])
def test_reject_invalid_internal_portfolio_without_partial_projection(change):
    paper = PaperClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=projection() | change)
    ))
    with pytest.raises(PaperCommandError) as caught:
        read_paper_portfolio(paper, TRACE)
    assert (caught.value.status, caught.value.code) == (503, "TEMPORARILY_UNAVAILABLE")


@pytest.mark.parametrize("positions", [[], None])
def test_empty_positions_are_distinct_from_missing_basis(positions):
    stored = projection() | {"positions": deepcopy(positions)}
    if positions is None:
        stored.update(
            snapshot_id=None, revision=None, generation=None, as_of=None, quality="UNAVAILABLE",
            reconciliation="UNKNOWN", cash=None, equity=None, exposure=None,
            blockers=["LEDGER_NOT_PREPARED"],
        )
    paper = PaperClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=stored)))
    result = read_paper_portfolio(paper, TRACE)
    assert result["positions"] == positions
    assert result["quality"] == ("UNAVAILABLE" if positions is None else "CURRENT")
