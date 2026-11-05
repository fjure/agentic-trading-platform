"""Disposable real PostgreSQL QSL migration/admission/immutability/restore."""

import json
import sys
from pathlib import Path
from uuid import uuid4

SQL_DIAGNOSTICS = []

TABLES = tuple(
    "backtest." + name
    for name in (
        "experiment_definitions",
        "experiment_candidates",
        "experiment_validations",
        "experiment_batches",
        "experiment_snapshots",
        "experiment_revision_bindings",
        "experiment_phase_bindings",
        "experiment_selections",
        "experiment_result_links",
        "experiment_reports",
        "experiment_events",
        "test_exposures",
        "experiment_source_receipts",
        "experiment_trust_bindings",
        "quant_strategy_revisions",
        "retrospective_reports",
        "runs",
        "manifests",
        "fold_results",
        "results",
    )
)


def host():
    from tests.integration import test_readiness_storage as harness

    previous = harness.TABLES
    try:
        harness.TABLES = (*previous, *TABLES)
        return harness.host("tests.integration.test_quant_lab_storage")
    finally:
        harness.TABLES = previous


def fixture():
    from packages.research.registry import definitions

    definition = json.loads(
        Path("contracts/examples/v1/quant-lab-experiment-request-strict.valid.json").read_text()
    )
    definition["instrumentId"] = str(definitions()[0][0].instrument_id)
    candidates = [
        dict(
            role="SEARCH",
            configuration=dict(
                strategyId="ema-cross-v1", version="1", parameters=dict(fast=fast, slow=slow)
            ),
        )
        for fast in (5, 10, 15)
        for slow in (30, 40, 50)
    ]
    candidates += [
        dict(role=role, configuration=dict(strategyId=strategy, version="1", parameters={}))
        for role, strategy in (("FLAT_REFERENCE", "flat-v1"), ("BUY_HOLD_REFERENCE", "buy-hold-v1"))
    ]
    return definition, candidates


def start_binding(definition, validation):
    return dict(
        definition_version=definition["definition_version"],
        validation_id=validation["validation_id"],
        validation_hash=validation["fingerprint"],
    )


def finish_legacy_assessment(storage, lease, request):
    from packages.domain.models import utc_string

    prior = lease.row
    storage.bind_dataset(lease, {"synthetic_storage_fixture": True})
    report = json.loads(Path("contracts/examples/v1/retrospective-report.valid.json").read_text())
    report.update(
        reportId=str(uuid4()),
        assessmentId=str(lease.assessment_id),
        runId=None,
        instrumentId=request["instrumentId"],
        protocolHash=prior["request_hash"],
        findingsCount=0,
        revisionReferenceCount=0,
        snapshotReference=dict(
            snapshotVersion=prior["snapshot_version"], snapshotAt=utc_string(prior["snapshot_at"])
        ),
    )
    bound = storage.publish_assessment(lease, report)
    return bound, report["reportId"]


def historical_retrospective(engine, request):
    """Exercise the published 0034 admission and fence before the additive upgrade."""
    from sqlalchemy import text

    from packages.backtesting.retrospective_storage import RetrospectiveLease, RetrospectiveStorage

    storage = RetrospectiveStorage(engine)
    request = dict(
        request, researchMode="RETROSPECTIVE_EXPLORATORY", selectionPolicyVersion="retrospective-v1"
    )
    assessment = storage.enqueue_assessment(
        request, "local-operator", "qsl-before-0035-retro-assess", "a" * 32, "b" * 40
    )
    owner = uuid4()
    with storage.transaction() as c:
        assessment = (
            c.execute(
                text(
                    "UPDATE backtest.retrospective_assessments SET status='RUNNING',"
                    "owner=:owner,fence=fence+1,attempts=attempts+1,started_at=clock_timestamp(),"
                    "lease_until=clock_timestamp()+interval '300 seconds' "
                    "WHERE assessment_id=:id RETURNING *"
                ),
                {"id": assessment["assessment_id"], "owner": owner},
            )
            .mappings()
            .one()
        )
    assessment_id = assessment["assessment_id"]
    lease = RetrospectiveLease(assessment_id, owner, assessment["fence"], dict(assessment))
    bound, _ = finish_legacy_assessment(storage, lease, request)
    run = storage.enqueue_run(
        dict(request, assessmentId=str(assessment_id), assessmentHash=bound["assessment_hash"]),
        "local-operator",
        "qsl-before-0035-retro-run",
        "a" * 32,
        "b" * 40,
    )
    owner = uuid4()
    with storage.transaction() as c:
        run = (
            c.execute(
                text(
                    "UPDATE backtest.runs SET status='RUNNING',owner=:owner,fence=fence+1,"
                    "attempts=attempts+1,started_at=clock_timestamp(),"
                    "lease_until=clock_timestamp()+interval '300 seconds' "
                    "WHERE run_id=:id RETURNING *"
                ),
                {"id": run["run_id"], "owner": owner},
            )
            .mappings()
            .one()
        )
    lease = RetrospectiveLease(assessment_id, owner, run["fence"], dict(run), run["run_id"])
    with storage.transaction(lease=lease) as c:
        terminal = (
            c.execute(
                text(
                    "UPDATE backtest.runs SET status='FAILED',error_code='INTERNAL_FAILURE',"
                    "owner=NULL,lease_until=NULL WHERE run_id=:id RETURNING *"
                ),
                {"id": run["run_id"]},
            )
            .mappings()
            .one()
        )
        storage._audit(c, terminal, "FAILED", run=True)
    return run["run_id"]


def mixed_admission(engine, control, worker, store, definition, request, submit):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import text

    from packages.backtesting.jobs import BacktestJobs
    from packages.backtesting.readiness_jobs import ReadinessJobs
    from packages.backtesting.retrospective_storage import RetrospectiveStorage
    from services.control_api.backtest_service import BacktestService

    fold = definition["body"]["protocol"]["folds"][0]
    mixed = dict(
        request,
        folds=[
            dict(
                trainingStart=fold["training"]["start"],
                trainingEnd=fold["training"]["end"],
                testStart=fold["validation"]["start"],
                testEnd=fold["validation"]["end"],
            )
        ],
    )
    retro = dict(
        mixed, researchMode="RETROSPECTIVE_EXPLORATORY", selectionPolicyVersion="retrospective-v1"
    )
    reads = RetrospectiveStorage(control)
    legacy_worker = RetrospectiveStorage(worker)
    prior = reads.enqueue_assessment(
        retro, "local-operator", "qsl-legacy-report-prep01", "a" * 32, "b" * 40
    )
    lease = legacy_worker.claim(uuid4())
    assert lease.assessment_id == prior["assessment_id"]
    bound, report_id = finish_legacy_assessment(legacy_worker, lease, retro)
    legacy_api = BacktestService(control, code_version="b" * 40, cursor_key=b"q" * 32)
    assert legacy_api.read_reliability_report(report_id, {})["report"]["reportId"] == report_id
    run_request = dict(
        retro, assessmentId=str(lease.assessment_id), assessmentHash=bound["assessment_hash"]
    )

    def admission(index):
        args = ("local-operator", f"qsl-mixed-admission-{index:03}", "a" * 32, "b" * 40)
        if index == 0:
            return submit(0)
        if index == 1:
            return BacktestJobs(control).enqueue(mixed, *args)
        if index == 2:
            return reads.enqueue_assessment(retro, *args)
        if index == 3:
            return reads.enqueue_run(run_request, *args)
        return ReadinessJobs(control).enqueue(mixed, *args)

    with ThreadPoolExecutor(max_workers=5) as pool:
        quant, strict, assessment, retro_run, readiness = pool.map(admission, range(5))
    ids = (strict["run_id"], assessment["assessment_id"], retro_run["run_id"])
    previous = store.exposure_history(quant["batch_id"])
    with engine.connect() as c:
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 15
        for identifier in ids:
            first_exposure = c.execute(
                text("SELECT min(exposure_id) FROM backtest.test_exposures WHERE source_id=:id"),
                {"id": identifier},
            ).scalar_one()
            assert first_exposure is not None
            assert any(row["source_id"] == identifier for row in previous) == (
                first_exposure <= quant["registry_watermark"]
            )
        assert (
            c.execute(
                text("SELECT count(*) FROM backtest.test_exposures WHERE source_id=:id"),
                {"id": readiness["assessment_id"]},
            ).scalar_one()
            == 0
        )
    return ids


def verify_definition_guards(engine, store, d, denied):
    from copy import deepcopy

    from sqlalchemy import text

    from packages.backtesting.jobs import encoded
    from packages.research.models import content_fingerprint

    with engine.connect() as c:
        candidates = [
            dict(
                role=r["role"],
                candidateId=r["candidate_id"],
                configuration=r["body"],
                canonicalText=r["canonical_text"],
            )
            for r in c.execute(
                text("SELECT * FROM backtest.experiment_candidates WHERE definition_id=:id"),
                {"id": d["definition_id"]},
            ).mappings()
        ]
    original = json.loads(d["canonical_text"])

    def raw(body=None, preimage=None, items=None, canonical_text=None):
        preimage = original if preimage is None else preimage
        with store.transaction() as c:
            c.execute(
                text(
                    "SELECT backtest.request_experiment_definition(:id,'local-operator',"
                    "CAST(:body AS "
                    "jsonb),:canonical,:hash,:policy,:policy_canonical,CAST(:candidates AS "
                    "jsonb))"
                ),
                dict(
                    id=uuid4(),
                    body=encoded(d["body"] if body is None else body),
                    canonical=encoded(preimage) if canonical_text is None else canonical_text,
                    hash=content_fingerprint(preimage)
                    if canonical_text is None
                    else __import__("hashlib").sha256(canonical_text.encode()).hexdigest(),
                    policy=d["policy_hash"],
                    policy_canonical=d["policy_canonical"],
                    candidates=encoded(candidates if items is None else items),
                ),
            )

    body = deepcopy(d["body"])
    body["extra"] = True
    denied(lambda: raw(body=body))
    body = deepcopy(d["body"])
    body["searchSpaces"][0]["parameterValues"]["fast"] = [5]
    denied(lambda: raw(body=body))
    body = deepcopy(d["body"])
    body["feed"] = "SIP"
    denied(lambda: raw(body=body))
    for key, value in (("definition_version", 2), ("instrument_id", str(uuid4()))):
        preimage = deepcopy(original)
        preimage[1][key] = value
        denied(lambda preimage=preimage: raw(preimage=preimage))
    preimage = deepcopy(original)
    preimage[4] = 0
    denied(lambda preimage=preimage: raw(preimage=preimage))
    preimage = deepcopy(original)
    preimage[1]["candidates"][0]["implementation_fingerprint"] = "e" * 64
    preimage[6][0][1] = "e" * 64
    denied(lambda preimage=preimage: raw(preimage=preimage))
    denied(lambda: raw(canonical_text=json.dumps(original, indent=1)))
    # Both caller-provided candidate lists agree, but differ from the Cartesian grid.
    from packages.backtesting.experiment import CandidateConfiguration
    from packages.research.models import _canonical

    flat = CandidateConfiguration("flat-v1", ())
    preimage = deepcopy(original)
    preimage[1]["candidates"] = [_canonical(flat)]
    preimage[6] = [[flat.candidate_id, flat.implementation_fingerprint]]
    preimage[4] = 0
    supplied = [item for item in candidates if item["role"] != "SEARCH"]
    supplied.append(
        dict(
            role="SEARCH",
            configuration=dict(strategyId="flat-v1", version="1", parameters={}),
            candidateId=flat.candidate_id,
            canonicalText=encoded(("flat-v1", "1", ())),
        )
    )
    denied(lambda: raw(preimage=preimage, items=supplied))
    # Invalid nullable budget values must fail before INSERT, not at a duplicate key.
    draft = store.create_definition(
        d["body"],
        [dict(role=item["role"], configuration=item["configuration"]) for item in candidates],
    )
    validation = dict(
        definitionHash=draft["fingerprint"],
        calendarVerified=True,
        simulationBarSteps=9510,
        snapshotRows=1000,
        publicationBytes=100000,
        seedBars=49,
    )
    for field in ("simulationBarSteps", "snapshotRows", "publicationBytes", "seedBars"):
        denied(
            lambda field=field: store.validate(
                draft["definition_id"], dict(validation, **{field: None})
            )
        )
    with engine.connect() as c:
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM backtest.experiment_validations WHERE definition_id=:id"
                ),
                {"id": draft["definition_id"]},
            ).scalar_one()
            == 0
        )


def verify_publication(engine, store, jobs, denied):
    """Synthetic storage inputs exercise DB bindings; no simulator result claimed."""
    from copy import deepcopy
    from dataclasses import replace
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy import text

    from packages.backtesting.experiment import PhaseSeedBinding, QuantExperimentManifest
    from packages.backtesting.experiment_storage import definition_domain
    from packages.backtesting.models import DataReference
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

    body, _ = fixture()
    body["researchMode"] = "RETROSPECTIVE_EXPLORATORY"
    body["dataSelectionPolicyVersion"] = "retrospective-v1"
    body["strategySelectionPolicy"]["minimumEntryCount"] = 1
    body["searchSpaces"] = [dict(strategyId="flat-v1", version="1", parameterValues={})]
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    body["instrumentId"] = str(instrument.instrument_id)
    domain = definition_domain(body)
    source = ResearchStore()
    revisions = []
    calendars = []
    phase_inputs = []
    with engine.begin() as c:
        source.add_instrument(c, instrument)
        source.add_mapping(
            c,
            SymbolMapping(
                instrument.instrument_id,
                "alpaca",
                "SPY",
                domain.protocol.folds[0].training.start - timedelta(days=1),
            ),
        )
    wrong_outcome = None
    for index, phase, _, window in domain.protocol.phases:
        start = window.start
        bar = BarRevision(
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
        calendar = uuid4()
        version = content_fingerprint(("storage-calendar", phase))
        with engine.begin() as c:
            bar = source.append_bar(c, bar, symbol="SPY")
            if phase == "training":
                wrong_outcome = bar.revision_id
                revisions.append(("BAR", wrong_outcome))
                bar = source.append_bar(
                    c,
                    replace(
                        bar,
                        revision_id=uuid4(),
                        open=Decimal("101"),
                        high=Decimal("101"),
                        low=Decimal("101"),
                        close=Decimal("101"),
                    ),
                    symbol="SPY",
                )
            c.execute(
                text(
                    "INSERT INTO research.calendar_versions(session_id,session_date,opens_at,"
                    "closes_at,version,source,commit_version,ingested_at) "
                    "VALUES(:id,:day,:start,:end,"
                    ":version,'alpaca-v2-calendar',research.reserve_version(),clock_timestamp())"
                ),
                dict(
                    id=calendar,
                    day=start.date(),
                    start=start,
                    end=start + timedelta(hours=6),
                    version=version,
                ),
            )
        revisions.append(("BAR", bar.revision_id))
        revisions.append(("CALENDAR", calendar))
        calendars.append(version)
        phase_inputs.append((index, phase, window, bar))
    candidates = [
        dict(role=role, configuration=dict(strategyId=strategy, version="1", parameters={}))
        for role, strategy in (
            ("SEARCH", "flat-v1"),
            ("FLAT_REFERENCE", "flat-v1"),
            ("BUY_HOLD_REFERENCE", "buy-hold-v1"),
        )
    ]
    d = store.create_definition(body, candidates)
    assert d["fingerprint"] == domain.definition_hash
    assert d["policy_hash"] == domain.strategy_selection_policy.policy_hash
    validation = store.validate(
        d["definition_id"],
        dict(
            definitionHash=d["fingerprint"],
            calendarVerified=True,
            simulationBarSteps=100,
            snapshotRows=8,
            publicationBytes=100000,
            seedBars=0,
        ),
    )
    batch = store.enqueue(
        d["definition_id"],
        d["fingerprint"],
        "local-operator",
        "qsl-publication-fixture-0001",
        "a" * 32,
        "b" * 40,
        **start_binding(d, validation),
    )
    lease = jobs.claim(uuid4())
    assert lease.batch_id == batch["batch_id"]
    receipt = store.source_receipt(batch["batch_id"])
    bindings = tuple(
        PhaseSeedBinding(index, phase, window, (), (bar.identity.bar_start,))
        for index, phase, window, bar in phase_inputs
    )
    manifest = QuantExperimentManifest(
        domain,
        batch["snapshot_version"],
        batch["snapshot_at"],
        bindings,
        tuple(
            sorted(
                DataReference(bar.revision_id, bar.fingerprint, batch["snapshot_at"])
                for _, _, _, bar in phase_inputs
            )
        ),
        tuple(
            sorted(
                DataReference(bar.revision_id, bar.fingerprint, batch["snapshot_at"])
                for _, _, _, bar in phase_inputs
            )
        ),
        tuple(sorted(calendars)),
        (),
        (),
        "b" * 40,
        receipt["trust_fingerprint"],
        receipt["license_fingerprint"],
    )
    common = dict(definitionHash=d["fingerprint"], strategySelectionPolicyHash=d["policy_hash"])
    snapshot = dict(
        common,
        manifestVersion="quant-experiment-v1",
        snapshotVersion=batch["snapshot_version"],
        snapshotAt=utc_string(batch["snapshot_at"]),
        strategySelectionPolicy=d["body"]["strategySelectionPolicy"],
        protocol=body["protocol"],
        registryHash=batch["registry_hash"],
    )
    phases = [
        dict(
            phaseKey="holdout" if index is None else f"{index}:{phase}",
            phase=phase,
            fold=index,
            start=window.start,
            end=window.end,
            seedRevisionIds=[],
            body=dict(common, seedRevisionIds=[], outcomeRevisionIds=[str(bar.revision_id)]),
        )
        for index, phase, window, bar in phase_inputs
    ]
    bad = deepcopy(phases)
    bad[0]["body"]["seedRevisionIds"] = [str(uuid4())]
    denied(lambda: jobs.bind_snapshot(lease, snapshot, revisions, bad, manifest=manifest))
    forged = deepcopy(phases)
    forged[0]["body"]["outcomeRevisionIds"] = [str(wrong_outcome)]
    denied(lambda: jobs.bind_snapshot(lease, snapshot, revisions, forged, manifest=manifest))
    assert SQL_DIAGNOSTICS[-1][0] == "quant snapshot conflict"
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM backtest.experiment_snapshots WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).scalar_one()
            == 0
        )
    jobs.bind_snapshot(lease, snapshot, revisions, phases, manifest=manifest)
    jobs.bind_snapshot(lease, snapshot, revisions, phases, manifest=manifest)
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT fingerprint FROM backtest.experiment_snapshots WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).scalar_one()
            == manifest.manifest_hash
        )

    # Retention cannot remove source revisions referenced by a committed snapshot.
    def remove_bound_revision():
        with engine.begin() as c:
            c.execute(text("ALTER TABLE research.bar_revisions DISABLE TRIGGER USER"))
            c.execute(
                text("DELETE FROM research.bar_revisions WHERE revision_id=:id"),
                {"id": phase_inputs[0][3].revision_id},
            )

    denied(remove_bound_revision)
    with engine.connect() as c:
        candidate_rows = [
            dict(r)
            for r in c.execute(
                text(
                    "SELECT * FROM backtest.experiment_candidates "
                    "WHERE definition_id=:id ORDER BY candidate_id,role"
                ),
                {"id": d["definition_id"]},
            ).mappings()
        ]
    values = dict(
        netReturn="0",
        maxDrawdown="0",
        turnover="0",
        investedBarRatio="0",
        entryCount=0,
        completeValidationSessions=21,
    )
    validation_hash = None
    child_publications = []
    with engine.connect() as c:
        references = []
        for kind, identifier in revisions:
            table, key = (
                ("research.bar_revisions", "revision_id")
                if kind == "BAR"
                else ("research.calendar_versions", "session_id")
            )
            source_row = (
                c.execute(text(f"SELECT * FROM {table} WHERE {key}=:id"), {"id": identifier})
                .mappings()
                .one()
            )
            reference = dict(
                kind=kind,
                referenceId=str(identifier),
                fingerprint=source_row["fingerprint" if kind == "BAR" else "version"],
                localRevision=source_row.get("local_revision"),
                recordedAt=utc_string(source_row["ingested_at"]),
                period=dict(
                    start=utc_string(source_row["bar_start" if kind == "BAR" else "opens_at"]),
                    end=utc_string(source_row["bar_end" if kind == "BAR" else "closes_at"]),
                ),
            )
            references.append(reference)
    reliability_fixture = json.loads(
        Path("contracts/examples/v1/quant-lab-reliability-retro.valid.json").read_text()
    )
    reliability = {
        key: reliability_fixture[key]
        for key in (
            "overallStatus",
            "evidenceStatus",
            "rating",
            "dimensions",
            "historicalAvailabilityStatus",
            "resultLimitations",
        )
    }

    def result(row, phase):
        nonlocal validation_hash
        phase_key = phase["phaseKey"]
        if row["role"] == "SEARCH" and phase["phase"] in ("test", "holdout"):
            jobs.record_result(
                lease,
                dict(common),
                candidate_id=row["candidate_id"],
                role=row["role"],
                phase_key=phase_key,
                status="NOT_EVALUATED_NO_SELECTION",
            )
            return
        request = dict(
            common, candidateId=row["candidate_id"], role=row["role"], phaseKey=phase_key
        )
        content = dict(
            request,
            batchId=str(lease.batch_id),
            snapshotHash=manifest.manifest_hash,
            selectionValues=values,
        )
        link = dict(dataComplete=True, requiredMetricsValid=True)
        publication = jobs.publish_child(
            lease,
            request,
            content,
            content,
            link=link,
            reliability=reliability,
            revision_references=references,
        )
        child_publications.append(publication)
        assert (
            jobs.publish_child(
                lease,
                request,
                content,
                content,
                link=link,
                reliability=reliability,
                revision_references=references,
            )
            == publication
        )
        if row["role"] == "SEARCH" and phase["phase"] == "validation":
            validation_hash = publication["link_hash"]

    for phase in phases[:2]:
        for row in candidate_rows:
            result(row, phase)
    # No OOS publication can precede the atomic all-selections commit.
    reference_candidate = next(row for row in candidate_rows if row["role"] == "FLAT_REFERENCE")
    denied(lambda: result(reference_candidate, phases[2]))
    denied(
        lambda: jobs.record_result(
            lease,
            common,
            candidate_id=candidate_rows[0]["candidate_id"],
            role=candidate_rows[0]["role"],
            phase_key="0:test",
            status="NOT_EVALUATED_NO_SELECTION",
        )
    )
    gates = []
    for gate, threshold, value, status in (
        ("COMPLETE_DATA", "1", "1", "PASSED"),
        ("RELIABILITY", None, None, "PASSED"),
        ("VALID_REQUIRED_METRICS", None, None, "PASSED"),
        ("MINIMUM_VALIDATION_SESSIONS", 20, 21, "PASSED"),
        ("MAX_DRAWDOWN", "0.1", "0", "PASSED"),
        ("MINIMUM_ENTRY_COUNT", 1, 0, "FAILED"),
        ("MINIMUM_INVESTED_BAR_RATIO", None, None, "DISABLED"),
    ):
        gates.append(
            dict(gate=gate, status=status, threshold=threshold, value=value, reasonCodes=[])
        )
    search = next(row for row in candidate_rows if row["role"] == "SEARCH")
    evidence = dict(
        candidateId=search["candidate_id"],
        eligibility="INELIGIBLE",
        validationValues=[dict(fold=0, selectionValues=values, gates=gates)],
        medianSelectionValues={
            key: values[key] for key in ("netReturn", "maxDrawdown", "turnover", "investedBarRatio")
        },
        rankingKey=None,
        rank=None,
        reasons=["MINIMUM_ENTRY_COUNT"],
    )
    selection = dict(
        strategySelectionPolicyHash=d["policy_hash"],
        status="NO_ELIGIBLE_CANDIDATE",
        selectedCandidateId=None,
        strategySelectionPolicy=d["body"]["strategySelectionPolicy"],
        candidateCount=1,
        eligibleCount=0,
        candidateIds=[search["candidate_id"]],
        candidates=[evidence],
        validationInputHash=content_fingerprint((validation_hash,)),
    )
    selections = [dict(selection, scope="FOLD", fold=0), dict(selection, scope="FINAL", fold=None)]

    def raw_selections(entries):
        with jobs.transaction(lease) as connection:
            for value in entries:
                scope = "0" if value["scope"] == "FOLD" else "final"
                preimage = (
                    "quant-selection-v1",
                    json.loads(d["policy_canonical"]),
                    "fold:0" if scope == "0" else "final",
                    (validation_hash,),
                    value,
                )
                jobs._insert(
                    connection,
                    "experiment_selections",
                    lease,
                    dict(value, selectionHash=content_fingerprint(preimage)),
                    canonical_preimage=preimage,
                    scope=scope,
                    selected_candidate_id=None,
                    status=value["status"],
                )

    denied(lambda: raw_selections(selections[:1]))
    assert SQL_DIAGNOSTICS[-1][0] == "quant atomic selection set required"
    denied(
        lambda: raw_selections([selections[0], dict(selections[1], validationInputHash="e" * 64)])
    )
    assert SQL_DIAGNOSTICS[-1][0] == "quant selection conflict"
    bad = deepcopy(selections)
    bad[1]["candidates"][0]["validationValues"][0]["gates"][4]["value"] = "0.9"
    denied(lambda: jobs.freeze_selections(lease, bad))
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM backtest.experiment_selections WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).scalar_one()
            == 0
        )
    jobs.freeze_selections(lease, selections)
    for phase in phases[2:]:
        for row in candidate_rows:
            result(row, phase)
    with engine.connect() as c:
        selection_hashes = list(
            c.execute(
                text(
                    "SELECT fingerprint FROM backtest.experiment_selections "
                    "WHERE batch_id=:id ORDER BY scope"
                ),
                {"id": lease.batch_id},
            ).scalars()
        )
        result_hashes = list(
            c.execute(
                text(
                    "SELECT fingerprint FROM backtest.experiment_result_links "
                    "WHERE batch_id=:id ORDER BY candidate_id,role,phase_key"
                ),
                {"id": lease.batch_id},
            ).scalars()
        )
        reliability_ids = list(
            c.execute(
                text(
                    "SELECT reliability_report_id::text FROM backtest.experiment_result_links "
                    "WHERE batch_id=:id AND status='COMPLETE' ORDER BY candidate_id,role,phase_key"
                ),
                {"id": lease.batch_id},
            ).scalars()
        )
    report = dict(
        common,
        snapshotHash=manifest.manifest_hash,
        registryHash=batch["registry_hash"],
        reliabilityStatus="EXPLORATORY_WITH_LIMITATIONS",
        reliabilityReferences=reliability_ids,
        selectionHashes=selection_hashes,
        resultHashes=result_hashes,
    )
    denied(lambda: jobs.publish(lease, dict(report, selectionHashes=[])))
    denied(lambda: jobs.publish(lease, dict(report, reliabilityReferences=[str(uuid4())])))
    with engine.begin() as c:
        old_license = c.execute(
            text("SELECT license_status FROM research.source_status WHERE singleton")
        ).scalar_one()
        c.execute(
            text("UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton")
        )
    try:
        denied(lambda: jobs.publish(lease, report))
        with engine.connect() as c:
            assert (
                c.execute(
                    text("SELECT count(*) FROM backtest.experiment_reports WHERE batch_id=:id"),
                    {"id": lease.batch_id},
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as c:
            c.execute(
                text("UPDATE research.source_status SET license_status=:value WHERE singleton"),
                {"value": old_license},
            )
    jobs.publish(lease, report)
    denied(lambda: jobs.freeze_selections(lease, selections))
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT status FROM backtest.experiment_batches WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).scalar_one()
            == "SUCCEEDED"
        )
    from packages.backtesting.jobs import BacktestJobs
    from services.control_api.backtest_service import BacktestService
    from tests.integration.test_backtest_jobs import valid_request

    first = child_publications[0]
    exposed = store.reliability_report(
        lease.batch_id, first["report_id"], section="REVISION_REFERENCES", limit=1
    )
    assert exposed["report"]["reportHash"] == first["reliability"]["reportHash"]
    assert len(exposed["items"]) == 1
    denied(lambda: store.reliability_report(uuid4(), first["report_id"]))
    legacy_api = BacktestService(store.engine, code_version="b" * 40, cursor_key=b"q" * 32)
    denied(lambda: legacy_api.read_run(str(first["run_id"])))
    denied(lambda: legacy_api.read_reliability_report(str(first["report_id"]), {}))
    legacy = BacktestJobs(store.engine).enqueue(
        valid_request(), "local-operator", "qsl-legacy-readable-0001", "a" * 32, "b" * 40
    )
    legacy_worker = BacktestJobs(jobs.engine)
    legacy_lease = legacy_worker.claim(uuid4())
    assert legacy_lease.run_id == legacy["run_id"]
    legacy_manifest = json.loads(
        Path("contracts/examples/v1/backtest-manifest.valid.json").read_text()
    )
    legacy_worker.bind_manifest(legacy_lease, dict(public=legacy_manifest, domain={}))
    legacy_worker.publish(legacy_lease, {"summary": [], "equity": [], "fills": []})
    assert legacy_api.read_run(str(legacy["run_id"]))["run"]["status"] == "SUCCEEDED"
    visible = legacy_api.read_runs({"status": "SUCCEEDED"})
    assert {item["runId"] for item in visible["items"]} == {str(legacy["run_id"])}

    strict_request = dict(
        d["body"], researchMode="STRICT_PIT", dataSelectionPolicyVersion="strict-pit-v1"
    )
    strict_definition = store.create_definition(strict_request, candidates)
    strict_validation = store.validate(
        strict_definition["definition_id"],
        dict(validation["body"], definitionHash=strict_definition["fingerprint"]),
    )
    strict_batch = store.enqueue(
        strict_definition["definition_id"],
        strict_definition["fingerprint"],
        "local-operator",
        "qsl-late-strict-cutoff-01",
        "a" * 32,
        "b" * 40,
        **start_binding(strict_definition, strict_validation),
    )
    strict_lease = jobs.claim(uuid4())
    strict_receipt = store.source_receipt(strict_lease.batch_id)
    late_manifest = replace(
        manifest,
        definition=definition_domain(strict_request),
        snapshot_version=strict_batch["snapshot_version"],
        snapshot_at=strict_batch["snapshot_at"],
        decision_data=tuple(
            replace(ref, cutoff=strict_batch["snapshot_at"]) for ref in manifest.decision_data
        ),
        outcome_data=tuple(
            sorted(
                DataReference(
                    bar.revision_id, bar.fingerprint, bar.identity.bar_end + timedelta(seconds=300)
                )
                for _, _, _, bar in phase_inputs
            )
        ),
        quality_revisions=("c" * 64,),
        coverage_revisions=("d" * 64,),
        evidence_policy_version=None,
        trust_fingerprint=strict_receipt["trust_fingerprint"],
        license_receipt_fingerprint=strict_receipt["license_fingerprint"],
    )
    late_snapshot = dict(
        snapshot,
        definitionHash=strict_definition["fingerprint"],
        snapshotVersion=strict_batch["snapshot_version"],
        snapshotAt=utc_string(strict_batch["snapshot_at"]),
        registryHash=strict_batch["registry_hash"],
    )
    denied(
        lambda: jobs.bind_snapshot(
            strict_lease, late_snapshot, revisions, phases, manifest=late_manifest
        )
    )
    assert SQL_DIAGNOSTICS[-1][0] == "quant decision cutoff conflict"
    jobs.publish(strict_lease, status="BLOCKED", error_code="RELIABILITY_BLOCKED")


def guest():
    from concurrent.futures import ThreadPoolExecutor

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.experiment_storage import ExperimentStorage
    from packages.backtesting.jobs import BacktestError, BacktestJobs
    from tests.integration.test_backtest_jobs import role_engine, valid_request

    payload = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=payload["role"],
            password=payload["password"],
            host="postgres",
            database=payload["database"],
        ),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "0034_retrospective_gate")
    legacy = BacktestJobs(engine)
    request = valid_request()
    historical_retro = historical_retrospective(engine, request)
    ids = []
    for status in ("FAILED", "BLOCKED", "ACCEPTED"):
        row = legacy.enqueue(
            request, "local-operator", "legacy-qsl-" + status + "-00001", "a" * 32, "b" * 40
        )
        ids.append(row["run_id"])
        with engine.begin() as c:
            c.execute(
                text("UPDATE backtest.runs SET status=:status,attempts=2 WHERE run_id=:id"),
                {"status": status, "id": row["run_id"]},
            )
    unknown = legacy.enqueue(
        {"instrumentId": request["instrumentId"]},
        "local-operator",
        "legacy-unknown-000001",
        "a" * 32,
        "b" * 40,
    )
    with engine.begin() as c:
        before = c.execute(
            text("SELECT jsonb_agg(to_jsonb(r) ORDER BY run_id) FROM backtest.runs r")
        ).scalar_one()
        config.attributes["connection"] = c
        command.upgrade(config, "head")
        after = c.execute(
            text(
                "SELECT jsonb_agg(to_jsonb(r)-'quant_batch_id' ORDER BY run_id) "
                "FROM backtest.runs r"
            )
        ).scalar_one()
        assert before == after
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM backtest.test_exposures WHERE research_role='UNKNOWN' "
                    "AND source_id=ANY(:ids)"
                ),
                {"ids": [*ids, unknown["run_id"]]},
            ).scalar_one()
            == 4
        )
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM backtest.test_exposures WHERE "
                    "research_role='WALK_FORWARD_OOS' AND source_id=ANY(:ids)"
                ),
                {"ids": ids},
            ).scalar_one()
            == 3
        )
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM backtest.test_exposures WHERE source_id=:id "
                    "AND start_at IS NULL"
                ),
                {"id": unknown["run_id"]},
            ).scalar_one()
            == 1
        )
        assert c.execute(
            text(
                "SELECT research_role,status,knowledge_status FROM backtest.test_exposures "
                "WHERE source_kind='RUN' AND source_id=:id ORDER BY research_role"
            ),
            {"id": historical_retro},
        ).all() == [("UNKNOWN", "FAILED", "REQUESTED"), ("WALK_FORWARD_OOS", "FAILED", "REQUESTED")]
        # Existing jobs precede new batches; finish the accepted upgrade fixtures.
        c.execute(text("UPDATE backtest.runs SET status='FAILED' WHERE status='ACCEPTED'"))
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )

    @event.listens_for(control, "handle_error")
    @event.listens_for(worker, "handle_error")
    def diagnostic(context):
        diag = getattr(context.original_exception, "diag", None)
        SQL_DIAGNOSTICS.append(
            (
                str(getattr(diag, "message_primary", ""))[:120],
                str(getattr(diag, "context", ""))[:120],
            )
        )

    store, jobs = ExperimentStorage(control), ExperimentStorage(worker)

    def denied(call):
        try:
            call()
        except (DBAPIError, BacktestError):
            pass
        else:
            raise AssertionError("forbidden SQL operation succeeded")

    # Old direct SQL clients cannot bypass exposure registration by calling old ports.
    def old_writer():
        with control.begin() as c:
            c.execute(
                text(
                    "SELECT backtest.request_run(:id,'local-operator','old-writer-0000001',"
                    "CAST(:body AS jsonb),:hash,:code,:trace)"
                ),
                dict(
                    id=uuid4(),
                    body=json.dumps(request),
                    hash="a" * 64,
                    code="b" * 40,
                    trace="c" * 32,
                ),
            )

    denied(old_writer)
    definition, candidates = fixture()
    d = store.create_definition(definition, candidates)
    assert d["candidate_count"] == 9
    verify_definition_guards(engine, store, d, denied)
    with engine.connect() as c:
        assert c.execute(
            text(
                "SELECT candidate_id FROM backtest.experiment_candidates WHERE "
                "definition_id=:id AND body->>'strategyId'='ema-cross-v1' AND body->'parameters'="
                "jsonb_build_object('fast',5,'slow',30)"
            ),
            {"id": d["definition_id"]},
        ).scalar_one() == ("ecbfe4ef9aef57abe3e36209544e8f9d0beff5008322ec2d0a20b38dcac3deb3")
    validation = store.validate(
        d["definition_id"],
        dict(
            definitionHash=d["fingerprint"],
            calendarVerified=True,
            simulationBarSteps=9510,
            snapshotRows=1000,
            publicationBytes=100000,
            seedBars=49,
        ),
    )
    batch = store.enqueue(
        d["definition_id"],
        d["fingerprint"],
        "local-operator",
        "qsl-first-batch-00001",
        "a" * 32,
        "b" * 40,
        **start_binding(d, validation),
    )
    assert (
        store.enqueue(
            d["definition_id"],
            d["fingerprint"],
            "local-operator",
            "qsl-first-batch-00001",
            "a" * 32,
            "b" * 40,
            **start_binding(d, validation),
        )["batch_id"]
        == batch["batch_id"]
    )
    with engine.connect() as c:
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 11
        assert (
            c.execute(
                text("SELECT count(*) FROM backtest.test_exposures WHERE source_id=:id"),
                {"id": batch["batch_id"]},
            ).scalar_one()
            == 4
        )
    lease = jobs.claim(uuid4())
    assert lease.batch_id == batch["batch_id"] and jobs.claim(uuid4()) is None
    denied(lambda: jobs.publish(lease, {"fabricated": "success"}))
    jobs.checkpoint(lease, additional_bar_steps=99999)
    jobs.checkpoint(lease, additional_bar_steps=1)
    denied(lambda: jobs.checkpoint(lease, additional_bar_steps=1))
    from tests.recovery.test_quant_lab_jobs import verify_recovery, verify_shared_guards

    verify_recovery(engine, jobs, lease, denied)
    with engine.connect() as c:
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 0
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM backtest.test_exposures WHERE source_id=:id "
                    "AND status='FAILED'"
                ),
                {"id": batch["batch_id"]},
            ).scalar_one()
            == 4
        )

    verify_shared_guards(control, worker, store, jobs, d, validation, request, denied)
    verify_publication(engine, store, jobs, denied)

    # Admissions in both directions share the same locked predecessor register.
    def submit(index):
        return store.enqueue(
            d["definition_id"],
            d["fingerprint"],
            "local-operator",
            f"qsl-concurrent-{index:08}",
            "a" * 32,
            "b" * 40,
            **start_binding(d, validation),
        )

    legacy_ids = mixed_admission(engine, control, worker, store, d, request, submit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(submit, range(1, 3)))
    assert accepted[0]["registry_watermark"] != accepted[1]["registry_watermark"]
    later = max(accepted, key=lambda r: r["registry_watermark"])
    visible_sources = {row["source_id"] for row in store.exposure_history(later["batch_id"])}
    assert set(legacy_ids) <= visible_sources
    # Eight 11-slot batches plus four old jobs reserve 92 shared slots.
    for index in range(3, 8):
        submit(index)
    for index in range(7):
        BacktestJobs(control).enqueue(
            request, "local-operator", f"qsl-queue-slot-{index:08}", "a" * 32, "b" * 40
        )
    with engine.connect() as c:
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 99
    BacktestJobs(control).enqueue(
        request, "local-operator", "qsl-queue-final-0001", "a" * 32, "b" * 40
    )
    with engine.connect() as c:
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 100
    denied(lambda: submit(9))
    denied(
        lambda: BacktestJobs(control).enqueue(
            request, "local-operator", "qsl-queue-full-00001", "a" * 32, "b" * 40
        )
    )
    from tests.security.test_quant_lab_roles import verify_roles

    verify_roles(engine)
    control.dispose()
    worker.dispose()
    engine.dispose()
    print(
        "PASS quant PG "
        "upgrade/legacy-unknown/policy/candidate/atomic-admission/queue100/fence/recovery/roles"
    )


def test_quant_lab_storage_actual_postgresql():
    assert host().endswith("/roles/restore")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own = [
            f
            for f in frames
            if f.filename.startswith("/opt/lab/")
            and ("test_quant_lab_storage" in f.filename or "0035_quant_lab" in f.filename)
        ]
        where = ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in own)
        primary = (
            getattr(getattr(getattr(error, "orig", None), "diag", None), "message_primary", "")
            or ""
        )
        detail = (
            str(error)[:200] if isinstance(error, (AssertionError, ValueError)) else primary[:200]
        )
        diag = getattr(getattr(error, "orig", None), "diag", None)
        context = (
            (getattr(diag, "context", "") or "")
            + " pos="
            + str(getattr(diag, "statement_position", ""))
        )
        context += " internal=" + str(getattr(diag, "internal_position", ""))
        internal_query = getattr(diag, "internal_query", "") or ""
        if internal_query:
            position = int(getattr(diag, "internal_position", "1") or "1")
            context += " query=" + internal_query[max(0, position - 90) : position + 90]
        elif getattr(error, "statement", None) and getattr(diag, "statement_position", None):
            position = int(diag.statement_position)
            context += " query=" + error.statement[max(0, position - 100) : position + 100]
        print("FAIL " + type(error).__name__ + " " + where + " " + detail + " " + context[:250])
        if SQL_DIAGNOSTICS:
            print("Last synthetic SQL diagnostic: " + " ".join(SQL_DIAGNOSTICS[-1]))
        raise SystemExit(1) from None
