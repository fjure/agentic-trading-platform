"""PTV-22 actual PostgreSQL boundaries; all inputs and transports are synthetic."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    ("worker", "raw-bootstrap", "execution", "execution-abort", "begin-run", "execution-canceled"),
)
def test_paper_smoke_postgres(scenario: str) -> None:
    root = Path(__file__).resolve().parents[2]
    assert f"PASS {scenario}" in host(
        guest_path=Path(__file__).with_name("paper_smoke_support.py"),
        guest_args=(scenario,),
        support_paths=(
            Path(__file__).with_name("alpaca_reconciliation_support.py"),
            root / "config/markets/paper.yaml",
        ),
    )
