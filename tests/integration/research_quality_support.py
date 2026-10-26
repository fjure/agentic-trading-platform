"""Real database rejection evidence: immutable, redacted and snapshot bound."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.domain.models import utc_string
from packages.research.jobs import ResearchJobs
from packages.research.models import BarInterval, QualityStatus
from packages.research.quality_storage import observe, rejected_quality
from packages.research.query import ResearchQuery
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.research_service import ResearchService


def verify(engine, execution, control, instrument, start):
    end = start + timedelta(hours=1)
    payload = dict(
        instrumentId=str(instrument.instrument_id),
        start=utc_string(start),
        end=utc_string(end),
        interval="15Min",
        feed="iex",
        adjustment="raw",
        mode="BACKFILL",
    )
    job = ResearchJobs(control).enqueue(
        payload, "local-operator", "quality-pg-test-00001", "d" * 32
    )
    lease = SimpleNamespace(job_id=UUID(job["jobId"]), fence=1, request=payload)
    query = ResearchQuery()
    with control.connect() as connection:
        now = datetime.now(UTC)
        before = query.snapshot(connection, now, now)
    auth = LocalAuth()
    app = create_app(auth=auth, research=ResearchService(control))
    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 46022),
        raise_server_exceptions=False,
    ) as client:
        activation = client.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={
                "Origin": "http://127.0.0.1:8000",
                "Idempotency-Key": "quality-http-session-01",
            },
        )
        assert activation.status_code == 204, "actual quality session"
        filters = {name: payload[name] for name in ("instrumentId", "start", "end", "interval")}
        unknown = client.get("/api/v1/research/context", params=filters)
        assert unknown.status_code == 200
        assert unknown.json()["coverage"][0]["status"] == "UNKNOWN_CALENDAR"
        assert unknown.json()["quality"] == "UNKNOWN_CALENDAR", (
            "unknown calendar is not a missing bar"
        )
        gap_filters = {
            **filters,
            "start": utc_string(start - timedelta(days=1)),
            "end": utc_string(end - timedelta(days=1)),
        }
        gap = client.get("/api/v1/research/context", params=gap_filters)
        assert gap.status_code == 200
        assert gap.json()["coverage"][0]["status"] == "NO_OBSERVATION"
        assert gap.json()["quality"] == "NO_OBSERVATION", "IEX absence retains feed semantics"
        with execution.begin() as connection:
            observe(connection, lease, QualityStatus.CONFLICT)
            observe(connection, lease, QualityStatus.CONFLICT)
        bars = client.get("/api/v1/research/bars", params=filters)
        assert bars.status_code == 200, "rejection remains readable"
        assert bars.json()["coverage"][0]["status"] == "CONFLICT", "public rejection coverage"
        assert bars.json()["incomplete"] is True
        context = client.get("/api/v1/research/context", params=filters)
        assert context.status_code == 200, "rejected context remains readable"
        assert context.json()["quality"] == "CONFLICT", "public context rejection quality"
        assert context.json()["volatility"]["value"] is None, "conflict blocksderivedmetrics"
    with control.connect() as connection:
        now = datetime.now(UTC)
        rejected = query.snapshot(connection, now, now)
        assert (
            rejected_quality(
                connection,
                instrument.instrument_id,
                start,
                end,
                BarInterval.FIFTEEN_MINUTES,
                before,
            )
            is None
        )
        assert (
            rejected_quality(
                connection,
                instrument.instrument_id,
                start,
                end,
                BarInterval.FIFTEEN_MINUTES,
                rejected,
            )
            == QualityStatus.CONFLICT
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.quality_observations WHERE job_id=:id"),
                dict(id=lease.job_id),
            ).scalar_one()
            == 1
        )
    with execution.begin() as connection:
        observe(
            connection,
            SimpleNamespace(job_id=lease.job_id, fence=2, request=payload),
            QualityStatus.VALID,
        )
    with control.connect() as connection:
        now = datetime.now(UTC)
        resolved = query.snapshot(connection, now, now)
        assert (
            rejected_quality(
                connection,
                instrument.instrument_id,
                start,
                end,
                BarInterval.FIFTEEN_MINUTES,
                resolved,
            )
            is None
        )
        assert (
            rejected_quality(
                connection,
                instrument.instrument_id,
                start,
                end,
                BarInterval.FIFTEEN_MINUTES,
                rejected,
            )
            == QualityStatus.CONFLICT
        )
        # Even an artificially large version cannot bypass an earlier knowledge bound.
        old_knowledge = replace(resolved, knowledge_at=before.knowledge_at)
        assert (
            rejected_quality(
                connection,
                instrument.instrument_id,
                start,
                end,
                BarInterval.FIFTEEN_MINUTES,
                old_knowledge,
            )
            is None
        )
    for role in ("atl_control_api", "atl_worker", "atl_execution"):
        for statement in (
            "UPDATE research.quality_observations SET quality='VALID'",
            "DELETE FROM research.quality_observations",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE " + role))
                    connection.execute(text(statement))
            except DBAPIError:
                pass
            else:
                raise AssertionError("quality evidence mutation permitted")
