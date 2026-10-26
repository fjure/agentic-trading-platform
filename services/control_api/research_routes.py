"""Bounded authenticated Research HTTP transport, backed by real stored query/job ports."""

import re
import secrets
from contextvars import ContextVar
from time import monotonic

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse

from packages.observability.research import ResearchMetricError, ResearchMetrics, ResearchOperation
from packages.research.jobs import ResearchJobError
from packages.research.models import QualityStatus
from services.control_api.auth import AuthDenied
from services.control_api.research_dto import RESPONSE_MODELS, ResearchJobAction
from services.control_api.research_service import identity, timestamp

ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "CURSOR_INVALID",
        "SNAPSHOT_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "UNAVAILABLE",
        "NOT_IMPLEMENTED",
        "BLOCKED_CREDENTIALS",
        "BLOCKED_LICENSE",
        "BLOCKED_ENTITLEMENT",
        "PROVIDER_TIMEOUT",
        "PROVIDER_RATE_LIMIT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_RESPONSE_INVALID",
        "PAGINATION_CYCLE",
        "BUDGET_EXHAUSTED",
        "LEASE_LOST",
    }
)
CURRENT_TRACE = ContextVar("research_trace", default=None)


def research_problem(status, code, trace):
    if code not in ERROR_CODES:
        code = "UNAVAILABLE"
    if status not in {400, 401, 403, 404, 409, 503}:
        status = 503
    if not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace):
        trace = secrets.token_hex(16)
    return JSONResponse(
        dict(
            type="urn:agentic-trading-lab:research-problem",
            title="Research request failed",
            status=status,
            code=code,
            traceId=trace,
        ),
        status_code=status,
        media_type="application/problem+json",
    )


def read_query(request, *, filtered=False, paged=False, pit=False):
    values = {}
    allowed = (
        {"instrumentId", "start", "end", "interval", "feed", "adjustment"} if filtered else set()
    )
    if filtered or pit:
        allowed |= {"valuationAt", "knowledgeAt"}
    if paged:
        allowed |= {"limit", "cursor"}
    for name, value in request.query_params.multi_items():
        if name not in allowed or name in values or len(value) > 2048:
            raise ValueError("invalid query")
        values[name] = value
    if filtered:
        if not {"instrumentId", "start", "end", "interval"} <= values.keys():
            raise ValueError("required query missing")
        identity(values["instrumentId"])
        for name in ["start", "end"]:
            timestamp(values[name])
        if values["interval"] not in {"15Min", "1Day"}:
            raise ValueError("unsupported interval")
    for name in ["valuationAt", "knowledgeAt"]:
        if name in values:
            timestamp(values[name])
    if "limit" in values:
        if not re.fullmatch(r"[1-9][0-9]{0,2}", values["limit"]) or int(values["limit"]) > 200:
            raise ValueError("bounded limit required")
    return values


def register_research_routes(app, *, research, body_reader, key_reader):
    admission = anyio.CapacityLimiter(2)
    research_workers = anyio.CapacityLimiter(2)
    metrics = ResearchMetrics()
    app.state.research_metrics = metrics
    operations = {
        "read_sources": ResearchOperation.SOURCES,
        "read_bars": ResearchOperation.BARS,
        "read_evidence": ResearchOperation.EVIDENCE,
        "read_context": ResearchOperation.CONTEXT,
        "read_jobs": ResearchOperation.JOBS,
        "read_job": ResearchOperation.JOB,
        "create_job": ResearchOperation.IMPORT,
        "resume_job": ResearchOperation.IMPORT,
    }

    async def execute(request, method, *args, accepted=False):
        started = monotonic()
        observed_rows = 0
        quality = None
        metric_error = None
        try:
            admission.acquire_nowait()
        except anyio.WouldBlock:
            metrics.observe_query(
                operations[method],
                0,
                error=ResearchMetricError.UNAVAILABLE,
                trace_id=request.state.trace_id,
            )
            return research_problem(503, "UNAVAILABLE", request.state.trace_id)
        token = CURRENT_TRACE.set(request.state.trace_id)
        try:
            if research is None:
                raise ResearchJobError(503, "UNAVAILABLE")
            result = await anyio.to_thread.run_sync(
                getattr(research, method), *args, limiter=research_workers
            )
            if isinstance(result, dict):
                result_items = result.get("items")
                if isinstance(result_items, list):
                    observed_rows = min(len(result_items), 200)
                elif method == "read_context":
                    observed_rows = min(len(result.get("evidenceIds", [])), 200)
                elif method in {"read_job", "read_evidence"}:
                    observed_rows = 1
                quality_value = result.get("quality")
                if (
                    isinstance(quality_value, str)
                    and quality_value in QualityStatus._value2member_map_
                ):
                    quality = QualityStatus(quality_value)
                elif method == "read_bars":
                    coverage = result.get("coverage", [])
                    if coverage:
                        coverage_value = coverage[0].get("status")
                        if coverage_value in QualityStatus._value2member_map_:
                            quality = QualityStatus(coverage_value)
            try:
                result = RESPONSE_MODELS[method].model_validate(result).model_dump()
            except ValueError:
                metric_error = ResearchMetricError.UNAVAILABLE
                return research_problem(503, "UNAVAILABLE", request.state.trace_id)
            return JSONResponse(result, status_code=202 if accepted else 200)
        except (ResearchJobError, AuthDenied) as error:
            metric_error = (
                ResearchMetricError(error.code)
                if error.code in ResearchMetricError._value2member_map_
                else ResearchMetricError.UNAVAILABLE
            )
            return research_problem(error.status, error.code, request.state.trace_id)
        except (ValueError, TypeError, KeyError):
            metric_error = ResearchMetricError.INVALID_REQUEST
            return research_problem(400, "INVALID_REQUEST", request.state.trace_id)
        except Exception:
            metric_error = ResearchMetricError.UNAVAILABLE
            return research_problem(503, "UNAVAILABLE", request.state.trace_id)
        finally:
            CURRENT_TRACE.reset(token)
            admission.release()
            metrics.observe_query(
                operations[method],
                min(monotonic() - started, 86400),
                rows=observed_rows,
                quality=quality,
                error=metric_error,
                trace_id=request.state.trace_id,
            )

    @app.get("/api/v1/research/sources", operation_id="listResearchSources")
    async def sources(request: Request):
        try:
            read_query(request)
        except ValueError:
            return research_problem(400, "INVALID_REQUEST", request.state.trace_id)
        return await execute(request, "read_sources")

    def add_read(path, operation, method, filtered=False, paged=False, pit=False):
        async def read(request: Request):
            try:
                values = read_query(request, filtered=filtered, paged=paged, pit=pit)
                identifier = request.path_params.get("id")
                if identifier:
                    identity(identifier)
            except (ValueError, TypeError):
                return research_problem(400, "INVALID_REQUEST", request.state.trace_id)
            args = (
                [identifier, values]
                if pit and identifier
                else [identifier]
                if identifier
                else [values]
            )
            return await execute(request, method, *args)

        app.add_api_route("/api/v1/research/" + path, read, methods=["GET"], operation_id=operation)

    add_read("bars", "listResearchBars", "read_bars", filtered=True, paged=True)
    add_read("evidence/{id}", "getResearchEvidence", "read_evidence", pit=True)
    add_read("context", "getResearchContext", "read_context", filtered=True)
    add_read("ingestion-jobs", "listResearchIngestionJobs", "read_jobs", paged=True)
    add_read("ingestion-jobs/{id}", "getResearchIngestionJob", "read_job")

    @app.post("/api/v1/research/ingestion-jobs", operation_id="createResearchIngestionJob")
    async def create(request: Request):
        try:
            read_query(request)
            key = key_reader(request)
            payload = await body_reader(request, ResearchJobAction)
        except (AuthDenied, ValueError) as error:
            return research_problem(
                getattr(error, "status", 400), "INVALID_REQUEST", request.state.trace_id
            )
        if "action" in payload:
            return await execute(
                request,
                "resume_job",
                payload,
                request.state.actor_id,
                key,
                request.state.trace_id,
                accepted=True,
            )
        return await execute(
            request,
            "create_job",
            payload,
            request.state.actor_id,
            key,
            request.state.trace_id,
            accepted=True,
        )
