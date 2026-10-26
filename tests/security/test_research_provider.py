"""Offline certification of the execution-only historical GET boundary."""

import asyncio
import gzip
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from packages.brokers.alpaca_history import (
    AlpacaHistorySource,
    CalendarSession,
    HistorySourceError,
    ProviderPolicy,
)
from packages.research.models import Adjustment, BarInterval
from packages.research.ports import (
    HistoricalBarsRequest,
    LicenseStatus,
    SourceErrorCode,
    SourceStatus,
)

ID = UUID("ce85d363-e845-4b4a-9b11-6de080bb43da")
START = datetime(2026, 10, 23, 13, 30, tzinfo=UTC)
END = START + timedelta(minutes=30)
OBSERVED = datetime(2026, 10, 25, tzinfo=UTC)
KEY, SECRET = "synthetic-key-canary", "synthetic-secret-canary"
POLICY = ProviderPolicy(LicenseStatus.CONFIRMED, "offline-test-v1")
SESSION = CalendarSession(
    date(2026, 10, 23), START, datetime(2026, 10, 23, 20, tzinfo=UTC), "0" * 64
)


@pytest.mark.parametrize("status", [400, 422])
def test_rejected_continuation_is_typed_cursor_recovery_without_provider_message(status):
    async def run():
        adapter = source(
            lambda _: httpx.Response(status, json={"message": "synthetic-private-canary"})
        )
        try:
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(
                    request(), "valid-private-continuation", sessions=(SESSION,)
                )
            assert failure.value.code == SourceErrorCode.INVALID_CURSOR
            assert failure.value.__context__ is None
            assert "canary" not in str(failure.value)
        finally:
            await adapter.close()

    asyncio.run(run())


def request(**changes):
    return replace(
        HistoricalBarsRequest(
            ID,
            START,
            END,
            symbol="SPY",
            feed="iex",
            interval=BarInterval.FIFTEEN_MINUTES,
            adjustment=Adjustment.RAW,
        ),
        **changes,
    )


def payload(**changes):
    bar = {
        "t": "2026-10-23T13:30:00Z",
        "o": "10.01",
        "h": "12",
        "l": "9",
        "c": "11.25",
        "v": "123.125",
    }
    bar.update(changes)
    return {"bars": {"SPY": [bar]}, "next_page_token": None}


def source(handler, **changes):
    values = dict(
        api_key=KEY,
        api_secret=SECRET,
        enabled=True,
        policy=POLICY,
        transport=httpx.MockTransport(handler),
        clock=lambda: OBSERVED,
    )
    values.update(changes)
    return AlpacaHistorySource(**values)


def run(coroutine):
    # A selector loop avoids Windows IOCP initialization; MockTransport uses no real sockets.
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine)


@pytest.mark.parametrize(
    "changes,code,state",
    [
        ({"enabled": False}, SourceErrorCode.UNAVAILABLE, SourceStatus.DISABLED),
        (
            {"policy": ProviderPolicy(LicenseStatus.UNKNOWN, None)},
            SourceErrorCode.BLOCKED_LICENSE,
            SourceStatus.BLOCKED,
        ),
        (
            {"policy": ProviderPolicy(LicenseStatus.BLOCKED, "v1")},
            SourceErrorCode.BLOCKED_LICENSE,
            SourceStatus.BLOCKED,
        ),
        (
            {"policy": ProviderPolicy(LicenseStatus.CONFIRMED, None)},
            SourceErrorCode.BLOCKED_LICENSE,
            SourceStatus.BLOCKED,
        ),
        ({"api_key": None}, SourceErrorCode.BLOCKED_CREDENTIALS, SourceStatus.BLOCKED),
    ],
)
def test_policy_and_credentials_block_before_any_io(changes, code, state):
    calls = []
    adapter = source(lambda req: calls.append(req), **changes)
    assert adapter.capability().status == state
    with pytest.raises(HistorySourceError) as failure:
        adapter.fetch_page(request(), sessions=(SESSION,)).send(None)
    assert failure.value.code == code
    assert calls == []
    assert KEY not in repr(adapter) and SECRET not in repr(adapter)


def test_fixed_host_get_raw_feed_and_decimal_normalization():
    calls = []

    def handler(req):
        calls.append(req)
        assert req.method == "GET"
        assert req.url.scheme == "https" and req.url.host == "data.alpaca.markets"
        assert req.url.path == "/v2/stocks/bars"
        assert dict(req.url.params) == {
            "symbols": "SPY",
            "timeframe": "15Min",
            "start": "2026-10-23T13:30:00Z",
            "end": "2026-10-23T14:00:00Z",
            "limit": "200",
            "feed": "iex",
            "adjustment": "raw",
            "currency": "USD",
            "sort": "asc",
            "asof": "-",
        }
        assert req.headers["Accept-Encoding"] == "identity"
        assert req.headers["APCA-API-KEY-ID"] == KEY
        assert req.extensions["timeout"] == dict(connect=5, read=5, write=5, pool=5)
        return httpx.Response(
            200,
            content=b'{"bars":{"SPY":[{"t":"2026-10-23T13:30:00Z",'
            b'"o":10.01,"h":12,"l":9,"c":11.25,"v":123.125}]},'
            b'"next_page_token":"private-token-canary"}',
        )

    async def scenario():
        adapter = source(handler)
        page = await adapter.fetch_page(request(), sessions=(SESSION,))
        await adapter.close()
        return page

    page = run(scenario())
    assert len(calls) == 1 and len(page.items) == 1
    bar = page.items[0]
    assert bar.open == Decimal("10.01") and bar.volume == Decimal("123.125")
    assert bar.identity.bar_end == START + timedelta(minutes=15)
    assert bar.available_at == bar.ingested_at == OBSERVED
    assert bar.published_at is None
    assert page.next_page_token == "private-token-canary"
    assert "private-token-canary" not in repr(page)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, SourceErrorCode.BLOCKED_ENTITLEMENT),
        (403, SourceErrorCode.BLOCKED_ENTITLEMENT),
        (429, SourceErrorCode.RATE_LIMITED),
        (500, SourceErrorCode.UNAVAILABLE),
        (503, SourceErrorCode.UNAVAILABLE),
        (302, SourceErrorCode.INVALID_RESPONSE),
    ],
)
def test_http_errors_are_redacted_and_redirects_never_followed(status, code):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            status,
            text=SECRET,
            headers={"Location": "https://evil.invalid", "Retry-After": "999999"},
        )

    async def scenario():
        adapter = source(handler)
        try:
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(request(), sessions=(SESSION,))
            return failure.value
        finally:
            await adapter.close()

    error = run(scenario())
    assert error.code == code and len(calls) == 1
    assert SECRET not in str(error) and KEY not in repr(error)
    assert error.__context__ is None
    if status == 429:
        assert error.retry_after == 60


@pytest.mark.parametrize(
    "body,headers,code",
    [
        (b" " * (2 * 1024 * 1024 + 1), {}, SourceErrorCode.RESPONSE_TOO_LARGE),
        (b"{}", {"Content-Length": "999999999"}, SourceErrorCode.RESPONSE_TOO_LARGE),
        (
            gzip.compress(b" " * (3 * 1024 * 1024)),
            {"Content-Encoding": "gzip"},
            SourceErrorCode.INVALID_RESPONSE,
        ),
        (b'{"bars":{},"bars":{}}', {}, SourceErrorCode.INVALID_RESPONSE),
        (b'{"bars":{"SPY":[]},"n":NaN}', {}, SourceErrorCode.INVALID_RESPONSE),
        (b'{"bars":{"SPY":[]},"n":Infinity}', {}, SourceErrorCode.INVALID_RESPONSE),
        (b"\xff", {}, SourceErrorCode.INVALID_RESPONSE),
    ],
    ids=[
        "stream-limit",
        "declared-limit",
        "compressed-bomb",
        "duplicate-json-key",
        "nan",
        "infinity",
        "invalid-unicode",
    ],
)
def test_response_size_compression_and_json_fail_closed(body, headers, code):
    async def scenario():
        adapter = source(lambda req: httpx.Response(200, content=body, headers=headers))
        try:
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(request(), sessions=(SESSION,))
            return failure.value
        finally:
            await adapter.close()

    error = run(scenario())
    assert error.code == code and error.__context__ is None


@pytest.mark.parametrize(
    "changes",
    [
        {"h": "8"},
        {"v": "-1"},
        {"c": "NaN"},
        {"c": "1e3"},
        {"t": "2026-10-23T13:30:00"},
        {"t": "2026-10-23T13:31:00Z"},
        {"t": "2026-10-23T13:30:00.1234567Z"},
    ],
)
def test_invalid_values_and_timestamps_are_rejected(changes):
    async def scenario():
        adapter = source(lambda req: httpx.Response(200, json=payload(**changes)))
        try:
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(request(), sessions=(SESSION,))
            assert failure.value.code == SourceErrorCode.INVALID_RESPONSE
        finally:
            await adapter.close()

    run(scenario())


@pytest.mark.parametrize("token", ["a&api_key=canary", "a\ncanary", "a" * 1025, "https://bad"])
def test_private_tokens_cannot_add_transport_parameters(token):
    calls = []
    adapter = source(lambda req: calls.append(req))
    with pytest.raises(HistorySourceError) as failure:
        adapter.fetch_page(request(), token, sessions=(SESSION,)).send(None)
    assert failure.value.code == SourceErrorCode.INVALID_CURSOR and not calls


def test_missing_calendar_and_unsupported_symbol_block_before_io():
    adapter = source(lambda req: pytest.fail("unexpected provider I/O"))
    for req, sessions in [(request(), ()), (request(symbol="AAPL"), (SESSION,))]:
        with pytest.raises(HistorySourceError) as failure:
            adapter.fetch_page(req, sessions=sessions).send(None)
        assert failure.value.code == SourceErrorCode.INVALID_REQUEST


def test_end_boundary_is_locally_half_open_and_pagination_cycles_fail():
    data = payload(t="2026-10-23T14:00:00Z")

    async def scenario():
        adapter = source(lambda req: httpx.Response(200, json=data))
        try:
            assert (await adapter.fetch_page(request(), sessions=(SESSION,))).items == ()
            data["next_page_token"] = "repeated"
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(request(), "repeated", sessions=(SESSION,))
            assert failure.value.code == SourceErrorCode.PAGINATION_CYCLE
        finally:
            await adapter.close()

    run(scenario())


def test_calendar_dst_early_close_empty_coverage_and_daily_event_time():
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.host == "paper-api.alpaca.markets":
            assert req.method == "GET" and req.url.path == "/v2/calendar"
            return httpx.Response(
                200,
                json=[
                    {"date": "2026-03-06", "open": "09:30", "close": "16:00"},
                    {"date": "2026-03-09", "open": "09:30", "close": "13:00"},
                ],
            )
        return httpx.Response(200, json=payload(t="2026-03-09T04:00:00Z"))

    async def scenario():
        adapter = source(handler)
        try:
            calendar = await adapter.fetch_calendar(date(2026, 3, 6), date(2026, 3, 9))
            assert calendar.coverage_known
            before, after = calendar.sessions
            assert before.opens_at.hour == 14 and after.opens_at.hour == 13
            assert after.closes_at == datetime(2026, 3, 9, 17, tzinfo=UTC)
            daily = request(
                start=datetime(2026, 3, 9, tzinfo=UTC),
                end=datetime(2026, 3, 10, tzinfo=UTC),
                interval=BarInterval.ONE_DAY,
            )
            page = await adapter.fetch_page(daily, sessions=calendar.sessions)
            assert page.items[0].identity.bar_end == after.closes_at
            assert page.items[0].identity.bar_start == after.opens_at
            assert page.items[0].identity.provider == "alpaca"
        finally:
            await adapter.close()
        empty = source(lambda req: httpx.Response(200, json=[]))
        try:
            assert not (
                await empty.fetch_calendar(date(2030, 1, 1), date(2030, 1, 2))
            ).coverage_known
        finally:
            await empty.close()

    run(scenario())


def test_httpx_timeout_does_not_retain_private_exception_context():
    def handler(req):
        raise httpx.ReadTimeout(SECRET, request=req)

    async def scenario():
        adapter = source(handler)
        try:
            with pytest.raises(HistorySourceError) as failure:
                await adapter.fetch_page(request(), sessions=(SESSION,))
            assert failure.value.code == SourceErrorCode.TIMEOUT
            assert failure.value.__context__ is None
            assert SECRET not in str(failure.value)
        finally:
            await adapter.close()

    run(scenario())


def test_retry_after_http_date_is_capped_and_extended_hours_are_excluded():
    async def scenario():
        limited = source(
            lambda req: httpx.Response(
                429, headers={"Retry-After": "Sun, 25 Oct 2026 00:02:00 GMT"}
            )
        )
        try:
            with pytest.raises(HistorySourceError) as failure:
                await limited.fetch_page(request(), sessions=(SESSION,))
            assert failure.value.retry_after == 60
        finally:
            await limited.close()
        extended = source(lambda req: httpx.Response(200, json=payload(t="2026-10-23T13:15:00Z")))
        try:
            page = await extended.fetch_page(
                request(start=START - timedelta(hours=1)), sessions=(SESSION,)
            )
            assert page.items == ()
        finally:
            await extended.close()

    run(scenario())
