"""PTV-24 SQL/MockTransport proof, submit, and recovery boundaries."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    (
        "proofs",
        "mismatch",
        "stale-fence",
        "activation",
        "success",
        "timeout",
        "crash-before",
        "crash-after",
        "commit-failure",
    ),
)
def test_alpaca_execution_sql_boundary(scenario: str) -> None:
    assert f"PASS {scenario}" in host(
        guest_path=Path(__file__).with_name("alpaca_execution_support.py"),
        guest_args=(scenario,),
    )
