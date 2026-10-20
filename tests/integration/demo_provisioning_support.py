"""Only UUID test logins/database are touched; fixed provisioner and lab DB stay untouched."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

GROUPS = ("atl_worker", "atl_execution", "atl_control_api", "atl_watchdog")


def host():
    from tests.integration.ledger_support import _admin

    suffix = uuid.uuid4().hex[:20]
    driver, target, extra = (f"atl_drift_{kind}_{suffix}" for kind in ("driver", "target", "extra"))
    database = f"atl_drift_db_{suffix}"
    password = secrets.token_urlsafe(32)
    root = Path(__file__).resolve().parents[2]
    created_roles = []
    created_database = False
    try:
        _admin(f"CREATE ROLE {driver} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
               f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}';")
        created_roles.append(driver)
        _admin(f"GRANT SET ON PARAMETER log_statement,log_min_error_statement TO {driver};")
        _admin(f"CREATE ROLE {target} LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT "
               "NOREPLICATION NOBYPASSRLS;")
        created_roles.append(target)
        _admin(f"CREATE ROLE {extra} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
               "NOREPLICATION NOBYPASSRLS;")
        created_roles.append(extra)
        _admin(f"CREATE DATABASE {database} OWNER {driver};")
        created_database = True
        command = ["docker", "run", "--rm", "-i", "--network",
                   "agentic-trading-lab-dev_local", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp",
                   "--workdir", "/opt/lab", "--entrypoint", "python"]
        for source, destination in ((root / "services", "/opt/lab/services"),
                                    (Path(__file__).resolve(), "/opt/lab/test_guest.py")):
            command.extend(("--mount", f"type=bind,source={source},target={destination},readonly"))
        command.extend(("agentic-trading-lab-dev-build-smoke:latest", "test_guest.py", "guest"))
        for scenario in ("extra", "inherit", "admin", "set", "missing"):
            _admin("\n".join(f"GRANT {group} TO {target} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
                             for group in GROUPS))
            if scenario == "extra":
                _admin(f"GRANT {extra} TO {target} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;")
            elif scenario == "missing":
                _admin(f"REVOKE atl_worker FROM {target};")
            else:
                option = {"inherit": "INHERIT TRUE", "admin": "ADMIN TRUE", "set": "SET FALSE"}
                _admin(f"GRANT atl_worker TO {target} WITH {option[scenario]};")
            completed = subprocess.run(command, input=json.dumps({"database": database,
                "driver": driver, "password": password, "target": target}), text=True,
                capture_output=True, check=False)
            if completed.returncode or "PASS" not in completed.stdout:
                # Never return driver password/SQL diagnostics, even on failed fixtures.
                raise RuntimeError("isolated provisioner drift test failed: " + scenario)
            if scenario == "extra":
                _admin(f"REVOKE {extra} FROM {target};")
        return "PASS: 5 isolated provisioner drift scenarios"
    finally:
        if created_database:
            _admin(f"DROP DATABASE {database} WITH (FORCE);")
        if driver in created_roles:
            _admin(f"REVOKE SET ON PARAMETER log_statement,log_min_error_statement FROM {driver};")
        for name in reversed(created_roles):
            _admin(f"DROP ROLE {name};")


def guest():
    import tempfile

    import psycopg

    from services.runtime import provision_demo as module

    payload = json.loads(sys.stdin.read())
    target = payload["target"]
    if not target.startswith("atl_drift_target_") or target == module._LOGIN:
        raise AssertionError("Test target must be a distinct synthetic role")
    module._LOGIN = target
    original_connect = psycopg.connect
    common = {"host": "postgres", "user": payload["driver"], "password": payload["password"]}

    def snapshot(connection):
        roles = connection.execute("SELECT r.rolname,m.admin_option,m.inherit_option,m.set_option "
            "FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
            "WHERE m.member=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY r.rolname",
            (target,)).fetchall()
        databases = connection.execute("SELECT datname,datdba,datacl::text FROM pg_database "
            "WHERE datname IN ('lab','lab_demo','lab_runtime',%s) ORDER BY datname",
            (payload["database"],)).fetchall()
        flags = connection.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,"
            "rolcreaterole,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (target,)).fetchone()
        return roles, databases, flags

    class GuardedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, query, *args):
            # Any attempt to reach normal/fixed metadata or repair a drift aborts
            # this synthetic test before the statement can reach PostgreSQL.
            if (not isinstance(query, str) or "lab_demo" in query or "lab_runtime" in query
                    or not query.lstrip().startswith(("SELECT", "SET log_"))):
                raise AssertionError("Unexpected provisioning mutation/metadata access")
            return self.connection.execute(query, *args)

    def connect(*args, **kwargs):
        assert not args and kwargs["dbname"] == "lab"
        kwargs["dbname"] = payload["database"]
        return GuardedConnection(original_connect(**kwargs))

    with original_connect(dbname=payload["database"], autocommit=True, **common) as inspection:
        before = snapshot(inspection)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "demo.json"
            canary = b'{"synthetic":"never-real-credentials"}'
            path.write_bytes(canary)
            path.chmod(0o600)
            prior_metadata = path.stat()
            psycopg.connect = connect
            try:
                try:
                    module.provision_demo(common, Path(temporary))
                except PermissionError as error:
                    assert str(error) == "DEMO_ROLE_MEMBERSHIP_UNSAFE"
                else:
                    raise AssertionError("Membership drift accepted")
            finally:
                psycopg.connect = original_connect
            assert path.read_bytes() == canary
            assert path.stat().st_mtime_ns == prior_metadata.st_mtime_ns
            assert path.stat().st_mode & 0o777 == 0o600
        assert snapshot(inspection) == before
    print("PASS: synthetic role membership and database metadata unchanged")


if __name__ == "__main__":
    guest()
