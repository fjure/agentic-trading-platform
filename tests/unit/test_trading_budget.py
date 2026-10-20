"""Exact cap arithmetic and strict public profile validation."""

from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.domain.models import Target
from packages.risk.budget import budget_reasons, read_cap, read_strategy, validate_budget
from services.control_api.auth import AuthDenied
from services.control_api.trading_profile import TradingProfileBackend


@pytest.mark.parametrize("value", ["0", "-1", "1e2", "NaN", "Infinity", "1,00", "01",
    "1000000.000000000000000001", "0.0000000000000000001", "1000001", 100, 100.0, None])
def test_budget_strict_invalid(value):
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        validate_budget(value)


@pytest.mark.parametrize("value", ["100", "0.000000000000000001", "1000000", "999999.9"])
def test_budget_exact_valid(value):
    assert validate_budget(value) == Decimal(value)


def _facts(gross="0", reserved="0"):
    return SimpleNamespace(gross_notional=Decimal(gross), reserved_notional=Decimal(reserved),
                           market_price=Decimal("100"), spread=Decimal("0.01"))


def _proposal(target=Target.LONG):
    return SimpleNamespace(target=target, quantity=Decimal("1"), limit_price=None)


def test_budget_adds_candidate_once_and_buffer_exact():
    assert budget_reasons(_proposal(), _facts(), Decimal("101.0101")) == ()
    assert budget_reasons(_proposal(), _facts(), Decimal("101.010099999999999999")) == (
        "CASH_LIMIT",)
    assert budget_reasons(_proposal(), _facts("100.01", "100.01"), Decimal("202.0202")) == ()
    assert budget_reasons(_proposal(), _facts("100.01", "100.01"), Decimal("202.02")) == (
        "CASH_LIMIT",)


def test_global_decimal_precision_cannot_relax_cap():
    with localcontext() as context:
        context.prec = 3
        assert budget_reasons(_proposal(), _facts(), Decimal("101.01")) == ("CASH_LIMIT",)


def test_exit_and_legacy_cap_behavior_preserve_other_gates():
    assert budget_reasons(_proposal(Target.FLAT), _facts("1000"), Decimal("1")) == ()
    assert budget_reasons(_proposal(), _facts("1000"), None) == ()


def test_missing_profile_does_not_mask_unavailable_database():
    connection = Mock()
    connection.execute.return_value.scalar_one_or_none.return_value = None
    assert read_cap(connection, "paper") is None
    connection.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        read_cap(connection, "paper")


def test_strategy_version_pair_is_allowlisted():
    connection = Mock()
    connection.execute.return_value.one_or_none.return_value = None
    assert read_strategy(connection, "paper") == ("mock-neutral", "mock-neutral.v1")
    connection.execute.return_value.one_or_none.return_value = SimpleNamespace(
        strategy="neutral-baseline", strategy_version="unrecognized-version")
    with pytest.raises(ValueError, match="INVALID_STRATEGY_VERSION"):
        read_strategy(connection, "paper")


@pytest.mark.parametrize("payload", [{"budget": "1", "currency": "EUR", "strategy": "mock-neutral"},
    {"budget": "1", "currency": "USD", "strategy": "jev"},
    {"budget": "100.0000000000000000001", "currency": "USD", "strategy": "mock-neutral"},
    {"budget": "1", "currency": "USD", "strategy": [], "extra": "value"}])
def test_invalid_profile_has_no_database_access(payload):
    ledger = Mock()
    with pytest.raises(AuthDenied) as error:
        TradingProfileBackend(ledger, "paper").update(payload, "operator", "x" * 16, "a" * 32)
    assert error.value.code == "INVALID_REQUEST" and error.value.status == 400
    ledger.transaction.assert_not_called()
