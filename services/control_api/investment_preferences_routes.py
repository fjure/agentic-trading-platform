"""Authenticated, actor-scoped onboarding preferences transport."""

from datetime import datetime
from typing import Annotated, Literal, Self

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from services.control_api.auth import AuthDenied

PREFERENCES_PATH = "/api/v1/experience/preferences"
_NAMESPACES = {"FAKE": "fake", "DEMO": "demo", "ALPACA_PAPER": "alpaca-paper"}
_PROBLEMS = {
    401: ("SESSION_REQUIRED", "Lokale Sitzung erforderlich"),
    403: ("REQUEST_FORBIDDEN", "Anfrage abgelehnt"),
    404: ("RESOURCE_NOT_FOUND", "Quelle nicht verfügbar"),
    409: ("VERSION_CONFLICT", "Präferenzen haben sich geändert"),
    422: ("INVALID_REQUEST", "Ungültige Präferenzen"),
    429: ("CAPABILITY_UNAVAILABLE", "Zu viele Anfragen"),
    503: ("CAPABILITY_UNAVAILABLE", "Präferenzen derzeit nicht verfügbar"),
}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ContextReference(_Closed):
    context: Literal["DEMO", "FAKE", "ALPACA_PAPER"]
    namespace: Literal["demo", "fake", "alpaca-paper"]
    demoRunId: Annotated[
        str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ] | None = None

    @model_validator(mode="after")
    def context_binding(self) -> Self:
        if self.namespace != _NAMESPACES[self.context]:
            raise ValueError("context namespace mismatch")
        if (self.context == "DEMO") != bool(self.demoRunId):
            raise ValueError("demo reference mismatch")
        if self.context != "DEMO" and "demoRunId" in self.model_fields_set:
            raise ValueError("unexpected demo reference")
        return self


def _check_utc(value: str | None) -> str | None:
    if value is not None:
        if "T" not in value or not value.endswith("Z"):
            raise ValueError("UTC timestamp required")
        datetime.fromisoformat(value)
    return value


class OnboardingStep(_Closed):
    step: Literal["EXPERIENCE", "GOALS", "HORIZON", "LOSS_TOLERANCE", "CONTEXT"]
    state: Literal["COMPLETED", "SKIPPED", "UNSEEN"]
    updatedAt: str | None

    _utc = field_validator("updatedAt")(_check_utc)


class PreferencesFields(_Closed):
    experienceLevel: Literal["UNSPECIFIED", "BEGINNER", "EXPERIENCED"]
    goals: Annotated[
        list[Literal["LEARN", "UNDERSTAND_PORTFOLIO", "RESEARCH_STRATEGIES"]],
        Field(max_length=3),
    ]
    horizon: Literal["UNSPECIFIED", "SHORT", "MEDIUM", "LONG"]
    lossTolerance: Literal["UNSPECIFIED", "LOW", "MEDIUM", "HIGH"]
    contextReference: ContextReference
    onboardingSteps: Annotated[list[OnboardingStep], Field(max_length=5)]

    @model_validator(mode="after")
    def distinct_entries(self) -> Self:
        if len(set(self.goals)) != len(self.goals):
            raise ValueError("duplicate goal")
        if len({step.step for step in self.onboardingSteps}) != len(self.onboardingSteps):
            raise ValueError("duplicate onboarding step")
        return self


class PreferencesUpdate(PreferencesFields):
    expectedVersion: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    reset: bool


class PreferencesView(PreferencesFields):
    schemaVersion: Literal["1"]
    version: Annotated[int, Field(ge=0)]
    updatedAt: str | None

    _utc = field_validator("updatedAt")(_check_utc)


def preferences_problem(status: int, code: str, trace: str) -> JSONResponse:
    if status == 400:
        status = 422
    if status not in _PROBLEMS:
        status = 503
    default_code, title = _PROBLEMS[status]
    public_code = default_code
    if status == 409 and code == "IDEMPOTENCY_CONFLICT":
        public_code = "IDEMPOTENCY_CONFLICT"
    return JSONResponse(
        {"type": f"urn:atl:experience:{public_code.lower()}", "title": title,
         "status": status, "code": public_code, "traceId": trace},
        status_code=status, media_type="application/problem+json",
    )


def register_preferences_routes(app, *, preferences, demo, body_reader, key_reader):
    from services.control_api.investment_preferences_store import PreferencesError

    def port():
        if preferences is None:
            raise AuthDenied(503, "CAPABILITY_UNAVAILABLE")
        return preferences

    def response(value):
        return JSONResponse(PreferencesView.model_validate(value).model_dump(exclude_unset=True))

    @app.get(PREFERENCES_PATH, operation_id="getInvestmentPreferences")
    async def read_preferences(request: Request):
        return response(await run_in_threadpool(port().read, request.state.actor_id))

    @app.put(PREFERENCES_PATH, operation_id="updateInvestmentPreferences")
    async def update_preferences(request: Request):
        idem = key_reader(request)
        data = await body_reader(request, PreferencesUpdate)
        context = data["contextReference"]
        demo_id = context.get("demoRunId")
        if demo_id is None:
            context.pop("demoRunId", None)
        actor = request.state.actor_id
        service = port()
        try:
            if not data["reset"] and demo_id is not None:
                receipt = await run_in_threadpool(service.replay, actor, data, idem)
                if receipt is not None:
                    return response(receipt)
                if demo is None:
                    raise AuthDenied(503, "CAPABILITY_UNAVAILABLE")
                run = await run_in_threadpool(demo.get_run, demo_id)
                if not isinstance(run, dict) or run.get("runId") != demo_id:
                    raise AuthDenied(404, "RESOURCE_NOT_FOUND")
            result = await run_in_threadpool(service.update, actor, data, idem)
        except PreferencesError as error:
            return preferences_problem(error.status, error.code, request.state.trace_id)
        return response(result)
