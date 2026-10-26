"""Real PostgreSQL crash/fence/queue and atomic immutable publication checks."""

import json
from pathlib import Path
from uuid import uuid4

from tests.integration.test_backtest_dataset import guest_engine, host


def role_engine(engine, role):
    from sqlalchemy import create_engine, event

    selected = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(selected, "connect")
    def choose_role(dbapi, record):
        dbapi.execute("SET ROLE " + role)
        dbapi.commit()

    return selected


def valid_request():
    from datetime import UTC, datetime

    from packages.backtesting.request import parse_request
    from packages.research.registry import definitions

    root = Path(__file__).resolve().parents[2]
    request = json.loads((root / "contracts/examples/v1/backtest-request.valid.json").read_text())
    request["instrumentId"] = str(definitions()[0][0].instrument_id)
    parse_request(request, datetime.now(UTC))
    return request


def guest():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.jobs import BacktestError, BacktestJobs
    from tests.security.test_backtest_roles import verify_roles

    engine = guest_engine()
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    reads, jobs = BacktestJobs(control), BacktestJobs(worker)
    request = valid_request()

    def enqueue(key, body=None):
        return reads.enqueue(body or request, "local-operator", key, "a" * 32, "b" * 40)

    def expect_error(call, code):
        try:
            call()
        except BacktestError as error:
            assert error.code == code, (error.code, code)
        else:
            raise AssertionError("expected " + code)

    first = enqueue("backtest-pg-job-00001")
    assert enqueue("backtest-pg-job-00001")["run_id"] == first["run_id"]
    expect_error(
        lambda: enqueue("backtest-pg-job-00001", {**request, "startCash": "20000"}),
        "IDEMPOTENCY_CONFLICT",
    )
    second = enqueue("backtest-pg-job-00002")
    owner = uuid4()
    lease1 = jobs.claim(owner)
    assert lease1.run_id == first["run_id"] and lease1.fence == 1
    assert jobs.claim(uuid4()) is None, "separate owner cannot exceed one active job"
    manifest = {"snapshot": first["snapshot_version"], "input": "immutable-test-evidence"}
    jobs.bind_manifest(lease1, manifest)
    jobs.bind_manifest(lease1, manifest)
    expect_error(lambda: jobs.bind_manifest(lease1, {"snapshot": "changed"}), "SNAPSHOT_CONFLICT")
    fold = {"summary": {"foldIndex": 0}, "equity": [], "fills": []}
    jobs.checkpoint(lease1, 0, fold)
    jobs.checkpoint(lease1, 0, fold)
    expect_error(lambda: jobs.checkpoint(lease1, 0, {"changed": True}), "SNAPSHOT_CONFLICT")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE backtest.runs SET lease_until=clock_timestamp()-interval '1s' "
                "WHERE run_id=:id"
            ),
            {"id": lease1.run_id},
        )
    lease2 = jobs.claim(uuid4())
    assert lease2.run_id == lease1.run_id and lease2.fence == 2
    for call in (
        lambda: jobs.publish(lease1, {}),
        lambda: jobs.bind_manifest(lease1, manifest),
        lambda: jobs.checkpoint(lease1, 1, fold),
    ):
        expect_error(call, "LEASE_LOST")
    # A recovered worker reuses the exact manifest/fold and publishes once.
    jobs.bind_manifest(lease2, manifest)
    jobs.checkpoint(lease2, 0, fold)
    result = {"summary": [fold["summary"]], "equity": [], "fills": []}
    jobs.publish(lease2, result)
    expect_error(lambda: jobs.publish(lease2, result), "LEASE_LOST")
    with control.connect() as conn:
        assert (
            conn.execute(
                text("SELECT body FROM backtest.results WHERE run_id=:id"), {"id": lease2.run_id}
            ).scalar_one()
            == result
        )
        assert (
            conn.execute(
                text("SELECT checkpoint FROM backtest.runs WHERE run_id=:id"), {"id": lease2.run_id}
            ).scalar_one()
            == 1
        )
    # Inject an actual transaction failure after result insertion and run update.
    next_lease = jobs.claim(uuid4())
    assert next_lease.run_id == second["run_id"]
    original_audit = jobs.audit

    def crash_after_writes(connection, row, event):
        raise RuntimeError("synthetic crash before commit")

    jobs.audit = crash_after_writes
    try:
        jobs.publish(next_lease, result)
    except RuntimeError:
        pass
    else:
        raise AssertionError("crash injection must propagate")
    jobs.audit = original_audit
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": next_lease.run_id},
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT status FROM backtest.runs WHERE run_id=:id"), {"id": next_lease.run_id}
            ).scalar_one()
            == "RUNNING"
        )
    # Each expired attempt is recovered once, then terminally exhausted.
    for expected_attempt in (2, 3):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE backtest.runs SET lease_until=clock_timestamp()-interval '1s' "
                    "WHERE run_id=:id"
                ),
                {"id": next_lease.run_id},
            )
        next_lease = jobs.claim(uuid4())
        assert next_lease.row["attempts"] == expected_attempt
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE backtest.runs SET lease_until=clock_timestamp()-interval '1s' "
                "WHERE run_id=:id"
            ),
            {"id": next_lease.run_id},
        )
    assert jobs.claim(uuid4()) is None
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status,error_code FROM backtest.runs WHERE run_id=:id"),
            {"id": next_lease.run_id},
        ).one()
        assert tuple(row) == ("FAILED", "BUDGET_EXHAUSTED")
    for sql in (
        "UPDATE backtest.results SET fingerprint=repeat('0',64)",
        "DELETE FROM backtest.results",
        "DELETE FROM backtest.manifests",
        "DELETE FROM backtest.fold_results",
        "DELETE FROM backtest.audit",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except DBAPIError:
            pass
        else:
            raise AssertionError("append-only results and audit")
    for index in range(100):
        enqueue(f"backtest-pg-queue-{index:04}")
    expect_error(lambda: enqueue("backtest-pg-queue-overflow"), "UNAVAILABLE")
    assert enqueue("backtest-pg-queue-0000")["status"] == "ACCEPTED", "retry survives full queue"
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM backtest.runs WHERE status IN ('ACCEPTED','RUNNING')")
            ).scalar_one()
            == 100
        )
    verify_roles(engine)
    worker.dispose()
    control.dispose()
    engine.dispose()
    print("PASS backtest PG idempotency/queue/lease/fence/crash/recovery/atomic/roles")


def test_backtest_jobs_actual_postgresql():
    assert host("tests.integration.test_backtest_jobs").startswith(
        "PASS backtest PG idempotency/queue/lease/fence"
    )


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-3:])
        print("FAIL " + type(error).__name__ + " " + where)
        raise SystemExit(1) from None
