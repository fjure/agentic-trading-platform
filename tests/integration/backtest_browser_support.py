"""Disposable real Backtest browser owner: PostgreSQL, canonical API without provider access."""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:4223"
CANARY = "synthetic-backtest-browser-provider-canary"
PHASE = "BOOT"
SQLSTATE = "NONE"


def relay():
    import socketserver

    from services.runtime import host_bridge

    name = os.environ.get("BACKTEST_BROWSER_CONTAINER", "")
    if not re.fullmatch(r"atl-backtest-browser-[0-9a-f]{20}-app", name):
        raise ValueError("isolated Backtest owner required")
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
    with socketserver.ThreadingTCPServer(("127.0.0.1", 4223), host_bridge.Handler) as server:
        server.serve_forever()


def host():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4223))
    identity = "atl-backtest-browser-" + uuid4().hex[:20]
    network, postgres, application = identity + "-net", identity + "-pg", identity + "-app"
    created, child = [], None

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("isolated Backtest Docker operation failed")
        return result.stdout.strip()

    try:
        docker("network", "create", "--internal", network)
        created.append(("network", network))
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            postgres,
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
            "postgres:18.6-trixie",
        )
        created.append(("container", postgres))
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", postgres, "pg_isready", "-U", "lab_migration", "-d", "lab"],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("isolated Backtest PostgreSQL unavailable")
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--name",
            application,
            "--network",
            network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--workdir",
            "/opt/lab",
        ]
        for source, target in [
            (ROOT / "packages", "/opt/lab/packages"),
            (ROOT / "services", "/opt/lab/services"),
            (ROOT / "alembic.ini", "/opt/lab/alembic.ini"),
            (ROOT / "apps/dashboard/dist/dashboard/browser", "/opt/lab/dashboard"),
            (Path(__file__), "/opt/lab/backtest_browser_support.py"),
        ]:
            command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        command += [
            "--entrypoint",
            "python",
            "agentic-trading-lab-research-test:local",
            "-u",
            "backtest_browser_support.py",
            "guest",
        ]
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        created.append(("container", application))
        lines = queue.Queue()

        def collect():
            for line in child.stdout:
                lines.put(line.strip())
            lines.put(None)

        threading.Thread(target=collect, daemon=True).start()

        def read_line(timeout):
            try:
                value = lines.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("Backtest browser owner deadline exceeded") from None
            if value is None:
                raise RuntimeError("Backtest browser owner exited")
            return value

        line = read_line(120)
        if line.startswith("BACKTEST_BROWSER_FAIL:"):
            if re.fullmatch(
                r"BACKTEST_BROWSER_FAIL:[A-Za-z]+:[a-z_]+[.]py:[0-9]+(?::[A-Z_0-9]+)?", line
            ):
                print(line, flush=True)
            raise RuntimeError(line)
        ready = json.loads(line)
        activation = ready.pop("activationCode")
        env = os.environ.copy()
        env.update(
            BACKTEST_BROWSER_CONTAINER=application,
            BACKTEST_BROWSER_PYTHON=sys.executable,
            BACKTEST_BROWSER_ACTIVATION=activation,
            BACKTEST_BROWSER_START=ready["start"],
            BACKTEST_BROWSER_END=ready["end"],
            BACKTEST_BROWSER_KNOWLEDGE=ready["knowledge"],
            PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        result = subprocess.run(
            [
                str(ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"),
                str(ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                "playwright.backtest.config.ts",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        env.pop("BACKTEST_BROWSER_ACTIVATION", None)
        public = result.stdout + result.stderr
        for private in (activation, CANARY):
            public = public.replace(private, "[redacted synthetic input]")
        print(public.strip(), flush=True)
        if result.returncode:
            raise RuntimeError("real Backtest browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        proof = read_line(20)
        if proof != "PASS SQL: real Backtest browser, durable states and no orders/provider reads":
            raise RuntimeError("Backtest browser SQL evidence failed")
        print(proof, flush=True)
        child.wait(timeout=20)
        if child.returncode:
            raise RuntimeError("Backtest browser owner exit failed")
    finally:
        if child is not None and child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        for kind, name in reversed(created):
            subprocess.run(
                ["docker", "rm", "--force", name]
                if kind == "container"
                else ["docker", "network", "rm", name],
                capture_output=True,
                timeout=30,
                check=False,
            )


def seed_backtest_fixture(engine):
    """Only disposable migration-owner databases: explicit synthetic past observations."""
    from sqlalchemy import text

    from packages.research.models import BarIdentity, BarInterval, BarRevision
    from packages.research.registry import definitions, register
    from packages.research.storage import identity_parameters

    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    end = start + timedelta(hours=1)
    instrument = definitions()[0][0]
    with engine.begin() as connection:
        register(connection)
        for table in (
            "research.bar_revisions",
            "research.calendar_versions",
            "backtest.corporate_action_coverage",
        ):
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER a_stamp"))
        for i in range(4):
            opening = start + timedelta(minutes=15 * i)
            closing = opening + timedelta(minutes=15)
            close = Decimal("12") if i == 3 else Decimal("10")
            bar = BarRevision(
                uuid4(),
                BarIdentity(
                    instrument.instrument_id,
                    "alpaca",
                    "iex",
                    BarInterval.FIFTEEN_MINUTES,
                    opening,
                    closing,
                ),
                Decimal("10"),
                max(close, Decimal("10")),
                Decimal("10"),
                close,
                Decimal("100"),
                closing + timedelta(seconds=30),
                closing + timedelta(seconds=30),
                commit_version=10 + i,
            )
            params = dict(
                identity_parameters(bar.identity),
                id=bar.revision_id,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                fingerprint=bar.fingerprint,
                ingested=bar.ingested_at,
                version=bar.commit_version,
            )
            connection.execute(
                text("""INSERT INTO research.bar_revisions
                (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,bar_end,
                 adjustment,open,high,low,close,volume,fingerprint,local_revision,
                 predecessor_id,quality,commit_version,ingested_at)
                VALUES(:id,:instrument_id,:provider,'SPY',:feed,:interval,:bar_start,:bar_end,
                :adjustment,:open,:high,:low,:close,:volume,:fingerprint,1,NULL,'VALID',:version,:ingested)
            """),
                params,
            )
        connection.execute(
            text("""INSERT INTO research.calendar_versions
            VALUES(:id,:day,:opening,:closing,:version,'alpaca-v2-calendar',1,:ingested)"""),
            dict(
                id=uuid4(),
                day=start.date(),
                opening=start,
                closing=end,
                version="a" * 64,
                ingested=start - timedelta(days=3),
            ),
        )
        connection.execute(
            text("""INSERT INTO backtest.corporate_action_coverage
            VALUES(:id,:instrument,:start,:end,:certified,'offline-certified-fixture',
                   :fingerprint,true,3,:ingested)"""),
            dict(
                id=uuid4(),
                instrument=instrument.instrument_id,
                start=start - timedelta(days=2),
                end=end,
                certified=start - timedelta(days=3),
                fingerprint="f" * 64,
                ingested=start - timedelta(days=2),
            ),
        )
        for table in (
            "research.bar_revisions",
            "research.calendar_versions",
            "backtest.corporate_action_coverage",
        ):
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER a_stamp"))
        connection.execute(text("UPDATE research.version_state SET version=50"))
    return dict(
        start=start.isoformat().replace("+00:00", "Z"),
        end=end.isoformat().replace("+00:00", "Z"),
        instrument=str(instrument.instrument_id),
    )


def guest():
    import logging

    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL

    from packages.persistence.ledger import PostgresLedger
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.backtest_service import BacktestService
    from services.control_api.events import EventFeed
    from services.control_api.research_service import ResearchService
    from services.control_api.snapshot import SnapshotReader
    from services.runtime.api import create_bridge
    from services.runtime.provision import seed
    from services.worker.backtesting import BacktestWorker

    logging.disable(logging.CRITICAL)
    engines, servers = [], []

    def engine(role=None):
        value = create_engine(
            URL.create(
                "postgresql+psycopg", username="lab_migration", host="postgres", database="lab"
            ),
            hide_parameters=True,
        )
        if role:

            @event.listens_for(value, "connect")
            def initialize(connection, record):
                with connection.cursor() as cursor:
                    cursor.execute("SET ROLE " + role)
                    cursor.execute("SET TIME ZONE 'UTC'")
                connection.commit()

        engines.append(value)
        return value

    admin = engine()
    migration = Config("/opt/lab/alembic.ini")
    with admin.begin() as connection:
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    seed(PostgresLedger(engine=admin), Decimal("10000"))
    fixture = seed_backtest_fixture(admin)
    control, worker_engine = engine("atl_control_api"), engine("atl_backtest_worker")
    worker = BacktestWorker(worker_engine, enabled=True, code_version="b" * 40)
    done, failures = threading.Event(), []

    def work():
        while not done.wait(0.1):
            try:
                worker.tick()
            except Exception as error:
                failures.append(type(error).__name__)
                return

    worker_thread = threading.Thread(target=work, daemon=True)
    worker_thread.start()
    auth = LocalAuth(ORIGIN, transport_origin="http://127.0.0.1:8001")
    ledger = PostgresLedger(engine=control)
    app = create_app(
        auth=auth,
        backend=ControlBackend(ledger, "paper"),
        supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-backtest"),
        events=EventFeed(ledger, "paper"),
        research=ResearchService(control),
        backtesting=BacktestService(control, code_version="b" * 40),
        health=lambda: dict(status="HEALTHY", checks=dict(DATABASE="HEALTHY")),
    )

    def serve(application, port):
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_config=None,
                proxy_headers=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("isolated Backtest HTTP unavailable")
            time.sleep(0.02)

    serve(app, 8001)
    serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000)
    print(
        json.dumps(
            dict(
                ready=True,
                activationCode=auth.issue_activation_code(),
                start=fixture["start"],
                end=fixture["end"],
                knowledge="2026-10-26T00:00:00Z",
            )
        ),
        flush=True,
    )
    try:
        if sys.stdin.readline().strip() == "verify":
            assert not failures, failures
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM public.broker_orders")
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM research.bar_revisions")
                    ).scalar_one()
                    == 4
                )
                states = set(connection.execute(text("SELECT status FROM backtest.runs")).scalars())
                assert {"SUCCEEDED", "BLOCKED"} <= states, states
            print(
                "PASS SQL: real Backtest browser, durable states and no orders/provider reads",
                flush=True,
            )
    finally:
        done.set()
        worker_thread.join(timeout=5)
        for server, thread in reversed(servers):
            server.should_exit = True
            thread.join(timeout=5)
        for value in engines:
            value.dispose()


if __name__ == "__main__":
    try:
        {"host": host, "guest": guest, "relay": relay}[sys.argv[1]]()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        detail = ":" + str(error) if re.fullmatch(r"[A-Z_0-9]{1,80}", str(error)) else ""
        print(
            f"BACKTEST_BROWSER_FAIL:{type(error).__name__}:{Path(frame.filename).name}:{frame.lineno}{detail}",
            flush=True,
        )
        raise SystemExit(1) from None
