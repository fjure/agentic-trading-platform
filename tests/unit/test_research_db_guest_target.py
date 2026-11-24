"""Verify that a partial test override cannot mix two Docker projects."""

import pytest

from tests.integration.ledger_support import guest_target, postgres_command

ISOLATED_TARGET = {
    "ATL_RESEARCH_TEST_POSTGRES": "synthetic-db",
    "ATL_RESEARCH_TEST_NETWORK": "synthetic_local",
    "ATL_RESEARCH_TEST_IMAGE": "synthetic-test:local",
}


def test_guest_and_database_use_the_isolated_project(monkeypatch):
    for key, value in ISOLATED_TARGET.items():
        monkeypatch.setenv(key, value)

    assert postgres_command() == ["docker", "exec", "-i", "--user", "postgres", "synthetic-db"]
    assert guest_target("legacy:local") == ("synthetic_local", "synthetic-test:local")


@pytest.mark.parametrize("missing", ISOLATED_TARGET)
def test_incomplete_isolated_target_cannot_fall_back_to_dev(monkeypatch, missing):
    for key, value in ISOLATED_TARGET.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match="Incomplete isolated PostgreSQL guest configuration"):
        guest_target("legacy:local")
