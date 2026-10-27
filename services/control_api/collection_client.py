"""Fixed private API relay; bounded sanitized DTOs without ambient credentials."""

import json
import secrets
from time import monotonic

import httpx

from services.control_api.backtest_service import identity, pairs
from services.control_api.collection_dto import RESPONSE_MODELS
from services.control_api.collection_routes import CURRENT_TRACE
from services.control_api.collection_service import CollectionError
from services.control_api.readiness_dto import ReadinessProblem


class CollectionClient:
    def __init__(self, *, transport=None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._transport = transport

    def _request(self, operation, path, *, params=None, body=None, key=None):
        failure = (503, "UNAVAILABLE")
        try:
            with httpx.Client(
                transport=self._transport, timeout=5, trust_env=False, follow_redirects=False
            ) as client:
                headers = {"Accept-Encoding": "identity"}
                if key:
                    headers["Idempotency-Key"] = key
                trace = CURRENT_TRACE.get()
                if trace:
                    headers["traceparent"] = "00-" + trace + "-" + secrets.token_hex(8) + "-00"
                with client.stream(
                    "PUT" if operation == "update_plan" else "GET",
                    "http://paper-api:8012/api/v1/research/collection/plans" + path,
                    params=params,
                    json=body,
                    headers=headers,
                ) as response:
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError()
                    length = response.headers.get("content-length")
                    if length and (
                        not length.isascii()
                        or not length.isdigit()
                        or len(length) > 12
                        or int(length) > 16 * 1024 * 1024
                    ):
                        raise ValueError()
                    raw, deadline = bytearray(), monotonic() + 8
                    for chunk in response.iter_raw():
                        if len(raw) + len(chunk) > 16 * 1024 * 1024 or monotonic() >= deadline:
                            raise ValueError()
                        raw.extend(chunk)
                    value = json.loads(
                        raw,
                        object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if response.status_code == (200):
                        return RESPONSE_MODELS[operation].model_validate(value).model_dump()
                    error = ReadinessProblem.model_validate(value)
                    if error.status == response.status_code:
                        failure = error.status, error.code
        except Exception:
            failure = 503, "UNAVAILABLE"
        raise CollectionError(*failure)

    def read_plans(self, values):
        return self._request("read_plans", "", params=values)

    def update_plan(self, instrument_id, payload, actor, key, trace, if_match=None):
        identity(instrument_id)
        if actor != "local-operator":
            raise CollectionError(403, "FORBIDDEN")
        if if_match is not None:
            raise CollectionError(400, "INVALID_REQUEST")
        return self._request("update_plan", "/" + instrument_id, body=payload, key=key)
