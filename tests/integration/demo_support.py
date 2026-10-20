"""Offline PostgreSQL harness; only UUID test resources are created/deleted."""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import uuid
from pathlib import Path


def host():
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    name = "atl_demo_test_" + uuid.uuid4().hex[:20]
    password = secrets.token_urlsafe(32)
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
    created = False
    try:
        _admin(
            "\n".join(
                f"GRANT {group} TO {name} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;"
                for group in GROUPS
            )
        )
        _admin(f"CREATE DATABASE {name} OWNER {name};")
        created = True
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
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
            (root / "packages", "/opt/lab/packages"),
            (root / "services", "/opt/lab/services"),
            (Path(__file__).resolve(), "/opt/lab/test_guest.py"),
        ):
            command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
        command.extend(("agentic-trading-lab-dev-build-smoke:latest", "test_guest.py", "guest"))
        result = subprocess.run(
            command,
            input=json.dumps({"database": name, "username": name, "password": password}),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.replace(password, "[redacted]").splitlines()[-12:]
            raise RuntimeError("isolated Demo verification failed: " + " | ".join(detail))
        return result.stdout
    finally:
        # Only databases owned by this generated test role can be cleaned up.
        databases = _admin(
            "SELECT datname FROM pg_database WHERE datdba="
            f"(SELECT oid FROM pg_roles WHERE rolname='{name}');"
        )
        for candidate in databases.splitlines():
            candidate = candidate.strip()
            if re.fullmatch(r"lab_demo_[0-9a-f]{32}", candidate):
                _admin(f'DROP DATABASE "{candidate}" WITH (FORCE);')
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE);")
        _admin(f"DROP ROLE {name};")


def guest():
    # Fail-closed production responses hide implementation diagnostics. This
    # synthetic-only harness records exception type and source locations.
    import traceback
    from decimal import Decimal

    from sqlalchemy import URL, create_engine, text

    import services.demo.coordinator as module
    from services.demo.broker import SimulatedCrash
    from services.demo.coordinator import DemoCoordinator, DemoError
    from services.demo.storage import financial_engine

    for function in ("provision_run", "execute_run"):
        original = getattr(module, function)

        def checked(*args, _original=original, **kwargs):
            try:
                return _original(*args, **kwargs)
            except Exception as error:
                frames = traceback.extract_tb(error.__traceback__)
                print(
                    type(error).__name__
                    + ":"
                    + ";".join(
                        f"{Path(item.filename).name}:{item.name}:{item.lineno}"
                        for item in frames[-4:]
                    ),
                    file=sys.stderr,
                )
                raise

        setattr(module, function, checked)

    credentials = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            host="postgres",
            port=5432,
            database=credentials["database"],
            username=credentials["username"],
            password=credentials["password"],
        ),
        hide_parameters=True,
    )
    coordinator = DemoCoordinator(engine, asynchronous=False)
    runs = []
    for index, (budget, strategy) in enumerate(
        (("100", "mock-neutral"), ("200", "neutral-baseline"))
    ):
        payload = {"initialBudget": budget, "currency": "USD", "strategy": strategy}
        status, run, _headers = coordinator.start(
            payload, f"start-{index}", "demo-test", uuid.uuid4().hex
        )
        assert status == 202 and run["status"] == "SUCCEEDED", run
        run_id = run["runId"]
        runs.append(run_id)
        retried = coordinator.start(payload, f"start-{index}", "demo-test", uuid.uuid4().hex)[1]
        assert retried["runId"] == run_id
        snapshot = coordinator.snapshot(run_id, uuid.uuid4().hex)
        assert len(snapshot["recentOrders"]) == 1, snapshot
        order = snapshot["recentOrders"][0]
        assert order["status"] == "FILLED" and Decimal(order["filledQuantity"]) == 1
        assert order["traceId"] == run["traceId"]
        assert snapshot["mode"]["pauseNew"] is True
        assert Decimal(snapshot["portfolioCash"]) == Decimal(budget) - 5
        assert any(
            item["instrument"] == "SPY" and Decimal(item["quantity"]) == 1
            for item in snapshot["positions"]
        )
        for resource in (
            "market-states",
            "decisions",
            "proposals",
            "risk-decisions",
            "orders",
            "audit",
        ):
            items = coordinator.history(run_id, resource, trace_id=run["traceId"])["items"]
            assert items, resource
        owner = financial_engine(engine, run_id)
        with owner.connect() as c:
            assert not c.execute(
                text(
                    "SELECT has_database_privilege('atl_control_api',current_database(),'CONNECT')"
                )
            ).scalar_one()
            assert (
                c.execute(
                    text("SELECT quantity FROM positions WHERE instrument_id='SPY'")
                ).scalar_one()
                == 1
            )
            assert (
                c.execute(text("SELECT sum(reserved_notional) FROM risk_reservations")).scalar_one()
                == 0
            )
            cash = c.execute(
                text("""SELECT cash FROM portfolio_snapshots
              WHERE id=(SELECT portfolio_snapshot_id FROM risk_account_context)""")
            ).scalar_one()
            assert cash == Decimal(budget) - 5, cash
            assert (
                c.execute(
                    text("SELECT count(*) FROM demo_transport_journal WHERE kind='accept'")
                ).scalar_one()
                == 1
            )
            assert (
                c.execute(
                    text("SELECT count(*) FROM execution_reports WHERE filled_quantity>0")
                ).scalar_one()
                == 2
            )
        owner.dispose()
    assert runs[0] != runs[1]
    # A changed request cannot reuse the same durable idempotency key.
    try:
        coordinator.start(
            {"initialBudget": "999", "currency": "USD", "strategy": "mock-neutral"},
            "start-0",
            "demo-test",
            uuid.uuid4().hex,
        )
    except DemoError as error:
        assert error.code == "IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("changed retry admitted")
    coordinator.control(runs[0], "kill", "demo-test", uuid.uuid4().hex)
    assert coordinator.snapshot(runs[0], uuid.uuid4().hex)["mode"]["killSwitch"] is True
    # Previous user kill stays latched while an independent new run can start.
    third = coordinator.start(
        {"initialBudget": "150", "currency": "USD", "strategy": "mock-neutral"},
        "third",
        "demo-test",
        uuid.uuid4().hex,
    )[1]
    assert third["status"] == "SUCCEEDED"
    assert coordinator.snapshot(runs[0], uuid.uuid4().hex)["mode"]["killSwitch"] is True
    # Crash after acceptance: replay durable Fake evidence and retain reservation.
    coordinator.fail_after_accept = True
    try:
        coordinator.start(
            {"initialBudget": "100", "currency": "USD", "strategy": "mock-neutral"},
            "crash",
            "demo-test",
            uuid.uuid4().hex,
        )
    except SimulatedCrash:
        pass
    else:
        raise AssertionError("acceptance crash failpoint did not interrupt execution")
    crash = coordinator.list_runs()["items"][0]
    assert crash["status"] == "RUNNING"
    crash_id = crash["runId"]
    # Startup, rather than the interrupted worker, must reconstruct the journal
    # and reconcile with a fresh fencing owner without a second submission.
    restarted = DemoCoordinator(engine, asynchronous=False)
    assert restarted.get_run(crash_id)["status"] == "PAUSED"
    owner = financial_engine(engine, crash_id)
    with owner.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM demo_transport_journal WHERE kind='accept'")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(text("SELECT sum(reserved_notional) FROM risk_reservations")).scalar_one() > 0
        )
        assert (
            c.execute(text("SELECT safety_state FROM risk_account_context")).scalar_one()
            == "PAUSE_NEW"
        )
        assert c.execute(text("SELECT count(*) FROM broker_orders")).scalar_one() == 1
        assert c.execute(text("SELECT status FROM broker_orders")).scalar_one() == "ACKNOWLEDGED"
    owner.dispose()
    # Simultaneous browser retries record exactly one bounded start request.
    from concurrent.futures import ThreadPoolExecutor

    restarted.asynchronous = True
    submit_future = restarted.executor.submit
    restarted.executor.submit = lambda _function, _run_id: None
    payload = {"initialBudget": "100", "currency": "USD", "strategy": "mock-neutral"}

    def concurrent_start(_index):
        return restarted.start(payload, "concurrent", "demo-test", uuid.uuid4().hex)[1]

    with ThreadPoolExecutor(max_workers=2) as pool:
        double = list(pool.map(concurrent_start, range(2)))
    assert double[0]["runId"] == double[1]["runId"]
    try:
        restarted.start(payload, "second-active", "demo-test", uuid.uuid4().hex)
    except DemoError as error:
        assert error.code == "VERSION_CONFLICT"
    else:
        raise AssertionError("second concurrent active run admitted")
    restarted._finish(double[0]["runId"], "PAUSED")
    for action in ("pause", "kill"):
        pending = restarted.start(payload, "preprovision-" + action, "demo-test", uuid.uuid4().hex)[
            1
        ]
        response = restarted.control(pending["runId"], action, "demo-test", uuid.uuid4().hex)
        assert response["status"] == "PAUSED"
        restarted._execute(pending["runId"])
        from services.demo.storage import database_name

        with engine.connect() as c:
            assert not c.execute(
                text("SELECT EXISTS(SELECT FROM pg_database WHERE datname=:name)"),
                {"name": database_name(pending["runId"])},
            ).scalar_one()
            assert (
                c.execute(
                    text("SELECT control_action FROM demo_runs WHERE run_id=:id"),
                    {"id": pending["runId"]},
                ).scalar_one()
                == action
            )
        if action == "kill":
            restarted.control(pending["runId"], "pause", "demo-test", uuid.uuid4().hex)
            with engine.connect() as c:
                assert (
                    c.execute(
                        text("SELECT control_action FROM demo_runs WHERE run_id=:id"),
                        {"id": pending["runId"]},
                    ).scalar_one()
                    == "kill"
                )
    restarted.executor.submit = submit_future
    restarted.asynchronous = False
    # New coordinator reads persisted results; no rerun after restart.
    assert restarted.get_run(runs[1])["status"] == "SUCCEEDED"
    assert len(restarted.snapshot(runs[1], uuid.uuid4().hex)["recentOrders"]) == 1
    assert restarted.get_run(crash_id)["status"] == "PAUSED"
    # A tiny budget is rejected by actual deterministic risk, with no broker acceptance.
    tiny = restarted.start(
        {"initialBudget": "0.01", "currency": "USD", "strategy": "neutral-baseline"},
        "tiny",
        "demo-test",
        uuid.uuid4().hex,
    )[1]
    assert tiny["status"] == "FAILED" and tiny["errorCode"] == "RISK_LIMIT_EXCEEDED", tiny
    owner = financial_engine(engine, tiny["runId"])
    with owner.connect() as c:
        assert c.execute(text("SELECT count(*) FROM demo_transport_journal")).scalar_one() == 0
    owner.dispose()
    # Independently latch while a valid intent exists, just before actual submit.
    # The existing Execution gate must prevent Fake acceptance, and the worker
    # must not overwrite the user's PAUSED metadata or clear its financial latch.
    from services.execution.intents import ExecutionProcess

    original_submit = ExecutionProcess.submit
    for action in ("pause", "kill"):

        def latch_before_submit(process, intent_id, _action=action):
            active_id = restarted.list_runs()["items"][0]["runId"]
            restarted.control(active_id, _action, "demo-test", uuid.uuid4().hex)
            return original_submit(process, intent_id)

        ExecutionProcess.submit = latch_before_submit
        try:
            interrupted = restarted.start(
                {"initialBudget": "100", "currency": "USD", "strategy": "mock-neutral"},
                "before-submit-" + action,
                "demo-test",
                uuid.uuid4().hex,
            )[1]
        finally:
            ExecutionProcess.submit = original_submit
        assert interrupted["status"] == "PAUSED"
        owner = financial_engine(engine, interrupted["runId"])
        with owner.connect() as c:
            assert c.execute(text("SELECT count(*) FROM demo_transport_journal")).scalar_one() == 0
            latch = c.execute(text("SELECT safety_state FROM risk_account_context")).scalar_one()
            assert latch == ("HALTED" if action == "kill" else "PAUSE_NEW")
        owner.dispose()
    # A committed control request also stops synthetic fill generation while
    # retaining the already accepted order and its risk reservation.
    import services.demo.pipeline as pipeline

    actual_broker_factory = pipeline.journaled_broker

    def pause_before_fill(*args, **kwargs):
        broker = actual_broker_factory(*args, **kwargs)
        actual_fill = broker.fill

        def interrupted_fill(*fill_args, **fill_kwargs):
            active_id = restarted.list_runs()["items"][0]["runId"]
            restarted.control(active_id, "pause", "demo-test", uuid.uuid4().hex)
            return actual_fill(*fill_args, **fill_kwargs)

        broker.fill = interrupted_fill
        return broker

    pipeline.journaled_broker = pause_before_fill
    try:
        interrupted = restarted.start(
            {"initialBudget": "100", "currency": "USD", "strategy": "mock-neutral"},
            "before-fill",
            "demo-test",
            uuid.uuid4().hex,
        )[1]
    finally:
        pipeline.journaled_broker = actual_broker_factory
    assert interrupted["status"] == "PAUSED"
    owner = financial_engine(engine, interrupted["runId"])
    with owner.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM demo_transport_journal WHERE kind='accept'")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM demo_transport_journal WHERE kind='fill'")
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(text("SELECT quantity FROM positions WHERE instrument_id='SPY'")).scalar_one()
            == 0
        )
        assert (
            c.execute(text("SELECT sum(reserved_notional) FROM risk_reservations")).scalar_one() > 0
        )
    owner.dispose()
    restarted.close()
    coordinator.close()
    print(
        "PASS isolated Demo: two budgets/strategies, full fills, trace, reload, retries, "
        "kill, acceptance crash, tiny risk"
    )


if __name__ == "__main__":
    guest() if len(sys.argv) == 2 and sys.argv[1] == "guest" else print(host())
