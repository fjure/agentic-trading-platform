"""Authenticated browser endpoints and shared agent research wire validation."""

import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache, lru_cache, partial
from pathlib import Path
from types import MappingProxyType
from typing import Any

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from pydantic import RootModel

from services.control_api.auth import AuthDenied
from services.control_api.retrospective_dto import FORMATS

BASE = "/api/v1/research/agent"
SETUP = "/api/v1/integrations/research-providers"
PROVIDER_READINESS = "/api/v1/integrations/research-provider-readiness"
PAGED = ("cursor", "limit")
SCOPE = re.compile(r"[0-9a-f]{64}")
TRACE = re.compile(r"[0-9a-f]{32}")
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}")
OPAQUE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


@dataclass(frozen=True)
class AgentOperation:
    operation_id: str
    method: str
    path: str
    request: str | None
    responses: Mapping[int, str]
    query: tuple[str, ...]

    @property
    def setup(self) -> bool:
        return self.path.startswith(SETUP + "/")


def _operation(operation_id, method, path, responses, *, request=None, query=()):
    return AgentOperation(operation_id, method, path, request, MappingProxyType(responses), query)


OPERATIONS = (
    _operation(
        "createAgentProcessingPreview",
        "POST",
        BASE + "/dialogues/{id}/turns/{turnId}/processing-previews",
        {201: "ProcessingPreviewV1"},
        request="ProcessingPreviewRequestV1",
    ),
    _operation(
        "getAgentProcessingStatus",
        "GET",
        BASE + "/dialogues/{id}/turns/{turnId}/processing",
        {200: "ProcessingStatusV1"},
    ),
    _operation(
        "decideAgentProcessing",
        "POST",
        BASE + "/dialogues/{id}/turns/{turnId}/processing-decisions",
        {202: "AgentSendAcceptedV1", 200: "ProcessingStatusV1"},
        request="ProcessingDecisionV1",
    ),
    _operation(
        "listAgentIdeas",
        "GET",
        BASE + "/ideas",
        {200: "AgentIdeaListV1"},
        query=PAGED,
    ),
    _operation(
        "createAgentIdea",
        "POST",
        BASE + "/ideas",
        {201: "AgentIdeaVersionV1"},
        request="AgentCreateIdeaV1",
    ),
    _operation(
        "getAgentIdea",
        "GET",
        BASE + "/ideas/{id}",
        {200: "AgentIdeaVersionV1"},
        query=("version",),
    ),
    _operation(
        "reviseAgentIdea",
        "POST",
        BASE + "/ideas/{id}/revisions",
        {201: "AgentIdeaVersionV1"},
        request="AgentIdeaRevisionV1",
    ),
    _operation(
        "createAgentDialogue",
        "POST",
        BASE + "/dialogues",
        {201: "AgentDialogueV1"},
        request="AgentCreateDialogueV1",
    ),
    _operation(
        "getAgentDialogue",
        "GET",
        BASE + "/dialogues/{id}",
        {200: "AgentDialogueV1"},
        query=PAGED,
    ),
    _operation(
        "createAgentDialogueTurn",
        "POST",
        BASE + "/dialogues/{id}/turns",
        {202: "AgentDialogueTurnV1"},
        request="AgentCreateDialogueTurnV1",
    ),
    _operation(
        "decideAgentHypothesis",
        "POST",
        BASE + "/dialogues/{id}/decisions",
        {200: "AgentDialogueDecisionReceiptV1"},
        request="AgentDialogueDecisionV1",
    ),
    _operation(
        "getAgentRegistry",
        "GET",
        BASE + "/registry",
        {200: "AgentRegistryPageV1"},
        query=PAGED,
    ),
    _operation(
        "listResearchAgentRuns",
        "GET",
        BASE + "/runs",
        {200: "AgentRunPageV1"},
        query=PAGED,
    ),
    _operation(
        "getAgentRun",
        "GET",
        BASE + "/runs/{id}",
        {200: "AgentRunV1"},
    ),
    _operation(
        "listAgentRunEvents",
        "GET",
        BASE + "/runs/{id}/events",
        {200: "AgentRunEventPageV1"},
        query=PAGED,
    ),
    _operation(
        "cancelAgentRun",
        "POST",
        BASE + "/runs/{id}/cancel",
        {200: "AgentRunCancellationV1"},
        request="AgentRunCancelRequestV1",
    ),
    _operation(
        "getAgentSchedule",
        "GET",
        BASE + "/schedule",
        {200: "AgentScheduleV1"},
    ),
    _operation(
        "updateAgentSchedule",
        "PUT",
        BASE + "/schedule",
        {200: "AgentScheduleV1"},
        request="AgentScheduleUpdateV1",
    ),
    _operation(
        "commandAgentSchedule",
        "POST",
        BASE + "/schedule/commands",
        {200: "AgentScheduleCommandResultV1"},
        request="AgentScheduleCommandV1",
    ),
    _operation(
        "listAgentProviders",
        "GET",
        BASE + "/providers",
        {200: "AgentProviderPageV1"},
        query=PAGED,
    ),
    _operation(
        "probeAgentProvider",
        "POST",
        BASE + "/providers/{provider}/probe",
        {202: "AgentProviderProbeResultV1"},
        request="AgentProviderProbeRequestV1",
    ),
    _operation(
        "saveAgentProviderCredential",
        "PUT",
        SETUP + "/{provider}",
        {200: "AgentCredentialStatusV1"},
        request="AgentCredentialSaveRequestV1",
    ),
    _operation(
        "rotateAgentProviderCredential",
        "POST",
        SETUP + "/{provider}/rotate",
        {200: "AgentCredentialStatusV1"},
        request="AgentCredentialRotateRequestV1",
    ),
    _operation(
        "activateAgentProviderCredential",
        "POST",
        SETUP + "/{provider}/activate",
        {200: "AgentCredentialStatusV1"},
        request="AgentCredentialActivateRequestV1",
    ),
    _operation(
        "revokeAgentProviderCredential",
        "POST",
        SETUP + "/{provider}/revoke",
        {200: "AgentCredentialStatusV1"},
        request="AgentCredentialRevokeRequestV1",
    ),
    _operation(
        "listAgentQuantApprovals",
        "GET",
        BASE + "/approvals",
        {200: "AgentQuantApprovalPageV1"},
        query=PAGED,
    ),
    _operation(
        "getAgentQuantApproval",
        "GET",
        BASE + "/approvals/{id}",
        {200: "AgentQuantApprovalV1"},
    ),
    _operation(
        "decideAgentQuantApproval",
        "POST",
        BASE + "/approvals/{id}/decision",
        {200: "AgentQuantApprovalRejectedV1", 202: "AgentQuantApprovalStartedV1"},
        request="AgentQuantApprovalDecisionV1",
    ),
    _operation(
        "listAgentResearchReports",
        "GET",
        BASE + "/reports",
        {200: "AgentReportPageV1"},
        query=PAGED,
    ),
    _operation(
        "getAgentResearchReport",
        "GET",
        BASE + "/reports/{id}",
        {200: "AgentReportV1"},
        query=("version",),
    ),
)


class AgentResearchError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def _contract():
    path = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/agent-research.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def _validator(component):
    return Draft202012Validator(
        {**_contract(), "$ref": "#/$defs/" + component}, format_checker=FORMATS
    )


@cache
def _disabled_provider_readiness():
    path = Path(__file__).resolve().parents[2] / "config/research/provider-catalog-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not _validator("ResearchProviderReadinessV1").is_valid(value)
        or value["state"] != "DISABLED"
        or any(
            item["evidenceState"] != "INCOMPLETE" or not item["blockingReasons"]
            for item in value["candidates"]
        )
    ):
        raise ValueError("invalid disabled research provider catalog")
    return value


def agent_research_path(path: str) -> bool:
    return path == PROVIDER_READINESS or any(
        path == base or path.startswith(base + "/") for base in (BASE, SETUP)
    )


def validate_arguments(operation, params, query):
    required = set(re.findall(r"{(\w+)}", operation.path))
    if set(params) != required:
        raise AgentResearchError(422, "INVALID_REQUEST")
    for name, value in params.items():
        if not isinstance(value, str) or (
            value not in {"OPENAI", "ANTHROPIC"}
            if name == "provider"
            else not OPAQUE_ID.fullmatch(value) or value in {".", ".."}
        ):
            raise AgentResearchError(422, "INVALID_REQUEST")

    values = {}
    for name, value in query:
        if name not in operation.query or name in values:
            raise AgentResearchError(422, "INVALID_REQUEST")
        if name == "cursor":
            if not _validator("Cursor").is_valid(value) or not value.isprintable():
                raise AgentResearchError(422, "INVALID_REQUEST")
        else:
            if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,127}", value):
                value = int(value)
            if type(value) is not int or value < 1 or (name == "limit" and value > 200):
                raise AgentResearchError(422, "INVALID_REQUEST")
        values[name] = value
    return dict(params), values


def validate_payload(operation, payload):
    if operation.request is None:
        valid = payload is None
    else:
        valid = isinstance(payload, dict) and _validator(operation.request).is_valid(payload)
    if not valid:
        raise AgentResearchError(422, "INVALID_REQUEST")


def validate_response(operation, status, value):
    if type(status) is not int or status not in operation.responses or not isinstance(value, dict):
        raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")
    if not _validator(operation.responses[status]).is_valid(value):
        raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")


_PROBLEMS = {
    401: ("SESSION_REQUIRED",),
    403: ("REQUEST_FORBIDDEN", "MODEL_CONTEXT_DENIED"),
    404: ("RESOURCE_NOT_FOUND",),
    409: (
        "VERSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "PROCESSING_GRANT_REQUIRED",
        "PROCESSING_GRANT_INVALID",
        "PROCESSING_GRANT_REVOKED",
        "PROCESSING_PREVIEW_UNAVAILABLE",
    ),
    422: ("INVALID_REQUEST", "MODEL_CONTEXT_UNCLASSIFIED", "MODEL_CONTEXT_DENIED"),
    429: ("BUDGET_EXHAUSTED",),
    503: ("PROVIDER_UNAVAILABLE",),
}
_APPROVAL_PROBLEMS = {
    409: (
        "APPROVAL_STALE",
        "APPROVAL_REQUIRED",
        "APPROVAL_EXPIRED",
        "APPROVAL_ALREADY_DECIDED",
        "RUN_BLOCKED",
        "POLICY_REVOKED",
        "IDEMPOTENCY_CONFLICT",
    ),
    429: ("QUANT_CAPACITY_EXHAUSTED", "AGENT_BUDGET_EXHAUSTED"),
}


def _approval_problem(operation, status):
    return (
        operation is not None
        and operation.operation_id == "decideAgentQuantApproval"
        and status in _APPROVAL_PROBLEMS
    )


def _problem_codes(operation, status):
    if _approval_problem(operation, status):
        return _APPROVAL_PROBLEMS[status]
    return _PROBLEMS.get(status, ())


def problem_value(status, code, trace, operation=None):
    status = 422 if status == 400 else status
    if status not in _PROBLEMS:
        status = 503
    codes = _problem_codes(operation, status)
    if code not in codes:
        code = codes[0]
    if not isinstance(trace, str) or not TRACE.fullmatch(trace):
        trace = secrets.token_hex(16)
    approval = _approval_problem(operation, status)
    return {
        "type": "https://agentic-trading-lab.invalid/problems/agent-"
        + ("approval" if approval else "processing"),
        "title": "Research approval refused" if approval else "Research processing refused",
        "status": status,
        "code": code,
        "traceId": trace,
    }


def validate_problem(operation, status, value):
    component = (
        "AgentQuantApprovalProblemV1"
        if _approval_problem(operation, status)
        else "AgentProcessingProblem"
    )
    if (
        type(status) is not int
        or not _validator(component).is_valid(value)
        or value["status"] != status
        or value["code"] not in _problem_codes(operation, status)
    ):
        raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")


def agent_research_problem(status, code, trace, operation=None):
    value = problem_value(status, code, trace, operation)
    return JSONResponse(
        value,
        status_code=value["status"],
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


def register_agent_research_routes(
    app, *, handler, body_reader, key_reader, scope_reader, setup_handler=None
):
    # Separate the write-only setup boundary from the supervisor relay.
    workers = anyio.CapacityLimiter(2)
    setup_workers = anyio.CapacityLimiter(2)
    admission = anyio.CapacityLimiter(2)
    setup_admission = anyio.CapacityLimiter(2)

    def endpoint(operation):
        invoke = setup_handler if operation.setup else handler
        limiter = setup_workers if operation.setup else workers
        slots = setup_admission if operation.setup else admission

        async def route(request: Request):
            trace = request.state.trace_id
            try:
                slots.acquire_nowait()
            except anyio.WouldBlock:
                return agent_research_problem(503, "PROVIDER_UNAVAILABLE", trace, operation)
            try:
                actor = request.state.actor_id
                scope = scope_reader(request)
                if (
                    actor != "local-operator"
                    or not isinstance(scope, str)
                    or not SCOPE.fullmatch(scope)
                ):
                    raise AgentResearchError(403, "REQUEST_FORBIDDEN")
                if len(request.scope["query_string"]) > 8192:
                    raise AgentResearchError(422, "INVALID_REQUEST")
                params, query = validate_arguments(
                    operation, request.path_params, request.query_params.multi_items()
                )
                key, payload = None, None
                if operation.request is not None:
                    content_types = request.headers.getlist("content-type")
                    if len(content_types) != 1 or tuple(
                        part.strip().lower() for part in content_types[0].split(";")
                    ) not in (("application/json",), ("application/json", "charset=utf-8")):
                        raise AgentResearchError(422, "INVALID_REQUEST")
                    key = key_reader(request)
                    payload = await body_reader(request, RootModel[dict[str, Any]])
                validate_payload(operation, payload)
                if invoke is None:
                    raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")
                action = partial(
                    invoke,
                    operation,
                    params,
                    query,
                    payload,
                    actor=actor,
                    key=key,
                    trace=trace,
                    scope=scope,
                )
                status, value = await anyio.to_thread.run_sync(action, limiter=limiter)
                if status in _PROBLEMS:
                    validate_problem(operation, status, value)
                    return agent_research_problem(status, value["code"], trace, operation)
                validate_response(operation, status, value)
                return JSONResponse(
                    value, status_code=status, headers={"Cache-Control": "no-store"}
                )
            except (AgentResearchError, AuthDenied) as exc:
                return agent_research_problem(exc.status, exc.code, trace, operation)
            except Exception:
                return agent_research_problem(503, "PROVIDER_UNAVAILABLE", trace, operation)
            finally:
                slots.release()

        return route

    for operation in OPERATIONS:
        app.add_api_route(
            operation.path,
            endpoint(operation),
            methods=[operation.method],
            operation_id=operation.operation_id,
        )

    async def provider_readiness(request: Request):
        trace = request.state.trace_id
        if request.state.actor_id != "local-operator" or not SCOPE.fullmatch(
            scope_reader(request) or ""
        ):
            return agent_research_problem(403, "REQUEST_FORBIDDEN", trace)
        if request.scope["query_string"]:
            return agent_research_problem(422, "INVALID_REQUEST", trace)
        try:
            value = _disabled_provider_readiness()
        except (OSError, ValueError):
            return agent_research_problem(503, "PROVIDER_UNAVAILABLE", trace)
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    app.add_api_route(
        PROVIDER_READINESS,
        provider_readiness,
        methods=["GET"],
        operation_id="getResearchProviderReadiness",
    )
