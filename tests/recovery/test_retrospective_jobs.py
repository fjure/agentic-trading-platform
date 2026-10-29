"""Real PostgreSQL crash/restart with immutable references and separate current gates."""

from pathlib import Path

from tests.integration.test_backtest_dataset import guest_engine, host


def guest():
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from unittest.mock import patch
    from uuid import uuid4

    from sqlalchemy import text

    from packages.backtesting.jobs import BacktestError
    from packages.backtesting.retrospective_storage import MODE, POLICY, RetrospectiveStorage
    from packages.domain.models import utc_string
    from packages.research.evidence_storage import EvidenceInput, EvidenceStore, unknown_trust
    from packages.research.models import BarIdentity, BarInterval, BarRevision
    from packages.research.registry import definitions
    from packages.research.storage import ResearchStore
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_jobs import role_engine, valid_request

    engine = guest_engine()
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    reads, jobs = RetrospectiveStorage(control), RetrospectiveStorage(worker)
    instrument, mapping = definitions()[0]
    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    store, bars = ResearchStore(), []
    with engine.begin() as connection:
        store.add_instrument(connection, instrument)
        store.add_mapping(connection, mapping)
        for index in range(4):
            at = start + timedelta(minutes=15 * index)
            bar = BarRevision(
                uuid4(),
                BarIdentity(
                    instrument.instrument_id,
                    "alpaca",
                    "iex",
                    BarInterval.FIFTEEN_MINUTES,
                    at,
                    at + timedelta(minutes=15),
                ),
                *(Decimal("100"),) * 4,
                Decimal("10"),
                at + timedelta(minutes=15),
                at + timedelta(minutes=15),
            )
            bars.append(store.append_bar(connection, bar, symbol=mapping.symbol))
        connection.execute(
            text("""INSERT INTO research.calendar_versions
            (session_id,session_date,opens_at,closes_at,version,source,commit_version,ingested_at)
            VALUES(:id,:day,:start,:end,repeat('d',64),'alpaca-v2-calendar',0,clock_timestamp())"""),
            dict(id=uuid4(), day=start.date(), start=start, end=start + timedelta(hours=1)),
        )
        job = uuid4()
        connection.execute(
            text("""INSERT INTO research.ingestion_jobs
            (job_id,actor,idempotency_key,request_hash,instrument_id,start_at,end_at,interval,
             mode,status,trace_id) VALUES(:job,'local-operator','recovery-metadata-0001',
             repeat('e',64),:instrument,:start,:end,'15Min','BACKFILL','SUCCEEDED',repeat('a',32))"""),
            dict(
                job=job,
                instrument=instrument.instrument_id,
                start=start,
                end=start + timedelta(hours=1),
            ),
        )
        connection.execute(
            text("""INSERT INTO research.quality_observations
            (observation_id,job_id,instrument_id,start_at,end_at,interval,quality,rule_version,
             code,commit_version,ingested_at) VALUES(:id,:job,:instrument,:start,:end,'15Min',
             'VALID','research-quality-v1','NORMALIZED_JOB_COMPLETED',0,clock_timestamp())"""),
            dict(
                id=uuid4(),
                job=job,
                instrument=instrument.instrument_id,
                start=start,
                end=start + timedelta(hours=1),
            ),
        )
        observed = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        unknown = EvidenceInput(
            uuid4(),
            "synthetic-prior-period",
            "unknown-training-only",
            "CORPORATE_ACTION",
            instrument.instrument_id,
            start - timedelta(minutes=30),
            start,
            observed,
            None,
            1,
            None,
            "6" * 64,
            dict(type="CORPORATE_ACTION_COVERAGE", conclusion="UNKNOWN", actions=[]),
            unknown_trust(),
            {},
        )
        EvidenceStore().append_bundle(
            connection, "recovery-training-evidence-0001", "6" * 64, (unknown,), 0
        )
    request = {
        **valid_request(),
        "researchMode": MODE,
        "selectionPolicyVersion": POLICY,
        "strategyId": "buy-hold-v1",
        "parameters": {},
        "feeUsd": "0",
        "slippageBps": "0",
        "folds": [
            dict(
                trainingStart=utc_string(start - timedelta(minutes=30)),
                trainingEnd=utc_string(start),
                testStart=utc_string(start),
                testEnd=utc_string(start + timedelta(hours=1)),
            )
        ],
    }
    row = reads.enqueue_assessment(
        request, "local-operator", "retrospective-recovery-0001", "a" * 32, "b" * 40
    )
    dispatcher = BacktestWorker(worker, enabled=True, code_version="b" * 40)
    captured = {}

    class ProcessCrash(BaseException):
        pass

    def crash(lease, report, **entries):
        captured.update(lease=lease, report=report, entries=entries)
        raise ProcessCrash()

    dispatcher.retrospective_worker.storage.publish_assessment = crash
    with patch(
        "services.worker.retrospective_backtesting.evaluate",
        side_effect=AssertionError("preview CPU"),
    ):
        try:
            dispatcher.tick()
        except ProcessCrash:
            pass
        else:
            raise AssertionError("crash boundary not reached")
    stale = captured["lease"]
    before, bound = jobs.load_dataset(row["assessment_id"])
    assert len(bound) == 7 and {kind for kind, _ in bound} == {
        "BAR",
        "CALENDAR",
        "QUALITY",
        "EVIDENCE",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE backtest.retrospective_assessments "
                "DISABLE TRIGGER a_retrospective_policy"
            )
        )
        connection.execute(
            text(
                "UPDATE backtest.retrospective_assessments "
                "SET lease_until=clock_timestamp()-interval '1s' "
                "WHERE assessment_id=:id"
            ),
            {"id": row["assessment_id"]},
        )
        connection.execute(
            text(
                "ALTER TABLE backtest.retrospective_assessments "
                "ENABLE TRIGGER a_retrospective_policy"
            )
        )
        store.append_bar(
            connection,
            replace(bars[0], revision_id=uuid4(), close=Decimal("101"), high=Decimal("101")),
            symbol=mapping.symbol,
        )
    recovered = BacktestWorker(worker, enabled=True, code_version="b" * 40)
    assert recovered.tick()
    after, rebound = jobs.load_dataset(row["assessment_id"])
    assert after == before and rebound == bound
    preview = jobs.preview_report(row["assessment_id"])
    assert preview["body"]["reportHash"] == captured["report"]["reportHash"]
    assert preview["body"]["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
    assert preview["body"]["licenseStatus"] == "UNKNOWN"
    with jobs.transaction() as connection:
        assert jobs.current_assessment_gate(connection, row["assessment_id"]) is None
        assert jobs.has_retrospective_correction(connection, row["assessment_id"]) is True
    try:
        jobs.checkpoint(stale)
    except BacktestError as error:
        assert error.code == "LEASE_LOST"
    else:
        raise AssertionError("stale lease accepted")
    with engine.connect() as connection:
        assessment_hash = connection.execute(
            text(
                "SELECT assessment_hash FROM backtest.retrospective_assessments "
                "WHERE assessment_id=:id"
            ),
            {"id": row["assessment_id"]},
        ).scalar_one()
    run = reads.enqueue_run(
        {**request, "assessmentId": str(row["assessment_id"]), "assessmentHash": assessment_hash},
        "local-operator",
        "retrospective-recovery-run-0001",
        "a" * 32,
        "b" * 40,
    )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton")
        )
    with patch(
        "services.worker.retrospective_backtesting.evaluate",
        side_effect=AssertionError("denied CPU"),
    ):
        assert recovered.tick()
    with engine.connect() as connection:
        actual = (
            connection.execute(
                text("SELECT status,error_code FROM backtest.runs WHERE run_id=:id"),
                {"id": run["run_id"]},
            )
            .mappings()
            .one()
        )
        assert dict(actual) == dict(status="BLOCKED", error_code="LICENSE_DENIED")
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                {"id": run["run_id"]},
            ).scalar_one()
            == 0
        )
        final = connection.execute(
            text("SELECT body FROM backtest.retrospective_reports WHERE run_id=:id"),
            {"id": run["run_id"]},
        ).scalar_one()
        assert final["licenseStatus"] == "UNKNOWN"
        assert final["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
        assert final["reportHash"] == preview["body"]["reportHash"]
        assert (
            connection.execute(
                text("SELECT code FROM backtest.retrospective_execution_gates WHERE run_id=:id"),
                {"id": run["run_id"]},
            ).scalar_one()
            == "LICENSE_DENIED"
        )
        assert jobs.license_receipt(row["assessment_id"])["status"] == "UNKNOWN"
    with engine.begin() as connection:
        connection.execute(text("UPDATE research.source_status SET license_status='UNKNOWN'"))
    verify_current_sql_guards(engine, control, worker, request, start, instrument.instrument_id)
    verify_correction_families(engine, control, worker, request)
    control.dispose()
    worker.dispose()
    engine.dispose()
    print(
        "PASS retrospective crash/restart/exact-references/stale-fence/"
        "current-gate/no-CPU/no-result"
    )


def verify_current_sql_guards(engine, control, worker, request, start, instrument_id):
    """Synthetic local authorities exercise the real raw-role publication barriers."""
    import hashlib
    from datetime import timedelta
    from unittest.mock import patch
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.jobs import encoded
    from packages.backtesting.retrospective_storage import RetrospectiveStorage
    from packages.domain.models import utc_string
    from packages.research.evidence_storage import EvidenceInput, EvidenceStore, unknown_trust
    from services.control_api.backtest_service import BacktestService
    from services.worker.backtesting import BacktestWorker

    reads, jobs, evidence_store = (
        RetrospectiveStorage(control),
        RetrospectiveStorage(worker),
        EvidenceStore(),
    )
    with engine.connect() as connection:
        now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    admission = dict(
        decisionId=str(uuid4()),
        sourceId="synthetic-current-source",
        keyId="synthetic-current-key",
        witnessId=None,
        authorityType="SOURCE",
        effectiveAt=now - timedelta(days=1),
        validFrom=now - timedelta(days=2),
        validTo=now + timedelta(days=2),
        revoked=False,
        compromisedAt=None,
        compromiseTimeKnown=True,
        policyVersion="synthetic-v1",
        reason="SYNTHETIC_CURRENT_TEST",
        scope=dict(instrumentIds=[str(instrument_id)], kinds=["CORPORATE_ACTION"]),
        publicKey="A" * 43 + "=",
        algorithm="Ed25519",
        methods=["RFC8785-UTF8-v1"],
        actorRef="synthetic-operator",
        documentFingerprint="d" * 64,
        testOnly=True,
        predecessorDecisionId=None,
    )
    trust = {
        name: {**dict(value), "status": "UNKNOWN" if name == "temporalAuthenticity" else "VERIFIED"}
        for name, value in unknown_trust().items()
    }
    trust["sourceAuthenticity"]["evidenceRefs"] = [
        dict(
            kind="KEY",
            id=admission["keyId"],
            revision="1",
            fingerprint=hashlib.sha256(bytes(32)).hexdigest(),
        )
    ]
    entry = EvidenceInput(
        uuid4(),
        admission["sourceId"],
        "known-dividend",
        "CORPORATE_ACTION",
        instrument_id,
        start - timedelta(minutes=30),
        start + timedelta(hours=1),
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
                    type="CASH_DIVIDEND",
                    amount="0.5",
                    currency="USD",
                    effectiveAt=utc_string(start + timedelta(minutes=15)),
                )
            ],
        ),
        trust,
        dict(
            content=dict(keyId=admission["keyId"], signedAt=utc_string(now - timedelta(seconds=1)))
        ),
    )
    with engine.begin() as connection:
        evidence_store.append_trust_decision(connection, admission, 0)
        evidence_store.append_bundle(
            connection, "current-gate-evidence-0001", "e" * 64, (entry,), 1
        )
    assessed = reads.enqueue_assessment(
        request, "local-operator", "current-gate-assessment-0001", "a" * 32, "b" * 40
    )
    dispatcher = BacktestWorker(worker, enabled=True, code_version="b" * 40)
    assert dispatcher.tick()
    preview = jobs.preview_report(assessed["assessment_id"])
    assert preview["body"]["overallStatus"] == "EXPLORATORY_WITH_LIMITATIONS"
    assert preview["body"]["corporateActionStatus"] == "DIVIDENDS_EXCLUDED"
    with jobs.transaction() as connection:
        assert jobs.current_assessment_gate(connection, assessed["assessment_id"]) is None
    with engine.connect() as connection:
        assessment_hash = connection.execute(
            text(
                "SELECT assessment_hash FROM backtest.retrospective_assessments "
                "WHERE assessment_id=:id"
            ),
            {"id": assessed["assessment_id"]},
        ).scalar_one()
    run_request = {
        **request,
        "assessmentId": str(assessed["assessment_id"]),
        "assessmentHash": assessment_hash,
    }
    run = reads.enqueue_run(
        run_request, "local-operator", "current-gate-run-0001", "a" * 32, "b" * 40
    )
    lease = jobs.claim(uuid4(), run=True)
    assert lease.run_id == run["run_id"]
    with engine.begin() as connection:
        evidence_store.append_trust_decision(
            connection,
            {
                **admission,
                "decisionId": str(uuid4()),
                "sourceId": "synthetic-unrelated-source",
                "keyId": "synthetic-unrelated-key",
            },
            1,
        )
    assert jobs.current_gate(lease) is None  # Unrelated positive trust changes are harmless.
    revoked = {
        **admission,
        "decisionId": str(uuid4()),
        "revoked": True,
        "predecessorDecisionId": admission["decisionId"],
    }
    with engine.begin() as connection:
        evidence_store.append_trust_decision(connection, revoked, 2)
    assert jobs.current_gate(lease) == "TRUST_REVOKED"
    final = {
        **preview["body"],
        "reportId": str(uuid4()),
        "phase": "FINAL",
        "runId": str(lease.run_id),
    }

    def raw_denied(query, params, *, with_report=False):
        try:
            with worker.begin() as connection:
                connection.execute(
                    text(
                        "SELECT set_config('backtest.policy_version','retrospective-v1',true), "
                        "set_config('backtest.owner',:owner,true),set_config('backtest.fence',:fence,true)"
                    ),
                    dict(owner=str(lease.owner), fence=str(lease.fence)),
                )
                if with_report:
                    jobs._insert_report(
                        connection,
                        lease,
                        final,
                        preview["findings"],
                        preview["revision_references"],
                    )
                connection.execute(text(query), params)
        except DBAPIError as error:
            assert error.orig.diag.message_primary == "retrospective current gate blocked"
        else:
            raise AssertionError("raw role bypassed current execution gate")

    raw_writes = (
        (
            "INSERT INTO backtest.results VALUES(:id,repeat('f',64),'{}')",
            dict(id=lease.run_id),
            True,
        ),
        (
            "INSERT INTO backtest.manifests VALUES(:id,repeat('f',64),'{}')",
            dict(id=lease.run_id),
            False,
        ),
        ("INSERT INTO backtest.fold_results VALUES(:id,0,'{}')", dict(id=lease.run_id), False),
        (
            "UPDATE backtest.runs SET status='SUCCEEDED',owner=NULL,lease_until=NULL,"
            "updated_at=clock_timestamp() WHERE run_id=:id",
            dict(id=lease.run_id),
            True,
        ),
    )
    for query, params, with_report in raw_writes:
        raw_denied(query, params, with_report=with_report)
    # Insert the real refusal while its fence is live, then restore permission:
    # the durable refusal must still bar all subsequent writes and success.
    with jobs.transaction(lease=lease) as connection:
        jobs._insert_report(
            connection, lease, final, preview["findings"], preview["revision_references"]
        )
        connection.execute(
            text(
                "INSERT INTO backtest.retrospective_execution_gates(run_id,code,fence) "
                "VALUES(:id,'TRUST_REVOKED',:fence)"
            ),
            dict(id=lease.run_id, fence=lease.fence),
        )
    with engine.begin() as connection:
        evidence_store.append_trust_decision(
            connection,
            {
                **admission,
                "decisionId": str(uuid4()),
                "predecessorDecisionId": revoked["decisionId"],
            },
            3,
        )
    assert jobs.current_gate(lease) is None
    for query, params, _with_report in raw_writes:
        raw_denied(query, params)
    with jobs.transaction(lease=lease) as connection:
        row = (
            connection.execute(
                text(
                    "UPDATE backtest.runs SET status='BLOCKED',error_code='TRUST_REVOKED',"
                    "owner=NULL,lease_until=NULL,updated_at=clock_timestamp() "
                    "WHERE run_id=:id RETURNING *"
                ),
                dict(id=lease.run_id),
            )
            .mappings()
            .one()
        )
        jobs._audit(connection, row, "BLOCKED", True)
    # A newly recorded split is a current warning, never a rewrite of the preview.
    split = EvidenceInput(
        uuid4(),
        admission["sourceId"],
        "later-known-split",
        "CORPORATE_ACTION",
        instrument_id,
        entry.start_at,
        entry.end_at,
        entry.source_observed_at,
        None,
        1,
        None,
        "a" * 64,
        dict(
            type="CORPORATE_ACTION_COVERAGE",
            conclusion="ACTIONS_PRESENT",
            actions=[
                dict(type="SPLIT", ratio="2", effectiveAt=utc_string(start + timedelta(minutes=15)))
            ],
        ),
        trust,
        entry.provenance,
    )
    later = reads.enqueue_run(
        run_request, "local-operator", "current-gate-later-split-0001", "a" * 32, "b" * 40
    )
    with engine.begin() as connection:
        evidence_store.append_bundle(
            connection, "current-gate-late-split-0001", "a" * 64, (split,), 4
        )
    with patch(
        "services.worker.retrospective_backtesting.evaluate",
        side_effect=AssertionError("split CPU"),
    ):
        assert dispatcher.tick()
    service = BacktestService(control, code_version="b" * 40)
    page = service.read_reliability_report(
        str(preview["report_id"]), {"limit": 200, "section": "findings"}
    )
    assert page["report"] == preview["body"]
    assert any(
        w["category"] == "CORPORATE_ACTIONS"
        and w["blocksNewExecution"]
        and w["code"] in ("STRUCTURAL_CORPORATE_ACTION", "CORPORATE_ACTION_CONFLICT")
        for w in page["currentWarnings"]
    )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT status FROM backtest.runs WHERE run_id=:id"), dict(id=later["run_id"])
            ).scalar_one()
            == "BLOCKED"
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM backtest.results WHERE run_id=:id"),
                dict(id=later["run_id"]),
            ).scalar_one()
            == 0
        )
    assert encoded(page["report"]) == encoded(preview["body"])
    from dataclasses import replace

    conflicting = replace(
        entry,
        evidence_id=uuid4(),
        evidence_key="conflicting-no-actions",
        fingerprint="f" * 64,
        statement=dict(type="CORPORATE_ACTION_COVERAGE", conclusion="NO_ACTIONS", actions=[]),
    )
    with engine.begin() as connection:
        evidence_store.append_bundle(
            connection, "current-gate-conflict-0001", "f" * 64, (conflicting,), 4
        )
    with jobs.transaction() as connection:
        assert (
            jobs.current_assessment_gate(connection, assessed["assessment_id"])
            == "CORPORATE_ACTION_CONFLICT"
        )


def verify_correction_families(engine, control, worker, request):
    """Each boolean family lookup proves false before, true after a real append."""
    from dataclasses import replace
    from datetime import timedelta
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import text

    from packages.backtesting.retrospective_storage import RetrospectiveStorage
    from packages.research.evidence_storage import EvidenceInput, EvidenceStore
    from packages.research.models import content_fingerprint
    from packages.research.storage import ResearchStore, bar_from_row

    reads, jobs = RetrospectiveStorage(control), RetrospectiveStorage(worker)
    for kind, table, key, hash_column in (
        ("BAR", "bar_revisions", "revision_id", "fingerprint"),
        ("CALENDAR", "calendar_versions", "session_id", "version"),
        ("QUALITY", "quality_observations", "observation_id", None),
        ("EVIDENCE", "evidence_revisions", "evidence_id", "fingerprint"),
    ):
        with engine.connect() as connection:
            predicate = " WHERE source_id='synthetic-prior-period'" if kind == "EVIDENCE" else ""
            source = dict(
                connection.execute(
                    text(
                        "SELECT * FROM research."
                        + table
                        + predicate
                        + " ORDER BY commit_version DESC LIMIT 1"
                    )
                )
                .mappings()
                .one()
            )
            digest = (
                source[hash_column]
                if hash_column
                else connection.execute(
                    text(
                        "SELECT encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') "
                        "FROM research.quality_observations r WHERE observation_id=:id"
                    ),
                    {"id": source[key]},
                ).scalar_one()
            )
        assessed = reads.enqueue_assessment(
            request, "local-operator", "correction-family-" + kind, "a" * 32, "b" * 40
        )
        lease = jobs.claim(uuid4())
        assert lease.assessment_id == assessed["assessment_id"]
        jobs.bind_dataset(
            lease, {"synthetic": "one-correction-family"}, ((kind, source[key], digest),)
        )
        with jobs.transaction() as connection:
            assert jobs.has_retrospective_correction(connection, lease.assessment_id) is False
        with engine.begin() as connection:
            if kind == "BAR":
                bar = bar_from_row(source)
                corrected = replace(
                    bar,
                    revision_id=uuid4(),
                    close=bar.close + Decimal("1"),
                    high=max(bar.high, bar.close + Decimal("1")),
                )
                ResearchStore().append_bar(connection, corrected, symbol="SPY")
            elif kind == "CALENDAR":
                connection.execute(
                    text("""INSERT INTO research.calendar_versions
                    (session_id,session_date,opens_at,closes_at,version,source,commit_version,ingested_at)
                    VALUES(:id,:day,:opens,:closes,:hash,:source,0,clock_timestamp())"""),
                    dict(
                        id=uuid4(),
                        day=source["session_date"],
                        opens=source["opens_at"],
                        closes=source["closes_at"] + timedelta(minutes=15),
                        source=source["source"],
                        hash=content_fingerprint((source["version"], "corrected-calendar")),
                    ),
                )
            elif kind == "QUALITY":
                connection.execute(
                    text("""INSERT INTO research.quality_observations
                    (observation_id,job_id,instrument_id,start_at,end_at,interval,quality,rule_version,
                     code,commit_version,ingested_at)
                    VALUES(:id,:job,:instrument,:start,:end,:interval,
                    'INVALID',:rule,'REJECTED_PROVIDER_RESPONSE',0,clock_timestamp())"""),
                    dict(
                        id=uuid4(),
                        job=source["job_id"],
                        instrument=source["instrument_id"],
                        start=source["start_at"],
                        end=source["end_at"],
                        interval=source["interval"],
                        rule=source["rule_version"],
                    ),
                )
            else:
                entry = EvidenceInput(
                    uuid4(),
                    source["source_id"],
                    source["evidence_key"],
                    source["kind"],
                    source["instrument_id"],
                    source["start_at"],
                    source["end_at"],
                    source["source_observed_at"],
                    source["published_at"],
                    source["revision"] + 1,
                    source["evidence_id"],
                    content_fingerprint((digest, "correction")),
                    source["statement"],
                    source["trust"],
                    source["provenance"],
                )
                cap = connection.execute(
                    text("SELECT version FROM research.trust_version_state WHERE singleton")
                ).scalar_one()
                EvidenceStore().append_bundle(
                    connection, "correction-family-evidence-0001", "8" * 64, (entry,), cap
                )
        with jobs.transaction() as connection:
            assert jobs.has_retrospective_correction(connection, lease.assessment_id) is True
        jobs.publish_assessment(lease, error_code="INTERNAL_FAILURE")


def test_retrospective_jobs_actual_postgresql():
    assert host("tests.recovery.test_retrospective_jobs").endswith("/no-result")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        own = [
            f
            for f in traceback.extract_tb(error.__traceback__)
            if f.filename.startswith("/opt/lab/")
        ]
        where = ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in own[-4:])
        primary = (
            getattr(getattr(getattr(error, "orig", None), "diag", None), "message_primary", "")
            or ""
        )
        detail = str(error)[:150] if isinstance(error, AssertionError) else primary[:150]
        print("FAIL " + type(error).__name__ + " " + where + " " + detail)
        raise SystemExit(1) from None
