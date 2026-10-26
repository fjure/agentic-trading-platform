"""Fixed credential-free relay to the existing private Paper API."""

import json
import secrets
from time import monotonic

import httpx

from packages.research.jobs import ResearchJobError
from services.control_api.research_dto import RESPONSE_MODELS
from services.control_api.research_routes import CURRENT_TRACE, ERROR_CODES
from services.control_api.research_service import identity


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class ResearchClient:
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
                headers = {"Idempotency-Key": key} if key else {}
                headers["Accept-Encoding"] = "identity"
                trace = CURRENT_TRACE.get()
                if trace:
                    headers["traceparent"] = "00-" + trace + "-" + secrets.token_hex(8) + "-00"
                deadline = monotonic() + 8
                with client.stream(
                    "POST" if operation in {"create_job", "resume_job"} else "GET",
                    "http://paper-api:8012/api/v1/research/" + path,
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
                        or int(length) > 2 * 1024 * 1024
                    ):
                        raise ValueError()
                    raw = bytearray()
                    for chunk in response.iter_raw():
                        if len(raw) + len(chunk) > 2 * 1024 * 1024 or monotonic() >= deadline:
                            raise ValueError()
                        raw.extend(chunk)
                    value = json.loads(
                        raw,
                        object_pairs_hook=_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                    )
                    if response.status_code in {200, 202}:
                        if response.status_code != (
                            202 if operation in {"create_job", "resume_job"} else 200
                        ):
                            raise ValueError()
                        return RESPONSE_MODELS[operation].model_validate(value).model_dump()
                    if (
                        response.status_code in {400, 401, 403, 404, 409, 503}
                        and isinstance(value, dict)
                        and value.get("code") in ERROR_CODES
                        and set(value) <= {"type", "title", "status", "code", "traceId"}
                    ):
                        failure = response.status_code, value["code"]
        except Exception:
            failure = 503, "UNAVAILABLE"
        raise ResearchJobError(*failure)

    def read_sources(self):
        return self._request("read_sources", "sources")

    def read_bars(self, values):
        return self._request("read_bars", "bars", params=values)

    def read_evidence(self, evidence_id, values):
        identity(evidence_id)
        return self._request("read_evidence", "evidence/" + evidence_id, params=values)

    def read_context(self, values):
        return self._request("read_context", "context", params=values)

    def read_jobs(self, values):
        return self._request("read_jobs", "ingestion-jobs", params=values)

    def read_job(self, job_id):
        identity(job_id)
        return self._request("read_job", "ingestion-jobs/" + job_id)

    def create_job(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise ResearchJobError(403, "FORBIDDEN")
        return self._request("create_job", "ingestion-jobs", body=payload, key=key)

    def resume_job(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise ResearchJobError(403, "FORBIDDEN")
        return self._request("resume_job", "ingestion-jobs", body=payload, key=key)
