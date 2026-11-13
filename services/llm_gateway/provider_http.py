"""HTTP transports for fixed research provider routes."""

import asyncio
import json
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass, replace
from time import monotonic

import certifi
import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .content_policy import safe_result_content
from .provider_wire import (
    Provider,
    ProviderResult,
    ResultState,
    Usage,
    _reject_nonfinite,
    _unique_fields,
    build_request,
    parse_response,
)

_ENDPOINTS = {
    Provider.OPENAI: "https://api.openai.com/v1/responses",
    Provider.ANTHROPIC: "https://api.anthropic.com/v1/messages",
}
_MAX_RESPONSE_BYTES = 262144
_MAX_REQUEST_BYTES = 65536
_SYNTHETIC_CREDENTIAL = re.compile(r"offline-canary-[A-Za-z0-9_-]{32}")
_WIRE_CREDENTIAL = re.compile(r"[A-Za-z0-9._~+/=-]{1,2048}")
_PROVIDER_PROXY = "http://research-provider-egress:8080"


def _provider_tls_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    return context


def _bounded_usage(provider: Provider, payload: object, usage: Usage | None) -> Usage | None:
    if usage is None or not isinstance(payload, dict):
        return None
    raw = payload["usage"]
    if provider is Provider.OPENAI:
        details = raw.get("input_tokens_details")
        if payload.get("service_tier") != "default" or not isinstance(details, dict):
            return None
        cached, written = details.get("cached_tokens"), details.get("cache_write_tokens")
        if (
            type(cached) is not int or not 0 <= cached <= usage.input_tokens
            or type(written) is not int or written != 0
        ):
            return None
        return usage
    if raw.get("service_tier") != "standard":
        return None
    if (
        raw.get("inference_geo") not in (None, "global")
        or raw.get("speed") not in (None, "standard")
        or raw.get("server_tool_use") not in (None, {})
    ):
        return None
    cached = raw.get("cache_read_input_tokens")
    written = raw.get("cache_creation_input_tokens")
    if (
        type(cached) is not int or not 0 <= cached <= 2**31 - 1 - usage.input_tokens
        or type(written) is not int or written != 0
    ):
        return None
    return Usage(usage.input_tokens + cached, usage.output_tokens)


@dataclass(frozen=True, slots=True)
class Capability:
    provider: Provider
    model: str
    profile_id: str
    profile_generation: int
    model_generation: int
    credential_generation: int
    envelope_version: str
    instructions: str
    schema_json: str
    max_output_tokens: int
    timeout_seconds: float


def _response_status(response):
    if response.is_redirect:
        return ResultState.REDIRECT_DENIED
    if response.status_code == 429:
        return ResultState.RATE_LIMITED
    if response.status_code in {400, 401, 403, 404, 422}:
        return ResultState.REJECTED_BEFORE_EXECUTION
    if response.status_code != 200:
        return ResultState.OUTCOME_UNKNOWN
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        return ResultState.INVALID_OUTPUT
    return None

def conservative_input_bound(capability: Capability, projection: bytes) -> int:
    """Reserve for all serialized input bytes plus provider framing overhead."""
    schema = json.loads(
        capability.schema_json, object_pairs_hook=_unique_fields,
        parse_constant=_reject_nonfinite,
    )
    body = build_request(
        capability.provider, model=capability.model, instructions=capability.instructions,
        projection=projection.decode("utf-8"), max_output_tokens=capability.max_output_tokens,
        schema=schema,
    )
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()) + 256


class _ProviderHttp:
    def _send(
        self, capability: Capability, projection: bytes,
        *, cancelled: Callable[[], bool] | None, credential: str | None, production: bool,
    ) -> ProviderResult:
        if (
            type(capability.provider) is not Provider
            or type(capability.max_output_tokens) is not int
            or not 1 <= capability.max_output_tokens <= 65536
            or not 0 < capability.timeout_seconds <= (15 if production else 60)
            or type(projection) is not bytes or not 1 <= len(projection) <= 16384
        ):
            return ProviderResult(ResultState.CAPABILITY_MISMATCH)
        try:
            schema = json.loads(
                capability.schema_json, object_pairs_hook=_unique_fields,
                parse_constant=_reject_nonfinite,
            )
            Draft202012Validator.check_schema(schema)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                return ProviderResult(ResultState.CAPABILITY_MISMATCH)
            body = build_request(
                capability.provider, model=capability.model, instructions=capability.instructions,
                projection=projection.decode("utf-8"),
                max_output_tokens=capability.max_output_tokens, schema=schema,
            )
        except (ValueError, RecursionError, TypeError, SchemaError):
            return ProviderResult(ResultState.CAPABILITY_MISMATCH)
        headers = {"accept": "application/json", "accept-encoding": "identity"}
        if capability.provider is Provider.ANTHROPIC:
            headers["anthropic-version"] = "2026-10-21" if production else "2023-06-01"
        elif production:
            body["service_tier"] = "default"
        if credential is not None:
            pattern = _WIRE_CREDENTIAL if production else _SYNTHETIC_CREDENTIAL
            if not isinstance(credential, str) or not pattern.fullmatch(credential):
                return ProviderResult(ResultState.CAPABILITY_MISMATCH)
            if capability.provider is Provider.OPENAI:
                headers["authorization"] = f"Bearer {credential}"
            else:
                headers["x-api-key"] = credential
        elif production:
            return ProviderResult(ResultState.CAPABILITY_MISMATCH)
        try:
            encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        except (ValueError, RecursionError, TypeError):
            return ProviderResult(ResultState.CAPABILITY_MISMATCH)
        if len(encoded) > _MAX_REQUEST_BYTES:
            return ProviderResult(ResultState.CAPABILITY_MISMATCH)
        headers["content-type"] = "application/json"
        if cancelled is not None and cancelled():
            return ProviderResult(ResultState.DISPATCH_CANCELLED)
        data = self._read(capability, encoded, headers, cancelled)
        if isinstance(data, ProviderResult):
            return data
        try:
            payload = json.loads(
                data, object_pairs_hook=_unique_fields, parse_constant=_reject_nonfinite,
            )
        except (ValueError, RecursionError):
            return ProviderResult(ResultState.INVALID_OUTPUT)
        result = parse_response(
            capability.provider, payload, expected_model=capability.model,
            schema=Draft202012Validator(schema, registry=Registry()),
        )
        if production:
            usage = _bounded_usage(capability.provider, payload, result.usage)
            if result.state is ResultState.MODEL_MISMATCH:
                return replace(result, usage=None)
            if usage is None or usage.output_tokens > capability.max_output_tokens:
                return ProviderResult(ResultState.OUTCOME_UNKNOWN)
            result = replace(result, usage=usage)
        try:
            safe_output = safe_result_content(result.output) and (
                not production or result.output is None
                or credential not in json.dumps(result.output, ensure_ascii=False)
            )
        except RecursionError:
            safe_output = False
        if not safe_output:
            return ProviderResult(ResultState.INVALID_OUTPUT, result.usage)
        return result


class OfflineProviderHttp(_ProviderHttp):
    """Issue protocol requests only through an injected, in-process HTTP double."""

    def __init__(self, transport: httpx.MockTransport):
        if type(transport) is not httpx.MockTransport:
            raise ValueError("PROVIDER_UNAVAILABLE")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            transport=self._transport, timeout=timeout, trust_env=False, follow_redirects=False,
        )

    def _read(self, capability, encoded, headers, cancelled):
        deadline = monotonic() + capability.timeout_seconds
        try:
            with self._client(capability.timeout_seconds) as client, client.stream(
                "POST", _ENDPOINTS[capability.provider], content=encoded, headers=headers,
            ) as response:
                if cancelled is not None and cancelled():
                    return ProviderResult(ResultState.DISPATCH_CANCELLED)
                if monotonic() >= deadline:
                    return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                status = _response_status(response)
                if status is not None:
                    return ProviderResult(status)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if cancelled is not None and cancelled():
                        return ProviderResult(ResultState.DISPATCH_CANCELLED)
                    if monotonic() >= deadline:
                        return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        return ProviderResult(ResultState.INVALID_OUTPUT)
                    data.extend(chunk)
                return data
        except (httpx.HTTPError, OSError):
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)

    def send(self, capability, projection, *, cancelled=None, synthetic_credential=None):
        return self._send(
            capability, projection, cancelled=cancelled, credential=synthetic_credential,
            production=False,
        )


class ProviderHttp(_ProviderHttp):
    """Use the fixed CONNECT service and verified end-to-end TLS."""

    def _read(self, capability, encoded, headers, cancelled):
        async def receive():
            try:
                async with (
                    asyncio.timeout(capability.timeout_seconds),
                    httpx.AsyncClient(
                        proxy=_PROVIDER_PROXY, verify=_provider_tls_context(),
                        timeout=httpx.Timeout(capability.timeout_seconds, connect=3),
                        trust_env=False, follow_redirects=False,
                    ) as client,
                    client.stream(
                        "POST", _ENDPOINTS[capability.provider], content=encoded, headers=headers,
                    ) as response,
                ):
                    status = _response_status(response)
                    if status is not None:
                        return ProviderResult(status)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if cancelled is not None and cancelled():
                            return ProviderResult(ResultState.DISPATCH_CANCELLED)
                        if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                            return ProviderResult(ResultState.INVALID_OUTPUT)
                        data.extend(chunk)
                    return data
            except (TimeoutError, httpx.HTTPError, OSError):
                return ProviderResult(ResultState.OUTCOME_UNKNOWN)

        return asyncio.run(receive())

    def send(self, capability, projection, *, cancelled=None, credential: str):
        return self._send(
            capability, projection, cancelled=cancelled, credential=credential, production=True,
        )
