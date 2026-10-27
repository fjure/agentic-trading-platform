"""Separate opt-in simulation process; only its database identity is mounted."""

import hashlib
import re
from pathlib import Path
from time import sleep


def source_fingerprint(root):
    """Version exactly the copied implementation, public config and pinned dependencies."""
    paths = [root / name for name in ("pyproject.toml", "uv.lock")]
    for name in ("packages", "services", "config"):
        paths.extend(
            path
            for path in (root / name).rglob("*")
            if path.suffix in {".py", ".json", ".yaml", ".yml", ".toml"}
            and "__pycache__" not in path.parts
        )
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            raise ValueError("BACKTEST_SOURCE_UNAVAILABLE")
        content = path.read_bytes()
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big") + relative)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()[:40]


def source_version():
    root = Path("/opt/lab")
    try:
        value = (root / "source-revision").read_text("ascii").strip()
        actual = source_fingerprint(root)
    except OSError:
        return None
    return value if re.fullmatch(r"[0-9a-f]{40}", value) and value == actual else None


def configured_service(engine):
    from services.control_api.backtest_service import BacktestService

    version = source_version()
    return BacktestService(engine, code_version=version) if version else None


def configured_readiness_service(engine):
    from services.control_api.readiness_service import ReadinessService

    version = source_version()
    return ReadinessService(engine, code_version=version) if version else None


def engine():
    from sqlalchemy import URL, create_engine, event

    from services.runtime.config import secret_fields

    fields = secret_fields(Path("/run/secrets/database"), {"username", "password"})
    if fields["username"] != "atl_backtest_worker_login":
        raise PermissionError("BACKTEST_IDENTITY_REQUIRED")
    result = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=fields["username"],
            password=fields["password"],
            host="postgres",
            port=5432,
            database="lab_paper_runtime",
        ),
        hide_parameters=True,
        echo=False,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 5},
    )

    @event.listens_for(result, "connect")
    def assign(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE atl_backtest_worker")
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.execute("SET statement_timeout='2s'")
            cursor.execute("SET lock_timeout='1s'")
            cursor.execute("SELECT current_user,current_database(),session_user")
            if cursor.fetchone() != (
                "atl_backtest_worker",
                "lab_paper_runtime",
                "atl_backtest_worker_login",
            ):
                raise PermissionError("BACKTEST_IDENTITY_REQUIRED")
        connection.commit()

    return result


def provision():
    """One-shot operator action; no simulation dispatch, account, or trading activation."""
    import os
    import secrets
    import stat

    import psycopg
    from psycopg import sql

    from services.runtime.provision import _write_new_secret

    directory = Path("/run/provision")
    mode = directory.stat()
    if not stat.S_ISDIR(mode.st_mode) or directory.is_symlink() or mode.st_mode & 0o077:
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    path = directory / "backtest.json"
    admin = Path("/run/secrets/postgres_password").read_text("utf-8").strip()
    with psycopg.connect(
        host="postgres",
        port=5432,
        user="lab_migration",
        password=admin,
        dbname="lab_paper_runtime",
        connect_timeout=5,
    ) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        connection.execute("SET log_parameter_max_length_on_error=0")
        role = connection.execute(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls FROM pg_roles "
            "WHERE rolname='atl_backtest_worker_login'"
        ).fetchone()
        if role is None:
            if path.exists() or path.is_symlink():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
            password = secrets.token_urlsafe(48)
            connection.execute(
                sql.SQL(
                    "CREATE ROLE atl_backtest_worker_login LOGIN "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS "
                    "PASSWORD {}"
                ).format(sql.Literal(password))
            )
            connection.execute(
                "GRANT atl_backtest_worker TO atl_backtest_worker_login "
                "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
            )
            _write_new_secret(path, "atl_backtest_worker_login", password)
        else:
            if role != (True, False, False, False, False, False, False) or (
                not path.is_file() or path.is_symlink()
            ):
                raise PermissionError("BACKTEST_IDENTITY_UNSAFE")
        membership = connection.execute(
            "SELECT r.rolname FROM pg_auth_members m "
            "JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member "
            "WHERE u.rolname='atl_backtest_worker_login'"
        ).fetchall()
        if membership != [("atl_backtest_worker",)]:
            raise PermissionError("BACKTEST_IDENTITY_UNSAFE")
        connection.execute(
            "GRANT CONNECT ON DATABASE lab_paper_runtime TO atl_backtest_worker_login"
        )
        os.chown(path, 10001, 10001, follow_symlinks=False)
        os.chmod(path, 0o600, follow_symlinks=False)


def main():
    import sys

    if sys.argv[1:] == ["provision"]:
        provision()
        return
    if sys.argv[1:] == ["stamp"]:
        root = Path("/opt/lab")
        (root / "source-revision").write_text(source_fingerprint(root), encoding="ascii")
        return
    if sys.argv[1:] != ["--enable"]:
        return  # Deliberate opt-in: default never opens SQL or private mounts.
    from services.worker.backtesting import BacktestWorker

    version = source_version()
    if version is None:
        raise PermissionError("BACKTEST_CODE_VERSION_UNAVAILABLE")
    database = engine()
    worker = BacktestWorker(database, enabled=True, code_version=version)
    try:
        while True:
            try:
                worker.tick()
            except Exception:
                pass  # Next bounded lease handles recovery; no driver diagnostics in logs.
            sleep(1)
    finally:
        database.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("BACKTEST_UNAVAILABLE") from None
