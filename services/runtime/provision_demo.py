"""One-shot creation of a credential-isolated local demo database owner."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

_LOGIN = "atl_demo_provisioner_login"


def provision_demo(common: dict, directory: Path) -> None:
    import psycopg
    from psycopg import sql

    from services.runtime.provision import _write_new_secret

    login = _LOGIN
    path = directory / "demo.json"
    if path.is_symlink():
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    with psycopg.connect(dbname="lab", autocommit=True, **common) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        existing = connection.execute(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s",
            (login,),
        ).fetchone()
        if existing is None:
            if path.exists():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
            password = secrets.token_urlsafe(48)
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
            _write_new_secret(path, login, password)
        elif existing != (True, False, True, False, False, False, False) or not path.is_file():
            raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
        groups = ("atl_worker", "atl_execution", "atl_control_api", "atl_watchdog")
        if existing is None:
            for role in groups:
                connection.execute(
                    sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
                        sql.Identifier(role), sql.Identifier(login)
                    )
                )
        # Existing memberships are evidence, not something startup may repair.
        memberships = connection.execute(
            "SELECT r.rolname,m.admin_option,m.inherit_option,m.set_option "
            "FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
            "WHERE m.member=(SELECT oid FROM pg_roles WHERE rolname=%s)", (login,)
        ).fetchall()
        if set(memberships) != {(role, False, False, True) for role in groups}:
            raise PermissionError("DEMO_ROLE_MEMBERSHIP_UNSAFE")
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='lab_demo'"
        ).fetchone()
        if owner is None:
            connection.execute(
                sql.SQL("CREATE DATABASE lab_demo OWNER {}").format(sql.Identifier(login))
            )
        elif owner != (login,):
            raise PermissionError("DEMO_DATABASE_OWNER_MISMATCH")
        connection.execute("REVOKE ALL ON DATABASE lab_demo FROM PUBLIC")
        connection.execute(
            sql.SQL("REVOKE ALL ON DATABASE lab_runtime FROM {}").format(sql.Identifier(login))
        )
        # A non-superuser cannot connect to the normal database through PUBLIC
        # or the SET-only memberships above; normal provisioning already revokes PUBLIC.
        allowed = connection.execute(
            "SELECT has_database_privilege(%s,'lab_runtime','CONNECT')", (login,)
        ).fetchone()[0]
        if allowed:
            raise PermissionError("DEMO_NORMAL_DATABASE_ISOLATION_FAILED")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    os.chown(path, 10001, 10001, follow_symlinks=False)
    os.chmod(path, 0o600, follow_symlinks=False)


def initialize_provider_volumes() -> None:
    """Restrict only two explicitly mounted private directories; never read files."""
    for directory in (Path("/run/provider/alpaca"), Path("/run/provider/jev")):
        if not directory.exists():
            continue
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
            raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
        os.chown(directory, 10001, 10001, follow_symlinks=False)
        os.chmod(directory, 0o700, follow_symlinks=False)
