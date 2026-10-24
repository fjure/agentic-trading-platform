"""Fixed Paper provisioning in a disposable cluster, never the existing ledger."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from unittest.mock import patch

_STAGE = "START"


def stage(value):
    global _STAGE
    _STAGE = value


POSTGRES = (
    "postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
)


def host(guest_path=None, expected_marker=None, support_paths=(), guest_args=()):
    root = Path(__file__).resolve().parents[2]
    guest_path = Path(__file__).resolve() if guest_path is None else guest_path.resolve()
    if not guest_path.is_relative_to(root / "tests/integration") or guest_path.suffix != ".py":
        raise ValueError("isolated guest must be an integration test source")
    support_mounts = []
    if (
        type(guest_args) is not tuple
        or len(guest_args) > 4
        or any(
            type(value) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", value) is None
            for value in guest_args
        )
    ):
        raise ValueError("isolated guest arguments must be bounded scenario identifiers")
    for requested in support_paths:
        support = Path(requested).resolve()
        if (
            not support.is_relative_to(root / "tests")
            or support.suffix != ".py"
            or not support.is_file()
        ):
            raise ValueError("isolated support must be a repository test source")
        support_mounts.extend(
            ("--mount", f"type=bind,source={support},target=/opt/lab/{support.name},readonly")
        )
    expected_marker = expected_marker or (
        "PASS: isolated Paper provisioning, privileges, drift and no financial basis"
    )
    tag = "atl-paper-probe-" + uuid.uuid4().hex[:16]
    network, container = tag + "-net", tag + "-pg"

    def docker(*args):
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode:
            marker = re.search(
                r"APW02_FAIL:[A-Z_]+:[A-Za-z]{1,80}(?::(?!ERRNO)[A-Z_]+)?"
                r"(?::ERRNO[0-9]+)?(?::[a-z_]+\.py:[0-9]+)?",
                result.stdout,
            )
            if marker:
                raise RuntimeError(marker.group())
            raise RuntimeError("isolated Paper provisioning probe failed; output redacted")
        return result.stdout.strip()

    network_created = container_created = False
    try:
        docker("network", "create", "--internal", network)
        network_created = True
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--user",
            "postgres",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/var/lib/postgresql:uid=999,gid=999",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/var/run/postgresql:uid=999,gid=999",
            "--env",
            "POSTGRES_USER=lab_migration",
            "--env",
            "POSTGRES_DB=lab",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            POSTGRES,
        )
        container_created = True
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", container, "pg_isready", "-U", "lab_migration", "-d", "lab"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("isolated Paper PostgreSQL did not become ready")
        result = docker(
            "run",
            "--rm",
            "--pull=never",
            "--network",
            network,
            "--read-only",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--mount",
            f"type=bind,source={root / 'services'},target=/opt/lab/services,readonly",
            "--mount",
            f"type=bind,source={root / 'packages'},target=/opt/lab/packages,readonly",
            "--mount",
            f"type=bind,source={root / 'alembic.ini'},target=/opt/lab/alembic.ini,readonly",
            "--mount",
            f"type=bind,source={guest_path},target=/opt/lab/test_paper_provision.py,readonly",
            *support_mounts,
            "--workdir",
            "/opt/lab",
            "--entrypoint",
            "python",
            "agentic-trading-lab-dev-build-smoke:latest",
            "test_paper_provision.py",
            "guest",
            *guest_args,
        )
        assert result == expected_marker
        return result
    finally:
        if container_created:
            docker("rm", "--force", container)
        if network_created:
            docker("network", "rm", network)


def guest():
    stage("IMPORTS")
    import psycopg
    from alembic import command
    from psycopg import sql

    from services.runtime.config import PAPER_ROLES
    from services.runtime.provision_paper import (
        DATABASE,
        FILES,
        _verify_dormant_privileges,
        provision_paper,
    )

    def synthetic_private_bytes(path):
        # This disposable fixture owns only generated synthetic DB credentials.
        # Root with CHOWN/FOWNER cannot read uid10001/mode0600 files directly.
        info = path.stat()
        assert info.st_uid == 10001 and stat.S_IMODE(info.st_mode) == 0o600
        os.chown(path, 0, 0)
        try:
            return path.read_bytes()
        finally:
            os.chown(path, 10001, 10001)

    common = dict(host="postgres", user="lab_migration", password="synthetic-unused")
    stage("FIXTURE_DATABASES")
    with psycopg.connect(dbname="lab", autocommit=True, **common) as admin:
        # These databases only exist in this test's disposable cluster.
        for database in ("lab_runtime", "lab_demo"):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database),
                )
            )
            with psycopg.connect(dbname=database, **common) as normal:
                normal.execute("CREATE TABLE public.preserved(value numeric NOT NULL)")
                normal.execute("INSERT INTO public.preserved VALUES (10000)")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        stage("FIRST_PROVISION")
        # Exercise the real APW-02 baseline first, then the actual 0013 upgrade.
        upgrade = command.upgrade
        with patch(
            "alembic.command.upgrade",
            side_effect=lambda config, _target: upgrade(
                config,
                "0012_trading_profile",
            ),
        ):
            provision_paper(common, directory)
        first = {name: synthetic_private_bytes(directory / name) for name in FILES.values()}
        stage("BASELINE_MANIFEST")
        with psycopg.connect(dbname=DATABASE, autocommit=True, **common) as admin:
            assert admin.execute("SELECT version_num FROM public.alembic_version").fetchall() == [
                ("0012_trading_profile",),
            ]
            _verify_dormant_privileges(admin)
            admin.execute("GRANT USAGE ON SCHEMA public TO atl_paper_execution")
            try:
                provision_paper(common, directory)
            except PermissionError as error:
                assert str(error) == "PAPER_OBJECT_PRIVILEGES_UNSAFE"
            else:
                raise AssertionError("0012 must not accept future schema privileges")
            assert admin.execute(
                "SELECT has_schema_privilege('atl_paper_execution','public','USAGE')"
            ).fetchone() == (True,)
            admin.execute("REVOKE USAGE ON SCHEMA public FROM atl_paper_execution")
        stage("REPEAT_PROVISION")
        provision_paper(common, directory)
        provision_paper(common, directory)
        assert first == {name: synthetic_private_bytes(directory / name) for name in FILES.values()}
        stage("EMPTY_FINANCE")
        with psycopg.connect(dbname=DATABASE, **common) as admin:
            for table in (
                "accounts",
                "positions",
                "portfolio_snapshots",
                "risk_account_context",
                "risk_policies",
                "order_intents",
                "broker_orders",
                "trade_proposals",
            ):
                assert admin.execute(
                    sql.SQL("SELECT count(*) FROM public.{}").format(
                        sql.Identifier(table),
                    )
                ).fetchone() == (0,)
        stage("ROLE_BOUNDARIES")
        for role, group in PAPER_ROLES.items():
            private = json.loads(first[FILES[role]])
            credentials = dict(
                host="postgres",
                user=private["username"],
                password=private["password"],
            )
            with psycopg.connect(dbname=DATABASE, autocommit=True, **credentials) as restricted:
                restricted.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(group)))
                assert restricted.execute("SELECT current_user,current_database()").fetchone() == (
                    group,
                    DATABASE,
                )
                assert restricted.execute(
                    "SELECT has_schema_privilege(current_user,'public','USAGE')"
                ).fetchone() == (role in ("api", "execution", "worker", "watchdog"),)
                if role == "api":
                    public_status = restricted.execute(
                        "SELECT public.paper_status(%s)",
                        ("a" * 32,),
                    ).fetchone()[0]
                    assert public_status["entryGateOpen"] is False
                for statement in (
                    "CREATE TABLE public.forbidden(id int)",
                    "CREATE SCHEMA forbidden",
                    "CREATE TEMP TABLE forbidden(id int)",
                    "SET ROLE atl_execution",
                    "SET ROLE atl_paper_worker"
                    if role != "worker"
                    else "SET ROLE atl_paper_execution",
                    "INSERT INTO public.accounts(id,currency) VALUES ('paper','USD')",
                ):
                    try:
                        restricted.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError("Paper role escaped its dormant privilege boundary")
            for other in ("lab_runtime", "lab_demo"):
                try:
                    with psycopg.connect(dbname=other, **credentials):
                        pass
                except psycopg.OperationalError:
                    pass
                else:
                    raise AssertionError("Paper login reached another ledger")
        stage("MEMBERSHIP_DRIFT")
        with psycopg.connect(dbname="lab", autocommit=True, **common) as admin:
            scenarios = (
                (
                    "GRANT atl_worker TO atl_paper_execution WITH SET TRUE",
                    "REVOKE atl_worker FROM atl_paper_execution",
                    "PAPER_ROLE_MEMBERSHIP_UNSAFE",
                ),
                (
                    "ALTER ROLE atl_paper_execution CREATEDB",
                    "ALTER ROLE atl_paper_execution NOCREATEDB",
                    "PAPER_ROLE_ATTRIBUTES_UNSAFE",
                ),
                (
                    "GRANT atl_paper_execution TO atl_paper_execution_login WITH INHERIT TRUE",
                    "GRANT atl_paper_execution TO atl_paper_execution_login WITH INHERIT FALSE",
                    "PAPER_ROLE_MEMBERSHIP_UNSAFE",
                ),
                (
                    "GRANT atl_paper_execution_login TO atl_worker",
                    "REVOKE atl_paper_execution_login FROM atl_worker",
                    "PAPER_ROLE_MEMBERSHIP_UNSAFE",
                ),
            )
            for unsafe, restore, code in scenarios:
                admin.execute(unsafe)
                try:
                    provision_paper(common, directory)
                except PermissionError as error:
                    assert str(error) == code
                else:
                    raise AssertionError("unsafe role configuration was repaired or accepted")
                finally:
                    admin.execute(restore)
            assert first == {
                name: synthetic_private_bytes(directory / name) for name in FILES.values()
            }
        stage("OBJECT_PRIVILEGE_DRIFT")
        with psycopg.connect(dbname=DATABASE, autocommit=True, **common) as admin:
            admin.execute("CREATE SEQUENCE public.apw_probe_sequence")
            admin.execute(
                "CREATE FUNCTION public.apw_probe_function() RETURNS int LANGUAGE sql AS 'SELECT 1'"
            )

            def fingerprint():
                return (
                    admin.execute("SELECT version_num FROM public.alembic_version").fetchall(),
                    admin.execute(
                        "SELECT nspowner,nspacl::text FROM pg_namespace WHERE nspname='public'"
                    ).fetchall(),
                    admin.execute(
                        "SELECT relname,relowner,relacl::text FROM pg_class "
                        "WHERE relnamespace='public'::regnamespace "
                        "ORDER BY relname"
                    ).fetchall(),
                    admin.execute(
                        "SELECT attrelid,attnum,attacl::text FROM pg_attribute "
                        "WHERE attacl IS NOT NULL ORDER BY attrelid,attnum"
                    ).fetchall(),
                    admin.execute(
                        "SELECT oid,proowner,proacl::text FROM pg_proc "
                        "WHERE pronamespace='public'::regnamespace ORDER BY oid"
                    ).fetchall(),
                )

            scenarios = (
                (
                    "GRANT USAGE ON SCHEMA public TO atl_paper_worker_login",
                    "REVOKE USAGE ON SCHEMA public FROM atl_paper_worker_login",
                ),
                (
                    "GRANT UPDATE(currency) ON public.accounts TO atl_paper_execution",
                    "REVOKE UPDATE(currency) ON public.accounts FROM atl_paper_execution",
                ),
                (
                    "GRANT UPDATE(currency) ON public.accounts TO atl_paper_execution_login",
                    "REVOKE UPDATE(currency) ON public.accounts FROM atl_paper_execution_login",
                ),
                (
                    "GRANT USAGE ON SEQUENCE public.apw_probe_sequence TO atl_paper_worker_login",
                    "REVOKE USAGE ON SEQUENCE public.apw_probe_sequence FROM "
                    "atl_paper_worker_login",
                ),
                (
                    "GRANT EXECUTE ON FUNCTION public.apw_probe_function() TO "
                    "atl_paper_control_api",
                    "REVOKE EXECUTE ON FUNCTION public.apw_probe_function() "
                    "FROM atl_paper_control_api",
                ),
                (
                    "ALTER TABLE public.accounts OWNER TO atl_paper_execution",
                    "ALTER TABLE public.accounts OWNER TO lab_migration",
                ),
                (
                    "ALTER FUNCTION public.apw_probe_function() OWNER TO atl_paper_worker",
                    "ALTER FUNCTION public.apw_probe_function() OWNER TO lab_migration",
                ),
                (
                    "GRANT EXECUTE ON FUNCTION public.paper_status(text) TO atl_paper_execution",
                    "REVOKE EXECUTE ON FUNCTION public.paper_status(text) FROM atl_paper_execution",
                ),
                (
                    "GRANT EXECUTE ON FUNCTION public.paper_status(text) "
                    "TO atl_paper_control_api_login",
                    "REVOKE EXECUTE ON FUNCTION public.paper_status(text) "
                    "FROM atl_paper_control_api_login",
                ),
                (
                    "GRANT EXECUTE ON FUNCTION public.paper_status(text) "
                    "TO atl_paper_control_api WITH GRANT OPTION",
                    "REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION public.paper_status(text) "
                    "FROM atl_paper_control_api",
                ),
                (
                    "REVOKE EXECUTE ON FUNCTION public.paper_status(text) FROM "
                    "atl_paper_control_api",
                    "GRANT EXECUTE ON FUNCTION public.paper_status(text) TO atl_paper_control_api",
                ),
                (
                    "REVOKE USAGE ON SCHEMA public FROM atl_paper_execution",
                    "GRANT USAGE ON SCHEMA public TO atl_paper_execution",
                ),
            )
            for unsafe, restore in scenarios:
                admin.execute(unsafe)
                before = fingerprint()
                try:
                    provision_paper(common, directory)
                except PermissionError as error:
                    assert str(error) == "PAPER_OBJECT_PRIVILEGES_UNSAFE"
                else:
                    raise AssertionError("unsafe object privilege was accepted")
                assert fingerprint() == before, "drift rejection must not repair existing ACLs"
                admin.execute(restore)
            stage("UNKNOWN_MANIFEST")
            admin.execute("UPDATE public.alembic_version SET version_num='0014_unreviewed'")
            before = fingerprint()
            try:
                provision_paper(common, directory)
            except PermissionError as error:
                assert str(error) == "PAPER_PRIVILEGE_MANIFEST_UNSUPPORTED"
            else:
                raise AssertionError("unreviewed revision must not authorize existing grants")
            assert fingerprint() == before
            admin.execute(
                "UPDATE public.alembic_version SET version_num='0019_paper_cancellation'"
            )
        stage("PRESERVED_DATABASES")
        for database in ("lab_runtime", "lab_demo"):
            with psycopg.connect(dbname=database, **common) as normal:
                assert normal.execute("SELECT value FROM public.preserved").fetchall() == [(10000,)]
    print("PASS: isolated Paper provisioning, privileges, drift and no financial basis")


def test_paper_provisioning_actual_postgres():
    assert "PASS: isolated Paper provisioning" in host()


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            # Fixed stage and exception type only, never exception text or SQL.
            codes = {
                "PRIVATE_CONFIGURATION_UNSAFE",
                "PRIVATE_CONFIGURATION_INCOMPLETE",
                "PAPER_ROLE_ATTRIBUTES_UNSAFE",
                "PAPER_ROLE_CONFIGURATION_INCOMPLETE",
                "PAPER_ROLE_MEMBERSHIP_UNSAFE",
                "PAPER_DATABASE_OWNER_UNSAFE",
                "PAPER_DATABASE_OWNER_MISMATCH",
                "PAPER_DDL_PRIVILEGES_UNSAFE",
                "PAPER_DATABASE_ISOLATION_FAILED",
                "PAPER_OBJECT_PRIVILEGES_UNSAFE",
                "PAPER_PRIVILEGE_MANIFEST_UNSUPPORTED",
            }
            code = f":{error}" if type(error) is PermissionError and str(error) in codes else ""
            number = f":ERRNO{error.errno}" if isinstance(error, OSError) and error.errno else ""
            location = traceback.extract_tb(error.__traceback__)[-1]
            filename = Path(location.filename).name
            location_marker = (
                f":{filename}:{location.lineno}" if re.fullmatch(r"[a-z_]+\.py", filename) else ""
            )
            print(f"APW02_FAIL:{_STAGE}:{type(error).__name__}{code}{number}{location_marker}")
            raise SystemExit(1) from None
    else:
        print(host())
