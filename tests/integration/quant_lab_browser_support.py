"""Real Quant Lab dashboard harness with a disposable, isolated PostgreSQL owner."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from tests.integration.backtest_browser_support import ORIGIN, ROOT, relay

PAUSE = Path("/tmp/qsl-browser-worker-paused")


def host(*, full=False):
    prefix = "atl-backtest-browser-" + uuid4().hex[:20]
    network, postgres, app = (prefix + suffix for suffix in ("-net", "-pg", "-app"))
    created = []
    child = None
    output = ROOT / ".tooling/output" / prefix
    output.mkdir(parents=True)
    (output / "playwright.config.mts").write_text(
        """export default {
          testDir: '../../../tests/e2e', testMatch: 'quant-lab.spec.ts',
          timeout: 150000, expect: { timeout: 25000 }, workers: 1,
          reporter: [['list']], outputDir: './results', preserveOutput: 'never',
          use: { baseURL: 'http://127.0.0.1:4223', browserName: 'chromium',
                 screenshot: 'off', trace: 'off', video: 'off' },
          webServer: { command: process.env.QSL_BROWSER_RELAY,
            url: 'http://127.0.0.1:4223/health', timeout: 30000,
            cwd: '../../..', reuseExistingServer: false, stdout: 'ignore', stderr: 'ignore' },
        };
        """,
        encoding="utf-8",
    )

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError("isolated Docker operation failed")

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
            raise RuntimeError("isolated PostgreSQL unavailable")

        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--name",
            app,
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
        for source, target in (
            ("packages", "packages"),
            ("services", "services"),
            ("tests", "tests"),
            ("alembic.ini", "alembic.ini"),
            ("contracts/schemas", "contracts/schemas"),
            ("apps/dashboard/dist/dashboard/browser", "dashboard"),
        ):
            command += [
                "--mount",
                f"type=bind,source={ROOT / source},target=/opt/lab/{target},readonly",
            ]
        child = subprocess.Popen(
            [
                *command,
                "--entrypoint",
                "python",
                "agentic-trading-lab-research-test:local",
                "-u",
                "-m",
                "tests.integration.quant_lab_browser_support",
                "guest-full" if full else "guest",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        created.append(("container", app))
        lines = queue.Queue()

        def collect():
            for line in child.stdout:
                lines.put(line.strip())
            lines.put(None)

        threading.Thread(target=collect, daemon=True).start()

        def read_line(timeout):
            try:
                line = lines.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("isolated browser guest deadline exceeded") from None
            if line is None or line.startswith("FAIL "):
                raise RuntimeError(line or "isolated browser guest exited")
            return line

        ready = json.loads(read_line(120))
        activation = ready.pop("activationCode")
        python = sys.executable
        if any(char in python for char in '"\r\n&|<>^%'):
            raise RuntimeError("unsupported isolated Python path")
        system_env = (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LOCALAPPDATA",
            "USERPROFILE",
            "PLAYWRIGHT_BROWSERS_PATH",
        )
        env = {key: os.environ[key] for key in system_env if key in os.environ}
        env.update(
            QSL_BROWSER_ACTIVATION=activation,
            QSL_BROWSER_PERIODS=json.dumps(ready),
            QSL_BROWSER_FULL="1" if full else "0",
            QSL_BROWSER_RELAY=f'"{python}" -m tests.integration.quant_lab_browser_support relay',
            BACKTEST_BROWSER_CONTAINER=app,
            PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        node = ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"
        cli = ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"
        result = subprocess.run(
            [str(node), str(cli), "test", "--config", str(output / "playwright.config.mts")],
            env=env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=440,
        )
        print((result.stdout + result.stderr).replace(activation, "[redacted]"), flush=True)
        if result.returncode:
            raise RuntimeError("real Quant Lab browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        assert read_line(15) == "PASS SQL: Quant Lab browser and no broker orders"
        print("PASS SQL: Quant Lab browser and no broker orders")
        assert child.wait(timeout=20) == 0
    finally:
        if child is not None and child.poll() is None:
            with suppress(BrokenPipeError):
                child.stdin.close()
        for kind, name in reversed(created):
            command = ("rm", "--force", name) if kind == "container" else ("network", "rm", name)
            subprocess.run(["docker", *command], capture_output=True, timeout=30, check=False)


def seed_quant_market(engine):
    from sqlalchemy import text

    from packages.domain.models import utc_string
    from packages.research.models import BarIdentity, BarInterval, BarRevision, content_fingerprint
    from packages.research.registry import definitions, register

    origin = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    days = tuple(
        origin + timedelta(days=index)
        for index in range(49)
        if (origin + timedelta(days=index)).weekday() < 5
    )
    session = timedelta(minutes=90)
    instrument = definitions()[0][0]
    captured = origin - timedelta(days=1)
    price, volume = Decimal(100), Decimal(10)
    calendar = content_fingerprint("synthetic-quant-browser-calendar")
    tables = (
        "research.bar_revisions",
        "research.calendar_versions",
        "backtest.corporate_action_coverage",
    )
    with engine.begin() as connection:
        register(connection)
        version = connection.execute(text("SELECT research.reserve_version()")).scalar_one()
        for table in tables:
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER a_stamp"))
        connection.execute(
            text("""INSERT INTO research.calendar_versions
              VALUES(:id,:day,:opening,:closing,:calendar,'alpaca-v2-calendar',:version,:ingested)"""),
            [
                dict(
                    id=uuid4(),
                    day=opening.date(),
                    opening=opening,
                    closing=opening + session,
                    calendar=calendar,
                    version=version,
                    ingested=captured,
                )
                for opening in days
            ],
        )
        values = []
        for opening in days:
            for index in range(6):
                start = opening + timedelta(minutes=15 * index)
                end = start + timedelta(minutes=15)
                bar = BarRevision(
                    uuid4(),
                    BarIdentity(
                        instrument.instrument_id,
                        "alpaca",
                        "iex",
                        BarInterval.FIFTEEN_MINUTES,
                        start,
                        end,
                    ),
                    price,
                    price,
                    price,
                    price,
                    volume,
                    end,
                    end,
                )
                values.append(
                    dict(
                        id=bar.revision_id,
                        start=start,
                        end=end,
                        price=price,
                        volume=volume,
                        instrument=instrument.instrument_id,
                        fingerprint=bar.fingerprint,
                        version=version,
                    )
                )
        connection.execute(
            text("""INSERT INTO research.bar_revisions
                (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,
                 bar_end,adjustment,open,high,low,close,volume,fingerprint,local_revision,
                 predecessor_id,quality,commit_version,ingested_at)
                VALUES(:id,:instrument,'alpaca','SPY','iex','15Min',:start,:end,
                 'raw',:price,:price,:price,:price,:volume,:fingerprint,1,NULL,'VALID',:version,:end)"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO backtest.corporate_action_coverage
                VALUES(:id,:instrument,:start,:end,:captured,'offline-quant-browser',
                       :fingerprint,true,:version,:captured)"""),
            dict(
                id=uuid4(),
                instrument=instrument.instrument_id,
                start=origin - timedelta(days=1),
                end=days[-1] + session,
                captured=captured,
                version=version,
                fingerprint=content_fingerprint("synthetic-quant-browser-coverage"),
            ),
        )
        for table in tables:
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER a_stamp"))

    def period(first, last):
        return [utc_string(days[first]), utc_string(days[last] + session)]

    return {
        "training": period(9, 9),
        "validation": period(10, 30),
        "test": period(31, 31),
        "holdout": period(32, 32),
    }


def guest(*, full=False):
    import logging

    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from packages.persistence.ledger import PostgresLedger
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.backtest_service import BacktestService
    from services.control_api.events import EventFeed
    from services.control_api.quant_lab_routes import service_handler
    from services.control_api.quant_lab_service import QuantLabService
    from services.control_api.research_service import ResearchService
    from services.control_api.snapshot import SnapshotReader
    from services.runtime.api import create_bridge
    from services.runtime.provision import seed
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_jobs import role_engine

    logging.disable(logging.CRITICAL)
    admin = create_engine("postgresql+psycopg://lab_migration@postgres/lab", hide_parameters=True)
    config = Config("/opt/lab/alembic.ini")
    with admin.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    seed(PostgresLedger(engine=admin), Decimal("10000"))
    if full:
        from tests.integration.test_quant_lab_acceptance import PERIODS, seed_market

        seed_market(admin)
        periods = PERIODS
    else:
        periods = seed_quant_market(admin)
    control = role_engine(admin, "atl_control_api")
    dispatch = role_engine(admin, "atl_backtest_worker")
    worker = BacktestWorker(dispatch, enabled=True, code_version="b" * 40)
    auth = LocalAuth(ORIGIN, transport_origin="http://127.0.0.1:8001")
    ledger = PostgresLedger(engine=control)
    app = create_app(
        auth=auth,
        backend=ControlBackend(ledger, "paper"),
        supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-quant"),
        events=EventFeed(ledger, "paper"),
        research=ResearchService(control),
        backtesting=BacktestService(control, code_version="b" * 40),
        quant_lab=service_handler(QuantLabService(control, code_version="b" * 40)),
        health=lambda: dict(status="HEALTHY", checks=dict(DATABASE="HEALTHY")),
    )
    done, faults, servers = threading.Event(), [], []

    def work():
        while not done.wait(0.1):
            if PAUSE.exists():
                continue
            try:
                worker.tick()
            except Exception as error:
                faults.append(type(error).__name__)
                return

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
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("isolated HTTP unavailable")
            time.sleep(0.02)

    worker_thread = threading.Thread(target=work, daemon=True)
    worker_thread.start()
    try:
        serve(app, 8001)
        serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000)
        print(json.dumps(periods | {"activationCode": auth.issue_activation_code()}), flush=True)
        if sys.stdin.readline().strip() == "verify":
            assert not faults
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM public.broker_orders")
                    ).scalar_one()
                    == 0
                )
                assert (
                    connection.execute(
                        text("""SELECT count(*) FROM backtest.experiment_batches
                    WHERE status='SUCCEEDED' AND slots=11""")
                    ).scalar_one()
                    == 1
                )
                if full:
                    assert connection.execute(
                        text("""SELECT bar_steps FROM backtest.experiment_batches
                        WHERE status='SUCCEEDED'""")
                    ).scalar_one() == 9178
                assert (
                    connection.execute(
                        text("""SELECT count(*) FROM backtest.experiment_selections
                    WHERE status='NO_ELIGIBLE_CANDIDATE'""")
                    ).scalar_one()
                    == 2
                )
                assert (
                    connection.execute(
                        text("""SELECT count(*) FROM backtest.experiment_batches
                    WHERE status='ACCEPTED' AND slots=11""")
                    ).scalar_one()
                    == 1
                )
            print("PASS SQL: Quant Lab browser and no broker orders", flush=True)
    finally:
        done.set()
        worker_thread.join(timeout=5)
        for server, thread in reversed(servers):
            server.should_exit = True
            thread.join(timeout=5)
        for engine in (admin, control, dispatch):
            engine.dispose()


if __name__ == "__main__":
    try:
        entry, _, variant = sys.argv[1].partition("-")
        if entry == "relay":
            relay()
        else:
            {"host": host, "guest": guest}[entry](full=variant == "full")
    except Exception as error:
        import traceback

        location = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"FAIL {type(error).__name__} {Path(location.filename).name}:{location.lineno}",
            flush=True,
        )
        raise SystemExit(1) from None
