"""Run control backend against a disposable PostgreSQL database and real roles."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize("scenario", ["full", "missing-policy"])
def test_control_http_postgres(scenario) -> None:
    result = host(
        guest_path=Path(__file__).with_name("control_http_support.py"), guest_args=(scenario,)
    )
    assert "PASS" in result
