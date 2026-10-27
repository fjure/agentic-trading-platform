"""RDR-05: echte SQLtransaktionen, Crash, Fences und gemeinsame Workergrenze."""

import sys
from pathlib import Path

from tests.integration.test_backtest_dataset import guest_engine, host


def guest():
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.jobs import BacktestError, BacktestJobs
    from packages.backtesting.readiness_jobs import ReadinessJobs
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_jobs import role_engine, valid_request

    engine = guest_engine()
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    reads, jobs = ReadinessJobs(control), ReadinessJobs(worker)
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

    def expire(identifier):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE backtest.readiness_jobs SET "
                    "lease_until=clock_timestamp()-interval '1s' "
                    "WHERE assessment_id=:id"
                ),
                dict(id=identifier),
            )

    def counts(identifier):
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text("SELECT count(*) FROM backtest." + table + " WHERE assessment_id=:id"),
                    dict(id=identifier),
                ).scalar_one()
                for table in ("readiness_reports", "readiness_findings")
            )

    first = enqueue("readiness-recovery-0001")
    assert enqueue("readiness-recovery-0001")["assessment_id"] == first["assessment_id"]
    expect_error(
        lambda: enqueue("readiness-recovery-0001", {**request, "feeUsd": "2"}),
        "IDEMPOTENCY_CONFLICT",
    )
    dispatcher = BacktestWorker(worker, enabled=True, code_version="b" * 40)
    captured = {}

    class ProcessCrash(BaseException):
        pass

    def crash_before_publication(lease, report=None, findings=(), **values):
        assert report is not None
        captured.update(lease=lease, report=report, findings=findings, values=values)
        raise ProcessCrash()

    dispatcher.readiness_jobs.publish = crash_before_publication
    try:
        dispatcher.tick()
    except ProcessCrash:
        pass
    else:
        raise AssertionError("process crash required")
    stale = captured["lease"]
    assert stale.assessment_id == first["assessment_id"] and stale.fence == 1
    assert counts(stale.assessment_id) == (0, 0)
    assert jobs.claim(uuid4()) is None
    expire(stale.assessment_id)
    recovered = jobs.claim(uuid4())
    assert recovered.assessment_id == stale.assessment_id and recovered.fence == 2
    expect_error(lambda: jobs.checkpoint(stale), "LEASE_LOST")
    expect_error(
        lambda: jobs.publish(stale, captured["report"], captured["findings"], **captured["values"]),
        "LEASE_LOST",
    )

    # Crash nach allen INSERTs/UPDATEs, vor Commit: nichts wird teilpubliziert.
    audit = jobs.audit

    def crash_at_commit(*args):
        raise RuntimeError("synthetic before-commit crash")

    jobs.audit = crash_at_commit
    try:
        jobs.publish(recovered, captured["report"], captured["findings"], **captured["values"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("transaction crash required")
    jobs.audit = audit
    assert counts(recovered.assessment_id) == (0, 0)
    jobs.publish(recovered, captured["report"], captured["findings"], **captured["values"])
    assert counts(recovered.assessment_id) == (1, len(captured["findings"]))
    expect_error(
        lambda: jobs.publish(
            recovered, captured["report"], captured["findings"], **captured["values"]
        ),
        "LEASE_LOST",
    )
    for table in ("readiness_reports", "readiness_findings"):
        try:
            with worker.begin() as connection:
                connection.execute(text("DELETE FROM backtest." + table))
        except DBAPIError:
            pass
        else:
            raise AssertionError("published rows are immutable and role constrained")

    exhausted = enqueue("readiness-exhausted-01")
    for attempt in (1, 2, 3):
        lease = jobs.claim(uuid4())
        assert (
            lease.assessment_id == exhausted["assessment_id"] and lease.row["attempts"] == attempt
        )
        expire(lease.assessment_id)
    assert jobs.claim(uuid4()) is None
    with control.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM backtest.readiness_jobs WHERE assessment_id=:id"),
                dict(id=exhausted["assessment_id"]),
            )
            .mappings()
            .one()
        )
        assert (row["status"], row["eligibility"], row["error_code"]) == (
            "FAILED",
            "UNKNOWN",
            "ATTEMPTS_EXHAUSTED",
        )
    assert counts(exhausted["assessment_id"]) == (0, 0)

    # Die Zulassung ist atomar begrenzt; Replay bleibt auch bei voller Queue möglich.
    for index in range(32):
        enqueue(f"readiness-capacity-{index:04d}")
    expect_error(lambda: enqueue("readiness-capacity-over"), "QUEUE_EXHAUSTED")
    assert enqueue("readiness-capacity-0000")["status"] == "ACCEPTED"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE backtest.readiness_jobs SET status='FAILED',"
                "eligibility='UNKNOWN',error_code='INTERNAL_FAILURE' "
                "WHERE status='ACCEPTED'"
            )
        )

    enqueue("readiness-shared-worker-1")
    backtest_reads, backtest_jobs = BacktestJobs(control), BacktestJobs(worker)
    backtest_reads.enqueue(
        request, "local-operator", "backtest-shared-worker-1", "a" * 32, "b" * 40
    )

    def claim_one(port):
        try:
            return port.claim(uuid4())
        except BacktestError as error:
            assert error.code == "UNAVAILABLE"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim_one, (jobs, backtest_jobs)))
    assert sum(lease is not None for lease in leases) == 1
    with engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM backtest.runs "
                "WHERE status='RUNNING')+(SELECT count(*) FROM "
                "backtest.readiness_jobs WHERE status='RUNNING')"
            )
        ).scalar_one()
        assert active == 1
        assert (
            connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        )
    print(
        "PASS readiness real PG recovery/crash/atomic-publication/"
        "fence/attempts/queue32/shared-worker"
    )


def test_readiness_jobs_real_postgresql():
    assert host("tests.recovery.test_readiness_jobs").startswith("PASS readiness real PG recovery")


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            location = ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in frames[-4:])
            print("FAIL " + type(error).__name__ + " " + location)
            raise SystemExit(1) from None
    else:
        print(host("tests.recovery.test_readiness_jobs"))
