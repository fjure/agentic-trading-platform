"""Exact boundaries and fail-closed safety properties of the pure evaluator."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packages.domain import models as m
from packages.risk.engine import RiskFacts, RiskLimits, evaluate

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
LIMITS = RiskLimits()


def test_versioned_simulation_config():
    path = Path(__file__).resolve().parents[2] / "config" / "risk" / "paper.yaml"
    assert RiskLimits.from_yaml(path) == LIMITS


def _proposal(target=m.Target.LONG, quantity="1"):
    return m.TradeProposal(
        id="proposal",
        decision_id="decision",
        market_state_id="market",
        instrument_id="SPY",
        target=target,
        quantity=Decimal(quantity),
        created_at=NOW,
        valid_until=NOW + timedelta(seconds=30),
        trace_id="a" * 32,
    )


def _facts(**changes):
    values = dict(
        now=NOW,
        mode=m.Mode.PAPER,
        safety_state=m.SafetyState.RUNNING,
        policy_version=1,
        portfolio_version=1,
        proposal_portfolio_version=1,
        capability_version=1,
        capability=m.BrokerCapabilities(
            broker_id="fake",
            instrument_id="SPY",
            version=1,
            paper=m.Capability.CONFIRMED,
            regular_session=m.Capability.CONFIRMED,
            market_order=m.Capability.CONFIRMED,
            limit_order=m.Capability.CONFIRMED,
            fractional=m.Capability.CONFIRMED,
            quantity_step=Decimal("0.000000000000000001"),
            price_tick=Decimal("0.01"),
        ),
        session_open=True,
        session_valid_until=NOW + timedelta(minutes=1),
        market_quality=m.Quality.VALID,
        market_available_at=NOW,
        market_price=Decimal("100"),
        spread=Decimal("0"),
        equity=Decimal("1000"),
        cash=Decimal("1000"),
        day_start_equity=Decimal("1000"),
        realized_pnl_today=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        instrument_exposure=Decimal("0"),
        gross_notional=Decimal("0"),
        reserved_notional=Decimal("0"),
        position_count_instrument=0,
        position_count_total=0,
        open_exit_quantity=Decimal("0"),
        held_quantity=Decimal("0"),
        reconciliation_current=True,
    )
    values.update(changes)
    return RiskFacts(**values)


def test_exact_notional_and_loss_boundaries_ignore_ambient_rounding():
    prior = getcontext().prec
    try:
        getcontext().prec = 6
        assert evaluate(_proposal(), _facts(), LIMITS) == ()
        assert "POSITION_NOTIONAL_LIMIT" in evaluate(
            _proposal(), _facts(instrument_exposure=Decimal("0.000000000000000001")), LIMITS
        )
        assert "DAILY_LOSS_LIMIT" in evaluate(
            _proposal(), _facts(realized_pnl_today=Decimal("-10")), LIMITS
        )
        assert "DAILY_LOSS_LIMIT" not in evaluate(
            _proposal(), _facts(realized_pnl_today=Decimal("-9.999999999999999999")), LIMITS
        )
    finally:
        getcontext().prec = prior


def test_position_counts_capability_session_spread_and_expiry():
    for overrides, code in (
        ({"position_count_instrument": 1}, "INSTRUMENT_POSITION_LIMIT"),
        ({"position_count_total": 2}, "TOTAL_POSITION_LIMIT"),
        ({"mode": m.Mode.DISABLED}, "MODE_OR_SAFETY"),
        ({"safety_state": m.SafetyState.PAUSE_NEW}, "MODE_OR_SAFETY"),
        ({"session_open": False}, "SESSION_CLOSED"),
        ({"spread": Decimal("1.000000000000000001")}, "SPREAD"),
        ({"market_available_at": NOW - timedelta(seconds=121)}, "MARKET_STALE"),
        ({"portfolio_version": 2}, "PORTFOLIO_VERSION"),
        ({"reconciliation_current": False}, "RECONCILIATION"),
    ):
        assert code in evaluate(_proposal(), _facts(**overrides), LIMITS)
    assert "PROPOSAL_EXPIRED" in evaluate(
        _proposal(), _facts(now=NOW + timedelta(seconds=30)), LIMITS
    )
    unknown = replace(_facts().capability, paper=m.Capability.UNKNOWN)
    assert "BROKER_CAPABILITY" in evaluate(_proposal(), _facts(capability=unknown), LIMITS)


def test_flat_still_checks_safety_and_cannot_oversell():
    proposal = _proposal(m.Target.FLAT)
    assert (
        evaluate(
            proposal, _facts(held_quantity=Decimal("1"), realized_pnl_today=Decimal("-100")), LIMITS
        )
        == ()
    )
    assert "EXIT_QUANTITY" in evaluate(
        proposal, _facts(held_quantity=Decimal("1"), open_exit_quantity=Decimal("1")), LIMITS
    )
    assert "MODE_OR_SAFETY" in evaluate(
        proposal, _facts(held_quantity=Decimal("1"), mode=m.Mode.DISABLED), LIMITS
    )


def test_cash_is_reduced_by_existing_buy_reservations_and_market_spread():
    assert "CASH_LIMIT" in evaluate(
        _proposal(),
        _facts(cash=Decimal("100"), reserved_notional=Decimal("0.01")),
        LIMITS,
    )
    assert "CASH_LIMIT" in evaluate(
        _proposal(), _facts(cash=Decimal("100"), spread=Decimal("0.01")), LIMITS
    )


@given(st.integers(min_value=0, max_value=10**18))
def test_reserved_cash_never_becomes_available_again(units):
    reserved = Decimal(units).scaleb(-18)
    reasons = evaluate(_proposal(), _facts(cash=Decimal("100"), reserved_notional=reserved), LIMITS)
    assert ("CASH_LIMIT" in reasons) == (units > 0)


@given(st.integers(min_value=1, max_value=10**18))
def test_pending_exit_quantity_cannot_be_sold_twice(units):
    pending = Decimal(units).scaleb(-18)
    assert "EXIT_QUANTITY" in evaluate(
        _proposal(m.Target.FLAT),
        _facts(held_quantity=Decimal("1"), open_exit_quantity=pending),
        LIMITS,
    )


@pytest.mark.parametrize(
    "changes",
    [{"session_open": "false"}, {"reconciliation_current": 1}, {"position_count_total": True}],
)
def test_untyped_safety_context_is_rejected(changes):
    with pytest.raises(ValueError):
        _facts(**changes)
