"""RRB-08 actual constrained process: both modes, no trading or provider capabilities."""

import sys
from pathlib import Path

MARKER = "PASS retrospective actual container/CPU/memory/role/network/no-provider"


def seed():
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, text

    from packages.persistence.ledger import PostgresLedger
    from services.control_api.backtest_service import BacktestService
    from services.runtime.provision import seed as seed_ledger
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_jobs import role_engine
    from tests.integration.test_retrospective_acceptance import CODE, protocol, seed_current

    engine = create_engine(
        URL.create("postgresql+psycopg", username="lab_migration", host="postgres", database="lab"),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.execute(text("CREATE ROLE atl_backtest_worker_login LOGIN NOINHERIT"))
        connection.execute(text("GRANT atl_backtest_worker TO atl_backtest_worker_login"))
    seed_ledger(PostgresLedger(engine=engine), Decimal("10000"))
    request, _bars, _daily = seed_current(engine)
    control, worker = (
        role_engine(engine, role) for role in ("atl_control_api", "atl_backtest_worker")
    )
    service = BacktestService(control, code_version=CODE)
    preview = service.create_assessment(
        protocol(request), "local-operator", "rrb08-isolation-preview-0001", "a" * 32
    )
    assert BacktestWorker(worker, enabled=True, code_version=CODE).tick()
    preview = service.read_assessment(preview["assessmentId"])
    assert preview["status"] == "SUCCEEDED"
    # These three jobs execute only inside the constrained, worker-network container.
    service.create_run(request, "local-operator", "rrb08-isolation-strict-0001", "a" * 32)
    service.create_run(
        {
            **protocol(request),
            "assessmentId": preview["assessmentId"],
            "assessmentHash": preview["assessmentHash"],
        },
        "local-operator",
        "rrb08-isolation-run-0001",
        "a" * 32,
    )
    service.create_assessment(
        protocol(request), "local-operator", "rrb08-isolation-second-preview-0001", "a" * 32
    )
    for item in (engine, control, worker):
        item.dispose()
    print("SEEDED")


def probe():
    import os

    from sqlalchemy import URL, create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_retrospective_acceptance import CODE, forbidden_research_calls

    assert os.getuid() == 10001
    for forbidden in ("/run/secrets", "/run/provision", "/var/run/docker.sock"):
        assert not Path(forbidden).exists()
    assert not any(key.startswith(("ALPACA", "JEV", "BROKER")) for key in os.environ)
    assert int(Path("/sys/fs/cgroup/memory.max").read_text()) <= 256 * 1024 * 1024
    quota, period = map(int, Path("/sys/fs/cgroup/cpu.max").read_text().split())
    assert quota / period <= 0.5
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="atl_backtest_worker_login",
            host="postgres",
            database="lab",
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def restrict(dbapi, _record):
        dbapi.execute("SET ROLE atl_backtest_worker")
        dbapi.commit()

    stack, mocks = forbidden_research_calls()
    with stack:
        dispatcher = BacktestWorker(engine, enabled=True, code_version=CODE)
        assert dispatcher.tick()  # Strict cannot use the current backfill as PIT proof.
        assert dispatcher.tick()  # Genuine Decimal simulation from the bound snapshot.
        assert dispatcher.tick()  # Genuine metadata-only preview in the same process.
        assert not dispatcher.tick()
    assert all(mock.call_count == 0 for mock in mocks)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == "atl_backtest_worker"
        rows = connection.execute(
            text("SELECT coalesce(research_mode,'STRICT_PIT'),status FROM backtest.runs")
        ).all()
        assert set(rows) == {("STRICT_PIT", "BLOCKED"), ("RETROSPECTIVE_EXPLORATORY", "SUCCEEDED")}
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM backtest.retrospective_assessments "
                    "WHERE status='SUCCEEDED'"
                )
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM backtest.results r JOIN backtest.runs b "
                    "USING(run_id) WHERE b.research_mode='RETROSPECTIVE_EXPLORATORY'"
                )
            ).scalar_one()
            == 1
        )
    for sql in (
        "SELECT count(*) FROM public.accounts",
        "SELECT count(*) FROM public.positions",
        "SELECT count(*) FROM public.risk_account_context",
        "SELECT count(*) FROM public.order_intents",
        "INSERT INTO public.audit_events DEFAULT VALUES",
        "DELETE FROM research.bar_revisions",
        "DELETE FROM backtest.retrospective_reports",
    ):
        try:
            with engine.begin() as connection:
                connection.execute(text(sql))
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501", "canary must fail for missing privilege"
        else:
            raise AssertionError("research SQL role escaped")
    forbidden = create_engine(
        URL.create("postgresql+psycopg", username="lab_migration", host="postgres", database="lab"),
        connect_args={"connect_timeout": 1},
        hide_parameters=True,
    )
    try:
        with forbidden.connect():
            raise AssertionError("worker network reached admin identity")
    except DBAPIError:
        pass
    finally:
        forbidden.dispose()
        engine.dispose()
    print(MARKER)


def test_actual_retrospective_container_isolation():
    from tests.security.test_backtest_isolation import isolated_worker_host

    assert isolated_worker_host("tests.security.test_retrospective_isolation", MARKER) == MARKER


if __name__ == "__main__":
    try:
        {"seed": seed, "probe": probe}[sys.argv[1]]()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "FAIL backtest isolation "
            + type(error).__name__
            + " "
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
