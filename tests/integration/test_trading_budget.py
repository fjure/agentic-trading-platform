"""Real isolated PostgreSQL profile, serialization and fresh submit cap checks."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize("scenario", ["profile", "reserve-race", "update-exposure", "unknown",
                                     "submit-recheck", "legacy"])
def test_trading_budget_postgres(scenario):
    assert "PASS" in host(guest_path=Path(__file__).with_name("trading_budget_support.py"),
                          guest_args=(scenario,))
