"""Isolierte Readiness-Browserabnahme mit echter PostgreSQL-/API-Laufzeit."""

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
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:4224"
CANARY = "synthetic-readiness-browser-provider-canary"


def relay():
    import socketserver

    from services.runtime import host_bridge

    name = os.environ.get("READINESS_BROWSER_CONTAINER", "")
    if not re.fullmatch(r"atl-readiness-browser-[0-9a-f]{20}-app", name):
        raise ValueError("isolated Readiness owner required")
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
    with socketserver.ThreadingTCPServer(("127.0.0.1", 4224), host_bridge.Handler) as server:
        server.serve_forever()


def host():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4224))
    identity = "atl-readiness-browser-" + uuid4().hex[:20]
    network, postgres, application = identity + "-net", identity + "-pg", identity + "-app"
    created, child = [], None

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("isolated Readiness Docker operation failed")
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
            raise RuntimeError("isolated Readiness PostgreSQL unavailable")
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
            (ROOT / "contracts", "/opt/lab/contracts"),
            (ROOT / "alembic.ini", "/opt/lab/alembic.ini"),
            (ROOT / "apps/dashboard/dist/dashboard/browser", "/opt/lab/dashboard"),
            (Path(__file__), "/opt/lab/readiness_browser_support.py"),
        ]:
            command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        command += [
            "--entrypoint",
            "python",
            "agentic-trading-lab-research-test:local",
            "-u",
            "readiness_browser_support.py",
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
                raise RuntimeError("Readiness browser owner deadline exceeded") from None
            if value is None:
                raise RuntimeError("Readiness browser owner exited")
            return value

        line = read_line(120)
        if line.startswith("READINESS_BROWSER_FAIL:"):
            if re.fullmatch(
                r"READINESS_BROWSER_FAIL:[A-Za-z]+:[a-z_]+[.]py:[0-9]+(?::[A-Z_0-9]+)?", line
            ):
                print(line, flush=True)
            raise RuntimeError(line)
        ready = json.loads(line)
        activation = ready.pop("activationCode")
        env = os.environ.copy()
        env.update(
            READINESS_BROWSER_CONTAINER=application,
            READINESS_BROWSER_PYTHON=sys.executable,
            READINESS_BROWSER_ACTIVATION=activation,
            PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        result = subprocess.run(
            [
                str(ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"),
                str(ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                "playwright.readiness.config.ts",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        env.pop("READINESS_BROWSER_ACTIVATION", None)
        public = result.stdout + result.stderr
        for private in (activation, CANARY):
            public = public.replace(private, "[redacted synthetic input]")
        print(public.strip(), flush=True)
        if result.returncode:
            raise RuntimeError("real Readiness browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        proof = read_line(20)
        if re.fullmatch(
            r"READINESS_BROWSER_FAIL:[A-Za-z]+:[a-z_]+[.]py:[0-9]+(?::[A-Z_0-9]+)?", proof
        ):
            print(proof, flush=True)
        if proof != "PASS SQL: real Readiness browser, durable states and no orders/provider reads":
            raise RuntimeError("Readiness browser SQL evidence failed")
        print(proof, flush=True)
        child.wait(timeout=20)
        if child.returncode:
            raise RuntimeError("Readiness browser owner exit failed")
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


def guest():
    """Nur lokale flüchtige Testrollen und echte Gates, ohne Provider oder Testsignaturen."""
    import asyncio
    import logging
    from datetime import date, timedelta

    import httpx
    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL

    from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
    from packages.persistence.ledger import PostgresLedger
    from packages.research.collection import CollectionScheduler
    from packages.research.limits import SharedAccountHostLimiter
    from packages.research.ports import LicenseStatus
    from packages.research.registry import definitions, register
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.backtest_service import BacktestService
    from services.control_api.collection_service import CollectionPlanService
    from services.control_api.events import EventFeed
    from services.control_api.readiness_service import ReadinessService
    from services.control_api.research_service import ResearchService
    from services.control_api.snapshot import SnapshotReader
    from services.execution.research_collection import CalendarCollection
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
        register(connection)
    seed(PostgresLedger(engine=admin), Decimal("10000"))
    control, worker_engine = engine("atl_control_api"), engine("atl_backtest_worker")
    execution = engine("atl_execution")
    # QQQ ist ausschließlich kontrollierte lokale HTTPdouble-Vorbedingung. Der
    # Browser bedient danach SPY bei wieder deaktiviertem globalem Quellengate.
    http_requests = []

    async def http_double(request):
        assert request.url.path == "/v2/calendar"
        http_requests.append(request.url.path)
        first, last = (
            date.fromisoformat(request.url.params["start"]),
            date.fromisoformat(request.url.params["end"]),
        )
        days = []
        while first <= last:
            if first.weekday() < 5:
                days.append(dict(date=first.isoformat(), open="09:30", close="16:00"))
            first += timedelta(days=1)
        return httpx.Response(200, json=days)

    async def source():
        return AlpacaHistorySource(
            api_key=CANARY,
            api_secret=CANARY,
            enabled=True,
            policy=ProviderPolicy(LicenseStatus.CONFIRMED, "synthetic-browser-v1"),
            transport=httpx.MockTransport(http_double),
        )

    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.source_status SET state='IMPLEMENTED',"
                "license_status='CONFIRMED' WHERE singleton"
            )
        )
    plans = CollectionPlanService(control)
    qqq = str(definitions()[1][0].instrument_id)
    plans.update_plan(
        qqq,
        dict(enabled=True, expectedVersion=0),
        "local-operator",
        "browser-qqq-enable01",
        "a" * 32,
    )
    CollectionScheduler(execution, enabled=True).tick()
    calendar = CalendarCollection(
        execution,
        source,
        SharedAccountHostLimiter("synthetic-readiness-browser", 10000),
        enabled=True,
    )
    assert asyncio.run(calendar.tick())
    assert http_requests == ["/v2/calendar"]
    plans.update_plan(
        qqq,
        dict(enabled=False, expectedVersion=1),
        "local-operator",
        "browser-qqq-pause001",
        "a" * 32,
    )
    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.source_status SET state='DISABLED',"
                "license_status='UNKNOWN' WHERE singleton"
            )
        )
        assert (
            connection.execute(text("SELECT count(*) FROM research.calendar_versions")).scalar_one()
            > 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM research.commit_confirmations "
                    "WHERE receipt_kind='OBSERVATION'"
                )
            ).scalar_one()
            == 1
        )
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
        supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-readiness"),
        events=EventFeed(ledger, "paper"),
        research=ResearchService(control),
        backtesting=BacktestService(control, code_version="b" * 40),
        readiness=ReadinessService(control, code_version="b" * 40),
        collection=CollectionPlanService(control),
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
                raise RuntimeError("isolated Readiness HTTP unavailable")
            time.sleep(0.02)

    serve(app, 8001)
    serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000)
    print(json.dumps(dict(ready=True, activationCode=auth.issue_activation_code())), flush=True)
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
                    connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one() == 0
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM research.bar_revisions")
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM research.observation_receipts "
                            "WHERE instrument_id=:id"
                        ),
                        dict(id=definitions()[0][0].instrument_id),
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM research.observation_receipts "
                            "WHERE state='COMPLETED' AND first_observed_at IS NOT NULL"
                        )
                    ).scalar_one()
                    == 1
                )
                assert http_requests == ["/v2/calendar"]
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM backtest.readiness_reports")
                    ).scalar_one()
                    == 1
                )
                assert set(
                    connection.execute(text("SELECT status FROM backtest.readiness_jobs")).scalars()
                ) == {"SUCCEEDED"}
                plan = (
                    connection.execute(
                        text(
                            "SELECT enabled,status,version,generation FROM "
                            "research.collection_plans WHERE instrument_id=:id"
                        ),
                        dict(id=definitions()[0][0].instrument_id),
                    )
                    .mappings()
                    .one()
                )
                assert dict(plan) == dict(enabled=False, status="PAUSED", version=2, generation=2)
                source = connection.execute(
                    text("SELECT state,license_status FROM research.source_status WHERE singleton")
                ).one()
                assert tuple(source) == ("DISABLED", "UNKNOWN")
            print(
                "PASS SQL: real Readiness browser, durable states and no orders/provider reads",
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
            f"READINESS_BROWSER_FAIL:{type(error).__name__}:"
            f"{Path(frame.filename).name}:{frame.lineno}{detail}",
            flush=True,
        )
        raise SystemExit(1) from None
