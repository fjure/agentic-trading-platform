"""Closed, bounded Quant Lab routes shared by the public API and private relay."""

import re
import secrets
from functools import partial
from time import monotonic
from typing import Any

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse
from jsonschema.exceptions import ValidationError
from pydantic import RootModel

from packages.backtesting.jobs import BacktestError
from packages.observability.backtesting import BacktestMetrics
from services.control_api.auth import AuthDenied
from services.control_api.quant_lab_dto import (
    BASE,
    HEX,
    OPERATIONS,
    PHASES,
    ROLES,
    TRACE,
    UUID,
    validator,
)

PROBLEM_CODES = {
    400: {"INVALID_REQUEST", "CURSOR_INVALID"},
    401: {"UNAUTHORIZED"},
    403: {"FORBIDDEN"},
    404: {"NOT_FOUND"},
    409: {
        "IDEMPOTENCY_CONFLICT",
        "DEFINITION_CONFLICT",
        "STATE_CONFLICT",
        "VERSION_UNAVAILABLE",
        "REPORT_NOT_READY",
    },
    422: {"INVALID_PARAMETERS", "INVALID_PROTOCOL", "INVALID_POLICY"},
    429: {"ADMISSION_LIMIT"},
    503: {"UNAVAILABLE"},
}


def quant_lab_path(path):
    return path.startswith(BASE) and path[len(BASE) :].split("/", 1)[0] in {
        "strategies",
        "experiments",
        "experiment-batches",
        "experiment-comparisons",
    }


def quant_lab_problem(status, code, trace):
    if code not in PROBLEM_CODES.get(status, ()):
        status, code = 503, "UNAVAILABLE"
    if not isinstance(trace, str) or not TRACE.fullmatch(trace):
        trace = secrets.token_hex(16)
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = "30"
    return JSONResponse(
        {
            "schemaVersion": "quant-lab-v1",
            "type": "urn:agentic-trading-lab:quant-lab-problem",
            "title": "Quant Lab request failed",
            "status": status,
            "code": code,
            "traceId": trace,
        },
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


def _arguments(request, operation):
    if len(request.scope["query_string"]) > 8192:
        raise ValueError("oversized query")
    params = dict(request.path_params)
    for name, value in params.items():
        matcher = HEX if name == "candidateId" else UUID
        if not matcher.fullmatch(value):
            raise ValueError("invalid identifier")
    values = {}
    for name, value in request.query_params.multi_items():
        if name not in operation.query or name in values and name != "batchId":
            raise ValueError("unexpected query parameter")
        if name == "batchId":
            batch_ids = values.setdefault(name, [])
            if not UUID.fullmatch(value) or value in batch_ids or len(batch_ids) == 4:
                raise ValueError("invalid batch identifiers")
            batch_ids.append(value)
            continue
        if name == "cursor" and not re.fullmatch(r"[A-Za-z0-9_-]{16,1024}", value):
            raise BacktestError(400, "CURSOR_INVALID")
        if name == "limit":
            if not re.fullmatch(r"[1-9][0-9]{0,2}", value) or int(value) > 200:
                raise ValueError("invalid page size")
            value = int(value)
        if name == "fold":
            if value not in {"0", "1", "2", "3"}:
                raise ValueError("invalid fold")
            value = int(value)
        if (
            (name == "phase" and value not in PHASES)
            or (name == "role" and value not in ROLES)
            or (name == "candidateId" and not HEX.fullmatch(value))
            or (name == "validationId" and not UUID.fullmatch(value))
            or (name == "section" and value not in {"FINDINGS", "REVISION_REFERENCES"})
        ):
            raise ValueError("invalid filter")
        values[name] = value
    if operation.query == ("batchId",) and "batchId" not in values:
        raise ValueError("missing batch identifiers")
    if operation.service in {"get_equity", "get_drawdown"} and (
        "phase" not in values or ("fold" in values) == (values["phase"] == "holdout")
    ):
        raise ValueError("invalid curve phase")
    if values.get("phase") == "holdout" and "fold" in values:
        raise ValueError("invalid holdout fold")
    return params, values


def service_handler(service):
    """Adapt the normalized transport request to the private service interface."""
    if service is None:
        return None

    def invoke(operation, params, query, payload, *, actor, key, trace, scope):
        method = getattr(service, operation.service)
        args = [params[name] for name in ("id", "candidateId", "reportId") if name in params]
        if operation.request is not None:
            return method(*args, payload, actor=actor, key=key, trace=trace, scope=scope)
        if operation.query:
            return method(*args, query, scope=scope)
        return method(*args)

    return invoke


def register_quant_lab_routes(app, *, handler, body_reader, key_reader, scope_reader):
    admission = anyio.CapacityLimiter(2)
    workers = anyio.CapacityLimiter(2)
    metrics = BacktestMetrics()
    app.state.quant_lab_metrics = metrics

    def checked_invoke(operation, params, query, payload, **context):
        if handler is None:
            raise BacktestError(503, "UNAVAILABLE")
        if operation.request is not None and not validator(operation.request).is_valid(payload):
            raise BacktestError(400, "INVALID_REQUEST")
        value = handler(operation, params, query, payload, **context)
        try:
            validator(operation.response).validate(value)
        except ValidationError:
            raise BacktestError(503, "UNAVAILABLE") from None
        return value

    def endpoint(operation):
        label = "quant_" + operation.service

        async def route(request: Request):
            trace = request.state.trace_id
            started = monotonic()
            code, rows = None, 0

            def reject(status, error_code):
                nonlocal code
                if (
                    error_code not in PROBLEM_CODES.get(status, ())
                    or operation.request is None
                    and status in {422, 429}
                ):
                    status, error_code = 503, "UNAVAILABLE"
                code = error_code
                return quant_lab_problem(status, code, trace)

            try:
                admission.acquire_nowait()
            except anyio.WouldBlock:
                metrics.observe(label, 0, code="UNAVAILABLE", trace=trace)
                return quant_lab_problem(503, "UNAVAILABLE", trace)
            try:
                try:
                    params, query = _arguments(request, operation)
                except BacktestError:
                    raise
                except ValueError:
                    raise BacktestError(400, "INVALID_REQUEST") from None
                scope = scope_reader(request)
                if not isinstance(scope, str) or not HEX.fullmatch(scope):
                    raise BacktestError(403, "FORBIDDEN")
                payload, key = None, None
                if operation.request is not None:
                    types = request.headers.getlist("content-type")
                    if len(types) != 1 or [
                        item.strip().lower() for item in types[0].split(";")
                    ] not in (["application/json"], ["application/json", "charset=utf-8"]):
                        raise BacktestError(400, "INVALID_REQUEST")
                    try:
                        key = key_reader(request)
                    except ValueError:
                        raise BacktestError(400, "INVALID_REQUEST") from None
                    payload = await body_reader(request, RootModel[dict[str, Any]])
                context = dict(actor=request.state.actor_id, key=key, trace=trace, scope=scope)
                action = partial(checked_invoke, operation, params, query, payload, **context)
                result = await anyio.to_thread.run_sync(action, limiter=workers)
                page = (
                    result.get("items")
                    or result.get("findings")
                    or result.get("revisionReferences")
                )
                rows = min(len(page), 200) if page is not None else 0
                return JSONResponse(result, status_code=operation.status)
            except (AuthDenied, BacktestError) as error:
                return reject(error.status, error.code)
            except Exception:
                return reject(503, "UNAVAILABLE")
            finally:
                admission.release()
                metrics.observe(
                    label, min(monotonic() - started, 86400), rows=rows, code=code, trace=trace
                )

        return route

    for operation_id, operation in OPERATIONS.items():
        app.add_api_route(
            BASE + operation.path,
            endpoint(operation),
            methods=[operation.method],
            operation_id=operation_id,
        )
