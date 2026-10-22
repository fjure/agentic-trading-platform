"""Privileged fixed Paper schema provisioning, without any financial seed.

Dormant roles receive CONNECT only. Later versioned workflow migrations must
explicitly grant each business permission before persistent commands can run.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from .config import PAPER_ROLES, ROLES

DATABASE = "lab_paper_runtime"
FILES = {
    "execution": "paper-execution.json", "worker": "paper-worker.json",
    "api": "paper-control.json", "watchdog": "paper-watchdog.json",
}
_FLAGS = "rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls"


def _memberships(connection, name):
    return connection.execute(
        "SELECT r.rolname,m.admin_option,m.inherit_option,m.set_option "
        "FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid "
        "JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s",
        (name,),
    ).fetchall()


def _preflight(connection, directory):
    """Reject drift across all identities before any role/database mutation."""
    if directory.is_symlink() or not directory.is_dir():
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    existing = {}
    for role, group in PAPER_ROLES.items():
        login = group + "_login"
        path = directory / FILES[role]
        if path.is_symlink():
            raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
        rows = {}
        for name, canlogin in ((group, False), (login, True)):
            row = connection.execute(
                f"SELECT {_FLAGS} FROM pg_roles WHERE rolname=%s", (name,)
            ).fetchone()
            rows[name] = row
            if row is not None and row != (canlogin, False, False, False, False, False, False):
                raise PermissionError("PAPER_ROLE_ATTRIBUTES_UNSAFE")
        if (rows[group] is None) != (rows[login] is None):
            raise PermissionError("PAPER_ROLE_CONFIGURATION_INCOMPLETE")
        if rows[login] is None:
            if path.exists():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
        else:
            if not path.is_file():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
            if _memberships(connection, login) != [(group, False, False, True)]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            if _memberships(connection, group):
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            incoming = connection.execute(
                "SELECT member.rolname FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles member ON member.oid=m.member "
                "WHERE r.rolname=%s", (group,),
            ).fetchall()
            if incoming != [(login,)]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            if connection.execute(
                "SELECT count(*) FROM pg_auth_members m "
                "JOIN pg_roles r ON r.oid=m.roleid WHERE r.rolname=%s", (login,),
            ).fetchone()[0]:
                raise PermissionError("PAPER_ROLE_MEMBERSHIP_UNSAFE")
            owners = connection.execute(
                "SELECT count(*) FROM pg_database WHERE datdba IN "
                "(SELECT oid FROM pg_roles WHERE rolname IN (%s,%s))", (login, group),
            ).fetchone()[0]
            if owners:
                raise PermissionError("PAPER_DATABASE_OWNER_UNSAFE")
        existing[role] = rows[login] is not None
    owner = connection.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (DATABASE,)
    ).fetchone()
    if owner is not None and owner != ("lab_migration",):
        raise PermissionError("PAPER_DATABASE_OWNER_MISMATCH")
    return existing, owner


def _create_identities(connection, directory, existing):
    from psycopg import sql

    from .provision import _write_new_secret

    for role, group in PAPER_ROLES.items():
        if existing[role]:
            continue
        login = group + "_login"
        password = secrets.token_urlsafe(48)
        connection.execute(sql.SQL(
            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        ).format(sql.Identifier(group)))
        connection.execute(sql.SQL(
            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(login), sql.Literal(password)))
        connection.execute(sql.SQL(
            "GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
        ).format(sql.Identifier(group), sql.Identifier(login)))
        _write_new_secret(directory / FILES[role], login, password)


def _verify_dormant_privileges(connection):
    """No object privileges/ownership belong to APW-02's CONNECT-only roles.

    Future workflow grants require an explicit versioned manifest here. Never
    accept arbitrary existing ACLs just because a later migration may need them.
    """
    identities = tuple(PAPER_ROLES.values()) + tuple(
        name + "_login" for name in PAPER_ROLES.values()
    )
    # Inspect ACLs, not has_*_privilege alone: column grants and direct login
    # grants must be rejected even when the service always SET ROLEs initially.
    unexpected = connection.execute(
        "WITH identities AS (SELECT oid FROM pg_roles WHERE rolname=ANY(%s)), "
        "acls AS ("
        "SELECT nspacl AS acl FROM pg_namespace UNION ALL "
        "SELECT relacl FROM pg_class UNION ALL "
        "SELECT attacl FROM pg_attribute UNION ALL "
        "SELECT proacl FROM pg_proc) "
        "SELECT EXISTS(SELECT 1 FROM acls CROSS JOIN LATERAL aclexplode(acl) x "
        "WHERE x.grantee IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_class WHERE relowner IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_proc WHERE proowner IN (SELECT oid FROM identities)) OR "
        "EXISTS(SELECT 1 FROM pg_type WHERE typowner IN (SELECT oid FROM identities))",
        (list(identities),),
    ).fetchone()[0]
    if unexpected:
        raise PermissionError("PAPER_OBJECT_PRIVILEGES_UNSAFE")
    for identity in identities:
        if connection.execute(
            "SELECT has_database_privilege(%s,%s,'CREATE') OR "
            "has_database_privilege(%s,%s,'TEMP')",
            (identity, DATABASE, identity, DATABASE),
        ).fetchone()[0]:
            raise PermissionError("PAPER_DDL_PRIVILEGES_UNSAFE")


def _confine_schema(connection):
    """Remove legacy ACLs from this schema; never grant normal-role membership."""
    from psycopg import sql

    names = sql.SQL(",").join([sql.Identifier(name) for name in ROLES.values()])
    for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        connection.execute(sql.SQL(
            f"REVOKE ALL ON ALL {kind} IN SCHEMA public FROM PUBLIC, {{}}"
        ).format(names))
    # Table-level REVOKE does not remove the existing column-level grants.
    for table, column in connection.execute(
        "SELECT c.relname,a.attname FROM pg_attribute a "
        "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND a.attacl IS NOT NULL"
    ).fetchall():
        connection.execute(sql.SQL("REVOKE ALL ({}) ON public.{} FROM PUBLIC, {}").format(
            sql.Identifier(column), sql.Identifier(table), names,
        ))
    connection.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC, {}").format(names))


def provision_paper(common: dict, directory: Path):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from psycopg import sql
    from sqlalchemy import URL, create_engine

    with psycopg.connect(dbname="lab", autocommit=True, **common) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        existing, owner = _preflight(connection, directory)
        if owner is not None:
            with psycopg.connect(dbname=DATABASE, **common) as paper_connection:
                _verify_dormant_privileges(paper_connection)
        with connection.transaction():
            _create_identities(connection, directory, existing)
        if owner is None:
            # The migration superuser can connect while ordinary logins cannot.
            connection.execute(
                "CREATE DATABASE lab_paper_runtime OWNER lab_migration CONNECTION LIMIT 0"
            )
        connection.execute("REVOKE ALL ON DATABASE lab_paper_runtime FROM PUBLIC")
    engine = create_engine(URL.create(
        "postgresql+psycopg", username=common["user"], password=common["password"],
        host="postgres", port=5432, database=DATABASE,
    ), hide_parameters=True, echo=False, connect_args={"connect_timeout": 5})
    try:
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parents[1] / "control_api/migrations")
        )
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with psycopg.connect(dbname=DATABASE, **common) as connection:
            _confine_schema(connection)
            _verify_dormant_privileges(connection)
            for role, group in PAPER_ROLES.items():
                login = group + "_login"
                for identity in (login, group):
                    unsafe = connection.execute(
                        "SELECT has_database_privilege(%s,%s,'CREATE') OR "
                        "has_database_privilege(%s,%s,'TEMP') OR "
                        "has_schema_privilege(%s,'public','CREATE')",
                        (identity, DATABASE, identity, DATABASE, identity),
                    ).fetchone()[0]
                    if unsafe:
                        raise PermissionError("PAPER_DDL_PRIVILEGES_UNSAFE")
                connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(DATABASE), sql.Identifier(login),
                ))
                for other in ("lab_runtime", "lab_demo"):
                    allowed = connection.execute(
                        "SELECT has_database_privilege(%s,%s,'CONNECT')", (login, other)
                    ).fetchone()[0]
                    if allowed:
                        raise PermissionError("PAPER_DATABASE_ISOLATION_FAILED")
                path = directory / FILES[role]
                os.chown(path, 10001, 10001, follow_symlinks=False)
                os.chmod(path, 0o600, follow_symlinks=False)
            connection.execute("ALTER DATABASE lab_paper_runtime CONNECTION LIMIT -1")
    finally:
        engine.dispose()
