"""Authenticated HTTP transport for durable Paper commands; no broker dependencies."""

import re
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.control_api import paper_dto as dto
from services.control_api.auth import AuthDenied
from services.control_api.paper_commands import ERROR_CODES, PaperCommandError


def paper_problem(status, code, trace_id):
    if code not in ERROR_CODES:
        code = "TEMPORARILY_UNAVAILABLE"
    if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        trace_id = secrets.token_hex(16)
    return JSONResponse(
        {
            "type": "urn:agentic-trading-lab:paper-problem",
            "title": "Paper workflow request failed",
            "status": status,
            "code": code,
            "traceId": trace_id,
        },
        status_code=status,
        media_type="application/problem+json",
    )


def register_paper_routes(app, *, paper, body_reader, key_reader):
    """Caller middleware authenticates and installs actor_id/trace_id on request.state."""

    async def execute(request, method, *args):
        try:
            if paper is None:
                return paper_problem(503, "TEMPORARILY_UNAVAILABLE", request.state.trace_id)
            result = await run_in_threadpool(getattr(paper, method), *args)
            response_model = dto.Status if method == "read_status" else dto.Operation
            try:
                result = response_model.model_validate(result).model_dump(exclude_unset=True)
            except ValueError:
                return paper_problem(503, "TEMPORARILY_UNAVAILABLE", request.state.trace_id)
            return JSONResponse(result, status_code=202 if method == "enqueue" else 200)
        except (PaperCommandError, AuthDenied) as exc:
            return paper_problem(exc.status, exc.code, request.state.trace_id)
        except ValueError:
            return paper_problem(400, "INVALID_REQUEST", request.state.trace_id)
        except Exception:
            return paper_problem(503, "TEMPORARILY_UNAVAILABLE", request.state.trace_id)

    @app.get("/api/v1/paper/status", operation_id="getPaperStatus")
    async def status(request: Request):
        return await execute(request, "read_status", request.state.trace_id)

    @app.get("/api/v1/paper/operations/{operationId}", operation_id="getPaperOperation")
    async def operation(request: Request, operationId: str):
        try:
            dto.identity(operationId)
        except ValueError:
            return paper_problem(400, "INVALID_REQUEST", request.state.trace_id)
        return await execute(request, "read_operation", operationId, request.state.trace_id)

    def add(path, operation_id, kind, model):
        async def post(request: Request):
            orderId = request.path_params.get("orderId")
            try:
                key = key_reader(request)
                value = await body_reader(request, model)
                payload = value.model_dump() if hasattr(value, "model_dump") else value
                actual_kind = payload["action"] if kind == "CONTROL" else kind
                if orderId is not None:
                    dto.identity(orderId)
                return await execute(
                    request,
                    "enqueue",
                    actual_kind,
                    payload,
                    request.state.actor_id,
                    key,
                    request.state.trace_id,
                    orderId,
                )
            except (AuthDenied, PaperCommandError) as exc:
                return paper_problem(exc.status, exc.code, request.state.trace_id)
            except (ValueError, TypeError, KeyError):
                return paper_problem(400, "INVALID_REQUEST", request.state.trace_id)

        app.add_api_route(
            "/api/v1/paper/" + path, post, methods=["POST"], operation_id=operation_id
        )

    for path, operation_id, kind, model in [
        ("verify-connection", "verifyPaperConnection", "VERIFY_CONNECTION", dto.Verify),
        ("prepare-ledger", "preparePaperLedger", "PREPARE_LEDGER", dto.Prepare),
        ("start", "startPaperWorkflow", "START", dto.Start),
        ("control", "controlPaperWorkflow", "CONTROL", dto.Control),
        ("order-requests", "createPaperLimitOrderRequest", "PLACE_LIMIT_ORDER", dto.Limit),
        ("orders/{orderId}/cancel", "cancelPaperOrder", "CANCEL_ORDER", dto.Cancel),
    ]:
        add(path, operation_id, kind, model)
