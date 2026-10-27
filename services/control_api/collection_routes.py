"""Zwei authentifizierte Planoperationen ohne Provider- oder Credentialzugriff."""

import json
import re
from contextvars import ContextVar

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse

from services.control_api.auth import AuthDenied
from services.control_api.backtest_service import pairs
from services.control_api.collection_dto import RESPONSE_MODELS, CollectionPlanUpdate
from services.control_api.collection_service import CollectionError
from services.control_api.readiness_routes import MAX_HTTP_BYTES, readiness_problem

BASE = "/api/v1/research/collection/plans"
CURRENT_TRACE = ContextVar("collection_trace", default=None)


async def read_plan_body(request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        raise CollectionError(400, "INVALID_REQUEST")
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or lengths and not re.fullmatch(r"[0-9]+", lengths[0]):
        raise CollectionError(400, "INVALID_REQUEST")
    if lengths and int(lengths[0]) > MAX_HTTP_BYTES:
        raise CollectionError(413, "PAYLOAD_TOO_LARGE")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_HTTP_BYTES:
            raise CollectionError(413, "PAYLOAD_TOO_LARGE")
    try:

        def invalid(_):
            raise ValueError("finite JSON required")

        value = json.loads(body, object_pairs_hook=pairs, parse_constant=invalid)
        return CollectionPlanUpdate.model_validate(value).model_dump()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise CollectionError(400, "INVALID_REQUEST") from None


def register_collection_routes(app, *, collection, body_reader, key_reader):
    del body_reader
    admission, workers = anyio.CapacityLimiter(2), anyio.CapacityLimiter(2)

    async def execute(request, method, *args):
        trace = request.state.trace_id
        if getattr(request.state, "actor_id", None) != "local-operator":
            return readiness_problem(401, "UNAUTHORIZED", trace)
        try:
            admission.acquire_nowait()
        except anyio.WouldBlock:
            return readiness_problem(503, "UNAVAILABLE", trace)
        token = CURRENT_TRACE.set(trace)
        try:
            if collection is None:
                raise CollectionError(503, "UNAVAILABLE")
            value = await anyio.to_thread.run_sync(
                getattr(collection, method), *args, limiter=workers
            )
            try:
                value = RESPONSE_MODELS[method].model_validate(value).model_dump()
                if len(json.dumps(value, separators=(",", ":")).encode()) > MAX_HTTP_BYTES:
                    raise ValueError("response budget exhausted")
            except (ValueError, TypeError):
                raise CollectionError(503, "UNAVAILABLE") from None
            return JSONResponse(value)
        except (CollectionError, AuthDenied) as error:
            return readiness_problem(error.status, error.code, trace)
        except (ValueError, TypeError, KeyError):
            return readiness_problem(400, "INVALID_REQUEST", trace)
        except Exception:
            return readiness_problem(503, "UNAVAILABLE", trace)
        finally:
            CURRENT_TRACE.reset(token)
            admission.release()

    @app.get(BASE, operation_id="listCollectionPlans")
    async def plans(request: Request):
        if request.query_params:
            return readiness_problem(400, "INVALID_REQUEST", request.state.trace_id)
        return await execute(request, "read_plans", {})

    @app.put(BASE + "/{instrumentId}", operation_id="updateCollectionPlan")
    async def update(request: Request):
        try:
            if request.query_params or "if-match" in request.headers:
                raise CollectionError(400, "INVALID_REQUEST")
            key = key_reader(request)
            value = await read_plan_body(request)
        except (CollectionError, AuthDenied) as error:
            return readiness_problem(error.status, error.code, request.state.trace_id)
        except ValueError:
            return readiness_problem(400, "INVALID_REQUEST", request.state.trace_id)
        return await execute(
            request,
            "update_plan",
            request.path_params["instrumentId"],
            value,
            request.state.actor_id,
            key,
            request.state.trace_id,
            None,
        )
