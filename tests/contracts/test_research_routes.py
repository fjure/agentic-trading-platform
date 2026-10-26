"""Research route admission never borrows unbounded shared thread capacity."""

import asyncio
import threading
from types import SimpleNamespace

from starlette.requests import Request

from services.control_api.research_routes import CURRENT_TRACE, register_research_routes


def test_research_route_pool_saturates_with_immediate_safe_refusal_and_context_copy():
    entered = threading.Event()
    release = threading.Event()
    seen = []
    active = 0
    maximum = 0
    lock = threading.Lock()
    response = {
        "items": [],
        "observedAt": "2026-10-25T18:00:00Z",
        "incomplete": True,
    }

    class Research:
        def read_sources(self):
            nonlocal active, maximum
            seen.append(CURRENT_TRACE.get())
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    entered.set()
            try:
                if not release.wait(timeout=2):
                    raise TimeoutError("test release timed out")
                return response
            finally:
                with lock:
                    active -= 1

    class App:
        def __init__(self):
            self.routes = {}
            self.state = SimpleNamespace()

        def get(self, _path, *, operation_id):
            return lambda fn: self.routes.setdefault(operation_id, fn)

        def add_api_route(self, _path, endpoint, *, methods, operation_id):
            assert methods == ["GET"]
            self.routes[operation_id] = endpoint

        def post(self, _path, *, operation_id):
            return lambda fn: self.routes.setdefault(operation_id, fn)

    app = App()
    register_research_routes(app, research=Research(), body_reader=None, key_reader=None)
    endpoint = app.routes["listResearchSources"]

    def request(trace):
        value = Request(
            {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": []}
        )
        value.state.trace_id = trace
        return value

    async def exercise():
        first = asyncio.create_task(endpoint(request("a" * 32)))
        second = asyncio.create_task(endpoint(request("b" * 32)))
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set(), (seen, app.state.research_metrics.snapshot())
        overflow = await endpoint(request("c" * 32))
        assert overflow.status_code == 503
        release.set()
        accepted = await asyncio.gather(first, second)
        assert [item.status_code for item in accepted] == [200, 200]

    asyncio.run(exercise())
    assert set(seen) == {"a" * 32, "b" * 32}
    assert maximum == 2
    counters = app.state.research_metrics.snapshot()
    assert counters["research_query_sources_count"] == 3
    assert counters["research_query_sources_rows"] == 0
    assert counters["research_error_unavailable_count"] == 1
    assert all("a" * 32 not in name and "b" * 32 not in name for name in counters)
