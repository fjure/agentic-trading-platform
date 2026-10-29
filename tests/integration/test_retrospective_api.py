"""RRB-06: real SQL/HTTP/worker chain on genuine current local observations."""

import json
import sys
from pathlib import Path


def host():
    from tests.integration import test_readiness_storage as harness
    from tests.integration.test_retrospective_storage import TABLES

    previous = harness.TABLES
    try:
        harness.TABLES = tuple(
            dict.fromkeys((*previous, *TABLES, "backtest.retrospective_execution_gates"))
        )
        return harness.host("tests.integration.test_retrospective_api")
    finally:
        harness.TABLES = previous


def seed(engine):
    """Append synthetic market prices with actual DB receipt stamps; no backdating."""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import text

    from packages.research.models import BarIdentity, BarInterval, BarRevision
    from packages.research.registry import definitions, register
    from packages.research.storage import ResearchStore

    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    instrument = definitions()[0][0]
    store, bars = ResearchStore(), []
    with engine.begin() as connection:
        register(connection)
        observed = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        for index, value in enumerate(("10", "10", "10", "12")):
            opening = start + timedelta(minutes=15 * index)
            price = Decimal(value)
            bars.append(
                store.append_bar(
                    connection,
                    BarRevision(
                        uuid4(),
                        BarIdentity(
                            instrument.instrument_id,
                            "alpaca",
                            "iex",
                            BarInterval.FIFTEEN_MINUTES,
                            opening,
                            opening + timedelta(minutes=15),
                        ),
                        Decimal("10"),
                        max(Decimal("10"), price),
                        Decimal("10"),
                        price,
                        Decimal("100"),
                        observed,
                        observed,
                    ),
                    symbol="SPY",
                )
            )
        connection.execute(
            text("""INSERT INTO research.calendar_versions
            (session_id,session_date,opens_at,closes_at,version,source)
            VALUES(:id,:day,:opening,:closing,:version,'alpaca-v2-calendar')"""),
            dict(
                id=uuid4(),
                day=start.date(),
                opening=start,
                closing=start + timedelta(hours=1),
                version="a" * 64,
            ),
        )
    assert all(bar.ingested_at > bar.identity.bar_end for bar in bars)

    def stamp(value):
        return value.isoformat().replace("+00:00", "Z")

    request = dict(
        instrumentId=str(instrument.instrument_id),
        strategyId="buy-hold-v1",
        version="1",
        parameters={},
        startCash="1000",
        feeUsd="0",
        slippageBps="0",
        quantityStep="0.000001",
        folds=[
            dict(
                trainingStart=stamp(start - timedelta(minutes=30)),
                trainingEnd=stamp(start),
                testStart=stamp(start),
                testEnd=stamp(start + timedelta(hours=1)),
            )
        ],
    )
    return request, tuple(bars)


def guest():
    from dataclasses import replace
    from datetime import datetime, timedelta
    from decimal import Decimal
    from unittest.mock import patch
    from uuid import uuid4

    from jsonschema import Draft202012Validator, FormatChecker
    from sqlalchemy import text

    from packages.research.storage import ResearchStore
    from services.control_api.backtest_service import BacktestService
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine
    from tests.security.test_backtest_access import BASE, ORIGIN, client

    schemas = json.loads(Path("/opt/lab/contracts/schemas/v1/backtesting.schema.json").read_text())
    formats = FormatChecker()

    @formats.checks("date-time", raises=(ValueError, TypeError))
    def utc_timestamp(value):
        return not isinstance(value, str) or (
            value.endswith("Z")
            and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() == timedelta(0)
        )

    def validator(component):
        return Draft202012Validator(
            {**schemas, "$ref": "#/$defs/" + component}, format_checker=formats
        )

    from sqlalchemy import event

    engine = guest_engine()
    request, bars = seed(engine)
    control = role_engine(engine, "atl_control_api")
    worker = role_engine(engine, "atl_backtest_worker")
    sql_failures = []

    def capture_sql_failure(context):
        primary = getattr(getattr(context.original_exception, "diag", None), "message_primary", "")
        sql_failures.append((type(context.original_exception).__name__, primary))

    event.listen(control, "handle_error", capture_sql_failure)
    event.listen(worker, "handle_error", capture_sql_failure)
    code_version = "a" * 40
    service = BacktestService(control, code_version=code_version)

    def public_client(sql_service):
        import httpx
        from fastapi.testclient import TestClient

        from services.control_api.backtest_client import BacktestClient
        from services.runtime.paper_api import create_private_paper_app

        private = TestClient(
            create_private_paper_app(None, backtesting=sql_service),
            base_url="http://paper-api:8012",
            raise_server_exceptions=False,
        )

        def relay(request):
            assert request.url.host == "paper-api" and request.url.port == 8012
            assert "cookie" not in request.headers and "authorization" not in request.headers
            assert "origin" not in request.headers
            response = private.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )

        return client(BacktestClient(transport=httpx.MockTransport(relay)))

    http = public_client(service)
    runner = BacktestWorker(worker, enabled=True, code_version=code_version)

    def post(suffix, body, key):
        return http.post(
            BASE + suffix,
            json=body,
            headers={"Origin": ORIGIN, "Idempotency-Key": key},
        )

    def checked(response, component, status=200):
        assert response.status_code == status, (
            component,
            response.status_code,
            sql_failures[-1:] if response.status_code == 503 else [],
            response.json(),
        )
        value = response.json()
        validator(component).validate(value)
        return value

    def drain(ticks=8):
        for _ in range(ticks):
            if not runner.tick():
                break

    # Omission and explicit Strict use the legacy payload identity, including replay.
    strict_key = "rrb-api-strict-default-0001"
    strict = checked(post("/runs", request, strict_key), "BacktestRun", 202)
    explicit = checked(
        post("/runs", {**request, "researchMode": "STRICT_PIT"}, strict_key),
        "BacktestRun",
        202,
    )
    assert strict["runId"] == explicit["runId"]
    drain()
    strict = checked(http.get(BASE + "/runs/" + strict["runId"]), "BacktestRunDetail")["run"]
    assert strict["researchMode"] == "STRICT_PIT" and strict["status"] == "BLOCKED"

    protocol = {
        **request,
        "researchMode": "RETROSPECTIVE_EXPLORATORY",
        "selectionPolicyVersion": "retrospective-v1",
    }
    with engine.begin() as connection:
        run_count = connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one()
        result_count = connection.execute(
            text("SELECT count(*) FROM backtest.results")
        ).scalar_one()
    preview = checked(
        post("/assessments", protocol, "rrb-api-assessment-0001"), "BacktestAssessment", 202
    )
    assessment_id = preview["assessmentId"]
    assert preview["status"] == "ACCEPTED" and preview["assessmentHash"] is None
    repeated = checked(
        post("/assessments", protocol, "rrb-api-assessment-0001"), "BacktestAssessment", 202
    )
    assert repeated["assessmentId"] == assessment_id
    assert (
        post("/assessments", {**protocol, "feeUsd": "1"}, "rrb-api-assessment-0001").status_code
        == 409
    )
    with engine.begin() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one() == run_count
        )
        assert (
            connection.execute(text("SELECT count(*) FROM backtest.results")).scalar_one()
            == result_count
        )
    with patch(
        "services.worker.retrospective_backtesting.evaluate",
        side_effect=AssertionError("assessment started a simulation"),
    ):
        drain()
    preview = checked(http.get(BASE + "/assessments/" + assessment_id), "BacktestAssessment")
    assert preview["status"] == "SUCCEEDED", (
        preview["status"],
        preview["blockers"],
        sql_failures[-1:],
    )
    assert preview["eligibility"] == "EXPLORATORY_WITH_LIMITATIONS"
    report_id = preview["reliabilityReportId"]
    page = checked(
        http.get(BASE + "/reliability-reports/" + report_id, params={"limit": 1}),
        "BacktestReliabilityReportPage",
    )
    assert page["report"]["licenseStatus"] == "UNKNOWN"
    assert page["report"]["corporateActionStatus"] == "UNKNOWN"
    assert page["report"]["historicalAvailabilityStatus"] == "NOT_PROVEN"
    assert len(page["findings"]) == 1 and page["nextCursor"]
    immutable_preview = page["report"]
    findings = checked(
        http.get(BASE + "/reliability-reports/" + report_id, params={"limit": 200}),
        "BacktestReliabilityReportPage",
    )["findings"]
    all_references = checked(
        http.get(
            BASE + "/reliability-reports/" + report_id,
            params={"section": "revisions", "limit": 200},
        ),
        "BacktestReliabilityReportPage",
    )["revisionReferences"]
    assert all_references
    keys = {
        (reference["kind"], reference["referenceId"])
        for finding in findings
        if finding["category"] == "LICENSE"
        for reference in finding["references"]
    }
    filtered = checked(
        http.get(
            BASE + "/reliability-reports/" + report_id,
            params={"section": "revisions", "category": "LICENSE", "limit": 200},
        ),
        "BacktestReliabilityReportPage",
    )["revisionReferences"]
    assert filtered == [
        reference
        for reference in all_references
        if (reference["kind"], reference["referenceId"]) in keys
    ]
    restarted = public_client(BacktestService(control, code_version=code_version))
    next_page = checked(
        restarted.get(
            BASE + "/reliability-reports/" + report_id,
            params={"limit": 1, "cursor": page["nextCursor"]},
        ),
        "BacktestReliabilityReportPage",
    )
    assert next_page["report"] == immutable_preview
    assert next_page["findings"] != page["findings"]
    assert (
        restarted.get(
            BASE + "/reliability-reports/" + report_id,
            params={"cursor": page["nextCursor"], "category": "LICENSE"},
        ).status_code
        == 409
    )

    # A newer local revision must not replace the immutable preview's input.
    with engine.begin() as connection:
        correction = ResearchStore().append_bar(
            connection,
            replace(bars[-1], revision_id=uuid4(), close=Decimal("13"), high=Decimal("13")),
            symbol="SPY",
        )
    corrected = checked(
        http.get(BASE + "/reliability-reports/" + report_id),
        "BacktestReliabilityReportPage",
    )
    assert corrected["report"] == immutable_preview
    assert any(
        warning["code"] == "EX_POST_REVISIONS"
        and warning["category"] == "HISTORICAL_AVAILABILITY"
        and warning["blocksNewExecution"] is False
        for warning in corrected["currentWarnings"]
    )
    creation = {
        **protocol,
        "assessmentId": assessment_id,
        "assessmentHash": preview["assessmentHash"],
    }
    assert (
        post(
            "/runs", {**creation, "assessmentHash": "0" * 64}, "rrb-api-wrong-assessment-01"
        ).status_code
        == 409
    )
    assert (
        post("/runs", {**creation, "feeUsd": "1"}, "rrb-api-wrong-protocol-001").status_code == 409
    )
    run = checked(post("/runs", creation, "rrb-api-run-0001"), "BacktestRun", 202)
    run_id = run["runId"]
    assert (
        checked(post("/runs", creation, "rrb-api-run-0001"), "BacktestRun", 202)["runId"] == run_id
    )
    assert post("/runs", creation, strict_key).status_code == 409
    drain()
    detail = checked(http.get(BASE + "/runs/" + run_id), "BacktestRunDetail")
    assert detail["run"]["status"] == "SUCCEEDED"
    assert detail["manifest"] is None and detail["retrospectiveManifest"]
    assert detail["run"]["snapshotReference"] == preview["snapshotReference"]
    final_report = checked(
        http.get(BASE + "/reliability-reports/" + detail["run"]["reliabilityReportId"]),
        "BacktestReliabilityReportPage",
    )
    assert final_report["report"]["phase"] == "FINAL"
    assert final_report["report"]["reportHash"] == immutable_preview["reportHash"]
    revisions = checked(
        http.get(
            BASE + "/reliability-reports/" + final_report["report"]["reportId"],
            params={"section": "revisions", "limit": 200},
        ),
        "BacktestReliabilityReportPage",
    )
    assert str(correction.revision_id) not in {
        item["referenceId"] for item in revisions["revisionReferences"]
    }
    summary = checked(http.get(BASE + "/runs/" + run_id + "/results"), "BacktestSummaryPage")
    assert summary["items"] == [] and len(summary["retrospectiveItems"]) == 1
    assert summary["retrospectiveItems"][0]["coverage"]["corporateActionStatus"] == "UNKNOWN"
    assert Decimal(summary["retrospectiveItems"][0]["metrics"]["endEquity"]) == Decimal("1200")
    assert Decimal(summary["retrospectiveItems"][0]["metrics"]["netReturn"]) == Decimal("0.2")
    fills = checked(
        http.get(BASE + "/runs/" + run_id + "/results", params={"resultType": "fills"}),
        "BacktestFillsPage",
    )
    assert fills["items"] == [] and fills["retrospectiveItems"]
    assert all("simulatedMarketCutoff" in item for item in fills["retrospectiveItems"])
    assert all("observationCutoff" not in item for item in fills["retrospectiveItems"])
    assert fills["retrospectiveItems"][0]["executedAt"] == "2026-10-19T14:00:00Z"

    # Current rights block a new execution without rewriting any snapshot facts.
    blocked = checked(post("/runs", creation, "rrb-api-current-denial-001"), "BacktestRun", 202)
    with engine.begin() as connection:
        connection.execute(text("UPDATE research.source_status SET license_status='BLOCKED'"))
    drain()
    blocked = checked(http.get(BASE + "/runs/" + blocked["runId"]), "BacktestRunDetail")["run"]
    assert blocked["status"] == "BLOCKED" and blocked["resultHash"] is None
    warned = checked(
        http.get(BASE + "/reliability-reports/" + report_id), "BacktestReliabilityReportPage"
    )
    assert warned["report"] == immutable_preview
    assert any(
        w["code"] == "LICENSE_DENIED" and w["blocksNewExecution"] for w in warned["currentWarnings"]
    )
    with engine.begin() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": blocked["runId"]},
            ).scalar_one()
            == 0
        )
    print("PASS retrospective real PG/HTTP/worker/snapshot/replay/cursor/current-gate")


def test_retrospective_api_actual_postgresql():
    assert host().endswith("/restore")


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            locations = ",".join(
                f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-4:]
            )
            primary = (
                getattr(getattr(getattr(error, "orig", None), "diag", None), "message_primary", "")
                or ""
            )
            detail = str(error)[:250] if isinstance(error, AssertionError) else primary[:180]
            print("FAIL " + type(error).__name__ + " " + locations + " " + detail)
            raise SystemExit(1) from None
    else:
        print(host())
