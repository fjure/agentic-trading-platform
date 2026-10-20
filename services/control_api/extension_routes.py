"""Authenticated demo and write-only setup; never persists request credentials."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from services.control_api.auth import AuthDenied

Stamp = Annotated[str, Field(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")]
Trace = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Strategy = Literal["mock-neutral", "neutral-baseline"]
Amount = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,18})?$", max_length=26)]
Secret = Annotated[str, Field(min_length=1, max_length=512, repr=False)]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Profile(DTO):
    budget: Amount
    currency: Literal["USD"]
    strategy: Strategy

    @field_validator("budget")
    @classmethod
    def bounded(cls, value):
        if not Decimal("0") < Decimal(value) <= Decimal("1000000"):
            raise ValueError("INVALID_REQUEST")
        return value


class DemoStart(DTO):
    initialBudget: Amount
    currency: Literal["USD"]
    strategy: Strategy

    @field_validator("initialBudget")
    @classmethod
    def bounded(cls, value):
        return Profile.bounded(value)


class DemoRun(DemoStart):
    runId: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "PAUSED"]
    simulationOnly: Literal[True]
    traceId: Trace
    createdAt: Stamp
    updatedAt: Stamp
    errorCode: (
        Literal["TEMPORARILY_UNAVAILABLE", "INVALID_REQUEST", "RISK_LIMIT_EXCEEDED"] | None
    ) = None


class AlpacaWrite(DTO):
    apiKey: Secret
    apiSecret: Secret
    expectedAccountId: str
    paperOnly: Literal[True]

    @field_validator("apiKey", "apiSecret")
    @classmethod
    def no_controls(cls, value):
        if value.strip() != value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("INVALID_REQUEST")
        return value

    @field_validator("expectedAccountId")
    @classmethod
    def account_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("INVALID_REQUEST")
        return value


class JevWrite(DTO):
    apiKey: Secret

    @field_validator("apiKey")
    @classmethod
    def no_controls(cls, value):
        return AlpacaWrite.no_controls(value)


class IntegrationStatus(DTO):
    status: Literal["UNCONFIGURED", "CONFIGURED"]
    restartRequired: bool


class Integrations(DTO):
    alpaca: IntegrationStatus
    jev: IntegrationStatus


class IntegrationSaved(DTO):
    status: Literal["CONFIGURED"]
    restartRequired: Literal[True]


class DemoControl(DTO):
    reason: Annotated[str, Field(max_length=200)] = ""


def _runId(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError
        return value
    except ValueError:
        raise AuthDenied(400, "INVALID_REQUEST") from None


def _port(value):
    if value is None:
        raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
    return value


def _run(value):
    return DemoRun.model_validate(value).model_dump(exclude_none=True)


def register_extensions(app, *, body_reader, key_reader, demo, setup, trading_profile):
    @app.get("/api/v1/demo/runs", operation_id="listDemoRuns")
    async def runs():
        value = await run_in_threadpool(_port(demo).list_runs)
        return {"items": [_run(item) for item in value["items"]], "nextCursor": None}

    @app.post("/api/v1/demo/runs", operation_id="createDemoRun")
    async def start(request: Request):
        key = key_reader(request)
        payload = await body_reader(request, DemoStart)
        status, value, _headers = await run_in_threadpool(
            _port(demo).start, payload, key, request.state.actor_id, request.state.trace_id
        )
        if status not in (200, 201, 202):
            raise AuthDenied(
                status if status in (400, 409) else 503,
                "INVALID_REQUEST" if status == 400 else "TEMPORARILY_UNAVAILABLE",
            )
        return JSONResponse(_run(value), status_code=status)

    @app.get("/api/v1/demo/runs/{runId}", operation_id="getDemoRun")
    async def run(runId: str):
        return _run(await run_in_threadpool(_port(demo).get_run, _runId(runId)))

    @app.get("/api/v1/demo/runs/{runId}/snapshot", operation_id="getDemoSnapshot")
    async def snapshot(runId: str, request: Request):
        return await run_in_threadpool(_port(demo).snapshot, _runId(runId), request.state.trace_id)

    @app.get("/api/v1/demo/runs/{runId}/history/{resource}", operation_id="getDemoHistory")
    async def history(runId: str, resource: str, request: Request):
        if resource not in {
            "market-states",
            "decisions",
            "proposals",
            "risk-decisions",
            "orders",
            "audit",
            "runs",
            "costs",
        }:
            raise AuthDenied(400, "INVALID_REQUEST")
        if any(len(request.query_params.getlist(name)) != 1 for name in request.query_params):
            raise AuthDenied(400, "INVALID_REQUEST")
        cursor = request.query_params.get("cursor")
        raw_limit = request.query_params.get("limit", "50")
        trace = request.query_params.get("traceId")
        if (
            not re.fullmatch(r"[0-9]{1,3}", raw_limit)
            or not 1 <= int(raw_limit) <= 200
            or cursor is not None
            and not 1 <= len(cursor) <= 256
            or trace is not None
            and not re.fullmatch(r"[0-9a-f]{32}", trace)
        ):
            raise AuthDenied(400, "INVALID_REQUEST")
        return await run_in_threadpool(
            _port(demo).history,
            _runId(runId),
            resource,
            cursor=cursor,
            limit=int(raw_limit),
            trace_id=trace,
        )

    @app.post("/api/v1/demo/runs/{runId}/controls/{action}", operation_id="controlDemoRun")
    async def control(runId: str, action: str, request: Request):
        key_reader(request)
        await body_reader(request, DemoControl)
        if action not in {"pause", "kill"}:
            raise AuthDenied(400, "INVALID_REQUEST")
        value = await run_in_threadpool(
            _port(demo).control,
            _runId(runId),
            action,
            request.state.actor_id,
            request.state.trace_id,
        )
        return {
            "runId": _runId(runId),
            "action": action,
            "status": value["status"],
            "simulationOnly": True,
            "traceId": request.state.trace_id,
        }

    @app.get("/api/v1/integrations", operation_id="getIntegrationStatuses")
    async def integrations():
        value = await run_in_threadpool(_port(setup).read_integrations)
        return Integrations.model_validate(value).model_dump()

    def provider_route(provider, model, operation):
        async def save(request: Request):
            key_reader(request)
            payload = await body_reader(request, model)
            try:
                method = getattr(_port(setup), "save_" + provider)
                value = await run_in_threadpool(method, payload)
            finally:
                # No credential receipts or body digests; do not retain strings.
                payload.clear()
            return IntegrationSaved.model_validate(value).model_dump()

        app.add_api_route(
            "/api/v1/integrations/" + provider, save, methods=["PUT"], operation_id=operation
        )

    provider_route("alpaca", AlpacaWrite, "saveAlpacaIntegration")
    provider_route("jev", JevWrite, "saveJevIntegration")

    @app.get("/api/v1/trading-profile", operation_id="getTradingProfile")
    async def read_profile():
        value = await run_in_threadpool(_port(trading_profile).read)
        return Profile.model_validate(value).model_dump()

    @app.put("/api/v1/trading-profile", operation_id="updateTradingProfile")
    async def write_profile(request: Request):
        key = key_reader(request)
        payload = await body_reader(request, Profile)
        value = await run_in_threadpool(
            _port(trading_profile).update,
            payload,
            request.state.actor_id,
            key,
            request.state.trace_id,
        )
        return Profile.model_validate(value).model_dump()
