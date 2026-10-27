"""RDR04: reale Rollen und lokale Rate-/Safetybarrieren ohne Providerzugriff."""

import asyncio
import sys
from pathlib import Path


def test_collection_roles_disabled_safety_and_429():
    from tests.integration.test_readiness_storage import host

    assert host("tests.security.test_research_collection_isolation").startswith(
        "PASS collection isolation"
    )


def guest():
    import httpx
    from sqlalchemy import text

    from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
    from packages.research.collection import CollectionScheduler
    from packages.research.limits import SharedAccountHostLimiter
    from packages.research.ports import LicenseStatus, SourceError, SourceErrorCode
    from packages.research.registry import definitions, register
    from services.execution.research_collection import CalendarCollection
    from services.runtime.research import PaperResearchRuntime
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    execution = role_engine(engine, "atl_execution")
    with engine.begin() as connection:
        register(connection)
        connection.execute(text(
            "UPDATE research.source_status SET state='IMPLEMENTED',license_status='CONFIRMED',"
            "policy_version='synthetic-v1' WHERE singleton"
        ))
        connection.execute(text(
            "INSERT INTO research.collection_plans "
            "(instrument_id,enabled,version,generation,status,reason) "
            "VALUES(:id,true,1,1,'ACTIVE','OK')"
        ), dict(id=definitions()[0][0].instrument_id))
        for role in ("atl_control_api", "atl_worker", "atl_backtest_worker"):
            assert not connection.execute(text(
                "SELECT has_table_privilege(:role,'research.observation_receipts','INSERT')"
            ), dict(role=role)).scalar_one()
        assert not connection.execute(text(
            "SELECT has_table_privilege('atl_execution','research.evidence_revisions','INSERT')"
        )).scalar_one()
    assert CollectionScheduler(execution, enabled=True).tick() == 1
    requests, factories = [], []
    monotonic = [0.0]
    limiter = SharedAccountHostLimiter("synthetic-security", 120, clock=lambda: monotonic[0])

    async def factory():
        factories.append(True)
        # Safety becomes busy during capability construction: recheck before STARTED.
        if len(factories) == 1:
            limiter.set_safety_busy(True)
        return AlpacaHistorySource(
            api_key="synthetic", api_secret="synthetic", enabled=True,
            policy=ProviderPolicy(LicenseStatus.CONFIRMED, "synthetic-v1"),
            transport=httpx.MockTransport(http_double),
        )

    async def http_double(request):
        requests.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={})

    def starts():
        with engine.connect() as connection:
            return connection.execute(text(
                "SELECT count(*) FROM research.observation_receipts WHERE state='STARTED'"
            )).scalar_one()

    async def verify():
        assert not await CalendarCollection(execution, factory, limiter, enabled=False).tick()
        assert not factories and starts() == 0
        collector = CalendarCollection(execution, factory, limiter, enabled=True)
        assert not await collector.tick()
        assert len(factories) == 1 and not requests and starts() == 0
        limiter.set_safety_busy(False)
        monotonic[0] += 5
        assert await collector.tick()
        assert len(requests) == 1 and starts() == 1
        monotonic[0] += 5
        assert not await collector.tick()
        assert len(requests) == 1 and starts() == 1
        # Existing safety reads retain admission while research backs off.
        assert limiter.allow("paper-api.alpaca.markets", research=False)
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT state,lease_until>clock_timestamp() AS delayed FROM research.due_jobs"
            )).mappings().one()
            assert row["state"] == "PENDING" and row["delayed"]
            assert connection.execute(text(
                "SELECT count(*) FROM research.observation_receipts WHERE state='UNKNOWN'"
            )).scalar_one() == 1
        with engine.begin() as connection:
            connection.execute(text("UPDATE research.due_jobs SET lease_until=NULL"))

        async def blocked_factory():
            raise SourceError(SourceErrorCode.BLOCKED_CREDENTIALS)

        monotonic[0] += 60
        blocked = CalendarCollection(execution, blocked_factory, limiter, enabled=True,
                                     error_observer=PaperResearchRuntime._failed)
        assert not await blocked.tick()
        assert len(requests) == 1 and starts() == 1
        CollectionScheduler(execution, enabled=True).tick()
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT state FROM research.source_status WHERE singleton"
            )).scalar_one() == "BLOCKED"
            assert connection.execute(text(
                "SELECT status FROM research.collection_plans"
            )).scalar_one() == "BLOCKED"

    asyncio.run(verify())
    execution.dispose()
    engine.dispose()
    print("PASS collection isolation roles/defaultoff/safety-recheck/429/no-provider")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        own = [frame for frame in traceback.extract_tb(error.__traceback__)
               if frame.filename.startswith("/opt/lab/")]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own[-4:])
        print("FAIL " + type(error).__name__ + " " + where)
        sys.exit(1)
