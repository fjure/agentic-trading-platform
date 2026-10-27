"""Private fixed Paper command backend, reachable only on the control network."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from services.control_api import paper_dto as dto
from services.control_api.app import _body, _trace
from services.control_api.auth import AuthDenied
from services.control_api.paper_commands import PaperCommandError, PaperCommands
from services.control_api.paper_dto import DTO, Trace, Version, identity

from .common import write_status
from .paper import check_identity


class Context(DTO):
    actorId: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,255}$")]
    key: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{16,128}$")]
    traceId: Trace


class Command(Context):
    operation: Literal[
        "VERIFY_CONNECTION",
        "PREPARE_LEDGER",
        "START",
        "PAUSE",
        "STOP",
        "KILL",
        "PLACE_LIMIT_ORDER",
        "CANCEL_ORDER",
    ]
    payload: dict
    orderId: str | None


class RotationComplete(DTO):
    generation: Version


class ProfileContext(Context):
    payload: dict


def create_private_paper_app(commands, *, research=None, backtesting=None, readiness=None,
                             collection=None):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        headers = request.scope.get("headers", ())
        hosts = [value for key, value in headers if key.lower() == b"host"]
        if hosts != [b"paper-api:8012"] or any(
            key.lower() in {b"origin", b"forwarded"} or key.lower().startswith(b"x-forwarded-")
            for key, _ in headers
        ):
            return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)
        try:
            request.state.trace_id = _trace(request)
            request.state.actor_id = "local-operator"
            response = await call_next(request)
        except Exception:
            response = JSONResponse({"code": "TEMPORARILY_UNAVAILABLE"}, status_code=503)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PaperCommandError)
    async def command_error(_request, error):
        return JSONResponse({"code": error.code}, status_code=error.status)

    @app.exception_handler(AuthDenied)
    async def invalid_body(_request, _error):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)

    @app.exception_handler(ValueError)
    async def invalid_value(_request, _error):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)

    @app.exception_handler(HTTPException)
    async def missing(_request, _error):
        return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)

    @app.get("/status")
    async def status(request: Request):
        import re

        trace = request.query_params.get("trace", "")
        if not re.fullmatch(r"[0-9a-f]{32}", trace):
            raise ValueError()
        return await run_in_threadpool(commands.read_status, trace)

    @app.get("/snapshot")
    async def snapshot(request: Request):
        values = dto.read_query(request.query_params.multi_items(), private=True)
        return await run_in_threadpool(commands.read_snapshot, values["trace"])

    def add_page(resource):
        async def page(request: Request):
            values = dto.read_query(request.query_params.multi_items(), private=True, paged=True)
            return await run_in_threadpool(getattr(commands, "read_" + resource),
                                          values["limit"], values["cursor"], values["trace"])

        app.add_api_route("/" + resource, page, methods=["GET"])

    for resource in ("orders", "fills", "operations"):
        add_page(resource)

    @app.get("/traces/{trace_id}")
    async def trace_view(request: Request, trace_id: str):
        dto.read_query(request.query_params.multi_items())
        dto.read_query([("trace", trace_id)], private=True)
        return await run_in_threadpool(commands.read_trace, trace_id)

    @app.get("/operations/{operation_id}")
    async def operation(operation_id: str):
        identity(operation_id)
        return await run_in_threadpool(commands.read_operation, operation_id, secrets.token_hex(16))

    @app.get("/trading-profile")
    async def profile(request: Request):
        import re

        trace = request.query_params.get("trace", "")
        if not re.fullmatch(r"[0-9a-f]{32}", trace):
            raise ValueError()
        return await run_in_threadpool(commands.read_profile, trace)

    @app.put("/trading-profile")
    async def save_profile(request: Request):
        value = await _body(request, ProfileContext)
        return await run_in_threadpool(commands.save_profile, value["payload"], value["actorId"],
                                       value["key"], value["traceId"])

    @app.post("/commands")
    async def enqueue(request: Request):
        value = await _body(request, Command)
        result = await run_in_threadpool(
            commands.enqueue,
            value["operation"],
            value["payload"],
            value["actorId"],
            value["key"],
            value["traceId"],
            value["orderId"],
        )
        return JSONResponse(result, status_code=202)

    @app.post("/rotation/begin")
    async def rotation_begin(request: Request):
        value = await _body(request, Context)
        generation = await run_in_threadpool(
            commands.rotation_begin,
            value["actorId"],
            value["key"],
            value["traceId"],
        )
        return {"generation": generation}

    @app.post("/rotation/complete")
    async def rotation_complete(request: Request):
        value = await _body(request, RotationComplete)
        return {
            "completed": await run_in_threadpool(commands.rotation_complete, value["generation"])
        }

    from services.control_api.research_routes import register_research_routes
    def research_key(request):
        values = request.headers.getlist("idempotency-key")
        import re
        if len(values) != 1 or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", values[0]):
            raise ValueError("INVALID_REQUEST")
        return values[0]
    register_research_routes(app, research=research, body_reader=_body, key_reader=research_key)
    from services.control_api.backtest_routes import register_backtest_routes
    register_backtest_routes(app, backtesting=backtesting, body_reader=_body,
                             key_reader=research_key)
    from services.control_api.readiness_routes import register_readiness_routes
    register_readiness_routes(app, readiness=readiness, body_reader=_body, key_reader=research_key)
    from services.control_api.collection_routes import register_collection_routes
    register_collection_routes(app, collection=collection, body_reader=_body,
                               key_reader=research_key)
    return app


def run_private_paper_api(engine):
    import uvicorn

    commands = PaperCommands(engine)
    from services.control_api.research_service import ResearchService
    from services.runtime.config import database_engine

    research_engine = database_engine("api", deployment="paper", isolated_research=True)
    from services.control_api.collection_service import CollectionPlanService
    from services.runtime.backtesting import configured_readiness_service, configured_service

    application = create_private_paper_app(
        commands, research=ResearchService(research_engine),
        backtesting=configured_service(research_engine),
        readiness=configured_readiness_service(research_engine),
        collection=CollectionPlanService(research_engine),
    )

    def health_loop():
        while True:
            try:
                write_status("api", check_identity(engine, "api"))
            except Exception:
                write_status("api", "UNAVAILABLE")
            try:
                from packages.observability.research import observe_jobs
                with research_engine.connect() as connection:
                    aggregates = observe_jobs(connection)
                application.state.research_metrics.export_local(aggregates=aggregates)
            except Exception:
                # Research telemetry never changes control health or safety admission.
                pass
            time.sleep(5)

    threading.Thread(target=health_loop, daemon=True).start()
    try:
        uvicorn.run(
            application,
            host="0.0.0.0",
            port=8012,
            access_log=False,
            log_config=None,
            proxy_headers=False,
        )
    finally:
        research_engine.dispose()
