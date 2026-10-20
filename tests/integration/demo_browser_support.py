"""Actual browser/API/PostgreSQL acceptance; only UUID test resources are changed.

The host owns the lifecycle. Credentials travel over stdin, never CLI arguments,
files or diagnostics. A byte-only STDIO relay works on Docker Desktop's internal
network without opening a container port or touching the user's port 8000 lab.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:4210"
CANARIES = ("synthetic-demo-canary-key", "synthetic-demo-canary-secret")
SQL_PROOF = (
    "PASS SQL: 3 runs; 2 fills; 95/195 USD cash; failed risk paused; normal ledger unchanged"
)


def relay():
    from services.runtime import host_bridge

    name = os.environ.get("DEMO_BROWSER_CONTAINER", "")
    if not re.fullmatch(r"atl_demo_browser_[0-9a-f]{20}", name):
        raise RuntimeError("isolated browser owner required")
    host_bridge.command = lambda: [
        "docker",
        "exec",
        "-i",
        name,
        "python",
        "-u",
        "-m",
        "services.runtime.host_bridge",
        "guest",
    ]
    with host_bridge.Server(("127.0.0.1", 4210), host_bridge.Handler) as server:
        server.serve_forever(poll_interval=0.5)


def host():
    import socket
    import uuid

    from tests.integration.ledger_support import GROUPS, _admin

    # Refuse an occupied acceptance port before any test mutation. Never stop it.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4210))
    suffix = uuid.uuid4().hex[:20]
    name = "atl_demo_browser_" + suffix
    normal = "atl_demo_normal_" + suffix
    password = secrets.token_urlsafe(32)
    child = None
    databases = []
    created_role = False
    try:
        _admin(
            "\n".join(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{group}') "
                f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; "
                "END IF; END $$;"
                for group in GROUPS
            )
        )
        _admin(
            f"CREATE ROLE {name} LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOINHERIT "
            f"PASSWORD '{password}';"
        )
        created_role = True
        _admin(
            "\n".join(
                f"GRANT {group} TO {name} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
                for group in GROUPS
            )
        )
        for database in (name, normal):
            _admin(f"CREATE DATABASE {database} OWNER {name};")
            databases.append(database)
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--network",
            "agentic-trading-lab-dev_local",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--workdir",
            "/opt/lab",
            "--entrypoint",
            "python",
        ]
        for source, target in (
            (ROOT / "packages", "/opt/lab/packages"),
            (ROOT / "services", "/opt/lab/services"),
            (ROOT / "apps/dashboard/dist/dashboard/browser", "/opt/lab/dashboard"),
            (Path(__file__).resolve(), "/opt/lab/test_guest.py"),
        ):
            command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
        command.extend(
            ("agentic-trading-lab-dev-build-smoke:latest", "-u", "test_guest.py", "guest")
        )
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        child.stdin.write(
            json.dumps({"database": name, "normal": normal, "username": name, "password": password})
            + "\n"
        )
        child.stdin.flush()
        # The only credential returned is a newly issued test authority code.
        # It stays in private process memory, never a user lab/session credential.
        line = child.stdout.readline()
        if not line:
            raise RuntimeError("isolated API guest failed before readiness; diagnostics redacted")
        ready = json.loads(line)
        if ready.get("ready") is not True:
            raise RuntimeError("isolated API guest unavailable")
        env = os.environ.copy()
        env.update(
            DEMO_BROWSER_CONTAINER=name,
            DEMO_BROWSER_PYTHON=sys.executable,
            DEMO_BROWSER_ACTIVATION=ready.pop("activationCode"),
            PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        node = ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"
        cli = ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"
        result = subprocess.run(
            [str(node), str(cli), "test", "--config", "playwright.demo.config.ts"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        env.pop("DEMO_BROWSER_ACTIVATION", None)
        # A reporter must not accidentally retain synthetic credential values.
        public = result.stdout + result.stderr
        for value in (*CANARIES, password):
            public = public.replace(value, "[redacted test input]")
        print(public.strip(), flush=True)
        if result.returncode:
            raise RuntimeError("actual browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        proof = child.stdout.readline().strip()
        if proof != SQL_PROOF:
            if re.fullmatch(r"FAIL SQL [A-Za-z]+ at [0-9]+", proof):
                print(proof, flush=True)
            raise RuntimeError("actual PostgreSQL verification failed; diagnostics redacted")
        print(proof, flush=True)
        child.stdin.close()
        if child.wait(timeout=15):
            raise RuntimeError("isolated API guest shutdown failed")
        print(
            "PASS Chromium -> actual canonical API -> persistent PostgreSQL; "
            "test resources cleaned on exit"
        )
    finally:
        if child is not None:
            subprocess.run(
                ["docker", "stop", "--time", "2", name], capture_output=True, check=False
            )
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
        if created_role:
            owned = _admin(
                "SELECT datname FROM pg_database WHERE datdba="
                f"(SELECT oid FROM pg_roles WHERE rolname='{name}');"
            )
            for candidate in owned.splitlines():
                candidate = candidate.strip()
                if re.fullmatch(r"lab_demo_[0-9a-f]{32}", candidate):
                    _admin(f'DROP DATABASE "{candidate}" WITH (FORCE);')
            for database in databases:
                _admin(f"DROP DATABASE {database} WITH (FORCE);")
            _admin(f"DROP ROLE {name};")


def guest():
    import tempfile
    from decimal import Decimal
    from time import monotonic, sleep
    from types import SimpleNamespace

    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, event, text

    from packages.persistence import PostgresLedger
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.events import EventFeed
    from services.control_api.migrations.ledger_schema_v1 import TABLE_NAMES
    from services.control_api.snapshot import SnapshotReader
    from services.control_api.trading_profile import TradingProfileBackend
    from services.demo.coordinator import DemoCoordinator
    from services.demo.storage import financial_engine
    from services.runtime.api import create_bridge
    from services.runtime.provision import seed
    from services.setup.app import create_app as setup_app
    from services.setup.client import FixedSetupClient
    from services.setup.store import ProviderStore

    logging.disable(logging.CRITICAL)
    value = json.loads(sys.stdin.readline())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            host="postgres",
            port=5432,
            database=value["database"],
            username=value["username"],
            password=value["password"],
        ),
        hide_parameters=True,
    )
    normal = create_engine(engine.url.set(database=value["normal"]), hide_parameters=True)
    config = Config()
    config.set_main_option("script_location", "/opt/lab/services/control_api/migrations")
    with normal.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    seed(PostgresLedger(engine=normal), Decimal("999"))
    tables = (
        *TABLE_NAMES,
        "risk_account_context",
        "risk_instrument_context",
        "trading_profiles",
        "trading_profile_receipts",
        "control_http_receipts",
    )

    def fingerprint():
        with normal.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT md5(coalesce(jsonb_agg(to_jsonb(r) "
                        "ORDER BY to_jsonb(r)::text)::text, "
                        f"'[]')) FROM public.\"{table}\" r"
                    )
                ).scalar_one()
                for table in tables
            )

    before = fingerprint()
    api_engine = create_engine(normal.url, hide_parameters=True)

    @event.listens_for(api_engine, "connect")
    def role(connection, _record):
        connection.execute("SET ROLE atl_control_api")
        connection.execute("SET TIME ZONE 'UTC'")
        connection.commit()

    ledger = PostgresLedger(engine=api_engine)
    coordinator = DemoCoordinator(engine)
    auth = LocalAuth(ORIGIN, transport_origin="http://127.0.0.1:8001")
    activation = auth.issue_activation_code()
    servers = []

    def serve(application, port):
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_config=None,
                proxy_headers=False,
                server_header=False,
            )
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        deadline = monotonic() + 10
        while not server.started:
            if not thread.is_alive() or monotonic() > deadline:
                raise RuntimeError("test HTTP server unavailable")
            sleep(0.05)

    with tempfile.TemporaryDirectory(prefix="synthetic-demo-setup-") as directory:
        root = Path(directory)
        for provider in ("alpaca", "jev"):
            (root / provider).mkdir(mode=0o700)
        storage = ProviderStore(root, owner_uid=os.getuid())
        serve(setup_app(storage), 8011)

        class LoopbackSetup(FixedSetupClient):
            _base = "http://127.0.0.1:8011"

        canonical = create_app(
            auth=auth,
            backend=ControlBackend(ledger, "paper"),
            supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-normal"),
            events=EventFeed(ledger, "paper"),
            demo=coordinator,
            setup=LoopbackSetup(),
            trading_profile=TradingProfileBackend(ledger, "paper"),
            health=lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}},
        )
        serve(canonical, 8001)
        serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000)
        print(json.dumps({"ready": True, "activationCode": activation}), flush=True)
        del activation
        try:
            if sys.stdin.readline().strip() != "verify":
                raise RuntimeError("browser owner ended before SQL acceptance")
            rows = coordinator.list_runs()["items"]
            assert len(rows) == 3
            successful = {
                Decimal(row["initialBudget"]): row for row in rows if row["status"] == "SUCCEEDED"
            }
            assert set(successful) == {Decimal("100"), Decimal("200")}
            assert successful[Decimal("100")]["strategy"] == "mock-neutral"
            assert successful[Decimal("200")]["strategy"] == "neutral-baseline"
            for budget, run in successful.items():
                owner = financial_engine(engine, run["runId"])
                with owner.connect() as connection:
                    assert (
                        connection.execute(text("SELECT count(*) FROM broker_orders")).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(text("SELECT status FROM broker_orders")).scalar_one()
                        == "FILLED"
                    )
                    assert (
                        connection.execute(
                            text("SELECT quantity FROM positions WHERE instrument_id='SPY'")
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(
                            text("SELECT sum(reserved_notional) FROM risk_reservations")
                        ).scalar_one()
                        == 0
                    )
                    assert (
                        connection.execute(
                            text(
                                "SELECT cash FROM portfolio_snapshots WHERE id="
                                "(SELECT portfolio_snapshot_id FROM risk_account_context)"
                            )
                        ).scalar_one()
                        == Decimal(budget) - 5
                    )
                    assert (
                        connection.execute(
                            text("SELECT count(*) FROM demo_transport_journal WHERE kind='accept'")
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        connection.execute(
                            text("SELECT safety_state FROM risk_account_context")
                        ).scalar_one()
                        == "PAUSE_NEW"
                    )
                owner.dispose()
            failed = [row for row in rows if Decimal(row["initialBudget"]) == Decimal("0.01")]
            assert len(failed) == 1 and failed[0]["status"] == "FAILED"
            assert failed[0]["errorCode"] == "RISK_LIMIT_EXCEEDED"
            owner = financial_engine(engine, failed[0]["runId"])
            with owner.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM demo_transport_journal")
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text("SELECT safety_state FROM risk_account_context")
                    ).scalar_one()
                    == "PAUSE_NEW"
                )
            owner.dispose()
            assert storage.status() == {
                provider: {"status": "CONFIGURED", "restartRequired": True}
                for provider in ("alpaca", "jev")
            }
            assert fingerprint() == before
            print(
                SQL_PROOF,
                flush=True,
            )
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            print(f"FAIL SQL {type(error).__name__} at {frames[-1].lineno}", flush=True)
            raise
        finally:
            for server, _thread in reversed(servers):
                server.should_exit = True
            for _server, thread in reversed(servers):
                thread.join(timeout=3)
            coordinator.close()
            api_engine.dispose()
            normal.dispose()


if __name__ == "__main__":
    try:
        {"host": host, "guest": guest, "relay": relay}[sys.argv[1]]()
    except (Exception, KeyboardInterrupt) as error:
        # No exception values/tracebacks: SQL and HTTP internals may contain inputs.
        print("FAIL isolated demo browser acceptance: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
