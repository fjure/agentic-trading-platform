"""Fixed internal Paper command relay; no credentials, broker port or environment proxy."""

from __future__ import annotations

import json
import re
from time import monotonic

import httpx

from services.setup.store import generation_filename

from . import paper_dto as dto
from .paper_commands import ERROR_CODES, PaperCommandError


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class PaperClient:
    _base = "http://paper-api:8012"

    def __init__(self, *, transport: httpx.MockTransport | None = None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._transport = transport

    def _request(self, method, path, value=None):
        deadline = monotonic() + 15
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=5.0,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                with client.stream(method, self._base + path, json=value) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if len(body) + len(chunk) > 65536 or monotonic() >= deadline:
                            raise ValueError()
                        body.extend(chunk)
                    data = json.loads(
                        body,
                        object_pairs_hook=_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if not isinstance(data, dict):
                        raise ValueError()
                    if response.status_code not in {200, 202}:
                        if (
                            set(data) != {"code"}
                            or data["code"] not in ERROR_CODES
                            or response.status_code not in {400, 404, 409, 503}
                        ):
                            raise ValueError()
                        raise PaperCommandError(response.status_code, data["code"])
                    return data
        except PaperCommandError:
            raise
        except Exception:
            raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE") from None

    @staticmethod
    def _projection(model, value):
        try:
            return model.model_validate(value).model_dump(exclude_unset=True)
        except Exception:
            raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE") from None

    def read_status(self, trace_id):
        if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise PaperCommandError(400, "INVALID_REQUEST")
        value = self._request("GET", "/status?trace=" + trace_id)
        return self._projection(dto.Status, value)

    def read_profile(self, trace_id):
        if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise PaperCommandError(400, "INVALID_REQUEST")
        value = self._request("GET", "/trading-profile?trace=" + trace_id)
        return self._projection(dto.Profile, value)

    def read_snapshot(self, trace_id):
        dto.read_query([("trace", trace_id)], private=True)
        return self._projection(dto.Snapshot, self._request("GET", "/snapshot?trace=" + trace_id))

    def _read_page(self, resource, model, limit, cursor, trace_id):
        from urllib.parse import urlencode

        values = dto.read_arguments(limit, cursor, trace_id)
        query = urlencode({key: value for key, value in values.items() if value is not None})
        return self._projection(model, self._request("GET", "/" + resource + "?" + query))

    def read_orders(self, limit, cursor, trace_id):
        return self._read_page("orders", dto.OrderPage, limit, cursor, trace_id)

    def read_fills(self, limit, cursor, trace_id):
        return self._read_page("fills", dto.FillPage, limit, cursor, trace_id)

    def read_operations(self, limit, cursor, trace_id):
        return self._read_page("operations", dto.OperationPage, limit, cursor, trace_id)

    def read_trace(self, trace_id):
        dto.read_query([("trace", trace_id)], private=True)
        return self._projection(dto.TraceView, self._request("GET", "/traces/" + trace_id))

    def save_profile(self, payload, actor_id, key, trace_id):
        payload = dto.ProfileUpdate.model_validate(payload).model_dump()
        value = self._request("PUT", "/trading-profile", {
            "payload": payload, "actorId": actor_id, "key": key, "traceId": trace_id,
        })
        return self._projection(dto.Profile, value)

    def read_operation(self, operation_id, trace_id):
        dto.identity(operation_id)
        if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise PaperCommandError(400, "INVALID_REQUEST")
        value = self._request("GET", "/operations/" + operation_id)
        return self._projection(dto.Operation, value)

    def enqueue(self, operation, payload, actor_id, key, trace_id, order_id=None):
        value = self._request(
            "POST",
            "/commands",
            {
                "operation": operation,
                "payload": payload,
                "actorId": actor_id,
                "key": key,
                "traceId": trace_id,
                "orderId": order_id,
            },
        )
        return self._projection(dto.Operation, value)

    def rotation_begin(self, actor_id, key, trace_id):
        value = self._request(
            "POST",
            "/rotation/begin",
            {
                "actorId": actor_id,
                "key": key,
                "traceId": trace_id,
            },
        )
        if set(value) != {"generation"}:
            raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE")
        try:
            generation_filename(value["generation"])
        except Exception:
            raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE") from None
        return value["generation"]

    def rotation_complete(self, generation):
        generation_filename(generation)
        value = self._request("POST", "/rotation/complete", {"generation": generation})
        if set(value) != {"completed"} or type(value["completed"]) is not bool:
            raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE")
        return value["completed"]
