"""Isolated full-size Quant workload under the production worker limits."""

from contextlib import ExitStack, contextmanager
from decimal import Decimal
from pathlib import Path

MARKER = "PASS quant constrained worker with mixed admission and immutable ledger"
VERSION = "b" * 40
LIMIT = 256 * 1024 * 1024
PAPER_DATABASE = "lab_paper_runtime"


def _engine(role, database=PAPER_DATABASE):
    from sqlalchemy import URL, create_engine, event

    login = "atl_backtest_worker_login" if role == "atl_backtest_worker" else "lab_migration"
    engine = create_engine(
        URL.create("postgresql+psycopg", username=login, host="postgres", database=database),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 2},
    )
    if role != login:

        @event.listens_for(engine, "connect")
        def restrict(dbapi, _record):
            dbapi.execute("SET ROLE " + role)
            dbapi.commit()

    return engine


def _saturate_queue(admin, control, batch_id):
    from sqlalchemy import text

    from packages.backtesting.experiment_storage import ExperimentStorage
    from packages.backtesting.jobs import BacktestError, BacktestJobs
    from tests.integration.test_backtest_jobs import valid_request

    with admin.connect() as conn:
        bound = (
            conn.execute(
                text("""SELECT d.definition_id,d.definition_version,
                   d.fingerprint AS definition_hash,
                   v.validation_id,v.fingerprint AS validation_hash
              FROM backtest.experiment_batches b
              JOIN backtest.experiment_definitions d USING(definition_id)
              JOIN backtest.experiment_validations v ON v.validation_id=b.validation_id
              WHERE b.batch_id=:id"""),
                {"id": batch_id},
            )
            .mappings()
            .one()
        )
    store = ExperimentStorage(control)

    def submit(index):
        return store.enqueue(
            bound["definition_id"],
            bound["definition_hash"],
            "local-operator",
            f"qsl09-queued-quant-{index:04}",
            "a" * 32,
            VERSION,
            definition_version=bound["definition_version"],
            validation_id=bound["validation_id"],
            validation_hash=bound["validation_hash"],
        )

    for index in range(8):
        submit(index)
    legacy = BacktestJobs(control)
    request = valid_request()
    legacy.enqueue(request, "local-operator", "qsl09-legacy-queue-0001", "a" * 32, VERSION)
    with admin.connect() as conn:
        assert conn.execute(text("SELECT backtest.active_slots()")).scalar_one() == 100
    for call, code in (
        (lambda: submit(8), "QUEUE_EXHAUSTED"),
        (
            lambda: legacy.enqueue(
                request, "local-operator", "qsl09-legacy-queue-0002", "a" * 32, VERSION
            ),
            "UNAVAILABLE",
        ),
    ):
        try:
            call()
        except BacktestError as error:
            assert error.code == code
        else:
            raise AssertionError("queue admitted work above 100 slots")


def _ledger_state(conn):
    from sqlalchemy import text

    from tests.integration.test_readiness_acceptance import (
        SAFETY_STABLE_TABLES,
        financial_fingerprints,
    )

    assert conn.execute(text("SELECT count(*) FROM public.positions")).scalar_one()
    cash, equity = conn.execute(
        text("""SELECT trim_scale(p.cash)::text,trim_scale(p.equity)::text
      FROM public.risk_account_context c JOIN public.portfolio_snapshots p
        ON p.id=c.portfolio_snapshot_id WHERE c.account_id='paper'""")
    ).one()
    return dict(
        fingerprints=dict(financial_fingerprints(conn, SAFETY_STABLE_TABLES)),
        cash=cash,
        equity=equity,
    )


def _seed(admin, control):
    import json

    from sqlalchemy import text

    from services.control_api.quant_lab_service import QuantLabService
    from tests.integration.test_quant_lab_acceptance import seed_workflow

    with admin.begin() as conn:
        conn.execute(text("CREATE ROLE atl_backtest_worker_login LOGIN NOINHERIT"))
        conn.execute(text("GRANT atl_backtest_worker TO atl_backtest_worker_login"))
        conn.execute(
            text("GRANT CONNECT ON DATABASE lab_paper_runtime TO atl_backtest_worker_login")
        )
        baseline = _ledger_state(conn)
        conn.execute(
            text(
                "CREATE TABLE public.qsl09_isolation_fixture "
                "(baseline jsonb NOT NULL, batch_id uuid NOT NULL)"
            )
        )
    batch = seed_workflow(admin, QuantLabService(control, code_version=VERSION))
    with admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.qsl09_isolation_fixture(baseline,batch_id) "
                "VALUES(CAST(:body AS jsonb),:id)"
            ),
            {"body": json.dumps(baseline), "id": batch["id"]},
        )
    _saturate_queue(admin, control, batch["id"])
    return baseline


def _prove_denied(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    for sql in (
        "SELECT * FROM public.broker_orders",
        "SELECT * FROM public.portfolio_snapshots",
        "SELECT * FROM backtest.experiment_validation_requests",
        "DELETE FROM backtest.corporate_action_coverage",
        "SET ROLE atl_paper_execution",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501"
        else:
            raise AssertionError("worker escaped role isolation")
    for role, database in (("lab_migration", PAPER_DATABASE), ("atl_backtest_worker", "lab")):
        forbidden = _engine(role, database)
        try:
            with forbidden.connect():
                raise AssertionError("worker network reached an unauthorized identity or database")
        except DBAPIError as error:
            assert "pg_hba.conf rejects connection" in str(error.orig)
        finally:
            forbidden.dispose()


@contextmanager
def _research_load(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from uuid import uuid4

    from sqlalchemy import text

    from packages.agent_research.registry import QUANT_RESEARCH_V1, ToolId
    from packages.agent_research.run_state import RunState
    from packages.agent_research.runs import RunKind, RunStore
    from packages.agent_research.tools import ToolCall, ToolJournal

    engine = _engine("atl_agent_research", database)
    runs = RunStore(engine)
    journal = ToolJournal(engine)
    active, release = Event(), Event()
    try:
        run = runs.enqueue(
            actor="local-operator", key="apf-isolation-research-load", kind=RunKind.DIALOGUE,
            input_ref="synthetic-isolation-input", trace_id="b" * 32, agent=QUANT_RESEARCH_V1,
        )

        def authorize(scope):
            assert scope.execute(text("""
                SELECT input_ref FROM agent_research.runs
                WHERE run_id=:run AND actor='local-operator'
            """), {"run": run}).scalar_one() == "synthetic-isolation-input"

        def operation(_scope):
            active.set()
            # Keep the real journal transaction open across the measured safety calls.
            assert release.wait(timeout=20), "Paper safety calls did not finish under Research load"
            return "synthetic-isolation-result"

        def work():
            claim = runs.claim(uuid4())
            assert claim is not None and claim.run_id == run
            call = ToolCall("isolation-read", ToolId.RESEARCH_READ, 1, "c" * 64)
            assert journal.execute(claim, call, authorize=authorize, operation=operation) == (
                "synthetic-isolation-result"
            )
            runs.transition(claim, RunState.COMPLETED)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(work)
            try:
                if not active.wait(timeout=5):
                    pending.result(timeout=5)
                    raise AssertionError("Research tool transaction did not start")
                yield pending
                assert not pending.done(), "Research load ended before the safety checks"
            finally:
                release.set()
            pending.result(timeout=5)
        assert runs.snapshot(run, actor="local-operator").state is RunState.COMPLETED
    finally:
        engine.dispose()


def safety():
    from time import monotonic

    import psycopg
    from sqlalchemy import text

    from services.execution.recovery_health import heartbeat_execution_owner
    from tests.integration.test_paper_lifecycle import PaperFixture

    fixture = PaperFixture()
    admin = _engine("lab_migration", fixture.database)
    try:
        fixture.initial_holding_value = fixture.current_holding_value = Decimal("900")
        fixture.prepare()
        fixture.profile(budget="10000")
        fixture.refresh(publish=False)
        baseline = _seed(admin, fixture.engines["api"])
        with psycopg.connect(
            dbname=fixture.database, autocommit=True, **fixture.common
        ) as listener, ExitStack() as loads:
            listener.execute("LISTEN qsl09_active_simulation")
            print("READY", flush=True)
            deadline = monotonic() + 295
            while monotonic() < deadline:
                if next(listener.notifies(timeout=3, stop_after=1), None):
                    break
                heartbeat_execution_owner(
                    ledger=fixture.ledger, account_id="paper", owner_id=fixture.owner,
                    fencing_token=fixture.fence,
                )
            else:
                raise AssertionError("Quant simulation did not begin")
            fixture.refresh()
            _, _, _, activation = fixture.start()
            assert set(activation) == {"activationId", "expiresAt"}
            assert fixture.api.read_status(fixture.trace)["entryGateOpen"] is True
            loads.enter_context(_research_load(fixture.database))

            def timed(call, *args, **kwargs):
                started = monotonic()
                result = call(*args, **kwargs)
                elapsed = monotonic() - started
                assert elapsed < 5
                return result, elapsed

            paused, pause_seconds = timed(
                fixture.api.enqueue,
                "PAUSE",
                {"action": "PAUSE"},
                "operator",
                "qsl09-paper-pause-0001",
                fixture.trace,
            )
            assert paused["status"] == "ACCEPTED"
            assert fixture.sql("execution", "SELECT safety_state FROM risk_account_context") == (
                "PAUSE_NEW"
            )
            _, reconcile_seconds = timed(fixture.refresh, publish=False)
            killed, kill_seconds = timed(
                fixture.api.enqueue,
                "KILL",
                {"action": "KILL"},
                "operator",
                "qsl09-paper-kill-0001",
                fixture.trace,
            )
            assert killed["status"] == "ACCEPTED"
            assert fixture.api.read_status(fixture.trace)["operatingState"] == "HALTED"
            assert fixture.sql("execution", "SELECT safety_state FROM risk_account_context") == (
                "HALTED"
            )
            assert fixture.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            assert listener.execute("""SELECT count(*) FROM backtest.experiment_batches
                WHERE status='RUNNING'""").fetchone() == (1,)
        with admin.connect() as conn:
            assert _ledger_state(conn) == baseline
            assert conn.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        assert fixture.receipt.ready and all(method == "GET" for method, _ in fixture.http_calls)
        print(
            f"PASS Paper safety Pause {pause_seconds:.3f}s, Kill {kill_seconds:.3f}s, "
            f"Reconciliation {reconcile_seconds:.3f}s with overlapping Quant and APF Research",
            flush=True,
        )
    finally:
        admin.dispose()
        for engine in fixture.engines.values():
            engine.dispose()


def probe():
    import os
    from time import monotonic
    from unittest.mock import patch

    from sqlalchemy import text

    from services.worker import quant_lab
    from services.worker.backtesting import BacktestWorker

    cgroup = Path("/sys/fs/cgroup")
    assert os.getuid() == 10001
    assert all(
        not Path(path).exists()
        for path in ("/run/secrets", "/run/provision", "/var/run/docker.sock")
    )
    assert not any(key.startswith(("ALPACA", "JEV", "BROKER")) for key in os.environ)
    assert int((cgroup / "memory.max").read_text()) == LIMIT
    quota, period = map(int, (cgroup / "cpu.max").read_text().split())
    assert quota * 2 == period

    def cpu_usecs():
        return int(
            dict(line.split() for line in (cgroup / "cpu.stat").read_text().splitlines())[
                "usage_usec"
            ]
        )

    engine = _engine("atl_backtest_worker")
    try:
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT current_user,current_database(),session_user")
            ).one() == ("atl_backtest_worker", PAPER_DATABASE, "atl_backtest_worker_login")
        _prove_denied(engine)
        original = quant_lab.simulate_quant
        observed = False

        def check_active(*args, checkpoint, **kwargs):
            def observe():
                nonlocal observed
                checkpoint()
                if observed:
                    return
                assert engine.pool.checkedout() == 0
                with engine.begin() as conn:
                    assert (
                        conn.execute(
                            text("""SELECT count(*) FROM backtest.experiment_batches
                      WHERE status='RUNNING'""")
                        ).scalar_one()
                        == 1
                    )
                    assert (
                        conn.execute(
                            text("""SELECT sum(slots) FROM backtest.experiment_batches
                      WHERE status IN ('RUNNING','ACCEPTED')""")
                        ).scalar_one()
                        == 99
                    )
                    assert (
                        conn.execute(
                            text("""SELECT count(*) FROM backtest.runs
                      WHERE quant_batch_id IS NULL AND status='ACCEPTED'""")
                        ).scalar_one()
                        == 1
                    )
                    conn.execute(text("NOTIFY qsl09_active_simulation"))
                observed = True

            return original(*args, checkpoint=observe, **kwargs)

        started, cpu = monotonic(), cpu_usecs()
        with (
            patch.object(quant_lab, "simulate_quant", check_active),
            patch(
                "httpx.Client.send", side_effect=AssertionError("external HTTP forbidden")
            ) as http,
        ):
            assert BacktestWorker(engine, enabled=True, code_version=VERSION).tick()
            assert http.call_count == 0
        elapsed = monotonic() - started
        cpu = (cpu_usecs() - cpu) / 1_000_000
        memory = int((cgroup / "memory.peak").read_text())
        with engine.connect() as conn:
            row = conn.execute(
                text("""SELECT status,error_code,bar_steps,publication_bytes
              FROM backtest.experiment_batches ORDER BY accepted_at,batch_id LIMIT 1""")
            ).one()
            if row.status != "SUCCEEDED":
                assert row.error_code and row.error_code.replace("_", "").isalnum()
                print(
                    f"FAIL backtest isolation terminal {row.status} {row.error_code} "
                    f"after {elapsed:.3f}s wall, {cpu:.3f}s CPU, {memory} peak bytes"
                )
                raise SystemExit(1)
            assert row.bar_steps == 9510 and 0 < row.publication_bytes <= 12 * 1024 * 1024
        assert observed and elapsed < 295 and 0 < memory <= LIMIT and cpu > 0
        print(MARKER)
        print(
            f"{row.bar_steps} steps; {row.publication_bytes} bytes; {elapsed:.3f}s wall; "
            f"{cpu:.3f}s CPU; {memory} bytes cgroup peak"
        )
    finally:
        engine.dispose()


def verify():
    from sqlalchemy import text

    admin = _engine("lab_migration")
    try:
        with admin.connect() as conn:
            baseline, batch_id = conn.execute(
                text("SELECT baseline,batch_id FROM public.qsl09_isolation_fixture")
            ).one()
            assert _ledger_state(conn) == baseline
            assert (
                conn.execute(
                    text("""SELECT count(*) FROM backtest.experiment_result_links
              WHERE batch_id=:id AND status='COMPLETE'"""),
                    {"id": batch_id},
                ).scalar_one()
                == 28
            )
            assert conn.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        print("VERIFIED")
    finally:
        admin.dispose()


def oom():
    from unittest.mock import patch

    from services.worker import quant_lab
    from services.worker.backtesting import BacktestWorker

    original = quant_lab.simulate_quant

    def exhaust_at_bar(*args, checkpoint, **kwargs):
        def exhaust():
            checkpoint()
            assert worker.pool.checkedout() == 0
            print("ACTIVE_QUANT_OOM", flush=True)
            allocations = []
            for _ in range(32):
                allocation = bytearray(16 * 1024 * 1024)
                for page in range(0, len(allocation), 4096):
                    allocation[page] = 1
                allocations.append(allocation)
            raise AssertionError("kernel did not enforce the memory budget")

        return original(*args, checkpoint=exhaust, **kwargs)

    worker = _engine("atl_backtest_worker")
    try:
        with patch.object(quant_lab, "simulate_quant", exhaust_at_bar):
            BacktestWorker(worker, enabled=True, code_version=VERSION).tick()
        raise AssertionError("expected container termination")
    finally:
        worker.dispose()


def expire(attempt):
    from sqlalchemy import text

    admin = _engine("lab_migration")
    try:
        with admin.begin() as conn:
            # Advance only the dead process's lease in the disposable test database.
            conn.execute(
                text("ALTER TABLE backtest.experiment_batches DISABLE TRIGGER a_quant_batch")
            )
            batch_id = conn.execute(
                text("""UPDATE backtest.experiment_batches
                SET lease_until=clock_timestamp()-interval '1 second'
                WHERE status='RUNNING' AND attempts=:attempt AND fence=:attempt
                  AND bar_steps=:steps
                RETURNING batch_id"""),
                {"attempt": int(attempt), "steps": int(attempt) * (49 + 130)},
            ).scalar_one()
            conn.execute(
                text("ALTER TABLE backtest.experiment_batches ENABLE TRIGGER a_quant_batch")
            )
            assert (
                conn.execute(
                    text("""SELECT count(*) FROM backtest.experiment_result_links
                WHERE batch_id=:id AND status='COMPLETE'"""),
                    {"id": batch_id},
                ).scalar_one()
                == 0
            )
        print("EXPIRED")
    finally:
        admin.dispose()


def settle():
    from uuid import uuid4

    from packages.backtesting.experiment_storage import ExperimentStorage

    worker = _engine("atl_backtest_worker")
    try:
        ExperimentStorage(worker).claim(uuid4())
        print("RESTARTED")
    finally:
        worker.dispose()


def verify_oom():
    from sqlalchemy import text

    admin = _engine("lab_migration")
    try:
        with admin.connect() as conn:
            batch_id = conn.execute(
                text("""SELECT batch_id FROM backtest.experiment_batches
                WHERE status='FAILED' AND attempts=3 AND fence=3 AND owner IS NULL
                  AND bar_steps=537
                  AND lease_until IS NULL AND error_code='ATTEMPTS_EXHAUSTED'""")
            ).scalar_one()
            assert (
                conn.execute(
                    text("""SELECT count(*) FROM backtest.experiment_result_links
                WHERE batch_id=:id AND status='COMPLETE'"""),
                    {"id": batch_id},
                ).scalar_one()
                == 0
            )
            assert conn.execute(text("SELECT backtest.active_slots()")).scalar_one() == 78
            baseline = conn.execute(text("SELECT baseline FROM public.qsl09_isolation_fixture"))
            assert _ledger_state(conn) == baseline.scalar_one()
        print("VERIFIED_OOM")
    finally:
        admin.dispose()


def test_actual_constrained_quant_worker_and_mixed_queue():
    from tests.security.test_backtest_isolation import isolated_worker_host

    result = isolated_worker_host(
        "tests.security.test_quant_lab_isolation",
        MARKER,
        worker_timeout=320,
        verify=True,
        safety=True,
        oom=True,
    )
    assert result.splitlines()[0] == MARKER
    print(result)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        {
            "probe": probe,
            "verify": verify,
            "safety": safety,
            "oom": oom,
            "expire": expire,
            "settle": settle,
            "verify_oom": verify_oom,
        }[sys.argv[1]](*sys.argv[2:])
    except Exception as error:
        last = traceback.extract_tb(error.__traceback__)[-1]
        reason = getattr(error, "reason_code", "")
        print(
            f"FAIL backtest isolation {type(error).__name__} {reason} "
            f"{Path(last.filename).name}:{last.lineno}"
        )
        raise SystemExit(1) from None
