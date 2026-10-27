"""Fixed private API relay; bounded sanitized DTOs without ambient credentials."""

import json
import secrets
from time import monotonic

import httpx

from packages.backtesting.readiness_jobs import ReadinessError
from services.control_api.backtest_service import identity, pairs
from services.control_api.readiness_dto import RESPONSE_MODELS, ReadinessProblem
from services.control_api.readiness_routes import CURRENT_TRACE


class ReadinessClient:
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
                    "POST" if operation == "create_assessment" else "GET",
                    "http://paper-api:8012/api/v1/research/data-readiness/assessments" + path,
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
                    if response.status_code == (202 if operation == "create_assessment" else 200):
                        return RESPONSE_MODELS[operation].model_validate(value).model_dump()
                    error = ReadinessProblem.model_validate(value)
                    if error.status == response.status_code:
                        failure = error.status, error.code
        except Exception:
            failure = 503, "UNAVAILABLE"
        raise ReadinessError(*failure)

    def create_assessment(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise ReadinessError(403, "FORBIDDEN")
        return self._request("create_assessment", "", body=payload, key=key)

    def read_assessment(self, assessment_id):
        identity(assessment_id)
        return self._request("read_assessment", "/" + assessment_id)

    def read_findings(self, assessment_id, values):
        identity(assessment_id)
        return self._request("read_findings", "/" + assessment_id + "/findings", params=values)
