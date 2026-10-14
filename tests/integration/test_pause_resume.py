"""Role, watchdog, IPC and recovery health gates over isolated PostgreSQL."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize(
    "scenario",
    [
        "resume",
        "rights",
        "pause-kill",
        "watchdog",
        "future-heartbeat",
        "fencing",
        "socket",
        "stale-data",
        "pause-wins",
        "activation",
        "fenced-submit",
        "stale-submit",
        "db-down",
        "recover-db-sticky",
        "slow-health",
        "broker-extra",
        "socket-negative",
        "kill-cancel",
        "local-reject",
        "session-closed-repair",
        "post-fill-resume",
    ],
)
def test_independent_safety_controls(scenario):
    assert "PASS" in host(
        guest_path=Path(__file__).with_name("control_support.py"), guest_args=(scenario,)
    )
