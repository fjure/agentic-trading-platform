"""Authenticated bounded transport for isolated persistent local simulations."""

import re
import secrets
from contextvars import ContextVar
from time import monotonic

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse

from packages.backtesting.jobs import BacktestError
from packages.observability.backtesting import CODES, BacktestMetrics
from services.control_api.auth import AuthDenied
from services.control_api.backtest_dto import RESPONSE_MODELS, BacktestProblem, BacktestRequest
from services.control_api.backtest_service import STATES, identity

CURRENT_TRACE = ContextVar("backtest_trace", default=None)
BASE = "/api/v1/research/backtests"


def backtest_problem(status, code, trace):
    allowed = {
        400: {"INVALID_REQUEST", "CURSOR_INVALID"},
        401: {"UNAUTHORIZED"},
        403: {"FORBIDDEN"},
        404: {"NOT_FOUND"},
        409: {"IDEMPOTENCY_CONFLICT", "SNAPSHOT_CONFLICT"},
        503: {"UNAVAILABLE"},
    }
    if status not in allowed or code not in allowed[status]:
        status, code = 503, "UNAVAILABLE"
    if not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace):
        trace = secrets.token_hex(16)
    body = BacktestProblem(
        type="urn:agentic-trading-lab:backtest-problem",
        title="Backtest request failed",
        status=status,
        code=code,
        traceId=trace,
    )
    return JSONResponse(
        body.model_dump(), status_code=status, media_type="application/problem+json"
    )


def read_query(request, *, paged=False, results=False):
    allowed = {"limit", "cursor"} if paged else set()
    if paged:
        allowed |= {"resultType", "foldIndex"} if results else {"status", "instrumentId"}
    values = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in values or len(value) > 2048:
            raise ValueError("invalid query")
        values[key] = value
    if "limit" in values and (
        not re.fullmatch(r"[1-9][0-9]{0,2}", values["limit"]) or int(values["limit"]) > 200
    ):
        raise ValueError("bounded limit required")
    if "cursor" in values and not re.fullmatch(r"[A-Za-z0-9_-]{32,2048}", values["cursor"]):
        raise BacktestError(400, "CURSOR_INVALID")
    if "status" in values and values["status"] not in STATES:
        raise ValueError("invalid state")
    if "instrumentId" in values:
        identity(values["instrumentId"])
    if "resultType" in values and values["resultType"] not in {"summary", "equity", "fills"}:
        raise ValueError("invalid result kind")
    if "foldIndex" in values and not re.fullmatch(r"(?:[0-9]|1[01])", values["foldIndex"]):
        raise ValueError("bounded fold required")
    return values


def register_backtest_routes(app, *, backtesting, body_reader, key_reader):
    admission = anyio.CapacityLimiter(2)
    workers = anyio.CapacityLimiter(2)
    metrics = BacktestMetrics()
    app.state.backtest_metrics = metrics

    async def execute(request, method, *args, accepted=False):
        start = monotonic()
        code, rows = None, 0
        try:
            admission.acquire_nowait()
        except anyio.WouldBlock:
            metrics.observe(method, 0, code="UNAVAILABLE", trace=request.state.trace_id)
            return backtest_problem(503, "UNAVAILABLE", request.state.trace_id)
        token = CURRENT_TRACE.set(request.state.trace_id)
        try:
            if backtesting is None:
                raise BacktestError(503, "UNAVAILABLE")
            value = await anyio.to_thread.run_sync(
                getattr(backtesting, method), *args, limiter=workers
            )
            try:
                value = RESPONSE_MODELS[method].model_validate(value).model_dump()
            except ValueError:
                raise BacktestError(503, "UNAVAILABLE") from None
            rows = min(len(value.get("items", [])), 200)
            return JSONResponse(value, status_code=202 if accepted else 200)
        except (BacktestError, AuthDenied) as error:
            code = error.code if error.code in CODES else "UNAVAILABLE"
            return backtest_problem(error.status, code, request.state.trace_id)
        except (ValueError, TypeError, KeyError):
            code = "INVALID_REQUEST"
            return backtest_problem(400, code, request.state.trace_id)
        except Exception:
            code = "UNAVAILABLE"
            return backtest_problem(503, code, request.state.trace_id)
        finally:
            CURRENT_TRACE.reset(token)
            admission.release()
            metrics.observe(
                method,
                min(monotonic() - start, 86400),
                rows=rows,
                code=code,
                trace=request.state.trace_id,
            )

    def add_read(suffix, operation, method, *, paged=False, results=False):
        async def read(request: Request):
            try:
                values = read_query(request, paged=paged, results=results)
                run_id = request.path_params.get("runId")
                if run_id:
                    identity(run_id)
            except (ValueError, TypeError) as error:
                return backtest_problem(
                    getattr(error, "status", 400),
                    getattr(error, "code", "INVALID_REQUEST"),
                    request.state.trace_id,
                )
            args = (
                [run_id, values] if results else [run_id] if run_id else [values] if paged else []
            )
            return await execute(request, method, *args)

        app.add_api_route(BASE + suffix, read, methods=["GET"], operation_id=operation)

    add_read("/strategies", "listBacktestStrategies", "read_strategies")
    add_read("/runs", "listBacktestRuns", "read_runs", paged=True)
    add_read("/runs/{runId}", "getBacktestRun", "read_run")
    add_read(
        "/runs/{runId}/results", "getBacktestResults", "read_results", paged=True, results=True
    )

    @app.post(BASE + "/runs", operation_id="createBacktestRun")
    async def create(request: Request):
        try:
            read_query(request)
            key = key_reader(request)
            payload = await body_reader(request, BacktestRequest)
        except (AuthDenied, ValueError) as error:
            return backtest_problem(
                getattr(error, "status", 400), "INVALID_REQUEST", request.state.trace_id
            )
        return await execute(
            request,
            "create_run",
            payload,
            request.state.actor_id,
            key,
            request.state.trace_id,
            accepted=True,
        )
