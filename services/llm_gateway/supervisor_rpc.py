"""Bounded, mutually authenticated private supervisor admission messages."""

import base64
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hmac import compare_digest, digest
from uuid import UUID

import httpx
from fastapi import FastAPI, Request, Response
from starlette.concurrency import run_in_threadpool

from .offline_gateway import AuthorizedDispatch, DispatchRequest
from .provider_http import Capability
from .provider_wire import Provider, _reject_nonfinite, _unique_fields

ADMISSION_PATH = "/private/v1/research/dispatch-admission"
CANCELLATION_PATH = "/private/v1/research/dispatch-cancellation"
SIGNATURE = "x-research-signature"
_MAX_REQUEST = 4096
_MAX_REPLY = 131072
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HASH = re.compile(r"[0-9a-f]{64}")


def _encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _decode(body: bytes) -> dict:
    value = json.loads(body, object_pairs_hook=_unique_fields, parse_constant=_reject_nonfinite)
    if not isinstance(value, dict):
        raise ValueError("invalid admission message")
    return value


def _request_body(request: DispatchRequest) -> bytes:
    return _encode(asdict(request) | {
        "attempt_id": str(request.attempt_id), "expires_at": request.expires_at.isoformat(),
    })


def _parse_request(body: bytes) -> DispatchRequest:
    value = _decode(body)
    request = DispatchRequest(**(value | {
        "attempt_id": UUID(value["attempt_id"]), "provider": Provider(value["provider"]),
        "expires_at": datetime.fromisoformat(value["expires_at"]),
    }))
    now = datetime.now(UTC)
    if (
        request.expires_at.utcoffset() != timedelta(0)
        or not now < request.expires_at <= now + timedelta(minutes=10)
        or not all(_HASH.fullmatch(v) for v in (request.request_hash, request.projection_sha256))
        or request.purpose not in {"DIALOGUE", "DISCOVERY"}
        or not all(_IDENTIFIER.fullmatch(v) for v in (
            request.model, request.profile_id, request.envelope_version,
        ))
        or any(type(v) is not int or not 1 <= v <= 2**53 - 1 for v in (
            request.profile_generation, request.model_generation, request.credential_generation,
        ))
    ):
        raise ValueError("invalid admission message")
    return request


class AdmissionAuthentication:
    """The transport bootstrap supplies a dedicated private key for this RPC."""

    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("invalid admission authentication configuration")
        self._key = key

    def request_signature(self, body: bytes) -> str:
        return digest(self._key, b"apf-admit-request-v1\0" + body, "sha256").hex()

    def response_signature(self, request_body: bytes, body: bytes) -> str:
        bound_request = digest(self._key, request_body, "sha256")
        return digest(self._key, b"apf-admit-response-v1\0" + bound_request + body, "sha256").hex()

    def cancellation_signature(self, body: bytes) -> str:
        return digest(self._key, b"apf-dispatch-cancel-v1\0" + body, "sha256").hex()

    def gateway_signature(self, body: bytes) -> str:
        return digest(self._key, b"apf-gateway-dispatch-v1\0" + body, "sha256").hex()

    def gateway_response_signature(self, request_body: bytes, body: bytes) -> str:
        bound_request = digest(self._key, request_body, "sha256")
        return digest(
            self._key, b"apf-gateway-response-v1\0" + bound_request + body, "sha256",
        ).hex()


def _matches_signature(expected: str, received: str) -> bool:
    return bool(_HASH.fullmatch(received)) and compare_digest(expected, received)


async def _signed_body(
    request: Request, signature_for: Callable[[bytes], str],
) -> bytes | Response:
    signatures = request.headers.getlist(SIGNATURE)
    if len(signatures) != 1 or not _HASH.fullmatch(signatures[0]):
        return Response(status_code=403)
    buffer = bytearray()
    async for chunk in request.stream():
        if len(buffer) + len(chunk) > _MAX_REQUEST:
            return Response(status_code=413)
        buffer.extend(chunk)
    body = bytes(buffer)
    if not _matches_signature(signature_for(body), signatures[0]):
        return Response(status_code=403)
    return body


def create_admission_app(
    auth: AdmissionAuthentication,
    consume: Callable[[DispatchRequest], AuthorizedDispatch | None],
) -> FastAPI:
    """Expose only the private admission route; consume must commit before returning."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    register_admission(app, auth, consume)
    return app


def register_admission(app, auth, consume):
    @app.post(ADMISSION_PATH)
    async def admit(http_request: Request) -> Response:
        body = await _signed_body(http_request, auth.request_signature)
        if isinstance(body, Response):
            return body
        try:
            request = _parse_request(body)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return Response(status_code=400)
        try:
            approved = await run_in_threadpool(consume, request)
            if not isinstance(approved, AuthorizedDispatch) or approved.request != request:
                return Response(status_code=403)
            if type(approved.projection) is not bytes or len(approved.projection) > 16384:
                return Response(status_code=403)
            payload = _encode({
                "capability": asdict(approved.capability),
                "projection": base64.b64encode(approved.projection).decode("ascii"),
            })
        except Exception:
            return Response(status_code=503)
        if len(payload) > _MAX_REPLY:
            return Response(status_code=503)
        return Response(payload, media_type="application/json", headers={
            SIGNATURE: auth.response_signature(body, payload), "cache-control": "no-store",
        })

def create_cancellation_app(
    auth: AdmissionAuthentication, cancel: Callable[[UUID, str], None],
) -> FastAPI:
    """Accept only signed cancellation for the active attempt and binding."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post(CANCELLATION_PATH)
    async def cancel_attempt(http_request: Request) -> Response:
        body = await _signed_body(http_request, auth.cancellation_signature)
        if isinstance(body, Response):
            return body
        try:
            value = _decode(body)
            if set(value) != {"attempt_id", "request_hash", "expires_at"}:
                return Response(status_code=400)
            attempt_id = UUID(value["attempt_id"])
            request_hash = value["request_hash"]
            expires_at = datetime.fromisoformat(value["expires_at"])
            now = datetime.now(UTC)
            if (
                not _HASH.fullmatch(request_hash) or expires_at.utcoffset() != timedelta(0)
                or not now < expires_at <= now + timedelta(minutes=1)
            ):
                return Response(status_code=400)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            return Response(status_code=400)
        cancel(attempt_id, request_hash)
        return Response(status_code=204)

    return app


class CancellationClient:
    """Notify the gateway after the supervisor has committed a revocation."""

    def __init__(self, client: httpx.Client, auth: AdmissionAuthentication):
        self._client = client
        self._auth = auth

    def cancel(self, attempt_id: UUID, request_hash: str) -> bool:
        body = _encode({
            "attempt_id": str(attempt_id), "request_hash": request_hash,
            "expires_at": (datetime.now(UTC) + timedelta(seconds=20)).isoformat(),
        })
        try:
            with self._client.stream(
                "POST", CANCELLATION_PATH, content=body, follow_redirects=False,
                headers={SIGNATURE: self._auth.cancellation_signature(body)},
            ) as reply:
                return reply.status_code == 204
        except httpx.HTTPError:
            return False


def decode_admission(
    request: DispatchRequest, payload: bytes, signature: str, auth: AdmissionAuthentication,
) -> AuthorizedDispatch | None:
    if len(payload) > _MAX_REPLY or not _matches_signature(
        auth.response_signature(_request_body(request), payload), signature,
    ):
        return None
    try:
        value = _decode(payload)
        if set(value) != {"capability", "projection"}:
            return None
        capability = value["capability"]
        profile = Capability(**(capability | {"provider": Provider(capability["provider"])}))
        projection = base64.b64decode(value["projection"], validate=True)
    except (ValueError, TypeError, KeyError, RecursionError):
        return None
    return AuthorizedDispatch(request, profile, projection)


class AdmissionClient:
    """Call the fixed route on a private, bootstrap-configured HTTP client."""

    def __init__(self, client: httpx.Client, auth: AdmissionAuthentication):
        self._client = client
        self._auth = auth

    def authorize(self, request: DispatchRequest) -> AuthorizedDispatch | None:
        body = _request_body(request)
        if len(body) > _MAX_REQUEST:
            return None
        try:
            with self._client.stream(
                "POST", ADMISSION_PATH, content=body, follow_redirects=False,
                headers={SIGNATURE: self._auth.request_signature(body)},
            ) as reply:
                signatures = reply.headers.get_list(SIGNATURE)
                if reply.status_code != 200 or len(signatures) != 1:
                    return None
                payload = bytearray()
                for chunk in reply.iter_bytes(chunk_size=8192):
                    if len(payload) + len(chunk) > _MAX_REPLY:
                        return None
                    payload.extend(chunk)
        except httpx.HTTPError:
            return None
        return decode_admission(request, bytes(payload), signatures[0], self._auth)
