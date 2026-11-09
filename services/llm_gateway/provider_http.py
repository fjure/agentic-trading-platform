"""Offline HTTP transport for fixed research provider routes."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry

from .content_policy import safe_result_content
from .provider_wire import (
    Provider,
    ProviderResult,
    ResultState,
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
_SYNTHETIC_CREDENTIAL = re.compile(r"offline-canary-[A-Za-z0-9_-]{32}")


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


class OfflineProviderHttp:
    """Issue protocol requests only through an injected, in-process HTTP double."""

    def __init__(self, transport: httpx.MockTransport):
        if type(transport) is not httpx.MockTransport:
            raise ValueError("PROVIDER_UNAVAILABLE")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            transport=self._transport, timeout=timeout, trust_env=False, follow_redirects=False,
        )

    def send(
        self, capability: Capability, projection: bytes,
        *, cancelled: Callable[[], bool] | None = None, synthetic_credential: str | None = None,
    ) -> ProviderResult:
        if (
            type(capability.provider) is not Provider
            or type(capability.max_output_tokens) is not int
            or not 1 <= capability.max_output_tokens <= 65536
            or not 0 < capability.timeout_seconds <= 60
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
            headers["anthropic-version"] = "2023-06-01"
        if synthetic_credential is not None:
            if not _SYNTHETIC_CREDENTIAL.fullmatch(synthetic_credential):
                return ProviderResult(ResultState.CAPABILITY_MISMATCH)
            if capability.provider is Provider.OPENAI:
                headers["authorization"] = f"Bearer {synthetic_credential}"
            else:
                headers["x-api-key"] = synthetic_credential
        if cancelled is not None and cancelled():
            return ProviderResult(ResultState.DISPATCH_CANCELLED)
        deadline = monotonic() + capability.timeout_seconds
        try:
            with self._client(capability.timeout_seconds) as client, client.stream(
                "POST", _ENDPOINTS[capability.provider], json=body, headers=headers,
            ) as response:
                if cancelled is not None and cancelled():
                    return ProviderResult(ResultState.DISPATCH_CANCELLED)
                if monotonic() >= deadline:
                    return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                if response.is_redirect:
                    return ProviderResult(ResultState.REDIRECT_DENIED)
                if response.status_code == 429:
                    return ProviderResult(ResultState.RATE_LIMITED)
                if response.status_code in {400, 401, 403, 404, 422}:
                    return ProviderResult(ResultState.REJECTED_BEFORE_EXECUTION)
                if response.status_code != 200:
                    return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return ProviderResult(ResultState.INVALID_OUTPUT)
                data = bytearray()
                for chunk in response.iter_bytes():
                    if cancelled is not None and cancelled():
                        return ProviderResult(ResultState.DISPATCH_CANCELLED)
                    if monotonic() >= deadline:
                        return ProviderResult(ResultState.OUTCOME_UNKNOWN)
                    if len(data) + len(chunk) > _MAX_RESPONSE_BYTES:
                        return ProviderResult(ResultState.INVALID_OUTPUT)
                    data.extend(chunk)
        except httpx.HTTPError:
            return ProviderResult(ResultState.OUTCOME_UNKNOWN)
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
        try:
            safe_output = safe_result_content(result.output)
        except RecursionError:
            safe_output = False
        if not safe_output:
            return ProviderResult(ResultState.INVALID_OUTPUT, result.usage)
        return result
