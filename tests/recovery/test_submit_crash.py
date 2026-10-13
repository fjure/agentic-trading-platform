"""Real PostgreSQL submit/crash boundaries with a process-local FakeBroker only."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "flat",
        "crash-before",
        "crash-after",
        "timeout",
        "mismatch",
        "secret-error",
        "expiry",
        "capability-expiry",
        "pause-gap",
        "pause-race",
    ],
)
def test_durable_submit_and_crash_recovery(scenario):
    assert "PASS" in host(
        guest_path=Path(__file__).with_name("execution_support.py"), guest_args=(scenario,)
    )
