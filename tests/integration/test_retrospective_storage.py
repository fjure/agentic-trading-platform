"""Real disposable PostgreSQL migration, restore, leases and immutable bindings."""

import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

TABLES = tuple(
    "backtest." + name
    for name in (
        "retrospective_assessments",
        "retrospective_datasets",
        "retrospective_revision_bindings",
        "retrospective_reports",
        "retrospective_audit",
        "license_receipts",
        "retrospective_execution_gates",
        "runs",
        "manifests",
        "results",
    )
)

# Deliberately byte-for-byte the pre-RRB BacktestJobs.claim candidate/update SQL.
# New code must not replace this negative compatibility test with a filtered query.
LEGACY_CLAIM_SQL = """
              WITH candidate AS (SELECT run_id FROM backtest.runs
                WHERE status='ACCEPTED' OR
                  (status='RUNNING' AND lease_until<=clock_timestamp() AND attempts<3)
                ORDER BY accepted_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1)
              UPDATE backtest.runs r SET status='RUNNING',owner=:owner,fence=fence+1,
                attempts=attempts+1,started_at=clock_timestamp(),
                lease_until=clock_timestamp()+interval '300 seconds',updated_at=clock_timestamp()
              FROM candidate c WHERE r.run_id=c.run_id RETURNING r.*
            """


def host():
    from tests.integration import test_readiness_storage as harness

    previous = harness.TABLES
    try:
        harness.TABLES = (*previous, *TABLES)
        return harness.host("tests.integration.test_retrospective_storage")
    finally:
        harness.TABLES = previous


def guest():
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.jobs import BacktestError, BacktestJobs, encoded
    from packages.backtesting.retrospective_storage import MODE, POLICY, RetrospectiveStorage
    from packages.domain.models import utc_string
    from packages.research.models import (
        AssetClass,
        BarIdentity,
        BarInterval,
        BarRevision,
        Instrument,
        SymbolMapping,
        content_fingerprint,
    )
    from packages.research.storage import ResearchStore
    from tests.integration.test_backtest_jobs import role_engine, valid_request
    from tests.security.test_retrospective_roles import verify_roles

    credentials = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=credentials["role"],
            password=credentials["password"],
            host="postgres",
            port=5432,
            database=credentials["database"],
            query={"connect_timeout": "5"},
        ),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0032_collection_control")
    strict_request = valid_request()
    strict = BacktestJobs(engine).enqueue(
        strict_request, "local-operator", "legacy-before-migration-0001", "a" * 32, "b" * 40
    )
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO backtest.manifests VALUES(:id,:hash,CAST(:body AS jsonb))"),
            {"id": strict["run_id"], "hash": "c" * 64, "body": '{"legacy":"byte identity"}'},
        )
        before = connection.execute(
            text("SELECT to_jsonb(r) FROM backtest.runs r WHERE run_id=:id"),
            {"id": strict["run_id"]},
        ).scalar_one()
        financial_before = connection.execute(
            text("SELECT count(*) FROM public.audit_events")
        ).scalar_one()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        after = connection.execute(
            text(
                "SELECT "
                "to_jsonb(r)-ARRAY['research_mode','selection_policy_version','experiment_version','assessment_id']"
                " FROM backtest.runs r WHERE run_id=:id"
            ),
            {"id": strict["run_id"]},
        ).scalar_one()
        assert before == after
        assert (
            connection.execute(
                text("SELECT fingerprint FROM backtest.manifests WHERE run_id=:id"),
                {"id": strict["run_id"]},
            ).scalar_one()
            == "c" * 64
        )
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    reads, jobs, strict_jobs = (
        RetrospectiveStorage(control),
        RetrospectiveStorage(worker),
        BacktestJobs(worker),
    )
    sql_failures = []

    @event.listens_for(worker, "handle_error")
    def remember_synthetic_diagnostic(context):
        sql_failures.append(
            getattr(getattr(context.original_exception, "diag", None), "message_primary", "")
        )

    def denied(call, code=None):
        try:
            call()
        except (DBAPIError, BacktestError) as error:
            if code is not None:
                assert isinstance(error, BacktestError) and error.code == code, (
                    getattr(error, "code", None),
                    code,
                    sql_failures[-1:],
                )
        else:
            raise AssertionError("forbidden write unexpectedly succeeded")

    # Ordinary Strict remains claimable with the exact previous binary SQL.
    with worker.begin() as connection:
        legacy = connection.execute(text(LEGACY_CLAIM_SQL), {"owner": uuid4()}).mappings().one()
    from packages.backtesting.jobs import Lease

    strict_jobs.publish(
        Lease(legacy["run_id"], legacy["owner"], legacy["fence"], dict(legacy)),
        {"legacy": "unchanged"},
    )

    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    store = ResearchStore()
    with engine.begin() as connection:
        store.add_instrument(connection, instrument)
        store.add_mapping(
            connection,
            SymbolMapping(instrument.instrument_id, "alpaca", "SPY", start - timedelta(days=1)),
        )
    original = BarRevision(
        uuid4(),
        BarIdentity(
            instrument.instrument_id,
            "alpaca",
            "iex",
            BarInterval.FIFTEEN_MINUTES,
            start,
            start + timedelta(minutes=15),
        ),
        *(Decimal("100"),) * 4,
        Decimal("10"),
        start + timedelta(minutes=15),
        start + timedelta(minutes=15),
    )
    with engine.begin() as connection:
        original = store.append_bar(connection, original, symbol="SPY")
        calendar_id, quality_id, evidence_id, ingestion_id = (uuid4() for _ in range(4))
        params = {
            "instrument": instrument.instrument_id,
            "calendar": calendar_id,
            "quality": quality_id,
            "evidence": evidence_id,
            "job": ingestion_id,
            "start": start,
            "end": start + timedelta(hours=6),
            "day": start.date(),
        }
        connection.execute(
            text("""INSERT INTO research.calendar_versions
            (session_id,session_date,opens_at,closes_at,version,source,commit_version,ingested_at)
            VALUES(:calendar,:day,:start,:end,repeat('d',64),'alpaca-v2-calendar',0,clock_timestamp())"""),
            params,
        )
        connection.execute(
            text("""INSERT INTO research.ingestion_jobs
            (job_id,actor,idempotency_key,request_hash,instrument_id,start_at,end_at,interval,mode,status,trace_id)
            VALUES(:job,'local-operator','synthetic-metadata-0001',repeat('e',64),:instrument,:start,:end,
              '15Min','BACKFILL','SUCCEEDED',repeat('a',32))"""),
            params,
        )
        connection.execute(
            text("""INSERT INTO research.quality_observations
            (observation_id,job_id,instrument_id,start_at,end_at,interval,quality,rule_version,code,commit_version,ingested_at)
            VALUES(:quality,:job,:instrument,:start,:end,'15Min','VALID','research-quality-v1',
              'NORMALIZED_JOB_COMPLETED',0,clock_timestamp())"""),
            params,
        )
        connection.execute(
            text("""INSERT INTO research.evidence_revisions
            (evidence_id,source_id,evidence_key,kind,instrument_id,start_at,end_at,source_observed_at,
             revision,fingerprint,statement,trust,provenance,trust_version,commit_version,ingested_at)
            VALUES(:evidence,'synthetic','actions','CORPORATE_ACTION',:instrument,:start,:end,:end,
              1,repeat('f',64),'{}','{}','{}',0,research.reserve_version(),clock_timestamp())"""),
            params,
        )
        quality_hash = connection.execute(
            text(
                "SELECT encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') FROM "
                "research.quality_observations r WHERE observation_id=:id"
            ),
            {"id": quality_id},
        ).scalar_one()
    references = (
        ("BAR", original.revision_id, original.fingerprint),
        ("CALENDAR", calendar_id, "d" * 64),
        ("QUALITY", quality_id, quality_hash),
        ("EVIDENCE", evidence_id, "f" * 64),
    )
    request = {**strict_request, "researchMode": MODE, "selectionPolicyVersion": POLICY}
    request["instrumentId"] = str(instrument.instrument_id)

    def enqueue(key, body=None):
        return reads.enqueue_assessment(body or request, "local-operator", key, "a" * 32, "b" * 40)

    first = enqueue("retrospective-assessment-0001")
    assert reads.license_receipt(first["assessment_id"])["status"] == "UNKNOWN"
    assert enqueue("retrospective-assessment-0001")["assessment_id"] == first["assessment_id"]
    denied(
        lambda: enqueue("retrospective-assessment-0001", {**request, "startCash": "9"}),
        "IDEMPOTENCY_CONFLICT",
    )
    with engine.begin() as connection:
        receipt = (
            connection.execute(
                text("SELECT * FROM backtest.license_receipts WHERE assessment_id=:id"),
                {"id": first["assessment_id"]},
            )
            .mappings()
            .one()
        )
        assert receipt["status"] == "UNKNOWN" and receipt["recorded_at"] == first["snapshot_at"]
        assert receipt["recorded_at"] >= receipt["source_observed_at"]

    # A writer with an already allocated version remains invisible until its
    # transaction commits. Snapshot sees committed version_state, not sequence MAX.
    writer = engine.connect()
    pending = writer.begin()
    corrected = store.append_bar(
        writer,
        replace(original, revision_id=uuid4(), high=Decimal("101"), close=Decimal("101")),
        symbol="SPY",
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        frozen = pool.submit(enqueue, "retrospective-late-commit-0001").result(timeout=5)
    assert frozen["snapshot_version"] < corrected.commit_version
    pending.commit()
    writer.close()
    assert frozen["snapshot_at"] >= corrected.ingested_at, (
        "commit cap excludes a writer ingested before snapshot"
    )
    lease = jobs.claim(uuid4())
    assert lease.assessment_id == first["assessment_id"] and lease.fence == 1
    assert jobs.claim(uuid4()) is None and strict_jobs.claim(uuid4()) is None
    denied(
        lambda: jobs.bind_dataset(
            lease, {"synthetic": True}, (("BAR", corrected.revision_id, corrected.fingerprint),)
        ),
        "SNAPSHOT_CONFLICT",
    )
    dataset_hash = jobs.bind_dataset(lease, {"synthetic": True}, references)
    assert jobs.bind_dataset(lease, {"synthetic": True}, references) == dataset_hash
    denied(
        lambda: jobs.bind_dataset(
            lease, {"changed": True}, (("BAR", original.revision_id, original.fingerprint),)
        ),
        "SNAPSHOT_CONFLICT",
    )
    restored, rows = jobs.load_dataset(first["assessment_id"])
    assert (
        restored["fingerprint"] == dataset_hash
        and rows[0][1]["revision_id"] == original.revision_id
    )

    def report(held, eligibility="EXPLORATORY_WITH_LIMITATIONS"):
        from jsonschema import Draft202012Validator

        fixture = json.loads(
            Path("contracts/examples/v1/retrospective-report.valid.json").read_text()
        )
        result = {
            **fixture,
            "reportId": str(uuid4()),
            "assessmentId": str(held.assessment_id),
            "runId": str(held.run_id) if held.run_id else None,
            "researchMode": MODE,
            "selectionPolicyVersion": POLICY,
            "overallStatus": eligibility,
            "findingsCount": 0,
            "revisionReferenceCount": 0,
            "protocolHash": first["request_hash"],
            "phase": "FINAL" if held.run_id else "PREVIEW",
            "snapshotReference": {
                "snapshotVersion": held.row["snapshot_version"],
                "snapshotAt": utc_string(held.row["snapshot_at"]),
            },
        }
        schema = json.loads(Path("contracts/schemas/v1/backtesting.schema.json").read_text())
        Draft202012Validator({**schema, "$ref": "#/$defs/BacktestReliabilityReportV1"}).validate(
            result
        )
        return result

    def raw_positive_publication(held):
        # Bypass the public Python license check deliberately: SQL must still
        # roll back report, manifest, result and success while rights are denied.
        with jobs.transaction(lease=held) as connection:
            jobs._insert_report(connection, held, report(held))
            jobs._bind_run_manifest(connection, held, {"synthetic": "manifest"})
            connection.execute(
                text("""INSERT INTO backtest.results(run_id,fingerprint,body)
                    VALUES(:id,repeat('a',64),'{"synthetic":true}')"""),
                {"id": held.run_id},
            )
            connection.execute(
                text("UPDATE backtest.runs SET status='SUCCEEDED' WHERE run_id=:id"),
                {"id": held.run_id},
            )

    # Raw writers cannot publish on another owner's fence, even with policy marker.
    with jobs.transaction() as connection:
        denied(
            lambda: connection.execute(
                text(
                    "UPDATE backtest.retrospective_assessments SET status='FAILED' WHERE "
                    "assessment_id=:id"
                ),
                {"id": lease.assessment_id},
            )
        )

    def expire_lease(held):
        # Only the disposable database owner may inject expiry without waiting
        # five minutes; worker roles can never disable this policy trigger.
        table, key, identifier = (
            ("runs", "run_id", held.run_id)
            if held.run_id
            else ("retrospective_assessments", "assessment_id", held.assessment_id)
        )
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE backtest.{table} DISABLE TRIGGER a_retrospective_policy")
            )
            connection.execute(
                text(
                    f"UPDATE backtest.{table} SET lease_until=clock_timestamp()-interval '1s' "
                    f"WHERE {key}=:id"
                ),
                {"id": identifier},
            )
            connection.execute(
                text(f"ALTER TABLE backtest.{table} ENABLE TRIGGER a_retrospective_policy")
            )

    # A live owned lease cannot extend/reset its budget through raw SQL.
    for updates in (
        "lease_until=clock_timestamp()+interval '600 seconds'",
        "attempts=0",
        "started_at=clock_timestamp()",
    ):

        def mutate_lease(updates=updates):
            with jobs.transaction(lease=lease) as connection:
                connection.execute(
                    text(
                        f"UPDATE backtest.retrospective_assessments SET {updates} "
                        "WHERE assessment_id=:id"
                    ),
                    {"id": lease.assessment_id},
                )

        denied(mutate_lease, "LEASE_LOST")
    expire_lease(lease)
    stale = lease
    lease = jobs.claim(uuid4())
    assert lease.fence == 2
    denied(lambda: jobs.publish_assessment(stale, report(stale)), "LEASE_LOST")
    denied(lambda: jobs.bind_dataset(stale, {}), "LEASE_LOST")

    incomplete_report = report(lease)
    incomplete_report.pop("dimensions")
    denied(lambda: jobs.publish_assessment(lease, incomplete_report), "SNAPSHOT_CONFLICT")

    finding = {
        "findingId": str(uuid4()),
        "category": "LICENSE",
        "code": "LICENSE_UNKNOWN",
        "severity": "LIMITATION",
        "foldIndex": None,
        "period": None,
        "references": [],
    }
    reference = {
        "kind": "BAR",
        "referenceId": str(original.revision_id),
        "fingerprint": original.fingerprint,
        "localRevision": 1,
        "recordedAt": utc_string(original.ingested_at),
        "period": None,
    }
    complete_report = {**report(lease), "findingsCount": 1, "revisionReferenceCount": 1}
    denied(lambda: jobs.publish_assessment(lease, complete_report), "SNAPSHOT_CONFLICT")
    denied(
        lambda: jobs.publish_assessment(
            lease,
            complete_report,
            findings=[{**finding, "untrusted": True}],
            revision_references=[reference],
        ),
        "SNAPSHOT_CONFLICT",
    )
    denied(
        lambda: jobs.publish_assessment(
            lease,
            {**complete_report, "findingsCount": 2},
            findings=[finding, finding],
            revision_references=[reference],
        ),
        "SNAPSHOT_CONFLICT",
    )
    denied(
        lambda: jobs.publish_assessment(
            lease,
            {**complete_report, "revisionReferenceCount": 2},
            findings=[finding],
            revision_references=[reference, reference],
        ),
        "SNAPSHOT_CONFLICT",
    )

    def raw_incomplete_entries():
        envelope = {"report": complete_report, "findings": [], "revisionReferences": [reference]}
        with jobs.transaction(lease=lease) as connection:
            connection.execute(
                text("""INSERT INTO backtest.retrospective_reports
                    (report_id,assessment_id,run_id,phase,fingerprint,body,
                     findings,revision_references,canonical_text)
                    VALUES(:id,:assessment,NULL,'PREVIEW',:hash,CAST(:body AS jsonb),
                           '[]',CAST(:references AS jsonb),:canonical)"""),
                {
                    "id": UUID(complete_report["reportId"]),
                    "assessment": lease.assessment_id,
                    "hash": content_fingerprint(envelope),
                    "body": encoded(complete_report),
                    "references": encoded([reference]),
                    "canonical": encoded(envelope),
                },
            )

    denied(raw_incomplete_entries, "UNAVAILABLE")

    original_audit = jobs._audit

    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash before commit")

    jobs._audit = crash
    try:
        jobs.publish_assessment(
            lease, complete_report, findings=[finding], revision_references=[reference]
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("crash injection did not execute")
    jobs._audit = original_audit
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.retrospective_reports")
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT status FROM backtest.retrospective_assessments WHERE assessment_id=:id"
                ),
                {"id": lease.assessment_id},
            ).scalar_one()
            == "RUNNING"
        )
    published = jobs.publish_assessment(
        lease, complete_report, findings=[finding], revision_references=[reference]
    )
    with engine.connect() as connection:
        stored_report = (
            connection.execute(
                text("SELECT * FROM backtest.retrospective_reports WHERE report_id=:id"),
                {"id": UUID(complete_report["reportId"])},
            )
            .mappings()
            .one()
        )
        envelope = {
            "report": complete_report,
            "findings": [finding],
            "revisionReferences": [reference],
        }
        assert stored_report["body"] == complete_report
        assert stored_report["findings"] == [finding]
        assert stored_report["revision_references"] == [reference]
        assert stored_report["fingerprint"] == content_fingerprint(envelope)
        assert json.loads(stored_report["canonical_text"]) == envelope
    assert published["assessment_hash"] and published["status"] == "SUCCEEDED"
    denied(lambda: jobs.publish_assessment(lease, report(lease)), "LEASE_LOST")

    # Drain second accepted assessment before creating a run, preserving oldest admission.
    frozen_lease = jobs.claim(uuid4())
    assert frozen_lease.assessment_id == frozen["assessment_id"]
    denied(
        lambda: jobs.bind_dataset(
            frozen_lease, {}, (("BAR", corrected.revision_id, corrected.fingerprint),)
        ),
        "SNAPSHOT_CONFLICT",
    )
    jobs.bind_dataset(frozen_lease, {}, (("BAR", original.revision_id, original.fingerprint),))
    jobs.publish_assessment(frozen_lease, report(frozen_lease, "BLOCKED"))
    run_request = {
        **request,
        "assessmentId": str(lease.assessment_id),
        "assessmentHash": published["assessment_hash"],
    }
    denied(
        lambda: reads.enqueue_run(
            {**run_request, "startCash": "20"},
            "local-operator",
            "retrospective-run-invalid-0001",
            "a" * 32,
            "b" * 40,
        ),
        "SNAPSHOT_CONFLICT",
    )
    run = reads.enqueue_run(
        run_request, "local-operator", "retrospective-run-0001", "a" * 32, "b" * 40
    )
    # No nullable partial variant may silently become a Strict row.
    for fields in (
        {"selection_policy_version": POLICY},
        {"research_mode": MODE},
        {"experiment_version": "retrospective-experiment-v1"},
        {"assessment_id": first["assessment_id"]},
    ):

        def partial_variant(fields=fields):
            with worker.begin() as connection:
                extra_columns = ",".join(fields)
                extra_parameters = ",".join(":" + name for name in fields)
                connection.execute(
                    text(f"""INSERT INTO backtest.runs
                    (run_id,actor,idempotency_key,request,request_hash,snapshot_version,snapshot_at,code_version,trace_id,{extra_columns})
                    VALUES(:id,'local-operator',:key,'{{}}',repeat('a',64),0,clock_timestamp(),repeat('b',40),repeat('a',32),{extra_parameters})"""),
                    {"id": uuid4(), "key": "partial-variant-" + uuid4().hex, **fields},
                )

        denied(partial_variant)

    def downgrade_mode():
        with jobs.transaction() as connection:
            connection.execute(
                text("""UPDATE backtest.runs SET research_mode=NULL,
                  selection_policy_version=NULL,experiment_version=NULL,assessment_id=NULL,
                  request=CAST(:request AS jsonb) WHERE run_id=:id"""),
                {"id": run["run_id"], "request": json.dumps(strict_request)},
            )

    denied(downgrade_mode)

    # A raw UPDATE cannot bypass a real claim by keeping null owner/fence zero.
    for updates in (
        "status='RUNNING',lease_until=clock_timestamp()+interval '300 seconds'",
        "status='RUNNING',owner=gen_random_uuid(),fence=1,attempts=1",
        "fence=1,attempts=1,owner=gen_random_uuid()",
    ):

        def forged_claim(updates=updates):
            with jobs.transaction() as connection:
                connection.execute(
                    text(f"UPDATE backtest.runs SET {updates} WHERE run_id=:id"),
                    {"id": run["run_id"]},
                )

        denied(forged_claim)

    # Existing worker INSERT rights must never create a retrospective job in
    # a running or terminal state, even with correct mode, snapshot and marker.
    for initial_state in (
        {"status": "SUCCEEDED"},
        {"status": "BLOCKED"},
        {"status": "FAILED"},
        {"status": "RUNNING"},
        {"owner": str(uuid4())},
        {"fence": 1},
        {"attempts": 1},
        {"lease_until": utc_string(datetime.now(UTC))},
        {"started_at": utc_string(datetime.now(UTC))},
        {"checkpoint": 1},
        {"error_code": "INTERNAL_FAILURE"},
    ):

        def forged_initial_state(initial_state=initial_state):
            overrides = {
                "run_id": str(uuid4()),
                "idempotency_key": "forged-" + uuid4().hex,
                **initial_state,
            }
            with jobs.transaction() as connection:
                connection.execute(
                    text("""INSERT INTO backtest.runs SELECT
                        (jsonb_populate_record(NULL::backtest.runs,
                          to_jsonb(r)||CAST(:overrides AS jsonb))).*
                        FROM backtest.runs r WHERE run_id=:existing"""),
                    {"existing": run["run_id"], "overrides": json.dumps(overrides)},
                )

        denied(forged_initial_state)

    # Exactly the old binary's unfiltered claim must abort before owner/fence writes.
    def old_claim(policy=None):
        with worker.begin() as connection:
            if policy is not None:
                connection.execute(
                    text("SELECT set_config('backtest.policy_version',:policy,true)"),
                    {"policy": policy},
                )
            connection.execute(text(LEGACY_CLAIM_SQL), {"owner": uuid4()})

    denied(old_claim)
    denied(lambda: old_claim("foreign-policy-v999"))
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status,owner,fence,attempts FROM backtest.runs WHERE run_id=:id"),
            {"id": run["run_id"]},
        ).one()
        assert tuple(row) == ("ACCEPTED", None, 0, 0)
    assert strict_jobs.claim(uuid4()) is None
    run_lease = jobs.claim(uuid4(), run=True)
    assert run_lease.run_id == run["run_id"]

    def incomplete_success():
        with jobs.transaction(lease=run_lease) as connection:
            connection.execute(
                text("UPDATE backtest.runs SET status='SUCCEEDED' WHERE run_id=:id"),
                {"id": run_lease.run_id},
            )

    denied(incomplete_success)
    jobs._audit = crash
    try:
        jobs.publish_run(
            run_lease,
            {"synthetic": "result"},
            report(run_lease),
            manifest={"synthetic": "manifest"},
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("atomic final report crash did not execute")
    jobs._audit = original_audit
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": run["run_id"]},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.retrospective_reports WHERE run_id=:id"),
                {"id": run["run_id"]},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.manifests WHERE run_id=:id"),
                {"id": run["run_id"]},
            ).scalar_one()
            == 0
        )
    # Recovery uses a fresh fence and cannot bind or checkpoint from the old one.
    old_run_lease = run_lease
    expire_lease(run_lease)
    run_lease = jobs.claim(uuid4(), run=True)
    assert run_lease.fence == old_run_lease.fence + 1
    denied(lambda: jobs.bind_run_manifest(old_run_lease, {}), "LEASE_LOST")
    denied(lambda: jobs.checkpoint_run(old_run_lease, 0, {}), "LEASE_LOST")
    denied(
        lambda: jobs.publish_run(old_run_lease, {}, report(old_run_lease), manifest={}),
        "LEASE_LOST",
    )
    jobs.checkpoint_run(run_lease, 0, {"synthetic": "fold"})
    jobs.checkpoint_run(run_lease, 0, {"synthetic": "fold"})
    denied(lambda: jobs.checkpoint_run(run_lease, 0, {"changed": True}), "SNAPSHOT_CONFLICT")
    # A missing current rights port never authorizes positive publication.
    # Only the synthetic disposable database row is removed and restored.
    with engine.begin() as connection:
        source_row = connection.execute(
            text("SELECT row_to_json(s)::text FROM research.source_status s")
        ).scalar_one()
        connection.execute(text("DELETE FROM research.source_status"))
    try:
        denied(
            lambda: jobs.publish_run(
                run_lease, {}, report(run_lease), manifest={"synthetic": "manifest"}
            ),
            "UNAVAILABLE",
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM backtest.manifests WHERE run_id=:id"),
                    {"id": run_lease.run_id},
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT count(*) FROM backtest.retrospective_reports WHERE run_id=:id"),
                    {"id": run_lease.run_id},
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO research.source_status SELECT * FROM
                    jsonb_populate_record(NULL::research.source_status,CAST(:body AS jsonb))"""),
                {"body": source_row},
            )
    # Mutable current rights block new success without altering historical receipt.
    with engine.begin() as connection:
        connection.execute(text("UPDATE research.source_status SET license_status='BLOCKED'"))
    denied(
        lambda: jobs.publish_run(
            run_lease, {}, report(run_lease), manifest={"synthetic": "manifest"}
        ),
        "SNAPSHOT_CONFLICT",
    )
    with jobs.transaction() as connection:
        assert jobs.current_license(connection)["status"] == "DENIED"
    with engine.begin() as connection:
        connection.execute(text("UPDATE research.source_status SET license_status='CONFIRMED'"))
    with jobs.transaction() as connection:
        assert jobs.current_license(connection)["status"] == "DECLARED_CONFIRMED"
    jobs.publish_run(
        run_lease, {"synthetic": "result"}, report(run_lease), manifest={"synthetic": "manifest"}
    )
    denied(
        lambda: jobs.publish_run(
            run_lease, {}, report(run_lease), manifest={"synthetic": "manifest"}
        ),
        "LEASE_LOST",
    )
    blocked_run = reads.enqueue_run(
        run_request, "local-operator", "retrospective-run-blocked-0001", "a" * 32, "b" * 40
    )
    with engine.begin() as connection:
        connection.execute(text("UPDATE research.source_status SET license_status='BLOCKED'"))
    blocked_lease = jobs.claim(uuid4(), run=True)
    assert blocked_lease.run_id == blocked_run["run_id"]
    denied(
        lambda: jobs.publish_run(blocked_lease, {}, report(blocked_lease), manifest={}),
        "SNAPSHOT_CONFLICT",
    )
    denied(lambda: raw_positive_publication(blocked_lease), "UNAVAILABLE")
    assert jobs.block_current_gate(blocked_lease, "LICENSE_DENIED")["status"] == "BLOCKED"
    with engine.connect() as connection:
        denied_report = connection.execute(
            text("SELECT body FROM backtest.retrospective_reports WHERE run_id=:id"),
            {"id": blocked_lease.run_id},
        ).scalar_one()
        assert denied_report["licenseStatus"] == "UNKNOWN"
        assert denied_report["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": blocked_lease.run_id},
            ).scalar_one()
            == 0
        )
    # Even unavailable current rights permits bookkeeping to terminalize the
    # oldest job honestly. It never permits positive publication or simulation.
    unavailable_run = reads.enqueue_run(
        run_request, "local-operator", "retrospective-run-unavailable-0001", "a" * 32, "b" * 40
    )
    with engine.begin() as connection:
        source_row = connection.execute(
            text("SELECT row_to_json(s)::text FROM research.source_status s")
        ).scalar_one()
        connection.execute(text("DELETE FROM research.source_status"))
    try:
        unavailable_lease = jobs.claim(uuid4(), run=True)
        assert unavailable_lease.run_id == unavailable_run["run_id"]
        denied(
            lambda: jobs.publish_run(unavailable_lease, {}, report(unavailable_lease), manifest={}),
            "UNAVAILABLE",
        )
        denied(lambda: raw_positive_publication(unavailable_lease), "UNAVAILABLE")
        assert (
            jobs.block_current_gate(unavailable_lease, "TRUST_UNAVAILABLE")["status"] == "BLOCKED"
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                    {"id": unavailable_lease.run_id},
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO research.source_status SELECT * FROM
                    jsonb_populate_record(NULL::research.source_status,CAST(:body AS jsonb))"""),
                {"body": source_row},
            )
            connection.execute(text("UPDATE research.source_status SET license_status='CONFIRMED'"))
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT body FROM backtest.retrospective_reports WHERE run_id=:id"),
                {"id": blocked_run["run_id"]},
            ).scalar_one()
            == denied_report
        )

    for table, assignment in (
        ("retrospective_datasets", "fingerprint=repeat('0',64)"),
        ("retrospective_revision_bindings", "expected_fingerprint=repeat('0',64)"),
        ("retrospective_reports", "fingerprint=repeat('0',64)"),
        ("retrospective_reports", "findings='[]'::jsonb"),
        ("retrospective_reports", "revision_references='[]'::jsonb"),
        ("license_receipts", "status='DENIED'"),
        ("retrospective_audit", "event='FAILED'"),
    ):
        for sql in (f"DELETE FROM backtest.{table}", f"UPDATE backtest.{table} SET {assignment}"):

            def mutate(sql=sql):
                with engine.begin() as connection:
                    connection.execute(text(sql))

            denied(mutate)

    # FK is a real retention dependency independently of source's append-only trigger.
    def remove_bound_bar():
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE research.bar_revisions DISABLE TRIGGER immutable"))
            connection.execute(
                text("DELETE FROM research.bar_revisions WHERE revision_id=:id"),
                {"id": original.revision_id},
            )

    denied(remove_bound_bar)

    def remove_bound_calendar():
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE research.calendar_versions DISABLE TRIGGER immutable")
            )
            connection.execute(
                text("DELETE FROM research.calendar_versions WHERE session_id=:id"),
                {"id": calendar_id},
            )

    denied(remove_bound_calendar)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM backtest.license_receipts WHERE assessment_id=:id"),
                {"id": first["assessment_id"]},
            ).scalar_one()
            == "UNKNOWN"
        )
        assert (
            connection.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
            == financial_before
        )
    verify_roles(engine)
    # All job kinds share one 100-job queue. Two concurrent same-key Strict
    # requests at 99 must both replay the one accepted row, including at full.
    from packages.backtesting.readiness_jobs import ReadinessJobs

    readiness_reads, readiness_jobs = ReadinessJobs(control), ReadinessJobs(worker)
    readiness = readiness_reads.enqueue(
        strict_request, "local-operator", "shared-readiness-0001", "a" * 32, "b" * 40
    )
    for index in range(98):
        enqueue(f"shared-retrospective-{index:04d}")

    def strict_enqueue():
        return BacktestJobs(control).enqueue(
            strict_request, "local-operator", "shared-strict-samekey-0001", "a" * 32, "b" * 40
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        same = list(pool.map(lambda _: strict_enqueue(), range(2)))
    assert same[0]["run_id"] == same[1]["run_id"]
    denied(lambda: enqueue("shared-overflow-retrospective-0001"), "UNAVAILABLE")
    assert enqueue("shared-retrospective-0000")["status"] == "ACCEPTED"
    assert jobs.claim(uuid4()) is None and strict_jobs.claim(uuid4()) is None
    readiness_lease = readiness_jobs.claim(uuid4())
    assert readiness_lease.assessment_id == readiness["assessment_id"]
    assert jobs.claim(uuid4()) is None and strict_jobs.claim(uuid4()) is None
    readiness_jobs.publish(readiness_lease, error_code="INTERNAL_FAILURE")

    # At 99, independent concurrent request variants can admit exactly one job.
    def contend(index):
        try:
            if index:
                return enqueue("shared-concurrent-retrospective-0001")["assessment_id"]
            return BacktestJobs(control).enqueue(
                strict_request,
                "local-operator",
                "shared-concurrent-strict-0001",
                "a" * 32,
                "b" * 40,
            )["run_id"]
        except BacktestError as error:
            assert error.code == "UNAVAILABLE"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted = list(pool.map(contend, range(2)))
    assert sum(identifier is not None for identifier in admitted) == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("""SELECT (SELECT count(*) FROM backtest.runs
                  WHERE status IN ('ACCEPTED','RUNNING'))+
            (SELECT count(*) FROM backtest.readiness_jobs WHERE status IN ('ACCEPTED','RUNNING'))+
            (SELECT count(*) FROM backtest.retrospective_assessments WHERE status IN
              ('ACCEPTED','RUNNING'))""")
            ).scalar_one()
            == 100
        )
    oldest = jobs.claim(uuid4())
    assert oldest.row["idempotency_key"] == "shared-retrospective-0000"
    assert readiness_jobs.claim(uuid4()) is None and strict_jobs.claim(uuid4()) is None
    worker.dispose()
    control.dispose()
    engine.dispose()
    print(
        "PASS retrospective PG migration/legacy/snapshot/late-commit/revision/"
        "retention/fence/atomic/license/roles"
    )


def test_retrospective_storage_actual_postgresql():
    assert host().endswith("/roles/restore")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own = [f for f in frames if f.filename.startswith("/opt/lab/")]
        where = ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in own[-4:])
        primary = (
            getattr(getattr(getattr(error, "orig", None), "diag", None), "message_primary", "")
            or ""
        )
        detail = str(error)[:200] if isinstance(error, AssertionError) else primary[:180]
        print("FAIL " + type(error).__name__ + " " + where + " " + detail)
        raise SystemExit(1) from None
