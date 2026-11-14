"""Authenticated metadata-only channel between Setup and Research authority."""

import asyncio
import hashlib
import hmac
import json
import re

import httpx
from fastapi import Request, Response
from starlette.concurrency import run_in_threadpool

PATH = "/private/v1/research/credential-authority"
SIGNATURE = "x-research-credential-signature"
_DIGEST = re.compile(r"[a-f0-9]{64}")
_LIMIT = 1024


def _signature(key, domain, body):
    return hmac.digest(key, domain + b"\0" + body, hashlib.sha256).hex()


def _key(key):
    if type(key) is not bytes or len(key) != 32:
        raise ValueError("CREDENTIAL_AUTHORITY_UNAVAILABLE")
    return key


def register_credential_authority(app, store, key):
    key = _key(key)

    @app.post(PATH)
    async def update(request: Request):
        signatures = request.headers.getlist(SIGNATURE)
        if len(signatures) != 1 or not _DIGEST.fullmatch(signatures[0]):
            return Response(status_code=403)
        data = bytearray()
        try:
            async with asyncio.timeout(3):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > _LIMIT:
                        return Response(status_code=413)
                    data.extend(chunk)
        except TimeoutError:
            return Response(status_code=408)
        body = bytes(data)
        if not hmac.compare_digest(signatures[0], _signature(key, b"setup-request-v1", body)):
            return Response(status_code=403)
        try:
            payload = json.loads(body)
            if payload.keys() == {"provider", "expected_version"}:
                await run_in_threadpool(store.block_credential, **payload)
            elif payload.keys() == {
                "provider", "expected_version", "generation", "version", "digest",
            }:
                await run_in_threadpool(store.attest_credential, **payload)
            else:
                return Response(status_code=422)
        except Exception:
            return Response(status_code=409)
        return Response(status_code=204, headers={
            SIGNATURE: _signature(key, b"setup-confirmation-v1", body),
        })


class CredentialAuthorityClient:
    def __init__(self, client: httpx.Client, key: bytes):
        self._client = client
        self._key = _key(key)

    def _call(self, payload):
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
        try:
            with self._client.stream(
                "POST", PATH, content=body, follow_redirects=False,
                headers={SIGNATURE: _signature(self._key, b"setup-request-v1", body)},
            ) as response:
                signatures = response.headers.get_list(SIGNATURE)
                if response.status_code == 204 and len(signatures) == 1 and hmac.compare_digest(
                    signatures[0], _signature(self._key, b"setup-confirmation-v1", body),
                ):
                    return
        except (httpx.HTTPError, ValueError):
            pass
        raise RuntimeError("CREDENTIAL_AUTHORITY_UNAVAILABLE")

    def block(self, provider, expected_version):
        self._call({"provider": provider.value, "expected_version": expected_version})

    def attest(self, provider, status, digest, expected_version):
        self._call({
            "provider": provider.value, "expected_version": expected_version,
            "version": status.version, "generation": status.generation, "digest": digest,
        })
