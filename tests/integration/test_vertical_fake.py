"""The complete local Fake trade persists a single linked trace on PostgreSQL."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


def test_market_to_partial_and_final_fake_fill():
    assert "PASS full fixture-market-mock-planner-risk-execution-reconciliation chain" in host(
        guest_path=Path(__file__).with_name("vertical_support.py"),
        guest_args=("happy",),
    )


@pytest.mark.parametrize("scenario", ("budget-race", "decision-down"))
def test_vertical_budget_and_independent_pause(scenario):
    assert f"PASS {scenario}" in host(
        guest_path=Path(__file__).with_name("vertical_support.py"),
        guest_args=(scenario,),
    )
