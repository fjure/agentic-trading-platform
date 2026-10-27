"""RDR-04: echte PG-Barrieren, zwei Scheduler und lokaler HTTPdouble; keine Provider."""

import asyncio
import multiprocessing
import sys
from datetime import datetime, timedelta
from pathlib import Path


def test_collection_sql_dispatch_pause_and_recovery():
    from tests.integration.test_readiness_storage import host

    assert host("tests.integration.test_research_collection").startswith("PASS collection")


def scheduler_process(url, result):
    from sqlalchemy import create_engine

    from packages.research.collection import CollectionScheduler

    engine = create_engine(url, hide_parameters=True)
    try:
        result.put(CollectionScheduler(engine, enabled=True).tick())
    finally:
        engine.dispose()


def pause_process(url, instrument, result):
    from sqlalchemy import create_engine, text

    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "SELECT 1 FROM research.collection_plans WHERE instrument_id=:id FOR UPDATE"
            ), dict(id=instrument))
            connection.execute(text(
                "UPDATE research.collection_plans SET enabled=false,generation=generation+1,"
                "version=version+1,status='PAUSED',reason='USER_PAUSED' WHERE instrument_id=:id"
            ), dict(id=instrument))
        result.put(True)
    finally:
        engine.dispose()


def guest():
    import httpx
    from sqlalchemy import text

    from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
    from packages.research.collection import CollectionScheduler, CollectionStore
    from packages.research.jobs import ResearchJobs
    from packages.research.limits import SharedAccountHostLimiter
    from packages.research.ports import LicenseStatus
    from packages.research.registry import definitions, register
    from services.execution.research_collection import CalendarCollection
    from services.execution.research_ingestion import ResearchIngestion
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    execution = role_engine(engine, "atl_execution")
    instrument, _ = definitions()[0]
    with engine.begin() as connection:
        register(connection)
        now = connection.execute(text("SELECT clock_timestamp()" )).scalar_one()
        connection.execute(text(
            "UPDATE research.source_status SET state='IMPLEMENTED',license_status='CONFIRMED',"
            "policy_version='synthetic-v1' WHERE singleton"
        ))
        connection.execute(text("""
          INSERT INTO research.collection_plans
            (instrument_id,enabled,version,generation,status,reason,updated_at,scheduled_through_at)
          VALUES(:id,true,1,1,'ACTIVE','OK',:at,:at)
        """), dict(id=instrument.instrument_id, at=now - timedelta(hours=24)))
    assert CollectionScheduler(execution, enabled=False).tick() == 0
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    schedulers = [context.Process(target=scheduler_process, args=(engine.url, results))
                  for _ in range(2)]
    for process in schedulers:
        process.start()
    for process in schedulers:
        process.join(30)
        assert process.exitcode == 0
    created = [results.get(timeout=5) for _ in schedulers]
    assert sum(created) <= 2
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM research.due_jobs WHERE state IN ('PENDING','STARTED')"
        )).scalar_one() <= 2
        assert not connection.execute(text(
            "SELECT count(*) FROM research.observation_receipts"
        )).scalar_one()
    requests = []
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def http_double(request):
        requests.append(str(request.url.copy_with(query=None)))
        if request.url.path.endswith("/calendar"):
            first = datetime.fromisoformat(request.url.params["start"]).date()
            last = datetime.fromisoformat(request.url.params["end"]).date()
            days = []
            while first <= last:
                days.append(dict(date=first.isoformat(), open="09:30", close="16:00"))
                first += timedelta(days=1)
            return httpx.Response(200, json=days)
        start = datetime.fromisoformat(request.url.params["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.url.params["end"].replace("Z", "+00:00"))
        blocked.set()
        await release.wait()
        bars = []
        while start + timedelta(minutes=15) <= end:
            bars.append(dict(t=start.isoformat().replace("+00:00", "Z"),
                             o=100, h=101, l=99, c=100, v=1000))
            start += timedelta(minutes=15)
        return httpx.Response(200, json={"bars": {"SPY": bars}, "next_page_token": None})

    async def source():
        return AlpacaHistorySource(
            api_key="synthetic", api_secret="synthetic", enabled=True,
            policy=ProviderPolicy(LicenseStatus.CONFIRMED, "synthetic-v1"),
            transport=httpx.MockTransport(http_double),
        )

    monotonic = [0.0]
    limiter = SharedAccountHostLimiter("synthetic-collection", 10000, clock=lambda: monotonic[0])
    calendar = CalendarCollection(execution, source, limiter, enabled=True)
    ingestion = ResearchIngestion(
        ResearchJobs(execution), source, limiter, enabled=True,
        collection=CollectionStore(execution),
    )

    async def verify():
        assert await calendar.tick()
        assert len(requests) == 1
        CollectionScheduler(execution, enabled=True).tick()
        monotonic[0] += 5
        task = asyncio.create_task(ingestion.tick())
        await asyncio.wait_for(blocked.wait(), timeout=10)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT count(*) FROM research.observation_receipts WHERE state='STARTED'"
            )).scalar_one() == 2
        paused = context.Queue()
        process = context.Process(target=pause_process,
                                  args=(engine.url, instrument.instrument_id, paused))
        process.start()
        await asyncio.to_thread(process.join, 20)
        assert process.exitcode == 0 and paused.get(timeout=5)
        release.set()
        assert await asyncio.wait_for(task, timeout=10)
        before = len(requests)
        for _ in range(3):
            monotonic[0] += 5
            await ingestion.tick()
            assert not await calendar.tick()
        assert len(requests) == before
        with engine.connect() as connection:
            rows = tuple(connection.execute(text(
                "SELECT r.*,c.confirmed_at FROM research.observation_receipts r LEFT JOIN "
                "research.commit_confirmations c USING(receipt_id) WHERE r.event_at IS NOT NULL"
            )).mappings())
            assert rows and all(row["first_observed_at"] is not None for row in rows)
            assert all(row["published_at"] is None for row in rows)
            assert all(row["confirmed_at"] >= row["ingested_at"] for row in rows)
            assert all(row["commit_version"] > 0 for row in rows)
            # Echte Datenaufnahme nach Pause ist zulässig, neue STARTEDs sind es nicht.
            assert connection.execute(text(
                "SELECT count(*) FROM research.observation_receipts WHERE state='STARTED'"
            )).scalar_one() == 2

    asyncio.run(verify())
    engine.dispose()
    execution.dispose()
    print("PASS collection two-schedulers/HTTPdouble/DB-times/pause-process/barrier")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        own = [frame for frame in traceback.extract_tb(error.__traceback__)
               if frame.filename.startswith("/opt/lab/")]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own[-4:])
        diagnostic = getattr(getattr(error, "orig", None), "diag", None)
        message = getattr(diagnostic, "message_primary", "")
        print("FAIL " + type(error).__name__ + " " + where + " " + (message or "")[:160])
        sys.exit(1)
