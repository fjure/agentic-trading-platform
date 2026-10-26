"""PTV-19: endliche ASGI-/Generatorprüfungen ohne echte Netzwerkverbindungen."""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from scripts.contract_stages import pending_contract_operations
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.cursors import CursorExpired
from services.control_api.streaming import event_stream, frame
from tests.contracts.test_control_api import OAS, ORIGIN, SpyBackend, validate

TOKEN = "c_" + "A" * 43
NEXT = "c_" + "B" * 43
TRACE = "b" * 32


def test_all_runtime_operations_match_canonical_contract():
    pending_operations = pending_contract_operations()
    actual = {
        (route.path, method.lower(), route.operation_id)
        for route in create_app().routes
        for method in route.methods
    }
    expected = {
        (path, method, operation["operationId"])
        for path, methods in OAS["paths"].items()
        for method, operation in methods.items()
        if operation["operationId"] not in pending_operations
    }
    assert actual == expected


def event():
    return dict(
        eventId="event",
        eventType="mode.updated.v1",
        occurredAt="2026-10-16T10:00:00Z",
        aggregateId="paper",
        aggregateVersion=2,
        traceId=TRACE,
        cursor=NEXT,
        payload={"mode": "DISABLED"},
    )


class Reader:
    def __init__(self):
        self.calls = []

    def snapshot(self, trace):
        return dict(
            asOf="2026-10-16T10:00:00Z",
            cursor=TOKEN,
            mode={**SpyBackend().mode, "traceId": trace},
            paperAccount="paper",
            connections={"execution": "UNAVAILABLE"},
            portfolioEquity="1000",
            workers=[],
            recentOrders=[],
        )

    def page(self, resource, cursor=None, limit=50, trace_id=None):
        self.calls.append((resource, cursor, limit, trace_id))
        if cursor == "expired":
            raise CursorExpired()
        return {"items": [], "nextCursor": None}


class Feed:
    def __init__(self, fail=False):
        self.selected = None
        self.fail = fail

    def prepare(self, cursor):
        self.selected = cursor
        if self.fail or cursor is None:
            raise CursorExpired()
        return cursor

    def poll(self, cursor):
        if self.fail:
            raise CursorExpired()
        return [event()], NEXT


def client(reader=None, feed=None):
    auth = LocalAuth()
    token = auth.activate(auth.issue_activation_code(), "session-key-000001")
    http = TestClient(
        create_app(auth=auth, supervisor=reader, events=feed),
        base_url=ORIGIN,
        client=("127.0.0.1", 45000),
    )
    http.cookies.set("atl_session", token)
    return http


@pytest.mark.parametrize(
    "resource,schema",
    [
        ("market-states", "MarketStatePage"),
        ("decisions", "DecisionPage"),
        ("proposals", "ProposalPage"),
        ("risk-decisions", "RiskDecisionPage"),
        ("orders", "OrderPage"),
        ("audit", "AuditPage"),
        ("runs", "RunPage"),
        ("costs", "CostPage"),
    ],
)
def test_history_routes_and_cursor_errors(resource, schema):
    reader = Reader()
    http = client(reader)
    response = http.get("/api/v1/" + resource + "?limit=2")
    assert response.status_code == 200
    validate(schema, response.json())
    assert reader.calls[-1] == (resource, None, 2, None)
    response = http.get("/api/v1/" + resource + "?cursor=expired")
    assert response.status_code == 410
    validate("Problem", response.json())
    assert response.json()["code"] == "CURSOR_EXPIRED"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=201",
        "limit=nan",
        "limit=2&limit=3",
        "cursor=",
        "cursor=a&cursor=b",
        "traceId=bad",
    ],
)
def test_query_negatives_do_not_reach_reader(query):
    reader = Reader()
    assert client(reader).get("/api/v1/audit?" + query).status_code == 400
    assert reader.calls == []


def test_snapshot_and_missing_ports():
    response = client(Reader()).get("/api/v1/snapshot")
    assert response.status_code == 200
    validate("SupervisorSnapshot", response.json())
    assert client().get("/api/v1/snapshot").status_code == 503


async def request_stream(feed, *, header=None, query=""):
    """Stop after first frame; never buffer an infinite TestClient stream."""
    auth = LocalAuth()
    token = auth.activate(auth.issue_activation_code(), "stream-session-0001")
    app = create_app(auth=auth, events=feed)
    disconnected = asyncio.Event()
    messages = []
    headers = [(b"host", b"127.0.0.1:8000"), (b"cookie", ("atl_session=" + token).encode())]
    if header is not None:
        headers.append((b"last-event-id", header.encode()))
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            disconnected.set()

    await asyncio.wait_for(
        app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/v1/events",
                "raw_path": b"/api/v1/events",
                "query_string": query.encode(),
                "root_path": "",
                "headers": headers,
                "client": ("127.0.0.1", 45000),
                "server": ("127.0.0.1", 8000),
            },
            receive,
            send,
        ),
        3,
    )
    return messages


def test_last_event_id_precedence_and_real_asgi_sse_framing():
    feed = Feed()
    messages = asyncio.run(request_stream(feed, header=TOKEN, query="cursor=query-is-ignored"))
    assert feed.selected == TOKEN
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
    body = b"".join(m.get("body", b"") for m in messages)
    assert body == frame(event())
    validate("SupervisorEvent", json.loads(body.split(b"data: ")[1]))


def test_unknown_cursor_returns_410_before_stream_open():
    messages = asyncio.run(request_stream(Feed(fail=True), query="cursor=unknown"))
    assert next(m for m in messages if m["type"] == "http.response.start")["status"] == 410
    body = b"".join(m.get("body", b"") for m in messages)
    validate("Problem", json.loads(body))


def test_midstream_gap_reset_heartbeat_and_revocation():
    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        async def no_wait(_seconds):
            await asyncio.sleep(0)

        auth = LocalAuth()
        token = auth.activate(auth.issue_activation_code(), "stream-session-0001")
        stream = event_stream(
            Request(), Feed(fail=True), TOKEN, ([event()], NEXT), auth, token, TRACE, sleep=no_wait
        )
        assert await anext(stream) == frame(event())
        reset = await anext(stream)
        assert reset.startswith(b"event: reset-required\ndata: ")
        validate("Problem", json.loads(reset.split(b"data: ")[1]))
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        clock = iter([0, 16, 16])
        heartbeat = event_stream(
            Request(),
            Feed(),
            TOKEN,
            ([], TOKEN),
            auth,
            token,
            TRACE,
            sleep=no_wait,
            monotonic=lambda: next(clock),
        )
        assert await anext(heartbeat) == b": heartbeat\n\n"
        await heartbeat.aclose()
        revoked = event_stream(
            Request(), Feed(), TOKEN, ([], TOKEN), LocalAuth(), token, TRACE, sleep=no_wait
        )
        with pytest.raises(StopAsyncIteration):
            await anext(revoked)

    asyncio.run(run())


def test_sse_observation_runs_after_actual_yield_and_cannot_break_delivery():
    class Request:
        async def is_disconnected(self):
            return False

    async def run():
        calls = []

        def observe(moment):
            calls.append(moment)
            raise RuntimeError("synthetic-observer-canary")

        async def no_wait(_seconds):
            await asyncio.sleep(0)

        auth = LocalAuth()
        token = auth.activate(auth.issue_activation_code(), "observed-stream-0001")
        stream = event_stream(
            Request(), Feed(fail=True), TOKEN, ([event()], NEXT), auth, token, TRACE,
            sleep=no_wait, delivery_observer=observe,
        )
        assert await anext(stream) == frame(event())
        assert calls == []  # The generator has yielded, send has not resumed it yet.
        assert (await anext(stream)).startswith(b"event: reset-required")
        assert len(calls) == 1 and calls[0].tzinfo is not None
        await stream.aclose()

    asyncio.run(run())


def test_heartbeat_is_not_delayed_by_pending_database_poll():
    class Request:
        async def is_disconnected(self):
            return False

    entered, release = threading.Event(), threading.Event()

    class SlowFeed:
        def poll(self, cursor):
            entered.set()
            assert release.wait(2)
            return [], cursor

    async def run():
        auth = LocalAuth()
        token = auth.activate(auth.issue_activation_code(), "stream-session-0001")
        stream = event_stream(
            Request(),
            SlowFeed(),
            TOKEN,
            ([], TOKEN),
            auth,
            token,
            TRACE,
            heartbeat_seconds=0.02,
            poll_seconds=0.001,
        )
        try:
            assert await asyncio.wait_for(anext(stream), 1) == b": heartbeat\n\n"
            assert entered.is_set() and not release.is_set()
        finally:
            release.set()
            await stream.aclose()

    asyncio.run(run())
