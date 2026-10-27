"""RDR-08: reale SQL/HTTP/Importer/Worker-Kette, ausschließlich synthetische Quellen."""

import json
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

MARKER = "PASS RDR08 collection/import/diagnosis/backtest/immutability/no-orders"
SAFETY = "PASS: active isolated readiness assessment preserves Paper pause kill reconciliation"
PHASE = "BOOT"

# Feste Ledger-Whitelist: Cash/Snapshots, Bestände, Vorschläge, Orders/Fills,
# Buchungs-/Kostenbasis, Risikoverbindlichkeiten und Audit einschließlich Zeileninhalt.
FINANCIAL_TABLES = (
    "accounts",
    "instruments",
    "market_bars",
    "evidence_bundles",
    "positions",
    "position_versions",
    "portfolio_snapshots",
    "market_states",
    "decisions",
    "trade_proposals",
    "audit_events",
    "risk_policies",
    "risk_decisions",
    "risk_reservations",
    "order_intents",
    "broker_orders",
    "execution_reports",
    "agent_runs",
    "risk_account_context",
    "risk_instrument_context",
    "broker_account_bindings",
    "paper_activity_evidence",
    "paper_order_accounting_bases",
    "paper_initial_bases",
    "paper_position_cost_bases",
    "paper_daily_risk_evidence",
    "paper_reconciliation_baselines",
    "paper_reconciliation_receipts",
    "paper_workflow_state",
)
# Safetyaktionen dürfen Status/Audit/Outbox/Reconciliation-Snapshots verändern;
# Diagnose-CPU darf dabei keine Order-, Fill-, Bestands- oder Kostenbasiswrites auslösen.
SAFETY_STABLE_TABLES = (
    "positions",
    "position_versions",
    "trade_proposals",
    "risk_decisions",
    "risk_reservations",
    "order_intents",
    "broker_orders",
    "execution_reports",
    "paper_order_accounting_bases",
    "paper_initial_bases",
    "paper_position_cost_bases",
)


def financial_fingerprints(connection, tables=FINANCIAL_TABLES):
    from sqlalchemy import text

    assert set(tables) <= set(FINANCIAL_TABLES)
    return tuple(
        (
            table,
            connection.execute(
                text(
                    "SELECT encode(sha256(convert_to(COALESCE(jsonb_agg(to_jsonb(r) "
                    "ORDER BY to_jsonb(r)::text)::text,'[]'),'UTF8')),'hex') "
                    f"FROM public.{table} r"
                )
            ).scalar_one(),
        )
        for table in tables
    )


def test_actual_readiness_acceptance_chain():
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    name, password = "atl_rdr08_" + uuid4().hex[:18], secrets.token_urlsafe(32)
    groups = (*GROUPS, "atl_evidence_importer")
    _admin(
        "\n".join(
            f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{group}') "
            f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT; END IF; END $$;"
            for group in groups
        )
    )
    _admin(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOINHERIT")
    created = False
    try:
        _admin("\n".join(f"GRANT {group} TO {name} WITH ADMIN OPTION;" for group in groups))
        _admin(f"CREATE DATABASE {name} OWNER {name}")
        created = True
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network",
            "agentic-trading-lab-dev_local",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--workdir",
            "/opt/lab",
            "--entrypoint",
            "python",
        ]
        for directory in ("packages", "services", "tests", "contracts", "scripts", "alembic.ini"):
            command += [
                "--mount",
                f"type=bind,source={root / directory},target=/opt/lab/{directory},readonly",
            ]
        command += [
            "agentic-trading-lab-research-test:local",
            "-m",
            "tests.integration.test_readiness_acceptance",
            "guest",
        ]
        result = subprocess.run(
            command,
            input=json.dumps(dict(database=name, role=name, password=password)),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout.strip()[:600]
        assert result.stdout.strip() == MARKER
    finally:
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE)")
        _admin(f"DROP ROLE {name}")


def test_readiness_cpu_load_preserves_paper_safety():
    from tests.integration.test_paper_provision import host

    root = Path(__file__).resolve().parents[2]
    assert (
        host(
            Path(__file__),
            SAFETY,
            support_paths=(
                root / "tests/integration/test_paper_lifecycle.py",
                root / "tests/integration/backtest_browser_support.py",
            ),
            guest_args=("safety",),
        )
        == SAFETY
    )


def request_for(seed):
    from datetime import timedelta

    from packages.domain.models import utc_string
    from services.control_api.backtest_service import stamp

    return dict(
        instrumentId=seed["instrument"],
        strategyId="buy-hold-v1",
        version="1",
        parameters={},
        startCash="10000",
        feeUsd="0",
        slippageBps="0",
        quantityStep="0.000001",
        folds=[
            dict(
                trainingStart=utc_string(stamp(seed["start"]) - timedelta(days=1)),
                trainingEnd=seed["start"],
                testStart=seed["start"],
                testEnd=seed["end"],
            )
        ],
    )


def guest():
    global PHASE
    import asyncio
    import copy
    from datetime import timedelta
    from unittest.mock import patch

    import httpx
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
    from packages.persistence.ledger import PostgresLedger
    from packages.research.evidence_import import EvidenceImporter
    from packages.research.evidence_trust import EvidenceRejected, fingerprint
    from packages.research.jobs import ResearchJobs
    from packages.research.limits import SharedAccountHostLimiter
    from packages.research.ports import LicenseStatus
    from packages.research.registry import definitions
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backtest_service import BacktestService, stamp
    from services.control_api.collection_service import CollectionPlanService
    from services.control_api.readiness_service import ReadinessService
    from services.execution.research_ingestion import ResearchIngestion
    from services.runtime.provision import seed as seed_financial_fixture
    from services.worker.backtesting import BacktestWorker
    from tests.integration.backtest_browser_support import seed_backtest_fixture
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine
    from tests.security.test_research_evidence_trust import (
        sign_bundle,
        signed_fixture,
        synthetic_authority,
    )

    PHASE = "MIGRATE"
    admin = guest_engine()
    from decimal import Decimal

    seed_financial_fixture(PostgresLedger(engine=admin), Decimal("10000"))
    seed = seed_backtest_fixture(admin)
    control, worker, execution, certifier = [
        role_engine(admin, role)
        for role in (
            "atl_control_api",
            "atl_backtest_worker",
            "atl_execution",
            "atl_evidence_importer",
        )
    ]
    with admin.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM public.portfolio_snapshots")
        ).scalar_one()
        ledger = financial_fingerprints(connection)
    readiness = ReadinessService(control, code_version="b" * 40)
    auth, origin = LocalAuth(), "http://127.0.0.1:8000"
    http = TestClient(
        create_app(
            auth=auth,
            readiness=readiness,
            backtesting=BacktestService(control, code_version="b" * 40),
            collection=CollectionPlanService(control),
        ),
        base_url=origin,
        client=("127.0.0.1", 46000),
        raise_server_exceptions=False,
    )
    base = "/api/v1/research/data-readiness/assessments"
    plans = "/api/v1/research/collection/plans"
    payload = request_for(seed)

    def headers(key):
        return {"Origin": origin, "Idempotency-Key": key}

    PHASE = "AUTH_NO_SIDE_EFFECTS"
    assert http.get(plans).status_code == 401
    assert (
        http.post(base, json=payload, headers=headers("rdr08-unauthorized-0001")).status_code == 401
    )
    assert (
        http.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers=headers("rdr08-activate-0001"),
        ).status_code
        == 204
    )
    denied = http.post(
        base,
        json=payload,
        headers={**headers("rdr08-foreign-origin-001"), "Origin": "https://foreign.invalid"},
    )
    assert denied.status_code == 403
    with admin.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM backtest.readiness_jobs")).scalar() == 0
        )

    PHASE = "PLANS_DEFAULT_OFF"
    page = http.get(plans)
    assert page.status_code == 200, page.json()
    assert len(page.json()["items"]) == 2
    assert all(not p["enabled"] and not p["globalEnabled"] for p in page.json()["items"])
    target = plans + "/" + seed["instrument"]
    enabled = http.put(
        target,
        json={"enabled": True, "expectedVersion": 0},
        headers=headers("rdr08-enable-wish-0001"),
    )
    assert enabled.status_code == 200 and enabled.json()["status"] == "BLOCKED", enabled.json()
    paused = http.put(
        target,
        json={"enabled": False, "expectedVersion": 1},
        headers=headers("rdr08-pause-wish-0001"),
    )
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED", paused.json()

    dispatcher = BacktestWorker(worker, enabled=True, code_version="b" * 40)

    def diagnose(value, key):
        response = http.post(base, json=value, headers=headers(key))
        assert response.status_code == 202, response.json()
        accepted = response.json()
        assert http.post(base, json=value, headers=headers(key)).json() == accepted
        assert (
            http.post(base, json={**value, "feeUsd": "1"}, headers=headers(key)).status_code == 409
        )
        with (
            patch("httpx.Client.send", side_effect=AssertionError("worker network forbidden")),
            patch("httpx.AsyncClient.send", side_effect=AssertionError("worker network forbidden")),
        ):
            assert dispatcher.tick()
        response = http.get(base + "/" + accepted["assessmentId"])
        assert response.status_code == 200, response.json()
        detail = response.json()
        assert detail["status"] == "SUCCEEDED", detail
        return detail

    PHASE = "ELIGIBLE_PARITY"
    detail = diagnose(payload, "rdr08-eligible-diagnosis-001")
    assert detail["eligibility"] == "ELIGIBLE" and detail["report"]["evaluationComplete"]
    assert detail["report"]["windows"]["complete"]
    original = copy.deepcopy(detail["report"])
    with admin.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar() == 0
    run_base = "/api/v1/research/backtests/runs"
    accepted = http.post(run_base, json=payload, headers=headers("rdr08-explicit-backtest-001"))
    assert accepted.status_code == 202, accepted.json()
    assert dispatcher.tick()
    assert (
        http.get(run_base + "/" + accepted.json()["runId"]).json()["run"]["status"] == "SUCCEEDED"
    )

    PHASE = "TODAY_BACKFILL_HTTP_DOUBLE"
    instrument = definitions()[1][0]
    calls = []
    start = stamp(seed["start"])

    async def double(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        if request.url.path.endswith("/calendar"):
            return httpx.Response(
                200, json=[dict(date=start.date().isoformat(), open="09:30", close="16:00")]
            )
        bars = [
            dict(t=(start + timedelta(minutes=15 * n)).isoformat(), o=10, h=11, l=9, c=10, v=100)
            for n in range(4)
        ]
        return httpx.Response(200, json={"bars": {"QQQ": bars}, "next_page_token": None})

    async def factory():
        return AlpacaHistorySource(
            api_key="synthetic",
            api_secret="synthetic",
            enabled=True,
            policy=ProviderPolicy(LicenseStatus.CONFIRMED, "rdr08-synthetic"),
            transport=httpx.MockTransport(double),
        )

    ingest_request = dict(
        instrumentId=str(instrument.instrument_id),
        start=seed["start"],
        end=seed["end"],
        interval="15Min",
        mode="BACKFILL",
        feed="iex",
        adjustment="raw",
    )
    # Nur diese disposable Test-Execution bestätigt die synthetische Quelle.
    with execution.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.source_status SET state='IMPLEMENTED',license_status='CONFIRMED',"
                "policy_version='rdr08-synthetic',provider_verification='VERIFIED' WHERE singleton"
            )
        )
    ResearchJobs(control).enqueue(ingest_request, "local-operator", "rdr08-backfill-0001", "c" * 32)
    monotonic = [0.0]
    ingestion = ResearchIngestion(
        ResearchJobs(execution),
        factory,
        SharedAccountHostLimiter("synthetic-rdr08", 10000, clock=lambda: monotonic[0]),
        enabled=True,
    )
    for _ in range(3):
        asyncio.run(ingestion.tick())
        monotonic[0] += 5
    with admin.connect() as connection:
        status, error_code = connection.execute(
            text("SELECT status,error_code FROM research.ingestion_jobs")
        ).one()
        PHASE = "BACKFILL_" + status + "_" + (error_code or "NONE")
        assert status == "SUCCEEDED"
    assert "/v2/stocks/bars" in calls and all(
        p in {"/v2/stocks/bars", "/v2/calendar"} for p in calls
    )
    with admin.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*)=4 AND bool_and(ingested_at>bar_end) "
                "FROM research.bar_revisions WHERE instrument_id=:id"
            ),
            dict(id=instrument.instrument_id),
        ).scalar_one()
    negative_request = {**payload, "instrumentId": str(instrument.instrument_id)}
    negative = diagnose(negative_request, "rdr08-late-backfill-diagnosis-001")
    assert negative["eligibility"] == "BLOCKED"
    findings_url = base + "/" + negative["assessmentId"] + "/findings"
    findings, cursor = [], None
    while True:
        params = {"limit": 1, **({"cursor": cursor} if cursor else {})}
        response = http.get(findings_url, params=params)
        assert response.status_code == 200, response.json()
        findings.extend(response.json()["items"])
        cursor = response.json()["nextCursor"]
        if cursor is None:
            break
    assert findings
    assert {"OBSERVED_TOO_LATE", "CORPORATE_ACTIONS_UNKNOWN"} <= {
        item["category"] for item in findings
    }
    assert http.get(findings_url, params={"cursor": "tampered"}).status_code == 400

    PHASE = "TRUE_SIGNATURE_NO_HISTORICAL_PROOF"
    source, decision = synthetic_authority(
        scope={
            "instrumentIds": [str(instrument.instrument_id), seed["instrument"]],
            "kinds": ["CORPORATE_ACTION", "CALENDAR"],
        }
    )
    importer = EvidenceImporter(certifier, production=False)
    importer.admit(json.dumps(decision).encode(), 0)
    bundle, _, _ = signed_fixture("no-corporate-actions", source=source)
    content = bundle["entries"][0]["content"]
    content["instrumentId"] = str(instrument.instrument_id)
    content["interval"] = dict(startAt=seed["start"], endAt=seed["end"])
    sign_bundle(bundle, source)
    receipt = importer.import_bytes(json.dumps(bundle).encode())
    with admin.connect() as connection:
        trust = connection.execute(
            text("SELECT trust FROM research.evidence_revisions WHERE evidence_id=:id"),
            dict(id=receipt.entry_ids[0]),
        ).scalar_one()
        assert trust["sourceAuthenticity"]["status"] == "VERIFIED"
        assert trust["temporalAuthenticity"]["status"] == "UNKNOWN"
    assert (
        diagnose(negative_request, "rdr08-signed-no-time-diagnosis-001")["eligibility"] == "BLOCKED"
    )
    tampered = copy.deepcopy(bundle)
    tampered["entries"][0]["content"]["evidenceKey"] = "tampered-without-resigning"
    try:
        importer.import_bytes(json.dumps(tampered).encode())
    except EvidenceRejected:
        pass
    else:
        raise AssertionError("manipulated evidence imported")

    PHASE = "ACTUAL_MANAGED_CALENDAR_BINDING"
    witness, witness_decision = synthetic_authority(
        "WITNESS",
        scope={
            **decision["scope"],
            "assertions": ["PUBLICATION_OBSERVED_BY"],
        },
    )
    importer.admit(json.dumps(witness_decision).encode(), 1)
    calendar, _, _ = signed_fixture("calendar", source=source)
    calendar_content = calendar["entries"][0]["content"]
    calendar_content.update(
        evidenceKey="rdr08-managed-calendar",
        instrumentId=seed["instrument"],
        interval=dict(startAt="2026-10-19T00:00:00Z", endAt="2026-10-20T00:00:00Z"),
        sourceObservedAt="2026-10-18T09:00:00Z",
        signedAt="2026-10-18T09:00:01Z",
        statement=dict(
            type="CALENDAR_SESSIONS",
            calendarVersion="rdr08-historical-fixture",
            sessions=[dict(sessionDate="2026-10-19", opensAt=seed["start"], closesAt=seed["end"])],
        ),
    )
    proof_template, _, _ = signed_fixture("publication-proof", source=source, witness=witness)
    calendar["entries"][0]["temporalProof"] = copy.deepcopy(
        proof_template["entries"][0]["temporalProof"]
    )
    calendar["entries"][0]["temporalProof"]["payload"].update(
        witnessObservedAt="2026-10-18T09:01:00Z", signedAt="2026-10-18T09:01:01Z"
    )
    sign_bundle(calendar, source, witness)
    historical = importer.import_bytes(json.dumps(calendar).encode())
    assert historical.ingested_at > start
    # Explizites historisches Sichtbarkeitsfixture, nur disposable Migrationseigentümer.
    # Oben wurde bereits bewiesen, dass der echte Import die HEUTIGE DBuhr verwendet.
    # Die signierten Inhalte/Dimensionen bleiben unverändert und nicht vollständig.
    with admin.begin() as connection:
        connection.execute(
            text("ALTER TABLE research.evidence_revisions DISABLE TRIGGER immutable")
        )
        connection.execute(
            text(
                "UPDATE research.evidence_revisions "
                "SET ingested_at='2026-10-18T09:02:00Z' WHERE evidence_id=:id"
            ),
            dict(id=historical.entry_ids[0]),
        )
        connection.execute(text("ALTER TABLE research.evidence_revisions ENABLE TRIGGER immutable"))
    managed = diagnose(payload, "rdr08-managed-calendar-diagnosis-001")
    assert managed["eligibility"] != "ELIGIBLE"
    managed_url = base + "/" + managed["assessmentId"]
    original_managed = copy.deepcopy(managed["report"])
    with admin.connect() as connection:
        bindings = (
            connection.execute(
                text("SELECT reference FROM backtest.readiness_bindings WHERE assessment_id=:id"),
                dict(id=managed["assessmentId"]),
            )
            .scalars()
            .all()
        )
        assert str(historical.entry_ids[0]) in bindings
        now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    first_page = http.get(managed_url + "/findings", params={"limit": 1}).json()
    original_cursor = first_page["nextCursor"]
    assert original_cursor is not None
    future = {
        **witness_decision,
        "decisionId": str(uuid4()),
        "predecessorDecisionId": witness_decision["decisionId"],
        "revoked": True,
        "effectiveAt": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        "reason": "SYNTHETIC_FUTURE_REVOCATION",
    }
    importer.admit(json.dumps(future).encode(), 2)
    unrelated = {
        **decision,
        "decisionId": str(uuid4()),
        "sourceId": "unrelated-source",
        "revoked": True,
        "reason": "SYNTHETIC_OTHER_SOURCE_COLLISION",
    }
    importer.admit(json.dumps(unrelated).encode(), 3)
    projection = http.get(managed_url).json()["currentRevocation"]
    assert projection["complete"] and projection["warnings"] == []
    witness_revoked = {
        **witness_decision,
        "decisionId": str(uuid4()),
        "predecessorDecisionId": future["decisionId"],
        "revoked": True,
        "compromiseTimeKnown": False,
        "reason": "SYNTHETIC_WITNESS_COMPROMISE",
    }
    importer.admit(json.dumps(witness_revoked).encode(), 4)
    current = http.get(managed_url).json()
    assert current["report"] == original_managed
    warnings = current["currentRevocation"]["warnings"]
    assert len(warnings) == 1 and warnings[0]["decisionRef"]["id"] == witness_revoked["decisionId"]
    assert any(
        ref["id"] == str(historical.entry_ids[0]) for ref in warnings[0]["affectedReferences"]
    )
    revoked = {
        **decision,
        "decisionId": str(uuid4()),
        "predecessorDecisionId": decision["decisionId"],
        "revoked": True,
        "compromiseTimeKnown": False,
        "reason": "SYNTHETIC_UNKNOWN_COMPROMISE",
    }
    importer.admit(json.dumps(revoked).encode(), 5)
    current = http.get(managed_url).json()
    assert current["report"] == original_managed
    assert current["currentRevocation"]["currentTrustVersion"] == 6
    assert {item["decisionRef"]["id"] for item in current["currentRevocation"]["warnings"]} == {
        revoked["decisionId"],
        witness_revoked["decisionId"],
    }
    next_page = http.get(managed_url + "/findings", params={"limit": 1, "cursor": original_cursor})
    assert next_page.status_code == 200
    assert next_page.json()["reportHash"] == original_managed["reportHash"]
    newer = copy.deepcopy(bundle)
    newer["entries"][0]["content"]["evidenceKey"] = "after-revocation"
    sign_bundle(newer, source)
    try:
        importer.import_bytes(json.dumps(newer).encode())
    except EvidenceRejected as error:
        assert error.code == "AUTHORITY_REVOKED"
    else:
        raise AssertionError("revoked authority imported")
    assert fingerprint(
        http.get(base + "/" + detail["assessmentId"]).json()["report"]
    ) == fingerprint(original)
    blocked = http.post(
        run_base, json=negative_request, headers=headers("rdr08-blocked-backtest-001")
    )
    assert blocked.status_code == 202
    assert dispatcher.tick()
    assert http.get(run_base + "/" + blocked.json()["runId"]).json()["run"]["status"] == "BLOCKED"
    PHASE = "LEDGER_UNCHANGED"
    with admin.connect() as connection:
        assert financial_fingerprints(connection) == ledger
    for engine in (control, worker, execution, certifier, admin):
        engine.dispose()
    print(MARKER)


def safety_guest():
    global PHASE
    import hashlib
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from backtest_browser_support import seed_backtest_fixture
    from sqlalchemy import URL, create_engine, event, text
    from test_paper_lifecycle import PaperFixture

    from packages.backtesting.readiness_jobs import ReadinessJobs
    from services.worker import backtesting

    fixture, engines = PaperFixture(), []
    entered, release = threading.Event(), threading.Event()
    try:
        PHASE = "PAPER_PREPARE"
        fixture.prepare()
        fixture.profile()
        fixture.refresh()
        admin = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="lab_migration",
                host="postgres",
                database=fixture.database,
            ),
            hide_parameters=True,
        )
        engines.append(admin)
        seed = seed_backtest_fixture(admin)
        with admin.connect() as connection:
            financial_before = financial_fingerprints(connection, SAFETY_STABLE_TABLES)
        with admin.begin() as connection:
            connection.execute(text("CREATE ROLE atl_backtest_worker_login LOGIN NOINHERIT"))
            connection.execute(text("GRANT atl_backtest_worker TO atl_backtest_worker_login"))
            connection.execute(
                text("GRANT CONNECT ON DATABASE lab_paper_runtime TO atl_backtest_worker_login")
            )
        worker = create_engine(
            URL.create(
                "postgresql+psycopg",
                username="atl_backtest_worker_login",
                host="postgres",
                database=fixture.database,
            ),
            pool_size=1,
            max_overflow=0,
            pool_timeout=1,
            hide_parameters=True,
        )
        engines.append(worker)

        @event.listens_for(worker, "connect")
        def restrict(dbapi, _):
            dbapi.execute("SET ROLE atl_backtest_worker")
            dbapi.commit()

        jobs = ReadinessJobs(fixture.engines["api"])
        for index in range(3):
            jobs.enqueue(
                request_for(seed),
                "local-operator",
                f"rdr08-safety-readiness-{index:04}",
                "c" * 32,
                "d" * 40,
            )
        original, calls = backtesting.assess, 0

        def loaded_assess(*args, checkpoint, **kwargs):
            def actual_checkpoint():
                nonlocal calls
                checkpoint()
                calls += 1
                if calls != 2:
                    return
                entered.set()
                deadline = time.monotonic() + 10
                payload = b"explicit-synthetic-readiness-cpu-load" * 256
                while not release.is_set() and time.monotonic() < deadline:
                    hashlib.sha256(payload).digest()
                if not release.is_set():
                    raise AssertionError("Paper safety did not complete during assessment")

            return original(*args, checkpoint=actual_checkpoint, **kwargs)

        PHASE = "ACTIVE_ASSESSMENT"
        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(backtesting, "assess", loaded_assess),
        ):
            dispatcher = backtesting.BacktestWorker(worker, enabled=True, code_version="d" * 40)
            task = pool.submit(dispatcher.tick)
            assert entered.wait(8), "real assessor did not reach checkpoint"
            assert worker.pool.checkedout() == 0, "assessor CPU holds SQL transaction"
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM backtest.readiness_jobs WHERE status='RUNNING'")
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM backtest.readiness_jobs WHERE status='ACCEPTED'")
                    ).scalar_one()
                    == 2
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM public.broker_orders")
                    ).scalar_one()
                    == 0
                )
            PHASE = "PAPER_SAFETY_UNDER_ASSESSOR_LOAD"
            started = time.monotonic()
            pause = pool.submit(
                fixture.api.enqueue,
                "PAUSE",
                {"action": "PAUSE"},
                "operator",
                "rdr08-safety-pause-001",
                fixture.trace,
            )
            kill = pool.submit(
                fixture.api.enqueue,
                "KILL",
                {"action": "KILL"},
                "operator",
                "rdr08-safety-kill-0001",
                fixture.trace,
            )
            reconciliation = pool.submit(fixture.refresh, publish=False)
            assert pause.result(timeout=5)["status"] == "ACCEPTED"
            assert kill.result(timeout=5)["status"] == "ACCEPTED"
            reconciliation.result(timeout=5)
            assert time.monotonic() - started < 5
            status = fixture.api.read_status(fixture.trace)
            assert status["operatingState"] == "HALTED" and not status["entryGateOpen"]
            assert (
                fixture.sql(
                    "execution",
                    "SELECT safety_state='HALTED' "
                    "FROM public.risk_account_context WHERE account_id='paper'",
                )
                is True
            )
            assert fixture.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            assert not task.done(), "safety controls must finish while assessor is active"
            release.set()
            assert task.result(timeout=8)
        with admin.connect() as connection:
            assert financial_fingerprints(connection, SAFETY_STABLE_TABLES) == financial_before
            assert (
                connection.execute(
                    text("SELECT count(*) FROM backtest.readiness_jobs WHERE status='SUCCEEDED'")
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one()
                == 0
            )
        assert all(method == "GET" for method, _ in fixture.http_calls)
        print(SAFETY)
    finally:
        release.set()
        for engine in (*engines, *fixture.engines.values()):
            engine.dispose()


if __name__ == "__main__":
    try:
        if sys.argv[2:] == ["safety"]:
            safety_guest()
        else:
            guest()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "APW02_FAIL:READINESS:"
            + type(error).__name__
            + ":"
            + PHASE
            + ":"
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
