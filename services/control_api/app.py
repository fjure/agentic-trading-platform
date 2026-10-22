"""Lokaler HTTP-Transport; der kanonische Vertrag liegt unter contracts/http.

Keine Brokerfactory, Credentials oder implizite Datenbankkonfiguration. Ein Launcher
injiziert Auth und den persistenten Backendport; ohne Backend bleibt alles gesperrt.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Annotated, Literal, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from services.control_api.auth import AuthDenied, LocalAuth
from services.control_api.cursors import CursorExpired

_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")
_TRACE = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\Z")
_CODES = frozenset(
    {
        "OK",
        "ABSTAIN_MISSING_EVIDENCE",
        "ABSTAIN_INVALID_RESPONSE",
        "ABSTAIN_UNAVAILABLE",
        "RISK_STALE_MARKET",
        "RISK_SESSION_CLOSED",
        "RISK_POLICY_INCOMPLETE",
        "RISK_LIMIT_EXCEEDED",
        "RISK_CAPABILITY_UNCONFIRMED",
        "RISK_PROPOSAL_EXPIRED",
        "RISK_RECONCILIATION_REQUIRED",
        "MODE_NOT_PAPER",
        "PAUSED_NEW",
        "KILL_SWITCH_ACTIVE",
        "HEALTH_CHECK_FAILED",
        "BROKER_STATUS_UNKNOWN",
        "TEMPORARILY_UNAVAILABLE",
        "VERSION_CONFLICT",
        "PRECONDITION_REQUIRED",
        "IDEMPOTENCY_CONFLICT",
        "CURSOR_EXPIRED",
        "INVALID_REQUEST",
    }
)
Fraction = Annotated[str, Field(pattern=r"^(0(\.[0-9]{1,18})?|1(\.0{1,18})?)$")]
Stamp = Annotated[str, Field(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")]
Trace = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Activate(DTO):
    activationCode: Annotated[str, Field(min_length=32, max_length=256)]


class ControlReason(DTO):
    reason: Reason


class SetMode(ControlReason):
    mode: Literal["DISABLED", "REPLAY", "SHADOW", "PAPER"]


class PolicyUpdate(DTO):
    maxPositionsPerInstrument: Annotated[int, Field(ge=1)]
    maxOpenPositions: Annotated[int, Field(ge=1)]
    maxPositionEquityFraction: Fraction
    maxDailyLossFraction: Fraction


class Policy(PolicyUpdate):
    version: Annotated[int, Field(ge=1)]
    simulationOnly: Literal[True]
    effectiveAt: Stamp
    changedAt: Stamp
    changedBy: str
    auditEventId: str


class Mode(DTO):
    mode: Literal["DISABLED", "REPLAY", "SHADOW", "PAPER"]
    pauseNew: bool
    killSwitch: bool
    health: Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"]
    reconciliation: Literal["CURRENT", "PENDING", "MISMATCH"]
    changedAt: Stamp
    traceId: Trace


class RuntimeHealth(DTO):
    status: Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"]
    checks: dict[
        Literal["DATABASE", "BROKER", "MARKET", "DECISION", "EXECUTION", "WATCHDOG"],
        Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"],
    ] = Field(min_length=1)


class Backend(Protocol):
    def read_mode(self, trace_id: str) -> dict: ...
    def read_policy(self) -> tuple[dict, str]: ...
    def mutate(
        self,
        operation: str,
        payload: dict,
        actor_id: str,
        key: str,
        trace_id: str,
        if_match: str | None = None,
    ) -> tuple[int, dict, dict]: ...


def _trace(request: Request) -> str:
    values = request.headers.getlist("traceparent")
    match = _TRACE.fullmatch(values[0]) if len(values) == 1 else None
    if match and match[1] != "0" * 32 and match[2] != "0" * 16:
        return match[1]
    return secrets.token_hex(16)


def problem(status: int, code: str, trace_id: str) -> JSONResponse:
    code = code if code in _CODES else "TEMPORARILY_UNAVAILABLE"
    if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        trace_id = secrets.token_hex(16)
    slug, title = {
        "HEALTH_CHECK_FAILED": ("control-refused", "Control action refused"),
        "KILL_SWITCH_ACTIVE": ("control-refused", "Control action refused"),
        "IDEMPOTENCY_CONFLICT": (
            "idempotency-conflict",
            "Idempotency key reused with different payload",
        ),
        "VERSION_CONFLICT": ("version-conflict", "Policy version conflict"),
        "PRECONDITION_REQUIRED": ("precondition-required", "If-Match required"),
        "TEMPORARILY_UNAVAILABLE": ("temporarily-unavailable", "Dependency unavailable"),
    }.get(code, (code.lower().replace("_", "-"), code.replace("_", " ").capitalize()))
    return JSONResponse(
        {
            "type": "https://agentic-trading-lab.invalid/problems/" + slug,
            "title": title,
            "status": status,
            "code": code,
            "traceId": trace_id,
        },
        status_code=status,
        media_type="application/problem+json",
        headers={"Retry-After": "1"} if status == 503 else None,
    )


def _header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) > 1:
        raise AuthDenied(400, "INVALID_REQUEST")
    return values[0] if values else None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


async def _body(request: Request, model: type[DTO]) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise AuthDenied(400, "INVALID_REQUEST")
    try:
        data = json.loads(
            body,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        return model.model_validate(data).model_dump()
    except (ValueError, RecursionError, ValidationError):
        raise AuthDenied(400, "INVALID_REQUEST") from None


def create_app(
    *,
    auth: LocalAuth | None = None,
    backend: Backend | None = None,
    supervisor=None,
    events=None,
    health=None,
    delivery_observer=None,
    demo=None,
    setup=None,
    trading_profile=None,
    paper=None,
) -> FastAPI:
    from services.control_api.paper_commands import PaperCommandError
    from services.control_api.paper_routes import paper_problem, register_paper_routes

    auth = auth if auth is not None else LocalAuth()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    def public_problem(request, status, code):
        if request.url.path.startswith("/api/v1/paper/"):
            if status == 401:
                code = "AUTHENTICATION_REQUIRED"
            elif status == 400 and request.headers.getlist("origin") not in ([], [auth.origin]):
                status, code = 403, "ORIGIN_REJECTED"
            return paper_problem(status, code, request.state.trace_id)
        return problem(status, code, request.state.trace_id)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.trace_id = _trace(request)
        try:
            auth.guard(request, write=request.method not in {"GET", "HEAD"})
            operational_probe = (
                request.method == "GET" and request.url.path in {"/health", "/ready"}
            )
            if not operational_probe and (
                request.url.path != "/api/v1/session" or request.method != "POST"
            ):
                request.state.actor_id = auth.authenticate(request.cookies.get("atl_session", ""))
            response = await call_next(request)
        except AuthDenied as exc:
            response = public_problem(request, exc.status, exc.code)
        except Exception:
            response = public_problem(request, 503, "TEMPORARILY_UNAVAILABLE")
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers.setdefault(
            "traceparent", "00-" + request.state.trace_id + "-" + secrets.token_hex(8) + "-00"
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/v1/paper/"):
            return public_problem(request, 400, "INVALID_REQUEST")
        return problem(exc.status_code, "INVALID_REQUEST", request.state.trace_id)

    @app.exception_handler(CursorExpired)
    async def cursor_error(request: Request, _exc: CursorExpired):
        return problem(410, "CURSOR_EXPIRED", request.state.trace_id)

    @app.exception_handler(PaperCommandError)
    async def paper_command_error(request: Request, exc: PaperCommandError):
        return public_problem(request, exc.status, exc.code)

    def port() -> Backend:
        if backend is None:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
        return backend

    async def health_value() -> dict:
        try:
            value = await run_in_threadpool(health) if health is not None else {}
            result = RuntimeHealth.model_validate(value).model_dump()
            if "DATABASE" not in result["checks"]:
                raise ValueError
            return result
        except Exception:
            return {"status": "UNAVAILABLE", "checks": {"DATABASE": "UNAVAILABLE"}}

    @app.get("/health", operation_id="getRuntimeHealth")
    async def runtime_health():
        value = await health_value()
        # This endpoint proves process liveness, never a trading permission.
        return {"status": "HEALTHY", "checks": value["checks"]}

    @app.get("/ready", operation_id="getRuntimeReadiness")
    async def runtime_readiness():
        value = await health_value()
        ready = value["checks"]["DATABASE"] == "HEALTHY"
        return JSONResponse(value, status_code=200 if ready else 503)

    def key(request: Request) -> str:
        value = _header(request, "Idempotency-Key")
        if value is None or not _KEY.fullmatch(value):
            raise AuthDenied(400, "INVALID_REQUEST")
        return value

    @app.post("/api/v1/session", operation_id="activateLocalSession")
    async def activate(request: Request):
        idem = key(request)
        data = await _body(request, Activate)
        token = auth.activate(data["activationCode"], idem)
        response = Response(status_code=204)
        auth.set_cookie(response, token)
        return response

    @app.get("/api/v1/mode", operation_id="getMode")
    async def get_mode(request: Request):
        body = await run_in_threadpool(port().read_mode, request.state.trace_id)
        return JSONResponse(Mode.model_validate(body).model_dump())

    @app.get("/api/v1/risk-policy", operation_id="getRiskPolicy")
    async def get_policy():
        body, etag = await run_in_threadpool(port().read_policy)
        if not re.fullmatch(r'"[A-Za-z0-9._:-]+"', etag):
            raise ValueError("invalid internal etag")
        return JSONResponse(Policy.model_validate(body).model_dump(), headers={"ETag": etag})

    def register(path: str, method: str, operation: str, model: type[DTO]):
        async def mutate(request: Request):
            idem = key(request)
            data = await _body(request, model)
            match = _header(request, "If-Match")
            status, body, headers = await run_in_threadpool(
                port().mutate,
                operation,
                data,
                request.state.actor_id,
                idem,
                request.state.trace_id,
                match,
            )
            if status != 200:
                if status not in {400, 409, 412, 428, 503}:
                    raise ValueError("invalid internal status")
                return problem(
                    status,
                    body.get("code", "TEMPORARILY_UNAVAILABLE"),
                    body.get("traceId", request.state.trace_id),
                )
            result_model = Policy if operation == "updateRiskPolicy" else Mode
            value = result_model.model_validate(body).model_dump()
            safe_headers = {}
            if "ETag" in headers:
                if not re.fullmatch(r'"[A-Za-z0-9._:-]+"', headers["ETag"]):
                    raise ValueError("invalid internal etag")
                safe_headers["ETag"] = headers["ETag"]
            return JSONResponse(value, headers=safe_headers)

        app.add_api_route(path, mutate, methods=[method], operation_id=operation)

    register("/api/v1/mode", "PUT", "setMode", SetMode)
    register("/api/v1/control/pause", "POST", "pauseNewEntries", ControlReason)
    register("/api/v1/control/resume", "POST", "resumeNewEntries", ControlReason)
    register("/api/v1/control/kill", "POST", "activateKillSwitch", ControlReason)
    register("/api/v1/risk-policy", "PUT", "updateRiskPolicy", PolicyUpdate)

    def query(request: Request, name: str) -> str | None:
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise AuthDenied(400)
        return values[0] if values else None

    def read_port():
        if supervisor is None:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
        return supervisor

    @app.get("/api/v1/snapshot", operation_id="getSupervisorSnapshot")
    async def snapshot(request: Request):
        body = await run_in_threadpool(read_port().snapshot, request.state.trace_id)
        return JSONResponse(body)

    @app.get("/api/v1/events", operation_id="streamSupervisorEvents")
    async def stream(request: Request):
        from fastapi.responses import StreamingResponse

        from services.control_api.streaming import event_stream, frame

        if events is None:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
        header = _header(request, "Last-Event-ID")
        supplied = query(request, "cursor")
        selected = header if header is not None else supplied
        if selected is not None and not 1 <= len(selected) <= 256:
            raise AuthDenied(400)
        prepared = await run_in_threadpool(events.prepare, selected)
        initial = await run_in_threadpool(events.poll, prepared)
        # Validate framing before opening HTTP 200; no header injection or NaN.
        for item in initial[0]:
            frame(item)
        return StreamingResponse(
            event_stream(
                request,
                events,
                prepared,
                initial,
                auth,
                request.cookies.get("atl_session", ""),
                request.state.trace_id,
                delivery_observer=delivery_observer,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    def register_history(resource: str, operation: str):
        async def page(request: Request):
            supplied = query(request, "cursor")
            if supplied is not None and not 1 <= len(supplied) <= 256:
                raise AuthDenied(400)
            raw_limit = query(request, "limit")
            if raw_limit is not None and (
                not re.fullmatch(r"[0-9]{1,3}", raw_limit) or not 1 <= int(raw_limit) <= 200
            ):
                raise AuthDenied(400)
            trace_filter = query(request, "traceId") if resource == "audit" else None
            if trace_filter is not None and not re.fullmatch(r"[0-9a-f]{32}", trace_filter):
                raise AuthDenied(400)
            body = await run_in_threadpool(
                read_port().page,
                resource,
                supplied,
                int(raw_limit) if raw_limit is not None else 50,
                trace_filter,
            )
            return JSONResponse(body)

        app.add_api_route("/api/v1/" + resource, page, methods=["GET"], operation_id=operation)

    for resource, operation in (
        ("market-states", "listMarketStates"),
        ("decisions", "listDecisions"),
        ("proposals", "listProposals"),
        ("risk-decisions", "listRiskDecisions"),
        ("orders", "listOrders"),
        ("audit", "listAuditEvents"),
        ("runs", "listAgentRuns"),
        ("costs", "listApiCosts"),
    ):
        register_history(resource, operation)
    from services.control_api.extension_routes import register_extensions

    register_extensions(app, body_reader=_body, key_reader=key, demo=demo,
                        setup=setup, trading_profile=trading_profile, paper=paper)
    register_paper_routes(app, paper=paper, body_reader=_body, key_reader=key)
    return app


app = create_app()
