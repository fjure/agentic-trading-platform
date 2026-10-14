"""Full vertical crash and UNKNOWN paths over isolated PostgreSQL roles."""

from pathlib import Path

import pytest

from tests.integration.risk_support import host


@pytest.mark.parametrize("scenario", ("crash-before", "crash-after", "timeout"))
def test_vertical_recovery_never_repeats_submit(scenario):
    assert f"PASS {scenario}" in host(
        guest_path=Path(__file__).resolve().parents[1] / "integration" / "vertical_support.py",
        guest_args=(scenario,),
    )
