"""Authenticated, read-only portfolio projection and snapshot-bound pagination."""

import hmac
import json
import re
import secrets
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.control_api.auth import AuthDenied
from services.control_api.investment_portfolio import NAMESPACES, project

PORTFOLIO_PATH = "/api/v1/experience/portfolio"
_DEMO_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_LIMIT = re.compile(r"(?:[1-9][0-9]?|100)")
_CURSOR = re.compile(r"([0-9]{1,12})\.([0-9]{1,10})\.([a-f0-9]{64})\.([a-f0-9]{64})")
_CURSOR_TTL = 300
_MESSAGES = {
    401: ("SESSION_REQUIRED", "Lokale Sitzung erforderlich"),
    403: ("REQUEST_FORBIDDEN", "Anfrage abgelehnt"),
    404: ("RESOURCE_NOT_FOUND", "Portfolioquelle nicht verfügbar"),
    409: ("CURSOR_EXPIRED", "Portfolio hat sich geändert. Bitte neu laden."),
    422: ("INVALID_REQUEST", "Ungültige Portfolioanfrage"),
    429: ("CAPABILITY_UNAVAILABLE", "Zu viele Anfragen"),
    503: ("CAPABILITY_UNAVAILABLE", "Portfolio derzeit nicht verfügbar"),
}


def portfolio_problem(status: int, trace: str) -> JSONResponse:
    status = 422 if status == 400 else status
    status = status if status in _MESSAGES else 503
    code, title = _MESSAGES[status]
    return JSONResponse(
        {"type": f"urn:atl:experience:{code.lower()}", "title": title, "status": status,
         "code": code, "traceId": trace},
        status_code=status, media_type="application/problem+json",
    )


class PortfolioPages:
    def __init__(self):
        self._key = secrets.token_bytes(32)

    def _signature(self, binding: tuple, payload: str) -> str:
        message = json.dumps([binding, payload], ensure_ascii=True, separators=(",", ":"))
        return hmac.digest(self._key, message.encode("ascii"), "sha256").hex()

    def cursor(self, binding: tuple, offset: int, digest: str) -> str:
        payload = f"{int(time.time()) + _CURSOR_TTL}.{offset}.{digest}"
        return f"{payload}.{self._signature(binding, payload)}"

    def read(self, binding: tuple, token: str) -> tuple[int, str]:
        match = _CURSOR.fullmatch(token)
        if match is None:
            raise AuthDenied(409, "CURSOR_EXPIRED")
        expires, offset, digest, signature = match.groups()
        if not hmac.compare_digest(signature, self._signature(binding, token.rsplit(".", 1)[0])):
            raise AuthDenied(409, "CURSOR_EXPIRED")
        if int(expires) <= int(time.time()):
            raise AuthDenied(409, "CURSOR_EXPIRED")
        return int(offset), digest


def _query(request: Request):
    items = request.query_params.multi_items()
    query = dict(items)
    if len(items) != len(query) or query.keys() - {"context", "demoRunId", "limit", "cursor"}:
        raise AuthDenied(422, "INVALID_REQUEST")
    context = query.get("context")
    demo_id = query.get("demoRunId")
    raw_limit = query.get("limit", "20")
    cursor = query.get("cursor")
    if (
        context not in NAMESPACES
        or (context == "DEMO") != (demo_id is not None)
        or (demo_id is not None and not _DEMO_ID.fullmatch(demo_id))
        or not _LIMIT.fullmatch(raw_limit)
        or (cursor is not None and not 1 <= len(cursor) <= 1024)
    ):
        raise AuthDenied(422, "INVALID_REQUEST")
    return context, demo_id, int(raw_limit), cursor


def register_portfolio_route(app, *, fake_reader, demo, paper):
    from services.control_api.investment_portfolio_local import (
        read_demo_portfolio,
        read_fake_portfolio,
    )
    from services.control_api.investment_portfolio_paper import read_paper_portfolio

    pages = PortfolioPages()

    def read(context, demo_id, actor, trace):
        if context == "DEMO" and demo is not None:
            return read_demo_portfolio(demo, demo_id, actor, trace)
        if context == "FAKE" and fake_reader is not None:
            return read_fake_portfolio(fake_reader, trace)
        if context == "ALPACA_PAPER" and paper is not None:
            return read_paper_portfolio(paper, trace)
        return {
            "snapshot_id": None, "revision": None, "generation": None, "as_of": None,
            "quality": "UNAVAILABLE", "currency": "USD", "cash": None, "equity": None,
            "exposure": None, "positions": None, "blockers": ["CAPABILITY_UNAVAILABLE"],
            "reconciliation": "UNKNOWN" if context == "ALPACA_PAPER" else "NOT_APPLICABLE",
        }

    @app.get(PORTFOLIO_PATH, operation_id="getInvestmentPortfolio")
    async def read_portfolio(request: Request):
        context, demo_id, limit, cursor = _query(request)
        actor = request.state.actor_id
        binding = (actor, context, demo_id, limit)
        offset, expected = pages.read(binding, cursor) if cursor is not None else (0, None)
        try:
            source = await run_in_threadpool(read, context, demo_id, actor, request.state.trace_id)
        except LookupError:
            raise AuthDenied(404, "RESOURCE_NOT_FOUND") from None
        view, digest, count = project(source, context, demo_id, offset, limit)
        if expected is not None and (digest != expected or count is None or offset >= count):
            raise AuthDenied(409, "CURSOR_EXPIRED")
        next_offset = offset + limit
        view["nextCursor"] = (
            pages.cursor(binding, next_offset, digest)
            if count is not None and next_offset < count
            else None
        )
        return JSONResponse(view)
