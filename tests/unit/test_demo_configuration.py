import uuid

import pytest

from services.demo.coordinator import DemoError, validate_start
from services.demo.storage import database_name, validate_environment


@pytest.mark.parametrize("budget", ["0", "-1", "1000000.01", "NaN", "1e2", "01", 100])
def test_invalid_budget_rejected(budget):
    with pytest.raises(DemoError):
        validate_start({"initialBudget": budget, "currency": "USD", "strategy": "mock-neutral"})


def test_fixed_database_namespace_and_parameters():
    ident = str(uuid.uuid4())
    assert database_name(ident) == "lab_demo_" + uuid.UUID(ident).hex
    for value in ("lab_runtime", "';DROP DATABASE lab;--", str(uuid.uuid1())):
        with pytest.raises(ValueError):
            database_name(value)
    with pytest.raises(DemoError):
        validate_start({"initialBudget": "100", "currency": "USD", "strategy": "jev"})


def test_provider_environment_is_refused(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "synthetic-test-canary")
    with pytest.raises(ValueError, match="UNSAFE_DEMO_CONFIGURATION"):
        validate_environment()
