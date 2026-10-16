"""Isolierte PostgreSQL-Prüfung des Supervisor-Reconnectvertrags."""

import json
from pathlib import Path

import pytest

from tests.contracts.test_control_api import validate
from tests.integration.risk_support import host


@pytest.mark.parametrize("scenario", ["snapshot-race", "pagination", "retention", "publish-race"])
def test_sse_cursor_postgres(scenario):
    output = host(
        guest_path=Path(__file__).with_name("supervisor_support.py"), guest_args=(scenario,)
    )
    result = json.loads(output.splitlines()[-1])
    assert result["passed"] == scenario
    if "snapshot" in result:
        validate("SupervisorSnapshot", result["snapshot"])
    for item in result.get("events", []):
        validate("SupervisorEvent", item)
    for schema, data in result.get("pages", {}).items():
        validate(schema, data)
