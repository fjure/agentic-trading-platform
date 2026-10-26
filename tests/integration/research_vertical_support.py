"""Real PostgreSQL / loopback-provider / authenticated ASGI research certification.

The migration guest calls verify with actual execution and control-role engines.
Only the provider is replaced; the adapter, storage, jobs and HTTP service are real.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import text

from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
from packages.domain.models import utc_string
from packages.research.jobs import ResearchJobs
from packages.research.limits import SharedAccountHostLimiter
from packages.research.ports import LicenseStatus
from packages.research.quality_storage import observe as observe_quality
from packages.research.storage import ResearchStore, bar_from_row
from services.control_api.app import create_app
from services.control_api.auth import LocalAuth
from services.control_api.research_service import ResearchService
from services.execution.research_ingestion import ResearchIngestion

_ORIGIN = "http://127.0.0.1:8000"
_CANARY = "synthetic-vertical-header-canary"
_PAGE_TOKEN = "synthetic-vertical-page-two-canary"


def verify(engine, execution_engine, control_engine, instrument, start):
    """Raise a named assertion on failure; emit no credentials, payloads or driver errors."""
    del start  # Keep the shared guest's 23rd-of-October fixtures independent.
    start = datetime(2026, 10, 22, 13, 30, tzinfo=UTC)
    end = start + timedelta(minutes=45)
    instrument_id = instrument.instrument_id
    provider_calls = []
    fault = {"invalid_ohlc": False}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            provider_calls.append(self.path)
            assert self.headers.get("APCA-API-SECRET-KEY") == _CANARY, "provider header boundary"
            target = urlsplit(self.path)
            query = parse_qs(target.query)
            if target.path == "/v2/calendar":
                payload = [{"date": "2026-10-22", "open": "09:30", "close": "16:00"}]
            elif target.path == "/v2/stocks/bars":
                assert query.get("feed") == ["iex"], "explicit provider feed"
                assert query.get("adjustment") == ["raw"], "explicit provider adjustment"
                assert query.get("symbols") == ["SPY"], "explicit provider instrument"
                assert query.get("asof") == ["-"], "provider alias handling"
                page_two = query.get("page_token") == [_PAGE_TOKEN]
                indexes = [2] if page_two else [0, 1]
                bars = [
                    {
                        "t": utc_string(start + timedelta(minutes=15 * index)),
                        "o": 100,
                        "h": 105,
                        "l": 95,
                        "c": 200 if fault["invalid_ohlc"] else 100 + index,
                        "v": 1000,
                    }
                    for index in indexes
                ]
                payload = {
                    "bars": {"SPY": bars},
                    "next_page_token": None if page_two else _PAGE_TOKEN,
                }
            else:
                self.send_error(404)
                return
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def ingest():
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as local:

            async def forward(request):
                # A second real SQL writer can obtain the global version lock
                # while the provider is awaited: no transaction spans network I/O.
                with execution_engine.begin() as connection:
                    connection.execute(text("SELECT research.reserve_version()"))
                allowed = {
                    ("data.alpaca.markets", "/v2/stocks/bars"),
                    ("paper-api.alpaca.markets", "/v2/calendar"),
                }
                assert request.method == "GET" and request.url.scheme == "https", "GET-only HTTPS"
                assert (request.url.host, request.url.path) in allowed, "fixed provider targets"
                path = request.url.raw_path.decode("ascii")
                response = await local.get(
                    f"http://127.0.0.1:{server.server_port}{path}",
                    headers={"APCA-API-SECRET-KEY": _CANARY},
                )
                return httpx.Response(
                    response.status_code,
                    content=response.content,
                    headers={"Content-Type": "application/json"},
                )

            async def source():
                return AlpacaHistorySource(
                    api_key=_CANARY,
                    api_secret=_CANARY,
                    enabled=True,
                    policy=ProviderPolicy(LicenseStatus.CONFIRMED, "offline-vertical-v1"),
                    transport=httpx.MockTransport(forward),
                )

            request = dict(
                instrumentId=str(instrument_id),
                start=utc_string(start),
                end=utc_string(end),
                interval="15Min",
                feed="iex",
                adjustment="raw",
                mode="BACKFILL",
            )
            public_jobs = ResearchJobs(control_engine)
            job = public_jobs.enqueue(
                request, "local-operator", "vertical-coordinator-job-01", "e" * 32
            )
            from uuid import UUID

            now = [0.0]
            coordinator = ResearchIngestion(
                ResearchJobs(execution_engine),
                source,
                SharedAccountHostLimiter("offline-vertical", 120, clock=lambda: now[0]),
                enabled=True,
                quality_observer=observe_quality,
            )
            for _ in range(3):
                assert await coordinator.tick(), "actual durable coordinator tick"
                now[0] += 1
            result = public_jobs.read_job(UUID(job["jobId"]))
            assert result["status"] == "SUCCEEDED", "actual coordinator job completed"
            assert result["pages"] == 2 and result["bars"] == 3, "atomic page progress"
            # Malformed normalized values are rejected by the real adapter. The
            # fenced blocked transition and quality metadata commit atomically;
            # a complete later rerun resolves only the current view.
            fault["invalid_ohlc"] = True
            rejected_job = public_jobs.enqueue(
                request, "local-operator", "vertical-invalid-ohlc-job-01", "f" * 32
            )
            assert await coordinator.tick(), "actual rejected provider tick"
            now[0] += 1
            rejected_job = public_jobs.read_job(UUID(rejected_job["jobId"]))
            assert rejected_job["status"] == "BLOCKED" and rejected_job["pages"] == 0
            assert rejected_job["errorCode"] == "PROVIDER_RESPONSE_INVALID"
            filters = {name: request[name] for name in ("instrumentId", "start", "end", "interval")}
            rejected_view = ResearchService(control_engine).read_bars(filters)
            assert rejected_view["coverage"][0]["status"] == "INVALID", "durable adapter rejection"
            fault["invalid_ohlc"] = False
            repaired = public_jobs.enqueue(
                request, "local-operator", "vertical-valid-rerun-job-01", "1" * 32
            )
            for _ in range(2):
                assert await coordinator.tick(), "actual recovery rerun tick"
                now[0] += 1
            assert public_jobs.read_job(UUID(repaired["jobId"]))["status"] == "SUCCEEDED"
            assert (
                ResearchService(control_engine).read_bars(filters)["coverage"][0]["status"]
                == "VALID"
            )
            with control_engine.connect() as connection:
                rows = (
                    connection.execute(
                        text("""SELECT * FROM research.bar_revisions
                    WHERE instrument_id=:id AND bar_start>=:start AND bar_start<:end
                    ORDER BY bar_start"""),
                        dict(id=instrument_id, start=start, end=end),
                    )
                    .mappings()
                    .all()
                )
                persisted = tuple(bar_from_row(row) for row in rows)
                assert len(persisted) == 3, "all actual coordinator pages persisted"
                return persisted

    try:
        stored = asyncio.run(ingest())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(provider_calls) == 6, "calendar, two pages, rejected response, two recovery pages"

    root = Path.cwd()
    schema = json.loads((root / "contracts/schemas/v1/research.schema.json").read_text("utf-8"))

    def checked(response, component, status=200):
        assert response.status_code == status, "research HTTP status: " + component
        value = response.json()
        validator = Draft202012Validator(
            {**schema, "$ref": "#/$defs/" + component}, format_checker=FormatChecker()
        )
        assert not list(validator.iter_errors(value)), "research response schema: " + component
        public = json.dumps(value)
        assert _CANARY not in public and _PAGE_TOKEN not in public, "private canary projection"
        return value

    auth = LocalAuth()
    app = create_app(auth=auth, research=ResearchService(control_engine))
    with TestClient(
        app, base_url=_ORIGIN, client=("127.0.0.1", 46021), raise_server_exceptions=False
    ) as client:
        checked(client.get("/api/v1/research/sources"), "ResearchProblem", 401)
        activation = client.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": _ORIGIN, "Idempotency-Key": "research-vertical-session-01"},
        )
        assert activation.status_code == 204, "actual local-session activation"
        catalog = checked(client.get("/api/v1/research/sources"), "ResearchSourceCatalog")
        assert all(
            item["state"] == "NOT_IMPLEMENTED"
            for item in catalog["items"]
            if item["kind"] in {"NEWS", "FUNDAMENTAL", "MACRO_EVENT", "REGIME"}
        ), "unimplemented sources remain honest"
        filters = {
            "instrumentId": str(instrument_id),
            "start": utc_string(start),
            "end": utc_string(end),
            "interval": "15Min",
        }
        whole = checked(client.get("/api/v1/research/bars", params=filters), "ResearchBarPage")
        assert len(whole["items"]) == 3, "stored bars reachable through real API"
        first = whole["items"][0]
        checked(
            client.get("/api/v1/research/evidence/" + first["evidenceId"]), "ResearchEvidenceResult"
        )
        old_knowledge = utc_string(end)
        checked(
            client.get(
                "/api/v1/research/evidence/" + first["evidenceId"],
                params={"valuationAt": old_knowledge, "knowledgeAt": old_knowledge},
            ),
            "ResearchProblem",
            404,
        )
        context = checked(client.get("/api/v1/research/context", params=filters), "ResearchContext")
        assert context["priceChange"]["status"] == "COMPUTABLE", "bar price metric available"
        assert Decimal(context["priceChange"]["value"]) == Decimal("0.02"), (
            "independent price return"
        )
        # For two returns the population deviation is exactly half their absolute difference.
        exact_deviation = abs(Fraction(1, 100) - Fraction(1, 101)) / 2
        with localcontext() as calculation:
            calculation.prec = 60
            expected = (
                Decimal(exact_deviation.numerator) / Decimal(exact_deviation.denominator)
            ).quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
        assert context["volatility"]["status"] == "COMPUTABLE", "bar volatility available"
        assert Decimal(context["volatility"]["value"]) == expected, (
            "independent population deviation"
        )
        assert context["incomplete"] and {"NEWS", "FUNDAMENTALS", "MACRO", "REGIME"} <= set(
            context["missingSources"]
        ), "missing nonbar sources remain visible"

        paged = checked(
            client.get("/api/v1/research/bars", params={**filters, "limit": 1}), "ResearchBarPage"
        )
        snapshot = paged["snapshot"]
        seen = list(paged["items"])
        cursor = paged["nextCursor"]
        with execution_engine.begin() as connection:
            observed = datetime.now(UTC)
            ResearchStore().append_bar(
                connection,
                replace(
                    stored[1], close=Decimal("100.5"), available_at=observed, ingested_at=observed
                ),
                symbol="SPY",
            )
        while cursor:
            paged = checked(
                client.get(
                    "/api/v1/research/bars", params={**filters, "limit": 1, "cursor": cursor}
                ),
                "ResearchBarPage",
            )
            assert paged["snapshot"] == snapshot, "pagination retains committed snapshot"
            seen.extend(paged["items"])
            cursor = paged["nextCursor"]
        assert [bar["barId"] for bar in seen] == [str(bar.revision_id) for bar in stored], (
            "later revision cannot change bound pagination"
        )

        body = {**filters, "feed": "iex", "adjustment": "raw", "mode": "BACKFILL"}
        headers = {"Origin": _ORIGIN, "Idempotency-Key": "research-vertical-import-01"}
        accepted = checked(
            client.post("/api/v1/research/ingestion-jobs", json=body, headers=headers),
            "ResearchJob",
            202,
        )
        repeated = checked(
            client.post("/api/v1/research/ingestion-jobs", json=body, headers=headers),
            "ResearchJob",
            202,
        )
        assert accepted["jobId"] == repeated["jobId"], "durable HTTP idempotency"
        assert accepted["status"] == "ACCEPTED", "import request persists before dispatch"
        checked(
            client.post(
                "/api/v1/research/ingestion-jobs", json={**body, "mode": "UPDATE"}, headers=headers
            ),
            "ResearchProblem",
            409,
        )
        listing = checked(client.get("/api/v1/research/ingestion-jobs"), "ResearchJobPage")
        assert any(job["jobId"] == accepted["jobId"] for job in listing["items"]), (
            "durable job list"
        )
        detail = checked(
            client.get("/api/v1/research/ingestion-jobs/" + accepted["jobId"]), "ResearchJob"
        )
        assert detail["status"] == "ACCEPTED", "job GET does not dispatch work"
        checked(
            client.get(
                "/api/v1/research/bars", params=filters, headers={"Origin": "http://evil.invalid"}
            ),
            "ResearchProblem",
            403,
        )
        checked(
            client.get("/api/v1/research/bars", params={**filters, "cursor": "bad"}),
            "ResearchProblem",
            400,
        )
        first_page = checked(
            client.get("/api/v1/research/bars", params={**filters, "limit": 1}), "ResearchBarPage"
        )
        assert first_page["coverage"][0]["missing"] == 0, "unreturned pages are not missing data"
        assert first_page["coverage"][0]["expected"] == 1, "page coverage is explicitly scoped"
        checked(
            client.get(
                "/api/v1/research/bars",
                params={
                    **filters,
                    "cursor": first_page["nextCursor"],
                    "start": utc_string(start + timedelta(minutes=15)),
                },
            ),
            "ResearchProblem",
            409,
        )
        with engine.connect() as connection:
            job_count = connection.execute(
                text("SELECT count(*) FROM research.ingestion_jobs")
            ).scalar_one()
        checked(
            client.post(
                "/api/v1/research/ingestion-jobs",
                json={**body, "rawPayload": {}},
                headers={**headers, "Idempotency-Key": "research-vertical-invalid-01"},
            ),
            "ResearchProblem",
            400,
        )
        long_range = {**body, "end": utc_string(start + timedelta(days=367))}
        checked(
            client.post(
                "/api/v1/research/ingestion-jobs",
                json=long_range,
                headers={**headers, "Idempotency-Key": "research-vertical-invalid-02"},
            ),
            "ResearchProblem",
            400,
        )
        checked(
            client.get("/api/v1/research/bars", params={**filters, "end": long_range["end"]}),
            "ResearchProblem",
            400,
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM research.ingestion_jobs")
                ).scalar_one()
                == job_count
            ), "rejected requests create no jobs"
    assert len(provider_calls) == 6, "every research HTTP read and admission is provider-free"
