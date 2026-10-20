"""Fail-closed provision checks using synthetic files and an in-memory SQL connection."""

from unittest.mock import Mock

import psycopg
import pytest

from services.runtime import provision_demo as module

GROUPS = ("atl_worker", "atl_execution", "atl_control_api", "atl_watchdog")
SAFE = {(group, False, False, True) for group in GROUPS}
FLAGS = (True, False, True, False, False, False, False)


def fake_connection(monkeypatch, memberships, *, connected=False, flags=FLAGS):
    executed = []
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)

    def execute(query, *_args):
        query = str(query)
        executed.append(query)
        result = Mock()
        if "SELECT rolcanlogin" in query:
            result.fetchone.return_value = flags
        elif "FROM pg_auth_members" in query:
            result.fetchall.return_value = list(memberships)
        elif "SELECT pg_get_userbyid" in query:
            result.fetchone.return_value = (module._LOGIN,)
        elif "has_database_privilege" in query:
            result.fetchone.return_value = (connected,)
        return result

    connection.execute.side_effect = execute
    monkeypatch.setattr(psycopg, "connect", Mock(return_value=connection))
    monkeypatch.setattr(module.os, "chown", Mock(), raising=False)
    monkeypatch.setattr(module.os, "chmod", Mock())
    return executed


@pytest.mark.parametrize("memberships", [SAFE - {("atl_worker", False, False, True)},
    SAFE | {("synthetic_extra_group", False, False, True)},
    (SAFE - {("atl_worker", False, False, True)}) | {("atl_worker", True, False, True)},
    (SAFE - {("atl_worker", False, False, True)}) | {("atl_worker", False, True, True)},
    (SAFE - {("atl_worker", False, False, True)}) | {("atl_worker", False, False, False)}])
def test_membership_drift_rejected_before_any_database_metadata_access(tmp_path, monkeypatch,
                                                                   memberships):
    path = tmp_path / "demo.json"
    path.write_text('{"synthetic":"never-real-credentials"}')
    executed = fake_connection(monkeypatch, memberships)
    with pytest.raises(PermissionError, match="DEMO_ROLE_MEMBERSHIP_UNSAFE"):
        module.provision_demo({}, tmp_path)
    assert not any("lab_demo" in query or "lab_runtime" in query for query in executed)
    assert not any(token in query for query in executed for token in ("GRANT", "REVOKE", "CREATE"))
    module.os.chown.assert_not_called()


@pytest.mark.parametrize("connected", [False, True])
def test_expected_memberships_keep_normal_database_connect_guard(tmp_path, monkeypatch, connected):
    path = tmp_path / "demo.json"
    path.write_text('{"synthetic":"never-real-credentials"}')
    executed = fake_connection(monkeypatch, SAFE, connected=connected)
    # Existing private configuration is metadata evidence; never open/read it.
    monkeypatch.setattr(type(path), "read_text", Mock(side_effect=AssertionError("secret read")))
    monkeypatch.setattr(type(path), "read_bytes", Mock(side_effect=AssertionError("secret read")))
    if connected:
        with pytest.raises(PermissionError, match="DEMO_NORMAL_DATABASE_ISOLATION_FAILED"):
            module.provision_demo({}, tmp_path)
        module.os.chown.assert_not_called()
    else:
        module.provision_demo({}, tmp_path)
        module.os.chown.assert_called_once()
    assert any("has_database_privilege" in query for query in executed)


def test_wrong_existing_login_flags_are_not_repaired(tmp_path, monkeypatch):
    (tmp_path / "demo.json").write_text('{"synthetic":"never-real-credentials"}')
    executed = fake_connection(monkeypatch, SAFE,
                               flags=(True, True, True, False, False, False, False))
    with pytest.raises(PermissionError, match="PRIVATE_CONFIGURATION_INCOMPLETE"):
        module.provision_demo({}, tmp_path)
    assert not any("FROM pg_auth_members" in query for query in executed)
