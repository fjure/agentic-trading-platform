"""Private LLM gateway ASGI process. The default deployment has no dispatch authority."""

from contextlib import asynccontextmanager
from dataclasses import asdict

import httpx
from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from .content_policy import safe_result_content
from .dispatch_journal import DispatchJournal
from .offline_gateway import DispatchRequest, OfflineGateway, ProviderGateway
from .provider_wire import ProviderResult, ResultState, Usage
from .supervisor_rpc import (
    SIGNATURE,
    AdmissionAuthentication,
    AdmissionClient,
    _decode,
    _encode,
    _matches_signature,
    _parse_request,
    _request_body,
    _signed_body,
    create_cancellation_app,
)

DISPATCH_PATH = "/private/v1/research/dispatch"
_MAX_RESULT = 262144
_SUPERVISOR_ORIGIN = "http://research-supervisor:8013"


def create_provider_gateway_app(
    *, journal_path, auth: AdmissionAuthentication, credentials, capabilities,
) -> FastAPI:
    """Compose fixed provider I/O with the private supervisor admission port."""
    journal = DispatchJournal(journal_path)
    admission_http = httpx.Client(
        base_url=_SUPERVISOR_ORIGIN, timeout=3, trust_env=False, follow_redirects=False,
    )
    try:
        gateway = ProviderGateway(
            journal, AdmissionClient(admission_http, auth).authorize,
            credentials=credentials, capabilities=capabilities,
        )
        app = create_gateway_app(gateway, auth)
    except BaseException:
        admission_http.close()
        raise

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            admission_http.close()

    app.router.lifespan_context = lifespan
    return app


def create_gateway_app(
    gateway: OfflineGateway | None = None, auth: AdmissionAuthentication | None = None,
) -> FastAPI:
    if (gateway is None) != (auth is None):
        raise ValueError("incomplete gateway configuration")
    app = (
        create_cancellation_app(auth, gateway.cancel) if gateway is not None and auth is not None
        else FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    )

    @app.get("/health")
    async def health():
        if isinstance(gateway, ProviderGateway):
            return {"state": "PROVIDER_READY"}
        return {"state": "OFFLINE_READY" if gateway is not None else "DISABLED"}

    @app.post(DISPATCH_PATH)
    async def dispatch(request: Request) -> Response:
        if gateway is None or auth is None:
            return Response(status_code=503)
        body = await _signed_body(request, auth.gateway_signature)
        if isinstance(body, Response):
            return body
        try:
            command = _parse_request(body)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return Response(status_code=400)
        try:
            result = await run_in_threadpool(gateway.dispatch, command)
            payload = _encode(asdict(result))
            if len(payload) > _MAX_RESULT:
                return Response(status_code=503)
        except Exception:
            return Response(status_code=503)
        return Response(payload, media_type="application/json", headers={
            SIGNATURE: auth.gateway_response_signature(body, payload), "cache-control": "no-store",
        })

    return app


class GatewayDispatchClient:
    """Send an attempt through the private gateway and verify its bound result."""

    def __init__(self, client: httpx.Client, auth: AdmissionAuthentication):
        self._client = client
        self._auth = auth

    def dispatch(self, request: DispatchRequest) -> ProviderResult:
        body = _request_body(request)
        try:
            with self._client.stream(
                "POST", DISPATCH_PATH, content=body, follow_redirects=False,
                headers={SIGNATURE: self._auth.gateway_signature(body)},
            ) as reply:
                signatures = reply.headers.get_list(SIGNATURE)
                if reply.status_code != 200 or len(signatures) != 1:
                    return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                payload = bytearray()
                for chunk in reply.iter_bytes(chunk_size=8192):
                    if len(payload) + len(chunk) > _MAX_RESULT:
                        return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                    payload.extend(chunk)
            if _matches_signature(
                self._auth.gateway_response_signature(body, bytes(payload)), signatures[0],
            ):
                return _decode_result(bytes(payload))
        except (httpx.HTTPError, ValueError, TypeError, RecursionError):
            pass
        return ProviderResult(ResultState.OUTCOME_UNKNOWN)


def _decode_result(payload: bytes) -> ProviderResult:
    value = _decode(payload)
    if value.keys() != {"state", "usage", "output"}:
        raise ValueError("invalid dispatch result")
    state = ResultState(value["state"])
    output = value["output"]
    if state is ResultState.ACCEPTED:
        if type(output) is not dict or not safe_result_content(output):
            raise ValueError("invalid dispatch output")
    elif output is not None:
        raise ValueError("unexpected dispatch output")
    usage = value["usage"]
    if usage is not None:
        if type(usage) is not dict or usage.keys() != {"input_tokens", "output_tokens"} or any(
            type(count) is not int or not 0 <= count <= 2**31 - 1 for count in usage.values()
        ):
            raise ValueError("invalid dispatch usage")
        usage = Usage(**usage)
    return ProviderResult(state, usage, output)
