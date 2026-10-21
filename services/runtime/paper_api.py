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

from services.control_api.app import _body
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


def create_private_paper_app(commands):
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

    @app.get("/operations/{operation_id}")
    async def operation(operation_id: str):
        identity(operation_id)
        return await run_in_threadpool(commands.read_operation, operation_id, secrets.token_hex(16))

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

    return app


def run_private_paper_api(engine):
    import uvicorn

    commands = PaperCommands(engine)

    def health_loop():
        while True:
            try:
                write_status("api", check_identity(engine, "api"))
            except Exception:
                write_status("api", "UNAVAILABLE")
            time.sleep(5)

    threading.Thread(target=health_loop, daemon=True).start()
    uvicorn.run(
        create_private_paper_app(commands),
        host="0.0.0.0",
        port=8012,
        access_log=False,
        log_config=None,
        proxy_headers=False,
    )
