"""Public budget/request integrity and pre-I/O Worker operator guards."""

from dataclasses import replace
from decimal import Decimal

import pytest

from packages.domain.paper_smoke import SmokeRequest
from services.worker.paper_smoke_plan import main


def request(**changes):
    return replace(SmokeRequest(
        "10000000-0000-4000-8000-000000000001", "paper", "a" * 64, "SPY",
        Decimal("100"), Decimal("101"), "state:verified",
    ), **changes)


@pytest.mark.parametrize("changes", [
    {"run_id": "not-a-uuid"}, {"run_id": "10000000000040008000000000000001"},
    {"account_id": "live"}, {"account_fingerprint": "A" * 64},
    {"instrument_id": "BTC/USD"}, {"market_state_id": "unsafe\nstate"},
    {"limit_price": Decimal("100.001")}, {"limit_price": Decimal("0")},
    {"limit_price": Decimal("NaN")}, {"limit_price": Decimal("Infinity")},
    {"limit_price": "1e2"}, {"max_notional": Decimal("100.99")},
    {"max_notional": Decimal("1000.01")}, {"max_notional": Decimal("-1")},
])
def test_request_rejects_unbounded_or_unconfirmed_values(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_equivalent_decimal_spellings_preserve_binding():
    first = request()
    assert first.digest() == request(limit_price=Decimal("100.00")).digest()
    assert first.digest() != request(limit_price=Decimal("99.99")).digest()
    assert first.digest() != request(max_notional=Decimal("102")).digest()
    assert first.digest() != request(market_state_id="other-state").digest()
    assert first.proposal_id != first.decision_id
    assert first.trace_id == "10000000000040008000000000000001"
    with pytest.raises(AttributeError):
        first.limit_price = Decimal("99")


def test_worker_cli_rejects_noninteractive_before_private_configuration(monkeypatch, capsys):
    import services.runtime.config as config

    def forbidden(*_args, **_kwargs):
        pytest.fail("DB/private configuration reached without interactive opt-in")

    monkeypatch.setattr(config, "database_engine", forbidden)
    args = [
        "--paper-only", "--operator-confirmed", "--run-id", request().run_id,
        "--account-fingerprint", "a" * 64, "--instrument", "SPY",
        "--limit-price", "100", "--max-notional", "101", "--market-state-id", "state",
    ]
    assert main(args) == 2
    assert capsys.readouterr().err == "SMOKE_PLAN_ABORTED\n"
