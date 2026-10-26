"""Disposable real Research browser owner: PostgreSQL, canonical API and provider HTTP."""

from __future__ import annotations

import asyncio
import hashlib
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
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:4221"
CANARY = "synthetic-research-browser-provider-canary"
PHASE = "BOOT"
SQLSTATE = "NONE"


def relay():
    import socketserver

    from services.runtime import host_bridge

    name = os.environ.get("RESEARCH_BROWSER_CONTAINER", "")
    if not re.fullmatch(r"atl-research-browser-[0-9a-f]{20}-app", name):
        raise ValueError("isolated Research owner required")
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
    with socketserver.ThreadingTCPServer(("127.0.0.1", 4221), host_bridge.Handler) as server:
        server.serve_forever()


def host():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4221))
    identity = "atl-research-browser-" + uuid4().hex[:20]
    network, postgres, application = identity + "-net", identity + "-pg", identity + "-app"
    created, child = [], None

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("isolated Research Docker operation failed")
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
            raise RuntimeError("isolated Research PostgreSQL unavailable")
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
            (Path(__file__), "/opt/lab/research_browser_support.py"),
        ]:
            command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        command += [
            "--entrypoint",
            "python",
            "agentic-trading-lab-research-test:local",
            "-u",
            "research_browser_support.py",
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
                raise RuntimeError("Research browser owner deadline exceeded") from None
            if value is None:
                raise RuntimeError("Research browser owner exited")
            return value

        line = read_line(120)
        if line.startswith("RESEARCH_BROWSER_FAIL:"):
            if re.fullmatch(
                r"RESEARCH_BROWSER_FAIL:[A-Za-z]+:[a-z_]+[.]py:[0-9]+(?::[A-Z_0-9]+)?", line
            ):
                print(line, flush=True)
            raise RuntimeError(line)
        ready = json.loads(line)
        activation = ready.pop("activationCode")
        env = os.environ.copy()
        env.update(
            RESEARCH_BROWSER_CONTAINER=application,
            RESEARCH_BROWSER_PYTHON=sys.executable,
            RESEARCH_BROWSER_ACTIVATION=activation,
            RESEARCH_BROWSER_START=ready["start"],
            RESEARCH_BROWSER_END=ready["end"],
            RESEARCH_BROWSER_KNOWLEDGE=ready["knowledge"],
            PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        result = subprocess.run(
            [
                str(ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"),
                str(ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                "playwright.research.config.ts",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
        env.pop("RESEARCH_BROWSER_ACTIVATION", None)
        public = result.stdout + result.stderr
        for private in (activation, CANARY):
            public = public.replace(private, "[redacted synthetic input]")
        print(public.strip(), flush=True)
        if result.returncode:
            raise RuntimeError("real Research browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        proof = read_line(20)
        if proof != "PASS SQL: real Research browser, durable states and no orders/provider reads":
            raise RuntimeError("Research browser SQL evidence failed")
        print(proof, flush=True)
        child.wait(timeout=20)
        if child.returncode:
            raise RuntimeError("Research browser owner exit failed")
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
    import logging
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    import httpx
    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, event, text

    from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
    from packages.persistence.ledger import PostgresLedger
    from packages.research.calendar import ResearchSession
    from packages.research.calendar_storage import append_sessions
    from packages.research.jobs import ResearchJobs
    from packages.research.models import (
        Adjustment,
        AssetClass,
        BarInterval,
        Instrument,
        SymbolMapping,
        content_fingerprint,
    )
    from packages.research.ports import HistoricalBarsRequest, LicenseStatus, SourceErrorCode
    from packages.research.storage import ResearchStore
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.events import EventFeed
    from services.control_api.research_service import ResearchService
    from services.control_api.snapshot import SnapshotReader
    from services.runtime.api import create_bridge
    from services.runtime.provision import seed

    global PHASE
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
            def initialize(connection, _record):
                with connection.cursor() as cursor:
                    cursor.execute("SET ROLE " + role)
                    cursor.execute("SET TIME ZONE 'UTC'")
                connection.commit()

        @event.listens_for(value, "handle_error")
        def failed(context):
            global SQLSTATE
            code = getattr(context.original_exception, "sqlstate", "NONE")
            SQLSTATE = (
                code if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{5}", code) else "NONE"
            )

        engines.append(value)
        return value

    admin = engine()
    migration = Config("/opt/lab/alembic.ini")
    with admin.begin() as connection:
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    seed(PostgresLedger(engine=admin), Decimal("10000"))
    execution, control = engine("atl_execution"), engine("atl_control_api")
    store = ResearchStore()
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    start = datetime(2026, 10, 23, 13, 30, tzinfo=UTC)
    end = datetime(2026, 10, 23, 20, tzinfo=UTC)
    with execution.begin() as connection:
        store.add_instrument(connection, instrument)
        store.add_mapping(
            connection,
            SymbolMapping(
                instrument.instrument_id, "alpaca", "SPY", datetime(2000, 1, 1, tzinfo=UTC)
            ),
        )
        connection.execute(
            text("""UPDATE research.source_status SET state='IMPLEMENTED',
          license_status='CONFIRMED',policy_version='offline-browser-v1',
          provider_verification='UNVERIFIED',observed_at=clock_timestamp() WHERE singleton""")
        )
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            path = urlsplit(self.path)
            if path.path == "/v2/calendar":
                payload = [{"date": "2026-10-23", "open": "09:30", "close": "16:00"}]
            elif path.path == "/v2/stocks/bars":
                second = parse_qs(path.query).get("page_token") == ["page-two"]
                indexes = [2] if second else [0, 1]
                payload = {
                    "bars": {
                        "SPY": [
                            dict(
                                t=(start + timedelta(minutes=15 * i)).isoformat(),
                                o="100.125",
                                h="105",
                                l="95",
                                c=str(100 + i),
                                v="1000.125",
                            )
                            for i in indexes
                        ]
                    },
                    "next_page_token": None if second else "page-two",
                }
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()

    async def ingest():
        async with httpx.AsyncClient(trust_env=False) as local:

            async def forward(request):
                assert request.method == "GET" and request.url.host in {
                    "data.alpaca.markets",
                    "paper-api.alpaca.markets",
                }
                response = await local.get(
                    f"http://127.0.0.1:{provider.server_port}"
                    + request.url.raw_path.decode("ascii")
                )
                return httpx.Response(response.status_code, content=response.content)

            source = AlpacaHistorySource(
                api_key=CANARY,
                api_secret=CANARY,
                enabled=True,
                policy=ProviderPolicy(LicenseStatus.CONFIRMED, "offline-browser-v1"),
                transport=httpx.MockTransport(forward),
            )
            try:
                calendar = await source.fetch_calendar(start.date(), start.date())
                with execution.begin() as connection:
                    append_sessions(
                        connection,
                        tuple(
                            ResearchSession(
                                item.session_date,
                                item.opens_at,
                                item.closes_at,
                                item.version,
                                item.source,
                            )
                            for item in calendar.sessions
                        ),
                    )
                request = HistoricalBarsRequest(
                    instrument.instrument_id,
                    start,
                    end,
                    symbol="SPY",
                    feed="iex",
                    interval=BarInterval.FIFTEEN_MINUTES,
                    adjustment=Adjustment.RAW,
                )
                token = None
                pages = []
                for _ in range(2):
                    page = await source.fetch_page(request, token, sessions=calendar.sessions)
                    persisted = []
                    with execution.begin() as connection:
                        for bar in page.items:
                            persisted.append(store.append_bar(connection, bar, symbol="SPY"))
                    token = page.next_page_token
                    pages.append((tuple(persisted), token))
                assert token is None
                return tuple(pages)
            finally:
                await source.close()

    pages = asyncio.run(ingest())
    provider.shutdown()
    provider.server_close()
    assert len(calls) == 3
    admissions, jobs, owner = ResearchJobs(control), ResearchJobs(execution), uuid4()
    request = dict(
        instrumentId=str(instrument.instrument_id),
        start=start.isoformat().replace("+00:00", "Z"),
        end=end.isoformat().replace("+00:00", "Z"),
        interval="15Min",
        mode="BACKFILL",
        feed="iex",
        adjustment="raw",
    )
    partial = admissions.enqueue(
        {**request, "end": "2026-10-23T19:45:00Z"},
        "local-operator",
        "research-browser-partial-01",
        uuid4().hex,
    )
    first_page, first_cursor = pages[0]

    def first_page_write(connection):
        for bar in first_page:
            store.append_bar(connection, bar, symbol="SPY")
        return len(first_page)

    jobs.commit_page(
        jobs.claim(owner),
        content_fingerprint(tuple(bar.fingerprint for bar in first_page)),
        hashlib.sha256(b"").hexdigest(),
        first_cursor,
        first_page_write,
    )
    for attempt in range(4):
        PHASE = "CLAIM_" + str(attempt)
        claim = jobs.claim(owner)
        deadline = time.monotonic() + 8
        while claim is None and time.monotonic() < deadline:
            time.sleep(0.05)
            claim = jobs.claim(owner)
        assert claim is not None, (
            f"CLAIM_{attempt}_{jobs.read_job(UUID(partial['jobId']))['status']}"
        )
        PHASE = "RETRY_" + str(attempt)
        result = jobs.retry(claim, SourceErrorCode.TIMEOUT)
        if attempt < 3:
            wait = datetime.fromisoformat(result["retryAt"].replace("Z", "+00:00")) - datetime.now(
                UTC
            )
            time.sleep(max(0, wait.total_seconds()) + 0.02)
    assert result["status"] == "PARTIAL"
    blocked = admissions.enqueue(
        {**request, "end": "2026-10-23T19:30:00Z"},
        "local-operator",
        "research-browser-blocked-01",
        uuid4().hex,
    )
    jobs.block(jobs.claim(owner), SourceErrorCode.BLOCKED_CREDENTIALS)
    retrying = admissions.enqueue(
        {**request, "end": "2026-10-23T19:15:00Z"},
        "local-operator",
        "research-browser-retry-01",
        uuid4().hex,
    )
    jobs.retry(jobs.claim(owner), SourceErrorCode.RATE_LIMITED, 60)
    auth = LocalAuth(ORIGIN, transport_origin="http://127.0.0.1:8001")
    ledger = PostgresLedger(engine=control)
    app = create_app(
        auth=auth,
        backend=ControlBackend(ledger, "paper"),
        supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-research"),
        events=EventFeed(ledger, "paper"),
        research=ResearchService(control),
        health=lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}},
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
                raise RuntimeError("isolated Research HTTP unavailable")
            time.sleep(0.02)

    serve(app, 8001)
    serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000)
    print(
        json.dumps(
            dict(
                ready=True,
                activationCode=auth.issue_activation_code(),
                start=request["start"],
                end=request["end"],
                knowledge=(datetime.now(UTC) + timedelta(minutes=1))
                .isoformat()
                .replace("+00:00", "Z"),
            )
        ),
        flush=True,
    )
    try:
        if sys.stdin.readline().strip() == "verify":
            resumed = jobs.read_job(UUID(partial["jobId"]))
            assert (
                resumed["status"] == "ACCEPTED" and resumed["pages"] == 1 and resumed["bars"] == 2
            )
            assert jobs.read_job(UUID(blocked["jobId"]))["status"] == "BLOCKED"
            assert jobs.read_job(UUID(retrying["jobId"]))["status"] == "RETRY_WAIT"
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT private_cursor FROM research.ingestion_jobs WHERE job_id=:id"),
                        {"id": UUID(partial["jobId"])},
                    ).scalar_one()
                    == first_cursor
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM research.resume_requests")
                    ).scalar_one()
                    == 1
                )
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
                    == 3
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM research.ingestion_jobs")
                    ).scalar_one()
                    == 4
                )
            assert len(calls) == 3
            print(
                "PASS SQL: real Research browser, durable states and no orders/provider reads",
                flush=True,
            )
    finally:
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
        detail += "_" + PHASE + "_" + SQLSTATE
        print(
            f"RESEARCH_BROWSER_FAIL:{type(error).__name__}:{Path(frame.filename).name}:{frame.lineno}{detail}",
            flush=True,
        )
        raise SystemExit(1) from None
