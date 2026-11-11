"""Fixed credential-free relay to the existing private Paper API."""

import json
import secrets
from time import monotonic

import httpx

from packages.research.jobs import ResearchJobError
from services.control_api.agent_research_routes import AgentResearchError
from services.control_api.research_dto import RESPONSE_MODELS
from services.control_api.research_routes import CURRENT_TRACE, ERROR_CODES
from services.control_api.research_service import identity
from services.runtime import agent_research_relay as relay


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


class AgentResearchClient:
    """Send authenticated operations to the fixed private Research supervisor."""

    def __init__(self, auth: relay.AgentRelayAuthentication, *, transport=None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._auth = auth
        self._transport = transport

    def __call__(self, operation, params, query, payload, *, actor, key, trace, scope):
        try:
            body = relay.request_body(
                operation, params, query, payload, actor=actor, key=key, trace=trace, scope=scope,
            )
            deadline = monotonic() + 8
            with httpx.Client(
                transport=self._transport, timeout=5, trust_env=False, follow_redirects=False,
            ) as client:
                with client.stream("POST", relay.RELAY_URL, content=body, headers={
                    "Content-Type": "application/json", "Accept-Encoding": "identity",
                    relay.SIGNATURE_HEADER: self._auth.request_signature(body),
                }) as response:
                    signatures = response.headers.get_list(relay.SIGNATURE_HEADER)
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type") != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                        or len(signatures) != 1
                    ):
                        raise ValueError("invalid Research relay response")
                    lengths = response.headers.get_list("content-length")
                    if lengths and (
                        len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                        or len(lengths[0]) > 8 or int(lengths[0]) > relay.MAX_REPLY
                    ):
                        raise ValueError("invalid Research relay response length")
                    raw = bytearray()
                    for chunk in response.iter_raw():
                        if len(raw) + len(chunk) > relay.MAX_REPLY or monotonic() >= deadline:
                            raise ValueError("Research relay response limit exceeded")
                        raw.extend(chunk)
            reply = bytes(raw)
            expected_signature = self._auth.response_signature(body, reply)
            if not relay.matches_signature(expected_signature, signatures[0]):
                raise ValueError("invalid Research relay response signature")
            return relay.parse_reply(operation, trace, reply)
        except Exception:
            pass
        raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")
