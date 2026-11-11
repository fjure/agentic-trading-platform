"""Authenticated private Control-to-Research transport for registered operations."""

import asyncio
import json
import re
from hmac import compare_digest, digest
from math import isfinite
from time import time
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from services.control_api import agent_research_routes as routes

RELAY_PATH = "/private/v1/research/control"
RELAY_URL = "http://research-supervisor:8013" + RELAY_PATH
SIGNATURE_HEADER = "X-Research-Control-Signature"
MAX_REQUEST = 64 * 1024
MAX_REPLY = 2 * 1024 * 1024
_TTL = 30
_HEX64 = re.compile(r"[0-9a-f]{64}")
_FIELDS = {
    "operation", "params", "query", "payload", "actor", "key", "trace", "scope", "iat", "nonce",
}
_ALLOWED_OPERATIONS = frozenset({
    "createAgentProcessingPreview", "getAgentProcessingStatus", "decideAgentProcessing",
    "listAgentIdeas", "createAgentIdea", "getAgentIdea", "reviseAgentIdea",
    "createAgentDialogue", "getAgentDialogue", "createAgentDialogueTurn", "decideAgentHypothesis",
    "getAgentRegistry", "listResearchAgentRuns", "getAgentRun", "listAgentRunEvents",
    "cancelAgentRun", "getAgentSchedule", "updateAgentSchedule", "commandAgentSchedule",
    "listAgentProviders", "probeAgentProvider", "listAgentQuantApprovals", "getAgentQuantApproval",
    "decideAgentQuantApproval", "listAgentResearchReports", "getAgentResearchReport",
})


class AgentRelayAuthentication:
    """Use a dedicated transport key shared only by Control and Research."""

    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("invalid Research relay key")
        self._key = key

    def request_signature(self, body: bytes) -> str:
        return digest(self._key, b"apf-control-request-v1\0" + body, "sha256").hex()

    def response_signature(self, request: bytes, body: bytes) -> str:
        request_mac = digest(self._key, request, "sha256")
        return digest(self._key, b"apf-control-response-v1\0" + request_mac + body, "sha256").hex()


def matches_signature(expected: str, received: str) -> bool:
    return bool(_HEX64.fullmatch(received)) and compare_digest(expected, received)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate Research relay field")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("non-finite Research relay field")


def _parse_float(raw: str) -> float:
    value = float(raw)
    if not isfinite(value):
        _reject_constant(raw)
    return value


def _encode(value: dict, limit: int) -> bytes:
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(body) > limit:
        raise ValueError("Research relay body too large")
    return body


def _decode(body: bytes, limit: int) -> dict:
    if not 0 < len(body) <= limit:
        raise ValueError("invalid Research relay body length")
    value = json.loads(
        body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant,
        parse_float=_parse_float,
    )
    if not isinstance(value, dict):
        raise ValueError("invalid Research relay body")
    return value


def _request_arguments(value: dict):
    if set(value) != _FIELDS or value["operation"] not in _ALLOWED_OPERATIONS:
        raise ValueError("invalid Research relay operation")
    operation = next(op for op in routes.OPERATIONS if op.operation_id == value["operation"])
    actor, key, trace, scope = (value[field] for field in ("actor", "key", "trace", "scope"))
    if (
        actor != "local-operator"
        or not routes.TRACE.fullmatch(trace)
        or not routes.SCOPE.fullmatch(scope)
    ):
        raise ValueError("invalid Research relay authority")
    key_valid = (
        key is None if operation.method == "GET"
        else isinstance(key, str) and routes.IDEMPOTENCY_KEY.fullmatch(key)
    )
    if not key_valid:
        raise ValueError("invalid Research relay idempotency key")
    if (
        type(value["iat"]) is not int
        or not -2 <= int(time()) - value["iat"] <= _TTL
        or not routes.TRACE.fullmatch(value["nonce"])
        or not isinstance(value["params"], dict)
        or not isinstance(value["query"], dict)
    ):
        raise ValueError("invalid Research relay envelope")
    params, query = routes.validate_arguments(operation, value["params"], value["query"].items())
    payload = value["payload"]
    routes.validate_payload(operation, payload)
    if (operation.operation_id == "decideAgentQuantApproval"
            and payload["action"] == "APPROVE_AND_START"):
        raise ValueError("Quant admission belongs to the private operator authority")
    authority = {"actor": actor, "key": key, "trace": trace, "scope": scope}
    return operation, params, query, payload, authority


def request_body(operation, params, query, payload, *, actor, key, trace, scope) -> bytes:
    value = {
        "operation": operation.operation_id, "params": params, "query": query, "payload": payload,
        "actor": actor, "key": key, "trace": trace, "scope": scope,
        "iat": int(time()), "nonce": uuid4().hex,
    }
    validated, *_ = _request_arguments(value)
    if validated != operation:
        raise ValueError("invalid Research relay operation metadata")
    return _encode(value, MAX_REQUEST)


def _validated_reply(operation, trace: str, status: int, value: dict) -> dict:
    if type(status) is not int or not isinstance(value, dict):
        raise ValueError("invalid Research relay result")
    if 200 <= status < 300:
        routes.validate_response(operation, status, value)
        return value
    routes.validate_problem(operation, status, value)
    if value["status"] != status or value["traceId"] != trace:
        raise ValueError("invalid Research relay problem binding")
    return routes.problem_value(status, value["code"], trace, operation)


def parse_reply(operation, trace: str, body: bytes) -> tuple[int, dict]:
    value = _decode(body, MAX_REPLY)
    if set(value) != {"status", "value"}:
        raise ValueError("invalid Research relay reply envelope")
    status, result = value["status"], value["value"]
    return status, _validated_reply(operation, trace, status, result)


async def _receive(request: Request) -> bytes:
    body = bytearray()
    async with asyncio.timeout(8):
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST:
                raise ValueError("Research relay request too large")
            body.extend(chunk)
    return bytes(body)


def create_agent_research_relay(
    auth: AgentRelayAuthentication, handler, *, lifespan=None,
) -> FastAPI:
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False, lifespan=lifespan,
    )

    @app.post(RELAY_PATH)
    async def dispatch(request: Request) -> Response:
        signatures = request.headers.getlist(SIGNATURE_HEADER)
        if len(signatures) != 1 or not _HEX64.fullmatch(signatures[0]):
            return Response(status_code=403)
        if (
            request.url.query
            or request.headers.get("content-type") != "application/json"
            or request.headers.get("content-encoding", "identity") != "identity"
        ):
            return Response(status_code=400)
        try:
            body = await _receive(request)
        except (ValueError, TimeoutError):
            return Response(status_code=400)
        if not matches_signature(auth.request_signature(body), signatures[0]):
            return Response(status_code=403)
        try:
            operation, params, query, payload, authority = _request_arguments(
                _decode(body, MAX_REQUEST),
            )
        except (ValueError, TypeError, KeyError, StopIteration, RecursionError,
                routes.AgentResearchError):
            return Response(status_code=400)
        try:
            try:
                status, value = await run_in_threadpool(
                    handler, operation, params, query, payload, **authority,
                )
            except routes.AgentResearchError as error:
                value = routes.problem_value(
                    error.status, error.code, authority["trace"], operation,
                )
                status = value["status"]
            value = _validated_reply(operation, authority["trace"], status, value)
            reply = _encode({"status": status, "value": value}, MAX_REPLY)
        except Exception:
            return Response(status_code=503)
        return Response(reply, media_type="application/json", headers={
            SIGNATURE_HEADER: auth.response_signature(body, reply), "Cache-Control": "no-store",
        })

    return app
