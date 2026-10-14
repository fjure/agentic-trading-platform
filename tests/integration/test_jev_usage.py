"""Normalized Jev counts survive role-scoped PostgreSQL persistence and replay."""

from pathlib import Path

from tests.integration.risk_support import host


def test_jev_shadow_usage_survives_restart():
    assert "PASS Jev shadow usage persisted and replayed" in host(
        guest_path=Path(__file__).with_name("jev_usage_support.py"),
        guest_args=("usage",),
    )
