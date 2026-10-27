"""Drei begrenzte authentifizierte Diagnoseoperationen hinter der lokalen Grenze."""

import json
import re
import secrets
from contextvars import ContextVar

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse

from packages.backtesting.readiness_jobs import ReadinessError
from services.control_api.auth import AuthDenied
from services.control_api.backtest_dto import BacktestRequest
from services.control_api.backtest_service import identity, pairs
from services.control_api.readiness_dto import RESPONSE_MODELS, ReadinessProblem
from services.control_api.readiness_service import BASE, CATEGORIES

CURRENT_TRACE = ContextVar("readiness_trace", default=None)
MAX_HTTP_BYTES = 16 * 1024 * 1024


def readiness_problem(status, code, trace):
    allowed = {
        400: {"INVALID_REQUEST", "CURSOR_INVALID"},
        401: {"UNAUTHORIZED"},
        403: {"FORBIDDEN"},
        404: {"NOT_FOUND"},
        409: {"IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT", "REPORT_NOT_READY"},
        413: {"PAYLOAD_TOO_LARGE"},
        429: {"QUEUE_EXHAUSTED"},
        503: {"UNAVAILABLE"},
    }
    if status not in allowed or code not in allowed[status]:
        status, code = 503, "UNAVAILABLE"
    if not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace):
        trace = secrets.token_hex(16)
    value = ReadinessProblem(
        type="urn:agentic-trading-lab:readiness-problem",
        title="Research data request failed",
        status=status,
        code=code,
        traceId=trace,
    )
    return JSONResponse(
        value.model_dump(), status_code=status, media_type="application/problem+json"
    )


def query(request, *, paged=False):
    values = {}
    allowed = {"limit", "cursor", "category"} if paged else set()
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in values or len(value) > 2048:
            raise ReadinessError(400, "INVALID_REQUEST")
        values[key] = value
    if "limit" in values and (
        not re.fullmatch(r"[1-9][0-9]{0,2}", values["limit"]) or int(values["limit"]) > 200
    ):
        raise ReadinessError(400, "INVALID_REQUEST")
    if "category" in values and values["category"] not in CATEGORIES:
        raise ReadinessError(400, "INVALID_REQUEST")
    if "cursor" in values and (
        not 16 <= len(values["cursor"]) <= 2048
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", values["cursor"])
    ):
        raise ReadinessError(400, "CURSOR_INVALID")
    return values


async def request_body(request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise ReadinessError(400, "INVALID_REQUEST")
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1:
        raise ReadinessError(400, "INVALID_REQUEST")
    if lengths:
        if not re.fullmatch(r"[0-9]+", lengths[0]):
            raise ReadinessError(400, "INVALID_REQUEST")
        if int(lengths[0]) > MAX_HTTP_BYTES:
            raise ReadinessError(413, "PAYLOAD_TOO_LARGE")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_HTTP_BYTES:
            raise ReadinessError(413, "PAYLOAD_TOO_LARGE")
    try:

        def reject_constant(_):
            raise ValueError("finite JSON required")

        value = json.loads(body, object_pairs_hook=pairs, parse_constant=reject_constant)
        return BacktestRequest.model_validate(value).model_dump()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ReadinessError(400, "INVALID_REQUEST") from None


def register_readiness_routes(app, *, readiness, body_reader, key_reader):
    del body_reader  # Existing helper is limited to16KiB; this contract allows16MiB.
    admission, threads = anyio.CapacityLimiter(2), anyio.CapacityLimiter(2)

    async def execute(request, method, *args, accepted=False):
        trace = request.state.trace_id
        if getattr(request.state, "actor_id", None) != "local-operator":
            return readiness_problem(401, "UNAUTHORIZED", trace)
        try:
            admission.acquire_nowait()
        except anyio.WouldBlock:
            return readiness_problem(503, "UNAVAILABLE", trace)
        token = CURRENT_TRACE.set(trace)
        try:
            if readiness is None:
                raise ReadinessError(503, "UNAVAILABLE")
            value = await anyio.to_thread.run_sync(
                getattr(readiness, method), *args, limiter=threads
            )
            try:
                value = RESPONSE_MODELS[method].model_validate(value).model_dump()
                if len(json.dumps(value, separators=(",", ":")).encode()) > MAX_HTTP_BYTES:
                    raise ValueError("bounded response required")
            except (ValueError, TypeError):
                raise ReadinessError(503, "UNAVAILABLE") from None
            return JSONResponse(value, status_code=202 if accepted else 200)
        except (ReadinessError, AuthDenied) as error:
            return readiness_problem(error.status, error.code, trace)
        except (ValueError, TypeError, KeyError):
            return readiness_problem(400, "INVALID_REQUEST", trace)
        except Exception:
            return readiness_problem(503, "UNAVAILABLE", trace)
        finally:
            CURRENT_TRACE.reset(token)
            admission.release()

    @app.post(BASE, operation_id="createReadinessAssessment")
    async def create(request: Request):
        try:
            query(request)
            key = key_reader(request)
            value = await request_body(request)
        except (ValueError, AuthDenied) as error:
            return readiness_problem(
                getattr(error, "status", 400),
                getattr(error, "code", "INVALID_REQUEST"),
                request.state.trace_id,
            )
        return await execute(
            request,
            "create_assessment",
            value,
            request.state.actor_id,
            key,
            request.state.trace_id,
            accepted=True,
        )

    @app.get(BASE + "/{assessmentId}", operation_id="getReadinessAssessment")
    async def detail(request: Request):
        try:
            query(request)
            identifier = request.path_params["assessmentId"]
            identity(identifier)
        except (ValueError, TypeError) as error:
            return readiness_problem(
                getattr(error, "status", 400),
                getattr(error, "code", "INVALID_REQUEST"),
                request.state.trace_id,
            )
        return await execute(request, "read_assessment", identifier)

    @app.get(BASE + "/{assessmentId}/findings", operation_id="listReadinessFindings")
    async def findings(request: Request):
        try:
            values = query(request, paged=True)
            identifier = request.path_params["assessmentId"]
            identity(identifier)
        except (ValueError, TypeError) as error:
            return readiness_problem(
                getattr(error, "status", 400),
                getattr(error, "code", "INVALID_REQUEST"),
                request.state.trace_id,
            )
        return await execute(request, "read_findings", identifier, values)
