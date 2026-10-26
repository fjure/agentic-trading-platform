"""End-to-end offline reproduction, worker crash, partial folds and current backfills."""

import sys
from pathlib import Path

from tests.integration.test_backtest_dataset import guest_engine, host


def verify(engine):
    from copy import deepcopy
    from datetime import timedelta
    from decimal import Decimal
    from unittest.mock import patch

    from sqlalchemy import text

    from packages.domain.models import utc_string
    from packages.persistence.ledger import PostgresLedger
    from services.control_api.backtest_service import BacktestService
    from services.runtime.provision import seed
    from services.worker.backtesting import BacktestWorker
    from tests.integration.backtest_browser_support import seed_backtest_fixture
    from tests.integration.test_backtest_jobs import role_engine
    from tests.security.test_backtest_access import BASE, ORIGIN, client

    seed(PostgresLedger(engine=engine), Decimal("10000"))
    fixture = seed_backtest_fixture(engine)
    control, worker_engine = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    service = BacktestService(control, code_version="b" * 40)
    http = client(service)
    from packages.backtesting.request import parse_time

    start, end = parse_time(fixture["start"]), parse_time(fixture["end"])
    request = dict(
        instrumentId=fixture["instrument"],
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
                trainingEnd=utc_string(start),
                testStart=utc_string(start),
                testEnd=utc_string(end),
            )
        ],
    )

    def enqueue(key, body=None):
        reply = http.post(
            BASE + "/runs", json=body or request, headers={"Origin": ORIGIN, "Idempotency-Key": key}
        )
        assert reply.status_code == 202, (reply.status_code, reply.json())
        return reply.json()["runId"]

    def financial_fingerprint():
        with engine.connect() as connection:
            names = connection.execute(
                text("""SELECT tablename FROM pg_tables
              WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename""")
            ).scalars()
            return tuple(
                (
                    name,
                    connection.execute(
                        text(
                            "SELECT md5(COALESCE(jsonb_agg(to_jsonb(r) "
                            "ORDER BY to_jsonb(r)::text)::text,'[]')) "
                            f'FROM public."{name}" r'
                        )
                    ).scalar_one(),
                )
                for name in names
            )

    baseline = financial_fingerprint()
    first = enqueue("backtest-acceptance-0001")
    dispatched = BacktestWorker(worker_engine, enabled=True, code_version="b" * 40)
    assert BacktestWorker(worker_engine).tick() is False

    class Crash(BaseException):
        pass

    captured = []

    def crash(lease, result, status="SUCCEEDED", error_code=None):
        captured.append(deepcopy(result))
        assert status == "SUCCEEDED", error_code
        raise Crash()

    dispatched.jobs.publish = crash
    with patch(
        "httpx.Client.send", side_effect=AssertionError("external HTTP forbidden")
    ) as outbound:
        try:
            dispatched.tick()
        except Crash:
            pass
        else:
            raise AssertionError("worker crash not reached")
        assert outbound.call_count == 0
    assert len(captured) == 1
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM backtest.results")).scalar_one() == 0
        bound = connection.execute(
            text("SELECT fingerprint FROM backtest.manifests WHERE run_id=:id"), dict(id=first)
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE backtest.runs SET lease_until=clock_timestamp()-"
                "interval '1 second' WHERE run_id=:id"
            ),
            dict(id=first),
        )
    recovered = BacktestWorker(worker_engine, enabled=True, code_version="b" * 40)
    with patch(
        "httpx.Client.send", side_effect=AssertionError("external HTTP forbidden")
    ) as outbound:
        assert recovered.tick()
        assert outbound.call_count == 0
    detail = http.get(BASE + "/runs/" + first).json()
    assert detail["run"]["status"] == "SUCCEEDED", detail
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT fingerprint FROM backtest.manifests WHERE run_id=:id"), dict(id=first)
            ).scalar_one()
            == bound
        )
        stored = connection.execute(
            text("SELECT body FROM backtest.results WHERE run_id=:id"), dict(id=first)
        ).scalar_one()
        assert stored == captured[0]
    manifest_hash, result_hash = detail["run"]["manifestHash"], detail["run"]["resultHash"]
    # Independent hand calculation: buy at the third open (signal at +16min),
    # 1000 shares * final raw close12 =12000, no fees and no final liquidation.
    metrics = stored["summary"][0]["metrics"]
    assert Decimal(metrics["endEquity"]) == 12000
    assert Decimal(metrics["netReturn"]) == Decimal("0.2")
    assert Decimal(metrics["maxDrawdown"]) == 0
    assert metrics["fillCount"] == 1
    assert Decimal(metrics["endQuantity"]) == 1000
    assert Decimal(stored["summary"][0]["references"][0]["metrics"]["endEquity"]) == 10000
    assert manifest_hash and result_hash
    second = enqueue("backtest-acceptance-0002")
    assert recovered.tick()
    second_detail = http.get(BASE + "/runs/" + second).json()
    assert second_detail["run"]["status"] == "SUCCEEDED"
    # Fresh acceptance has its own snapshot time/manifest; the same data produces identical results.
    assert second_detail["run"]["resultHash"] == result_hash

    partial = deepcopy(request)
    partial["folds"].append(
        dict(
            trainingStart=request["folds"][0]["trainingStart"],
            trainingEnd=utc_string(end),
            testStart=utc_string(end),
            testEnd=utc_string(end + timedelta(minutes=30)),
        )
    )
    third = enqueue("backtest-acceptance-partial", partial)
    assert recovered.tick()
    partial_detail = http.get(BASE + "/runs/" + third).json()
    assert partial_detail["run"]["status"] == "BLOCKED", partial_detail
    assert len(partial_detail["run"]["completedFolds"]) == 1
    assert partial_detail["run"]["resultHash"] is None
    assert http.get(BASE + "/runs/" + third + "/results").json()["items"] == []

    # A present-day ingestion of otherwise identical old bars is not PIT evidence.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE research.bar_revisions DISABLE TRIGGER immutable"))
        connection.execute(text("UPDATE research.bar_revisions SET ingested_at=clock_timestamp()"))
        connection.execute(text("ALTER TABLE research.bar_revisions ENABLE TRIGGER immutable"))
    backfilled = enqueue("backtest-acceptance-backfill")
    assert recovered.tick()
    blocked = http.get(BASE + "/runs/" + backfilled).json()
    assert blocked["run"]["status"] == "BLOCKED"
    assert not blocked["run"]["publicationComplete"]
    assert blocked["run"]["resultHash"] is None
    assert blocked["run"]["completedFolds"] == []
    mismatch = enqueue("backtest-acceptance-code-mismatch")
    assert BacktestWorker(worker_engine, enabled=True, code_version="c" * 40).tick()
    different = http.get(BASE + "/runs/" + mismatch).json()
    assert different["run"]["status"] == "FAILED"
    assert different["run"]["resultHash"] is None
    assert different["run"]["completedFolds"] == []
    assert different["manifest"] is None
    assert financial_fingerprint() == baseline
    # Published input and completed results remain readable after the backfill/restart.
    restarted = BacktestService(control, code_version="b" * 40)
    assert restarted.read_run(first)["run"]["manifestHash"] == manifest_hash
    assert restarted.read_run(first)["run"]["resultHash"] == result_hash
    control.dispose()
    worker_engine.dispose()


def guest():
    engine = guest_engine()
    verify(engine)
    engine.dispose()
    print("PASS backtest complete/reproduce/crash/partial/backfill/no-HTTP/unchanged-ledger")


def test_backtest_complete_offline_acceptance():
    assert host("tests.integration.test_backtest_acceptance").startswith("PASS backtest complete/")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-4:])
        print("FAIL " + type(error).__name__ + " " + where)
        sys.exit(1)
