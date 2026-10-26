"""Actual BacktestWorker CPU load leaves fixed-role Paper controls operational."""

from pathlib import Path

MARKER = "PASS: active isolated Backtest simulation preserves Paper pause kill reconciliation"
PHASE = "BOOT"


def test_actual_backtest_load_preserves_paper_safety():
    from tests.integration.test_paper_provision import host

    root = Path(__file__).resolve().parents[2]
    assert (
        host(
            Path(__file__),
            MARKER,
            support_paths=(
                root / "tests/integration/test_paper_lifecycle.py",
                root / "tests/integration/backtest_browser_support.py",
            ),
        )
        == MARKER
    )


def guest():
    global PHASE
    import hashlib
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from backtest_browser_support import seed_backtest_fixture
    from sqlalchemy import URL, create_engine, event, text
    from test_paper_lifecycle import PaperFixture

    from packages.backtesting.jobs import BacktestJobs
    from packages.backtesting.request import parse_request
    from services.worker import backtesting

    fixture = PaperFixture()
    engines = []
    release, entered = threading.Event(), threading.Event()
    try:
        PHASE = "PAPER_PREPARE"
        fixture.prepare()
        fixture.profile()
        fixture.refresh()
        admin = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="lab_migration",
                host="postgres",
                database=fixture.database,
            ),
            hide_parameters=True,
        )
        engines.append(admin)
        seed = seed_backtest_fixture(admin)
        with admin.begin() as conn:
            conn.execute(text("CREATE ROLE atl_backtest_worker_login LOGIN NOINHERIT"))
            conn.execute(text("GRANT atl_backtest_worker TO atl_backtest_worker_login"))
            conn.execute(
                text("GRANT CONNECT ON DATABASE lab_paper_runtime TO atl_backtest_worker_login")
            )
        worker = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="atl_backtest_worker_login",
                host="postgres",
                database=fixture.database,
            ),
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            hide_parameters=True,
        )
        engines.append(worker)

        @event.listens_for(worker, "connect")
        def restrict(dbapi, _record):
            dbapi.execute("SET ROLE atl_backtest_worker")
            dbapi.commit()

        from datetime import UTC, datetime, timedelta

        from packages.domain.models import utc_string
        from services.control_api.backtest_service import stamp

        start = stamp(seed["start"])
        request = dict(
            instrumentId=seed["instrument"],
            strategyId="buy-hold-v1",
            version="1",
            parameters={},
            startCash="10000",
            feeUsd="0",
            slippageBps="0",
            quantityStep="0.000001",
            folds=[
                dict(
                    trainingStart=utc_string(start - timedelta(days=1)),
                    trainingEnd=seed["start"],
                    testStart=seed["start"],
                    testEnd=seed["end"],
                )
            ],
        )
        parse_request(request, datetime.now(UTC))
        admission = BacktestJobs(fixture.engines["api"])
        for index in range(3):
            admission.enqueue(
                request, "local-operator", f"backtest-safety-load-{index:04}", "c" * 32, "d" * 40
            )
        original = backtesting.evaluate
        counter = 0

        def loaded_evaluate(config, datasets, *, checkpoint, on_fold):
            def actual_step_checkpoint():
                nonlocal counter
                checkpoint()
                counter += 1
                if counter != 2:
                    return
                # This is called inside the actual simulator's first bar. Keep
                # that CPU task active while Paper uses independent SQL pools.
                entered.set()
                deadline = time.monotonic() + 8
                payload = b"explicit-synthetic-backtest-cpu-load" * 256
                while not release.is_set() and time.monotonic() < deadline:
                    hashlib.sha256(payload).digest()
                if not release.is_set():
                    raise AssertionError("Paper controls did not finish during CPU load")

            return original(config, datasets, checkpoint=actual_step_checkpoint, on_fold=on_fold)

        PHASE = "ACTIVE_SIMULATION"
        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(backtesting, "evaluate", loaded_evaluate),
        ):
            dispatcher = backtesting.BacktestWorker(worker, enabled=True, code_version="d" * 40)
            original_publish = dispatcher.jobs.publish
            failure_location = []

            def observe_publication(*args, **kwargs):
                import sys
                import traceback

                current = sys.exc_info()[2]
                if current:
                    frame = traceback.extract_tb(current)[-1]
                    failure_location.append((Path(frame.filename).name, frame.lineno))
                status = args[2] if len(args) > 2 else kwargs.get("status", "SUCCEEDED")
                code = args[3] if len(args) > 3 else kwargs.get("error_code")
                if code:
                    failure_location.append((status + "_" + code, 0))
                return original_publish(*args, **kwargs)

            dispatcher.jobs.publish = observe_publication
            task = pool.submit(dispatcher.tick)
            if not entered.wait(8):
                if task.done():
                    task.result()
                PHASE = "BEFORE_SIMULATOR"
                if failure_location:
                    name, line = failure_location[-1]
                    PHASE = name if line == 0 else PHASE + ":" + name + ":" + str(line)
                raise AssertionError("actual simulator did not reach a bar")
            assert worker.pool.checkedout() == 0, "CPU simulation must not hold an SQL transaction"
            with admin.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM backtest.runs WHERE status='RUNNING'")
                    ).scalar_one()
                    == 1
                )
                assert (
                    conn.execute(
                        text("SELECT count(*) FROM backtest.runs WHERE status='ACCEPTED'")
                    ).scalar_one()
                    == 2
                )
            PHASE = "PAPER_SAFETY_UNDER_LOAD"
            started = time.monotonic()
            pause = pool.submit(
                fixture.api.enqueue,
                "PAUSE",
                {"action": "PAUSE"},
                "operator",
                "backtest-safety-pause-001",
                fixture.trace,
            )
            kill = pool.submit(
                fixture.api.enqueue,
                "KILL",
                {"action": "KILL"},
                "operator",
                "backtest-safety-kill-0001",
                fixture.trace,
            )
            refresh = pool.submit(fixture.refresh, publish=False)
            assert pause.result(timeout=5)["status"] == "ACCEPTED"
            assert kill.result(timeout=5)["status"] == "ACCEPTED"
            refresh.result(timeout=5)
            assert time.monotonic() - started < 5
            status = fixture.api.read_status(fixture.trace)
            assert status["operatingState"] in {"PAUSED", "HALTED"}
            assert fixture.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            assert not task.done(), "Paper controls must complete while simulation is still active"
            release.set()
            PHASE = "BOUNDED_SIMULATION_COMPLETION"
            assert task.result(timeout=8)
        with admin.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM backtest.runs WHERE status='SUCCEEDED'")
                ).scalar_one()
                == 1
            )
        assert all(method == "GET" for method, _path in fixture.http_calls), "no broker orders"
        print(MARKER)
    finally:
        release.set()
        for engine in (*engines, *fixture.engines.values()):
            engine.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "APW02_FAIL:BACKTEST_SAFETY:"
            + type(error).__name__
            + ":"
            + PHASE
            + ":"
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
