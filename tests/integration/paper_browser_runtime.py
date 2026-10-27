"""Actual role-isolated components for the disposable Paper browser owner.

Only provider HTTP is synthetic. Paper starts empty: no financial fixture,
activation, order, receipt, session override or ledger repair is admitted here.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import uuid
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

from fastapi import FastAPI, Request

STAGE = "BOOT"


def guest():
    import httpx
    import psycopg
    import uvicorn
    from alembic import command
    from alembic.config import Config
    from fastapi.responses import JSONResponse
    from paper_browser_provider import PaperProvider
    from sqlalchemy import URL, create_engine, event, text

    from packages.persistence.ledger import PostgresLedger
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.events import EventFeed
    from services.control_api.paper_client import PaperClient
    from services.control_api.paper_commands import PaperCommands
    from services.control_api.snapshot import SnapshotReader
    from services.control_api.trading_profile import TradingProfileBackend
    from services.demo.coordinator import DemoCoordinator
    from services.runtime.api import create_bridge
    from services.runtime.common import Clock
    from services.runtime.config import PAPER_ROLES, ROLES, RuntimeConfig
    from services.runtime.paper_api import create_private_paper_app
    from services.runtime.provision import _private_logins, seed
    from services.runtime.provision_demo import provision_demo
    from services.runtime.provision_paper import provision_paper
    from services.runtime.worker import WorkerRuntime
    from services.setup.app import create_app as setup_app
    from services.setup.client import FixedSetupClient
    from services.setup.store import ProviderStore

    global STAGE
    logging.disable(logging.CRITICAL)
    origin = "http://127.0.0.1:4220"
    common = dict(host="postgres", user="lab_migration", password="synthetic-unused")
    engines, servers, threads = [], [], []
    stop = threading.Event()
    failures, external = [], []
    clock = Clock()

    def engine(database, username, group=None):
        value = create_engine(URL.create(
            "postgresql+psycopg", username=username, host="postgres", database=database,
        ), hide_parameters=True, connect_args={"connect_timeout": 5})
        if group:
            @event.listens_for(value, "connect")
            def initialize(connection, _record):
                with connection.cursor() as cursor:
                    cursor.execute("SET ROLE " + group)
                    cursor.execute("SET TIME ZONE 'UTC'")
                connection.commit()
        engines.append(value)
        return value

    def serve(application, port):
        server = uvicorn.Server(uvicorn.Config(
            application, host="127.0.0.1", port=port, access_log=False,
            log_config=None, proxy_headers=False, server_header=False,
        ))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        deadline = monotonic() + 10
        while not server.started:
            if not thread.is_alive() or monotonic() > deadline:
                raise RuntimeError("test HTTP unavailable")
            sleep(0.02)

    def schedule(callback, seconds):
        def loop():
            while not stop.is_set():
                try:
                    callback()
                except Exception as error:
                    # Diagnostic identity only, never exception values/SQL/HTTP.
                    import traceback

                    frame = traceback.extract_tb(error.__traceback__)[-1]
                    failures.append(f"{type(error).__name__}:{Path(frame.filename).name}:{frame.lineno}")
                stop.wait(seconds)
        thread = threading.Thread(target=loop, daemon=True)
        threads.append(thread)
        thread.start()

    with tempfile.TemporaryDirectory(prefix="synthetic-paper-browser-") as directory:
        root = Path(directory)
        private = root / "provider"
        private.mkdir(mode=0o700)
        for name in ("alpaca", "jev"):
            (private / name).mkdir(mode=0o700)
        secrets = root / "generated-db"
        secrets.mkdir(mode=0o700)
        STAGE = "PROVISION"
        with psycopg.connect(dbname="lab", autocommit=True, **common) as connection:
            connection.execute("CREATE DATABASE lab_runtime OWNER lab_migration")
        normal_admin = engine("lab_runtime", "lab_migration")
        alembic = Config()
        alembic.set_main_option("script_location", "/opt/lab/services/control_api/migrations")
        with normal_admin.begin() as connection:
            alembic.attributes["connection"] = connection
            command.upgrade(alembic, "head")
        # Only the separate Fake lab receives its normal disabled simulation
        # basis. The fixed Paper DB is provisioned empty by its real provisioner.
        seed(PostgresLedger(engine=normal_admin), Decimal("10000"))
        with psycopg.connect(dbname="lab_runtime", **common) as connection:
            connection.execute("REVOKE ALL ON DATABASE lab_runtime FROM PUBLIC")
            _private_logins(connection, secrets)
        provision_demo(common, secrets)
        provision_paper(common, secrets)
        roles = {name: engine("lab_paper_runtime", group + "_login", group)
                 for name, group in PAPER_ROLES.items()}
        normal = engine("lab_runtime", ROLES["api"] + "_login", ROLES["api"])
        demo_engine = engine("lab_demo", "atl_demo_provisioner_login")
        demo = DemoCoordinator(demo_engine)
        fake_ledger = PostgresLedger(engine=normal)
        paper_admin = engine("lab_paper_runtime", "lab_migration")

        def scalar(statement, values=None):
            with paper_admin.connect() as connection:
                return connection.execute(text(statement), values or {}).scalar_one()

        assert scalar("SELECT count(*) FROM accounts") == 0
        assert scalar("SELECT count(*) FROM broker_orders") == 0

        financial_tables = (
            "accounts", "positions", "portfolio_snapshots", "trade_proposals",
            "risk_decisions", "risk_reservations", "order_intents", "broker_orders",
            "execution_reports",
        )

        def normal_finances():
            with normal_admin.connect() as connection:
                return tuple(connection.execute(text(
                    "SELECT md5(coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text)"
                    f"::text,'[]')) FROM public.{table} r"
                )).scalar_one() for table in financial_tables)

        original_finances = normal_finances()
        original_runs = demo.list_runs()
        execution_process = [None]
        execution_events = {"crashes": 0, "restarts": 0, "armed": False}
        crashed_fences = []

        def crash_if_armed(method, connection):
            if execution_events["armed"] != method:
                return
            execution_events["armed"] = False
            crashed_fences.append(connection.execute(
                "SELECT fencing_token FROM execution_owners WHERE account_id='paper'"
            ).fetchone()[0])
            execution_process[0].kill()
            execution_process[0].wait(timeout=10)
            assert execution_process[0].returncode == -9
            execution_events["crashes"] += 1

        def start_execution():
            child = subprocess.Popen(
                [sys.executable, "-u", "/opt/lab/paper_browser_execution.py", "execution",
                 str(private)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True,
            )
            execution_process[0] = child
            ready = queue.Queue()

            def messages():
                for line in child.stdout:
                    value = line.strip()
                    if value == "READY":
                        ready.put(True)
                    else:
                        failures.append(value if value.startswith("FAIL:") else "CHILD_PROTOCOL")
                ready.put(False)

            threading.Thread(target=messages, daemon=True).start()
            if ready.get(timeout=10) is not True:
                raise RuntimeError("Execution child unavailable")

        def before_io(request):
            if request.url.host not in {"paper-api.alpaca.markets", "data.alpaca.markets"}:
                external.append(True)
                raise AssertionError("external provider request denied")
            # The child checks its actual calling thread's transaction depth
            # before forwarding. Here verify the durable attempt before effect.
            with psycopg.connect(dbname="lab_paper_runtime", **common) as connection:
                if request.method == "POST":
                    body = json.loads(request.content)
                    assert connection.execute(
                        "SELECT count(*) FROM broker_orders WHERE client_order_id=%s "
                        "AND status='SUBMITTING'", (body["client_order_id"],),
                    ).fetchone()[0] == 1
                    crash_if_armed("POST", connection)
                if request.method == "DELETE":
                    assert connection.execute(
                        "SELECT count(*) FROM paper_cancel_attempts WHERE kind='DELETE'"
                    ).fetchone()[0] >= 1
                    crash_if_armed("DELETE", connection)

        model = os.environ.get("PAPER_BROWSER_MODEL") == "1"
        provider = PaperProvider(clock, before_io=before_io, rising=model)
        provider_app = FastAPI()

        @provider_app.post("/request")
        async def forward_provider(request: Request):
            value = await request.json()
            if type(value) is not dict or set(value) != {"method", "url", "headers", "body"}:
                return JSONResponse({"error": "INVALID_FORWARD"}, status_code=400)
            original = httpx.Request(value["method"], value["url"],
                                     headers=value["headers"], content=value["body"].encode())
            try:
                reply = provider.handle(original)
            except httpx.ReadTimeout:
                return {"fault": "TIMEOUT"}
            reply.read()
            return {"status": reply.status_code, "headers": list(reply.headers.multi_items()),
                    "responseBody": reply.content.decode()}
        config = RuntimeConfig(deployment="paper", broker_provider="alpaca-paper")
        worker = WorkerRuntime(config, PostgresLedger(engine=roles["worker"]))

        def worker_tick():
            result = worker.tick()
            with roles["worker"].begin() as connection:
                connection.execute(text("SELECT paper_worker_heartbeat(:state)"),
                                   {"state": "READY" if result == "READY" else "UNAVAILABLE"})

        def watchdog_tick():
            with roles["watchdog"].begin() as connection:
                connection.execute(text("SELECT public.paper_watchdog_poll(:trace)"),
                                   {"trace": uuid.uuid4().hex})

        auth = LocalAuth(origin, transport_origin="http://127.0.0.1:8001")
        app = create_app(
            auth=auth, backend=ControlBackend(fake_ledger, "paper"),
            supervisor=SnapshotReader(fake_ledger, "paper", account_alias="isolated-fake"),
            events=EventFeed(fake_ledger, "paper"), demo=demo, setup=FixedSetupClient(),
            trading_profile=TradingProfileBackend(fake_ledger, "paper"), paper=PaperClient(),
            health=lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}},
        )
        receipts = {}

        @app.post("/api/v1/__test/execution")
        async def control_execution(request: Request):
            value = await request.json()
            if value in ({"action": "crash-next-delete"}, {"action": "crash-next-submit"}):
                execution_events["armed"] = "DELETE" if value["action"].endswith("delete") \
                    else "POST"
            elif value == {"action": "restart"} and execution_process[0].poll() is not None:
                start_execution()
                execution_events["restarts"] += 1
            else:
                return JSONResponse({"error": "INVALID_EXECUTION_CONTROL"}, status_code=409)
            return {"accepted": True}

        @app.post("/api/v1/__test/provider")
        async def control_provider(request: Request):
            value = await request.json()
            if type(value) is not dict or set(value) != {"action"}:
                return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
            key = request.headers.get("idempotency-key")
            prior = receipts.get(key)
            if prior is not None:
                return JSONResponse(prior[1], status_code=200 if prior[0] == value else 409)
            result = provider.control(value["action"])
            receipts[key] = (value, result)
            return result

        @app.get("/api/v1/__test/evidence")
        async def evidence():
            result = provider.evidence()
            own_orders = scalar("SELECT count(*) FROM broker_orders")
            return {
                "provider": result, "noDuplicateSubmit": result["posts"] == own_orders,
                "noExternal": not external, "normalFinanceUnchanged":
                normal_finances() == original_finances and demo.list_runs() == original_runs,
                "modelDecisions": scalar("SELECT count(*) FROM decisions"),
                "execution": execution_events | {"alive": execution_process[0].poll() is None},
            }

        STAGE = "SERVE"
        serve(setup_app(ProviderStore(private, owner_uid=os.getuid())), 8011)
        serve(create_private_paper_app(PaperCommands(roles["api"])), 8012)
        serve(app, 8001)
        serve(create_bridge(SimpleNamespace(origin=origin)), 8000)
        serve(provider_app, 8021)
        # Actual components run their ordinary ports. Only scheduling frequency
        # differs from deployment; every deadline/lease/calendar gate is intact.
        start_execution()
        schedule(worker_tick, 1)
        schedule(watchdog_tick, 1)
        STAGE = "BROWSER"
        print(json.dumps({"ready": True, "activationCode": auth.issue_activation_code()}),
              flush=True)
        try:
            instruction = sys.stdin.readline().strip()
            if instruction == "diagnose":
                with paper_admin.connect() as connection:
                    operations = connection.execute(text(
                        "SELECT status,error_code FROM paper_commands ORDER BY accepted_at"
                    )).all()
                    orders = connection.execute(text(
                        "SELECT status FROM broker_orders ORDER BY updated_at"
                    )).scalars().all()
                    reports = connection.execute(text(
                        "SELECT status,reason_code FROM execution_reports ORDER BY received_at"
                    )).all()
                    reconciliation = connection.execute(text(
                        "SELECT ready,reason_code FROM paper_reconciliation_receipts "
                        "ORDER BY observed_at DESC LIMIT 5"
                    )).all()
                    safety = connection.execute(text(
                        "SELECT safety_state,reconciliation_current,"
                        "valid_until>clock_timestamp() AS fresh FROM risk_account_context"
                    )).all()
                print(json.dumps({"failures": sorted(set(failures)),
                                  "provider": provider.evidence(),
                                  "operations": [list(row) for row in operations],
                                  "orders": orders,
                                  "reports": [list(row) for row in reports],
                                  "reconciliation": [list(row) for row in reconciliation],
                                  "safety": [list(row) for row in safety]},
                                 default=str), flush=True)
                return
            if instruction != "verify":
                return
            STAGE = "FINAL_SQL"
            assert not failures
            assert normal_finances() == original_finances and demo.list_runs() == original_runs
            assert not external
            submit_crash = os.environ.get("PAPER_BROWSER_SUBMIT_CRASH") == "1"
            expected_crashes = int(os.environ.get("PAPER_BROWSER_CRASH", "0")) + int(submit_crash)
            assert execution_events["crashes"] == expected_crashes
            assert execution_events["restarts"] == expected_crashes
            if expected_crashes:
                assert len(crashed_fences) == 1
                assert scalar("SELECT fencing_token FROM execution_owners "
                              "WHERE account_id='paper'") > crashed_fences[0]
            if submit_crash:
                assert scalar("SELECT count(*) FROM broker_orders WHERE status='FILLED'") == 1
                assert scalar("SELECT count(*) FROM broker_orders") == 1
                assert scalar("SELECT count(*) FROM paper_human_requests") == 1
                assert scalar("SELECT count(*) FROM paper_commands WHERE "
                              "operation_type='PLACE_LIMIT_ORDER' AND status='SUCCEEDED' "
                              "AND (payload->>'expiresAt')::timestamptz<clock_timestamp()") == 1
                assert scalar("SELECT count(*) FROM risk_reservations WHERE "
                              "state IN ('ACTIVE','HELD_UNKNOWN')") == 0
                assert scalar("SELECT operating_state FROM paper_workflow_state") == "HALTED"
                facts = provider.evidence()
                assert facts["posts"] == facts["fills"] == 1 and facts["deletes"] == 0
                assert facts["cash"] == "99985"
                print("PASS SQL: actual submit SIGKILL, lookup recovery and financial fill",
                      flush=True)
                return
            if model:
                assert scalar("SELECT count(*) FROM broker_orders WHERE status='FILLED'") == 1
                assert scalar("SELECT count(*) FROM broker_orders") == 1
                assert scalar("SELECT count(*) FROM paper_human_requests") == 0
                assert scalar("SELECT count(*) FROM trade_proposals WHERE decision_id "
                              "IS NOT NULL") >= 1
                assert scalar("SELECT count(*) FROM agent_runs WHERE status='DONE'") >= 1
                assert scalar("SELECT count(*) FROM risk_reservations WHERE "
                              "state IN ('ACTIVE','HELD_UNKNOWN')") == 0
                assert scalar("SELECT operating_state FROM paper_workflow_state") == "HALTED"
                facts = provider.evidence()
                assert facts["posts"] == facts["fills"] == 1 and facts["deletes"] == 0
                print("PASS SQL: actual Worker MODEL provenance, Risk and financial fill",
                      flush=True)
                return
            if os.environ.get("PAPER_BROWSER_DAY_BOUNDARY") == "1":
                from packages.risk.paper_daily import assess_daily_loss
                from packages.risk.paper_daily_sql import read_daily

                assert scalar("SELECT count(*) FROM paper_daily_risk_evidence WHERE "
                              "reference_kind='FIRST_DAY' AND expires_at<=clock_timestamp()") == 1
                assert scalar("SELECT count(*) FROM paper_daily_risk_evidence") == 1
                with roles["execution"].connect() as connection:
                    reference, observation = read_daily(connection, "paper", 1)
                assert assess_daily_loss(reference, observation, now=clock.now(),
                                         max_loss_fraction=Decimal("0.01")).reasons == (
                                             "PAPER_DAILY_EVIDENCE_UNPROVEN",)
                assert scalar("SELECT count(*) FROM broker_orders") == 0
                assert provider.evidence()["posts"] == provider.evidence()["deletes"] == 0
                print("PASS SQL: actual UTC boundary expires immutable FIRST_DAY authority",
                      flush=True)
                return
            facts = provider.evidence()
            assert facts["posts"] == 3 and facts["fills"] == 2 and facts["deletes"] == 1
            assert facts["cash"] == "99999.99"
            assert scalar("SELECT count(*) FROM broker_orders") == 3
            assert scalar("SELECT count(*) FROM broker_orders WHERE status='FILLED'") == 2
            assert scalar("SELECT count(*) FROM broker_orders WHERE status='CANCELED'") == 1
            assert scalar("SELECT count(*) FROM paper_human_requests") == 3
            assert scalar("SELECT count(*) FROM risk_reservations WHERE "
                          "state IN ('ACTIVE','HELD_UNKNOWN')") == 0
            assert scalar("SELECT operating_state FROM paper_workflow_state") == "HALTED"
            print("PASS SQL: real browser Paper lifecycle, financial recovery and isolation",
                  flush=True)
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=5)
            child = execution_process[0]
            if child is not None and child.poll() is None:
                child.stdin.write("stop\n")
                child.stdin.flush()
                child.wait(timeout=20)
            demo.close()
            for server, _thread in reversed(servers):
                server.should_exit = True
            for _server, thread in reversed(servers):
                thread.join(timeout=3)
            for value in engines:
                value.dispose()


def run_guest():
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        frame = frames[-1]
        print(f"PAPER_BROWSER_FAIL:{STAGE}:{type(error).__name__}:"
              f"{Path(frame.filename).name}:{frame.lineno}", flush=True)
        raise SystemExit(1) from None
