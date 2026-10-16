"""SSE-Framing ohne Datenbanktransaktion über eine Netzwerkpause hinweg."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Annotated

from pydantic import Field
from starlette.concurrency import run_in_threadpool

from services.control_api.app import DTO, Stamp, Trace, problem
from services.control_api.auth import AuthDenied, LocalAuth
from services.control_api.cursors import CursorExpired

Cursor = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$")]


class PublicEvent(DTO):
    eventId: str
    eventType: Annotated[str, Field(pattern=r"^[a-z]+\.[a-z]+\.v[1-9][0-9]*$")]
    occurredAt: Stamp
    aggregateId: str
    aggregateVersion: Annotated[int, Field(ge=1)]
    traceId: Trace
    cursor: Cursor
    payload: dict


def frame(event: dict) -> bytes:
    value = PublicEvent.model_validate(event).model_dump()
    data = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return f"id: {value['cursor']}\nevent: {value['eventType']}\ndata: {data}\n\n".encode()


async def event_stream(
    request,
    feed,
    cursor: str,
    initial: tuple[list[dict], str],
    auth: LocalAuth,
    token: str,
    trace_id: str,
    *,
    sleep: Callable = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    heartbeat_seconds: float = 15,
    poll_seconds: float = 1,
) -> AsyncIterator[bytes]:
    """Gaps reset explicitly; dependency failure or revoked session closes the stream."""
    batch, cursor = initial
    last_frame = monotonic()
    pending = None
    try:
        while True:
            try:
                auth.authenticate(token)
                if await request.is_disconnected():
                    return
                if batch:
                    for item in batch:
                        auth.authenticate(token)
                        encoded = frame(item)
                        yield encoded
                        last_frame = monotonic()
                        await sleep(0)
                elif monotonic() - last_frame >= heartbeat_seconds:
                    yield b": heartbeat\n\n"
                    last_frame = monotonic()
                await sleep(poll_seconds)
                pending = asyncio.create_task(run_in_threadpool(feed.poll, cursor))
                while not pending.done():
                    auth.authenticate(token)
                    if await request.is_disconnected():
                        return
                    remaining = heartbeat_seconds - (monotonic() - last_frame)
                    if remaining <= 0:
                        yield b": heartbeat\n\n"
                        last_frame = monotonic()
                        remaining = heartbeat_seconds
                    await asyncio.wait({pending}, timeout=min(1, remaining))
                completed, pending = pending, None
                batch, cursor = completed.result()
            except CursorExpired:
                payload = problem(410, "CURSOR_EXPIRED", trace_id).body
                yield b"event: reset-required\ndata: " + payload + b"\n\n"
                return
            except AuthDenied:
                return
            except Exception:
                # No sensitive exception or guessed state after headers were sent.
                return
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await pending
