"""Execution boundary checks; no external broker, credentials or network calls."""

import ast
from pathlib import Path

import pytest

from packages.domain.models import Mode
from tests.integration.risk_support import host


def test_live_is_not_a_supported_mode():
    with pytest.raises(ValueError):
        Mode("LIVE")


def test_worker_and_control_modules_do_not_import_execution_or_broker_submit():
    root = Path(__file__).resolve().parents[2]
    for folder in (root / "services/worker", root / "services/control_api"):
        for path in folder.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith(
                        ("services.execution", "packages.brokers")
                    )
                elif isinstance(node, ast.Import):
                    assert all(
                        not alias.name.startswith(("services.execution", "packages.brokers"))
                        for alias in node.names
                    )


@pytest.mark.parametrize("scenario", ["denials", "guards", "startup"])
def test_actual_worker_role_and_unsafe_modes_never_contact_broker(scenario):
    assert "PASS" in host(
        guest_path=Path(__file__).resolve().parents[1] / "recovery/execution_support.py",
        guest_args=(scenario,),
    )
