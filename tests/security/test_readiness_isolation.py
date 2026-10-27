"""RDR-08: echte Diagnose im begrenzten internen Workercontainer, keine Provider."""

import sys
from pathlib import Path

MARKER = "PASS readiness actual container/shared-worker/CPU/memory/mounts/internal-network/SQL-role"


def test_actual_readiness_worker_container_isolation():
    from tests.security.test_backtest_isolation import isolated_worker_host

    assert isolated_worker_host("tests.security.test_readiness_isolation", MARKER) == MARKER


def seed():
    import io
    from contextlib import redirect_stdout

    from sqlalchemy import URL, create_engine, text

    from packages.backtesting.readiness_jobs import ReadinessJobs
    from tests.security.test_backtest_isolation import seed as seed_backtest

    # Dieselbe explizite synthetische Vergangenheit wie die vorhandene BKT-Abnahme.
    # Nur der temporäre Migrationseigentümer kann dieses Testfixture anlegen.
    with redirect_stdout(io.StringIO()):
        seed_backtest()
    engine = create_engine(
        URL.create("postgresql+psycopg", username="lab_migration", host="postgres", database="lab"),
        hide_parameters=True,
    )
    with engine.connect() as connection:
        request = connection.execute(text("SELECT request FROM backtest.runs LIMIT 1")).scalar_one()
    ReadinessJobs(engine).enqueue(
        request,
        "local-operator",
        "readiness-container-isolation-0001",
        "a" * 32,
        "b" * 40,
    )
    engine.dispose()
    print("SEEDED")


def probe():
    import io
    import os
    from contextlib import redirect_stdout
    from unittest.mock import patch

    from sqlalchemy import URL, create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from services.worker.backtesting import BacktestWorker
    from tests.security.test_backtest_isolation import probe as probe_backtest

    # Der ältere Backtest und die neuere Diagnose müssen denselben begrenzten Worker benutzen.
    with redirect_stdout(io.StringIO()):
        probe_backtest()
    assert os.getuid() == 10001
    assert not Path("/run/provider").exists()
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="atl_backtest_worker_login",
            host="postgres",
            database="lab",
        ),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )

    @event.listens_for(engine, "connect")
    def restrict(dbapi, _):
        dbapi.execute("SET ROLE atl_backtest_worker")
        dbapi.commit()

    with patch("httpx.Client.send", side_effect=AssertionError("diagnosis HTTP forbidden")) as sync:
        with patch(
            "httpx.AsyncClient.send", side_effect=AssertionError("diagnosis HTTP forbidden")
        ) as asynchronous:
            assert BacktestWorker(engine, enabled=True, code_version="b" * 40).tick()
            assert sync.call_count == asynchronous.call_count == 0
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.readiness_jobs WHERE status='SUCCEEDED'")
            ).scalar_one()
            == 1
        )
        report = connection.execute(
            text("SELECT body FROM backtest.readiness_reports")
        ).scalar_one()
        assert report["eligibility"] == "ELIGIBLE"
        assert report["evaluationComplete"] and report["windows"]["complete"]
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.runs WHERE status='SUCCEEDED'")
            ).scalar_one()
            == 1
        )
        assert not connection.execute(
            text(
                "SELECT has_function_privilege(current_user,"
                "'research.append_evidence_bundle(uuid,text,text,jsonb,bigint)','EXECUTE')"
            )
        ).scalar_one()
    for statement in (
        "INSERT INTO research.import_rejections(rejection_id,code,recorded_at) "
        "VALUES('77777777-7777-4777-8777-777777777777','FORBIDDEN',clock_timestamp())",
        "SELECT count(*) FROM public.broker_orders",
    ):
        try:
            with engine.begin() as connection:
                connection.execute(text(statement))
        except DBAPIError:
            pass
        else:
            raise AssertionError("diagnosis SQL role escaped its boundary")
    engine.dispose()
    print(MARKER)


if __name__ == "__main__":
    try:
        {"seed": seed, "probe": probe}[sys.argv[1]]()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "FAIL backtest isolation RDR08 "
            + type(error).__name__
            + " "
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
