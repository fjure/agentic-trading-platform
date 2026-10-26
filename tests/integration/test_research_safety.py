"""Research pool saturation must not delay fixed-role Paper safety work."""

from pathlib import Path

MARKER = "PASS: isolated Research pool saturation preserves Paper safety progress"
_PHASE = "START"


def test_research_pool_saturation_preserves_paper_safety_postgres():
    from tests.integration.test_paper_provision import host

    root = Path(__file__).resolve().parents[2]
    fixture = root / "tests/integration/test_paper_lifecycle.py"
    assert host(
        Path(__file__),
        MARKER,
        support_paths=(fixture,),
    ) == MARKER


def guest():
    global _PHASE
    import asyncio
    import time

    from sqlalchemy import URL, create_engine, event, text
    from starlette.requests import Request
    from test_paper_lifecycle import PaperFixture

    from services.control_api.research_routes import register_research_routes
    from services.control_api.research_service import ResearchService

    fixture = PaperFixture()
    research_engine = None
    try:
        _PHASE = "PREPARE"
        fixture.prepare()
        fixture.profile()
        _PHASE = "INITIAL_RECONCILIATION"
        fixture.refresh()
        role = "atl_paper_control_api"
        research_engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username=role + "_login",
                host="postgres",
                database=fixture.database,
            ),
            pool_size=1,
            max_overflow=0,
            pool_timeout=8,
            connect_args={"connect_timeout": 3},
        )

        @event.listens_for(research_engine, "connect")
        def set_research_role(dbapi, _record):
            dbapi.execute("SET ROLE " + role)
            dbapi.commit()

        from fastapi import FastAPI

        research_app = FastAPI()
        register_research_routes(
            research_app,
            research=ResearchService(research_engine),
            body_reader=None,
            key_reader=None,
        )
        endpoint = next(
            route.endpoint for route in research_app.routes
            if getattr(route, "operation_id", None) == "listResearchSources"
        )
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/research/sources",
                "query_string": b"",
                "headers": [],
                "path_params": {},
            }
        )
        request.state.trace_id = "9" * 32

        async def saturated_safety_scenario():
            global _PHASE
            # Hold the Research-only SQL pool's sole connection. The actual route
            # starts and waits for its pool slot, but cannot borrow Paper capacity.
            with research_engine.connect() as held:
                _PHASE = "HOLD_RESEARCH_POOL"
                held.execute(text("SELECT 1"))
                task = asyncio.create_task(endpoint(request))
                await asyncio.sleep(0.15)
                assert not task.done()
                assert research_engine.pool.checkedout() == 1

                _PHASE = "PAPER_SAFETY_AND_RECONCILIATION"
                started = time.monotonic()
                pause, kill, _ = await asyncio.wait_for(
                    asyncio.gather(
                        asyncio.to_thread(
                            fixture.api.enqueue,
                            "PAUSE",
                            {"action": "PAUSE"},
                            "operator",
                            "research-safety-pause-0001",
                            fixture.trace,
                        ),
                        asyncio.to_thread(
                            fixture.api.enqueue,
                            "KILL",
                            {"action": "KILL"},
                            "operator",
                            "research-safety-kill-00001",
                            fixture.trace,
                        ),
                        asyncio.to_thread(fixture.refresh, publish=False),
                    ),
                    timeout=5,
                )
                elapsed = time.monotonic() - started
                assert elapsed < 5
                assert pause["status"] == "ACCEPTED"
                assert kill["status"] == "ACCEPTED"

                status = await asyncio.wait_for(
                    asyncio.to_thread(fixture.api.read_status, fixture.trace), timeout=2
                )
                assert status["operatingState"] in {"PAUSED", "HALTED"}
                assert fixture.sql("worker", "SELECT paper_worker_heartbeat('READY')")
                held.close()
                _PHASE = "RESEARCH_QUERY_COMPLETION"
                response = await asyncio.wait_for(task, timeout=3)
                assert response.status_code == 200
                assert research_engine.pool.checkedout() == 0

        asyncio.run(saturated_safety_scenario())
        research_engine.dispose()
        research_engine = None
        for engine in fixture.engines.values():
            engine.dispose()
        print(MARKER)
    finally:
        if research_engine is not None:
            research_engine.dispose()
        for engine in fixture.engines.values():
            engine.dispose()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            print("APW02_FAIL:RESEARCH_SAFETY:" + type(error).__name__ + ":" + _PHASE)
            raise SystemExit(1) from None
    else:
        print("Use the isolated fixed-role Paper integration host")
