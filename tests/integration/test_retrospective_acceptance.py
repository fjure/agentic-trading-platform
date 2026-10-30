"""RRB-08: real local backfill, immutable replay and Paper safety under research load."""

import sys
from pathlib import Path

MARKER = "PASS retrospective acceptance ledger/replay/daily-series/no-provider"
SAFETY = "PASS: retrospective active CPU preserves nonempty Paper safety"
PHASE = "BOOT"
CODE = "b" * 40


def seed_current(engine):
    """Actual receipt timestamps; the daily series shares the requested time range."""
    from dataclasses import replace
    from uuid import uuid4

    from sqlalchemy import text

    from packages.research.models import BarInterval
    from packages.research.storage import ResearchStore

    try:
        from tests.integration.test_retrospective_api import seed
    except ModuleNotFoundError:  # Flat, explicitly mounted Paper safety guest.
        from test_retrospective_api import seed

    request, bars = seed(engine)
    daily = replace(
        bars[0],
        revision_id=uuid4(),
        identity=replace(
            bars[0].identity, interval=BarInterval.ONE_DAY, bar_end=bars[-1].identity.bar_end
        ),
    )
    with engine.begin() as connection:
        daily = ResearchStore().append_bar(connection, daily, symbol="SPY")
        job = uuid4()
        values = dict(
            job=job,
            instrument=daily.identity.instrument_id,
            start=daily.identity.bar_start,
            end=daily.identity.bar_end,
        )
        connection.execute(
            text("""INSERT INTO research.ingestion_jobs
            (job_id,actor,idempotency_key,request_hash,instrument_id,start_at,end_at,
             interval,mode,status,trace_id) VALUES(:job,'local-operator',
             'rrb08-daily-quality-job-0001',repeat('e',64),:instrument,:start,:end,
             '1Day','BACKFILL','SUCCEEDED',repeat('a',32))"""),
            values,
        )
        connection.execute(
            text("""INSERT INTO research.quality_observations
            (observation_id,job_id,instrument_id,start_at,end_at,interval,quality,
             rule_version,code,commit_version,ingested_at) VALUES(:id,:job,:instrument,
             :start,:end,'1Day','INVALID','research-quality-v1',
             'REJECTED_PROVIDER_RESPONSE',0,clock_timestamp())"""),
            {**values, "id": uuid4()},
        )
    return request, bars, daily


def protocol(request):
    return {
        **request,
        "researchMode": "RETROSPECTIVE_EXPLORATORY",
        "selectionPolicyVersion": "retrospective-v1",
    }


def forbidden_research_calls(*, include_http=True):
    """Patch capability boundaries, never a successful research implementation."""
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    mocks = [
        stack.enter_context(patch(target, side_effect=AssertionError("research I/O")))
        for target in (
            *(("httpx.Client.send", "httpx.AsyncClient.send") if include_http else ()),
            "packages.brokers.fake.FakeBrokerAdapter.submit",
            "packages.brokers.fake.FakeBrokerAdapter.cancel",
            "packages.brokers.alpaca_paper.AlpacaPaperBrokerAdapter.submit",
            "packages.brokers.alpaca_paper.AlpacaPaperBrokerAdapter.cancel",
            "packages.brokers.alpaca_paper.AlpacaPaperBrokerAdapter.cancel_with_outcome",
            "packages.brokers.alpaca_history.AlpacaHistorySource.fetch_page",
            "packages.brokers.alpaca_history.AlpacaHistorySource.fetch_calendar",
            "packages.decisions.jev.JevDecisionEngine.decide",
        )
    ]
    return stack, mocks


def test_retrospective_integrated_acceptance_actual_postgresql():
    from tests.integration import test_readiness_storage as harness
    from tests.integration.test_retrospective_storage import TABLES

    previous = harness.TABLES
    try:
        harness.TABLES = tuple(dict.fromkeys((*previous, *TABLES)))
        assert harness.host("tests.integration.test_retrospective_acceptance").endswith("/restore")
    finally:
        harness.TABLES = previous


def guest():
    from dataclasses import replace
    from decimal import Decimal
    from unittest.mock import patch
    from uuid import uuid4

    from sqlalchemy import text

    from packages.persistence.ledger import PostgresLedger
    from packages.research.storage import ResearchStore
    from services.control_api.backtest_service import BacktestService
    from services.runtime.provision import seed as seed_ledger
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine
    from tests.integration.test_readiness_acceptance import financial_fingerprints
    from tests.security.test_backtest_access import BASE, ORIGIN, client

    engine = guest_engine()
    seed_ledger(PostgresLedger(engine=engine), Decimal("10000"))
    request, bars, daily = seed_current(engine)
    control, worker = (
        role_engine(engine, role) for role in ("atl_control_api", "atl_backtest_worker")
    )
    service = BacktestService(control, code_version=CODE)
    http = client(service)
    dispatcher = BacktestWorker(worker, enabled=True, code_version=CODE)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() > 0
        assert connection.execute(text("SELECT count(*) FROM public.positions")).scalar_one() > 0
        before = financial_fingerprints(connection)

    def post(path, body, key):
        response = http.post(
            BASE + path, json=body, headers={"Origin": ORIGIN, "Idempotency-Key": key}
        )
        assert response.status_code == 202, response.json()
        return response.json()

    def work(*, preview=False):
        stack, mocks = forbidden_research_calls()
        with stack:
            if preview:
                with patch(
                    "services.worker.retrospective_backtesting.evaluate",
                    side_effect=AssertionError("preview CPU"),
                ):
                    assert dispatcher.tick()
            else:
                assert dispatcher.tick()
        assert all(mock.call_count == 0 for mock in mocks)

    strict = post("/runs", request, "rrb08-strict-backfill-0001")
    work()
    assert service.read_run(strict["runId"])["run"]["status"] == "BLOCKED"
    preview = post("/assessments", protocol(request), "rrb08-preview-current-0001")
    work(preview=True)
    preview = service.read_assessment(preview["assessmentId"])
    assert preview["status"] == "SUCCEEDED"
    assert preview["eligibility"] == "EXPLORATORY_WITH_LIMITATIONS"
    report_id = preview["reliabilityReportId"]
    page = service.read_reliability_report(report_id, {"limit": 200, "section": "findings"})
    report = page["report"]
    assert report["licenseStatus"] == report["corporateActionStatus"] == "UNKNOWN"
    assert report["historicalAvailabilityStatus"] == "NOT_PROVEN"
    with engine.connect() as connection:
        ids = (
            connection.execute(
                text(
                    "SELECT reference_id::text FROM "
                    "backtest.retrospective_revision_bindings "
                    "WHERE assessment_id=:id AND kind='BAR'"
                ),
                {"id": preview["assessmentId"]},
            )
            .scalars()
            .all()
        )
    assert set(ids) == {str(bar.revision_id) for bar in bars}
    assert str(daily.revision_id) not in ids
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM "
                    "backtest.retrospective_revision_bindings b "
                    "JOIN research.quality_observations q "
                    "ON q.observation_id=b.reference_id WHERE b.assessment_id=:id "
                    "AND b.kind='QUALITY' AND q.interval='1Day'"
                ),
                {"id": preview["assessmentId"]},
            ).scalar_one()
            == 0
        )
    creation = {
        **protocol(request),
        "assessmentId": preview["assessmentId"],
        "assessmentHash": preview["assessmentHash"],
    }
    first = post("/runs", creation, "rrb08-first-real-run-0001")
    work()
    with engine.begin() as connection:
        ResearchStore().append_bar(
            connection,
            replace(bars[-1], revision_id=uuid4(), close=Decimal("13"), high=Decimal("13")),
            symbol="SPY",
        )
    repeated = post("/runs", creation, "rrb08-replay-real-run-0001")
    work()
    first, repeated = (service.read_run(row["runId"])["run"] for row in (first, repeated))
    assert first["status"] == repeated["status"] == "SUCCEEDED"
    assert first["resultHash"] == repeated["resultHash"]
    with engine.connect() as connection:
        results = connection.execute(
            text("SELECT body FROM backtest.results WHERE run_id=:id"), {"id": repeated["runId"]}
        ).scalar_one()
    assert Decimal(results["summary"][0]["metrics"]["endEquity"]) == Decimal("1200")
    warned = service.read_reliability_report(report_id, {"limit": 200, "section": "findings"})
    assert warned["report"] == report
    assert any(
        w["code"] == "EX_POST_REVISIONS" and not w["blocksNewExecution"]
        for w in warned["currentWarnings"]
    )
    append_split(engine, bars)
    blocked = post("/runs", creation, "rrb08-later-split-run-0001")
    work(preview=True)  # A current split must refuse CPU, even for buy-and-hold.
    assert service.read_run(blocked["runId"])["run"]["status"] == "BLOCKED"
    warned = service.read_reliability_report(report_id, {"limit": 200, "section": "findings"})
    assert warned["report"] == report
    assert any(
        w["category"] == "CORPORATE_ACTIONS" and w["blocksNewExecution"]
        for w in warned["currentWarnings"]
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": blocked["runId"]},
            ).scalar_one()
            == 0
        )
        assert financial_fingerprints(connection) == before
    for item in (engine, control, worker):
        item.dispose()
    print(MARKER)


def append_split(engine, bars):
    """Append a locally trusted synthetic statement through real evidence storage."""
    import hashlib
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy import text

    from packages.domain.models import utc_string
    from packages.research.evidence_storage import EvidenceInput, EvidenceStore, unknown_trust

    with engine.begin() as connection:
        now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        authority = dict(
            decisionId=str(uuid4()),
            sourceId="rrb08-synthetic-source",
            keyId="rrb08-synthetic-key",
            witnessId=None,
            authorityType="SOURCE",
            effectiveAt=now - timedelta(days=1),
            validFrom=now - timedelta(days=2),
            validTo=now + timedelta(days=2),
            revoked=False,
            compromisedAt=None,
            compromiseTimeKnown=True,
            policyVersion="synthetic-v1",
            reason="SYNTHETIC_TEST",
            scope=dict(
                instrumentIds=[str(bars[0].identity.instrument_id)], kinds=["CORPORATE_ACTION"]
            ),
            publicKey="A" * 43 + "=",
            algorithm="Ed25519",
            methods=["RFC8785-UTF8-v1"],
            actorRef="synthetic-operator",
            documentFingerprint="d" * 64,
            testOnly=True,
            predecessorDecisionId=None,
        )
        store = EvidenceStore()
        store.append_trust_decision(connection, authority, 0)
        trust = {
            name: {
                **dict(value),
                "status": "UNKNOWN" if name == "temporalAuthenticity" else "VERIFIED",
            }
            for name, value in unknown_trust().items()
        }
        trust["sourceAuthenticity"]["evidenceRefs"] = [
            dict(
                kind="KEY",
                id=authority["keyId"],
                revision="1",
                fingerprint=hashlib.sha256(bytes(32)).hexdigest(),
            )
        ]
        entry = EvidenceInput(
            uuid4(),
            authority["sourceId"],
            "later-split",
            "CORPORATE_ACTION",
            bars[0].identity.instrument_id,
            bars[0].identity.bar_start - timedelta(minutes=30),
            bars[-1].identity.bar_end,
            now - timedelta(seconds=1),
            None,
            1,
            None,
            "e" * 64,
            dict(
                type="CORPORATE_ACTION_COVERAGE",
                conclusion="ACTIONS_PRESENT",
                actions=[
                    dict(
                        type="SPLIT", ratio="2", effectiveAt=utc_string(bars[1].identity.bar_start)
                    )
                ],
            ),
            trust,
            dict(
                content=dict(
                    keyId=authority["keyId"], signedAt=utc_string(now - timedelta(seconds=1))
                )
            ),
        )
        store.append_bundle(connection, "rrb08-later-split-bundle-0001", "e" * 64, (entry,), 1)


def test_retrospective_load_preserves_nonempty_paper_safety():
    from tests.integration.test_paper_provision import host

    root = Path(__file__).resolve().parents[2]
    assert (
        host(
            Path(__file__),
            SAFETY,
            support_paths=(
                root / "tests/integration/test_paper_lifecycle.py",
                root / "tests/integration/test_retrospective_api.py",
            ),
            guest_args=("safety",),
        )
        == SAFETY
    )


def safety_guest():
    global PHASE
    import hashlib
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from unittest.mock import patch

    from sqlalchemy import URL, create_engine, event, text
    from test_paper_lifecycle import PaperFixture

    from packages.backtesting.jobs import BacktestError
    from packages.backtesting.readiness_jobs import ReadinessJobs
    from services.control_api.backtest_service import BacktestService
    from services.worker import retrospective_backtesting
    from services.worker.backtesting import BacktestWorker

    fixture = PaperFixture()
    fixture.initial_holding_value = fixture.current_holding_value = Decimal("770")
    release, entered = threading.Event(), threading.Event()
    engines = []
    stable = (
        "positions",
        "position_versions",
        "trade_proposals",
        "risk_policies",
        "risk_decisions",
        "risk_reservations",
        "order_intents",
        "broker_orders",
        "execution_reports",
        "paper_initial_bases",
        "paper_order_accounting_bases",
        "paper_position_cost_bases",
    )

    def fingerprints(connection):
        return tuple(
            connection.execute(
                text(
                    "SELECT md5(COALESCE(jsonb_agg(to_jsonb(r) "
                    "ORDER BY to_jsonb(r)::text)::text,'[]')) FROM public." + table + " r"
                )
            ).scalar_one()
            for table in stable
        )

    try:
        PHASE = "PREPARE_NONEMPTY_PAPER"
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
        request, _bars, _daily = seed_current(admin)
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
        def restrict(dbapi, _record):
            dbapi.execute("SET ROLE atl_backtest_worker")
            dbapi.commit()

        service = BacktestService(fixture.engines["api"], code_version=CODE)
        dispatcher = BacktestWorker(worker, enabled=True, code_version=CODE)
        preview = service.create_assessment(
            protocol(request), "local-operator", "rrb08-safety-preview-0001", "a" * 32
        )
        stack, preview_mocks = forbidden_research_calls()
        with (
            stack,
            patch.object(
                retrospective_backtesting, "evaluate", side_effect=AssertionError("preview CPU")
            ),
        ):
            assert dispatcher.tick()
        assert all(mock.call_count == 0 for mock in preview_mocks)
        preview = service.read_assessment(preview["assessmentId"])
        assert preview["status"] == "SUCCEEDED"
        creation = {
            **protocol(request),
            "assessmentId": preview["assessmentId"],
            "assessmentHash": preview["assessmentHash"],
        }
        run = service.create_run(creation, "local-operator", "rrb08-safety-run-0001", "a" * 32)
        service.create_run(request, "local-operator", "rrb08-safety-strict-0001", "a" * 32)
        ReadinessJobs(fixture.engines["api"]).enqueue(
            request, "local-operator", "rrb08-safety-readiness-0001", "a" * 32, CODE
        )
        for index in range(97):
            service.create_assessment(
                protocol(request), "local-operator", f"rrb08-safety-queued-{index:04}", "a" * 32
            )
        try:
            service.create_assessment(
                protocol(request), "local-operator", "rrb08-safety-overflow-0001", "a" * 32
            )
        except BacktestError as error:
            assert error.status == 503 and error.code == "UNAVAILABLE"
        else:
            raise AssertionError("shared 100-job capacity was bypassed")
        with admin.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM public.positions WHERE quantity>0")
                ).scalar_one()
                > 0
            )
            before = fingerprints(connection)
        original = retrospective_backtesting.evaluate

        def loaded(config, datasets, *, checkpoint, on_fold, validation_context):
            count = 0

            def step():
                nonlocal count
                checkpoint()
                count += 1
                if count == 2:
                    entered.set()
                    deadline = time.monotonic() + 12
                    while not release.is_set() and time.monotonic() < deadline:
                        hashlib.sha256(b"synthetic-retrospective-load" * 256).digest()
                    assert release.is_set(), "Paper controls missed active CPU window"

            return original(
                config,
                datasets,
                checkpoint=step,
                on_fold=on_fold,
                validation_context=validation_context,
            )

        PHASE = "ACTIVE_RETROSPECTIVE_CPU"
        import httpx

        original_send = httpx.Client.send
        original_async_send = httpx.AsyncClient.send
        research_thread = []
        research_http = []

        def guarded_send(client, *args, **kwargs):
            if research_thread and threading.get_ident() == research_thread[0]:
                research_http.append(True)
                raise AssertionError("research provider HTTP")
            return original_send(client, *args, **kwargs)

        def execute():
            research_thread.append(threading.get_ident())
            return dispatcher.tick()

        async def guarded_async_send(client, *args, **kwargs):
            if research_thread and threading.get_ident() == research_thread[0]:
                research_http.append(True)
                raise AssertionError("research provider HTTP")
            return await original_async_send(client, *args, **kwargs)

        capability_stack, capability_mocks = forbidden_research_calls(include_http=False)
        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(retrospective_backtesting, "evaluate", loaded),
            capability_stack,
            patch.object(httpx.Client, "send", guarded_send),
            patch.object(httpx.AsyncClient, "send", guarded_async_send),
        ):
            task = pool.submit(execute)
            assert entered.wait(8), "real simulator never entered"
            assert worker.pool.checkedout() == 0
            with admin.connect() as connection:
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM backtest.runs WHERE status='RUNNING'")
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT (SELECT count(*) FROM backtest.runs WHERE status='ACCEPTED') + "
                            "(SELECT count(*) FROM backtest.retrospective_assessments "
                            "WHERE status='ACCEPTED') + "
                            "(SELECT count(*) FROM backtest.readiness_jobs "
                            "WHERE status='ACCEPTED')"
                        )
                    ).scalar_one()
                    == 99
                )
            PHASE = "CONCURRENT_PAPER_SAFETY"
            started = time.monotonic()
            pause = pool.submit(
                fixture.api.enqueue,
                "PAUSE",
                {"action": "PAUSE"},
                "operator",
                "rrb08-pause-under-load-0001",
                fixture.trace,
            )
            kill = pool.submit(
                fixture.api.enqueue,
                "KILL",
                {"action": "KILL"},
                "operator",
                "rrb08-kill-under-load-0001",
                fixture.trace,
            )
            refresh = pool.submit(fixture.refresh, publish=False)
            assert pause.result(timeout=5)["status"] == "ACCEPTED"
            assert kill.result(timeout=5)["status"] == "ACCEPTED"
            refresh.result(timeout=5)
            assert time.monotonic() - started < 5
            status = fixture.api.read_status(fixture.trace)
            assert status["operatingState"] == "HALTED" and status["entryGateOpen"] is False
            assert fixture.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            assert not task.done()
            release.set()
            assert task.result(timeout=8)
        assert not research_http
        assert all(mock.call_count == 0 for mock in capability_mocks)
        assert service.read_run(run["runId"])["run"]["status"] == "SUCCEEDED"
        with admin.connect() as connection:
            assert fingerprints(connection) == before
            assert (
                connection.execute(
                    text("SELECT safety_state FROM public.risk_account_context")
                ).scalar_one()
                == "HALTED"
            )
        assert all(method == "GET" for method, _path in fixture.http_calls)
        print(SAFETY)
    finally:
        release.set()
        for engine in (*engines, *fixture.engines.values()):
            engine.dispose()


if __name__ == "__main__":
    try:
        (safety_guest if "safety" in sys.argv[1:] else guest)()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "APW02_FAIL:RETROSPECTIVE_ACCEPTANCE:"
            + type(error).__name__
            + ":"
            + PHASE
            + ":"
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
