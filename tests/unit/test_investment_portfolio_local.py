"""Portfolio sources preserve exact committed data and explicit ownership failures."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.control_api import investment_portfolio_local as local
from services.control_api.auth import AuthDenied
from services.demo.app import create_app

TRACE = "1" * 32


def _basis(**changes):
    return {
        "id": "seed-1",
        "currency": "USD",
        "as_of": datetime(2026, 11, 24, 12, 30, tzinfo=UTC),
        "cash": Decimal("0.000"),
        "equity": Decimal("10.007000"),
        "exposure": Decimal("10.007000"),
        "quality": "VALID",
        "ledger_version": 3,
        "account_revision": 3,
        "context_revision": 3,
        "account_currency": "USD",
        "has_other_broker": False,
        "future_valuation": False,
        "valuation_current": True,
    } | changes


@pytest.mark.parametrize("change", [
    {"has_other_broker": True},
    {"future_valuation": True},
    {"context_revision": 4},
    {"account_revision": 4},
    {"account_currency": "EUR"},
    {"quality": "CONFLICT"},
])
def test_mixed_or_inconsistent_source_is_unavailable(change):
    assert local._quality(_basis(**change)) == "INCONSISTENT"


def test_valid_but_expired_valuation_is_stale():
    assert local._quality(_basis(valuation_current=False)) == "STALE"
    assert local._quality(_basis()) == "CURRENT"


def test_positions_preserve_native_currency_and_exact_numbers():
    rows = [{
        "instrument": "QQQ",
        "currency": "EUR",
        "ledger_version": 2,
        "quantity": Decimal("5.000000000000000004"),
        "average_price": None,
    }]
    result = local._portfolio(_basis(quality="STALE"), "STALE", rows)
    assert result["cash"] == "0.000"
    assert result["as_of"] == "2026-11-24T12:30:00Z"
    assert result["blockers"] == ["STALE_EVIDENCE"]
    assert result["positions"] == [{
        "instrument": "QQQ",
        "currency": "EUR",
        "revision": 2,
        "quantity": "5.000000000000000004",
        "average_price": None,
        "cost_basis": None,
        "realized_pnl": None,
    }]
    assert local._portfolio(_basis(), "CURRENT", [])["positions"] == []
    assert local.unavailable_local_portfolio()["positions"] is None


def test_fake_read_only_targets_lab_runtime(monkeypatch):
    def read(ledger, account_id, database):
        return {"ledger": ledger, "account_id": account_id, "database": database}

    monkeypatch.setattr(local, "read_local_ledger", read)
    reader = SimpleNamespace(ledger=object(), account_id="paper")
    assert local.read_fake_portfolio(reader, TRACE) == {
        "ledger": reader.ledger, "account_id": "paper", "database": "lab_runtime"
    }
    assert local.read_fake_portfolio(None, TRACE)["blockers"] == ["CAPABILITY_UNAVAILABLE"]


@pytest.mark.parametrize(
    "status, failure", [(400, LookupError), (404, LookupError), (503, AuthDenied)]
)
def test_demo_errors_preserve_unavailability_distinction_with_exact_scope(status, failure):
    class Demo:
        def portfolio(self, run_id, actor_id, trace_id):
            assert (run_id, actor_id, trace_id) == ("selected-run", "local-a", TRACE)
            raise AuthDenied(status, "TEMPORARILY_UNAVAILABLE")

    with pytest.raises(failure):
        local.read_demo_portfolio(Demo(), "selected-run", "local-a", TRACE)


def test_private_demo_protocol_requires_exact_fields_and_does_not_return_actor():
    class Demo:
        def portfolio(self, run_id, actor, trace):
            assert (run_id, actor, trace) == ("selected-run", "local-a", TRACE)
            return local.unavailable_local_portfolio()

        def close(self):
            pass

    with TestClient(create_app(Demo())) as client:
        data = {
            "operation": "portfolio", "run_id": "selected-run", "actor": "local-a", "trace": TRACE,
        }
        assert client.post("/internal/demo", json=data | {"currency": "EUR"}).status_code == 400
        result = client.post("/internal/demo", json=data)
        assert result.status_code == 200
        assert result.json() == local.unavailable_local_portfolio()
