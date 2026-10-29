"""Credential-free retrospective worker; all simulation happens outside SQL transactions."""

import base64
import hashlib
from datetime import date, datetime
from time import monotonic
from uuid import uuid4, uuid5

from sqlalchemy import text

from packages.backtesting.dataset import DatasetBlocked, Snapshot
from packages.backtesting.evaluation import evaluate
from packages.backtesting.jobs import BacktestError, encoded
from packages.backtesting.models import BacktestBudgetExceeded, fingerprint
from packages.backtesting.reliability import ReliabilityLicenseReceipt, assess_reliability
from packages.backtesting.request import parse_request
from packages.backtesting.retrospective import (
    RESULT_LIMITATIONS,
    RetrospectiveDataset,
    RetrospectiveEvidenceReference,
    RetrospectiveManifest,
    RetrospectiveRevisionReference,
    RetrospectiveTrustContext,
    RetrospectiveTrustRecord,
    materialize_retrospective_fold,
    snapshot_reference,
)
from packages.backtesting.retrospective_storage import RetrospectiveStorage
from packages.backtesting.storage import bar_identity, fold_public
from packages.backtesting.strategies import warmup_count
from packages.domain.models import utc_string
from packages.research.evidence_storage import _currently_invalid
from packages.research.storage import bar_from_row


def evidence_reference(row):
    return RetrospectiveEvidenceReference(
        row["evidence_id"],
        row["fingerprint"],
        row["revision"],
        row["commit_version"],
        row["ingested_at"],
    )


def managed_calendars(evidence):
    rows = []
    for row in evidence:
        if row["kind"] != "CALENDAR":
            continue
        for session in row["statement"].get("sessions", ()):
            rows.append(
                dict(
                    session_id=uuid5(row["evidence_id"], session["sessionDate"]),
                    evidence_id=row["evidence_id"],
                    session_date=date.fromisoformat(session["sessionDate"]),
                    opens_at=datetime.fromisoformat(session["opensAt"]),
                    closes_at=datetime.fromisoformat(session["closesAt"]),
                    version=row["fingerprint"],
                    source=row["source_id"],
                    commit_version=row["commit_version"],
                    ingested_at=row["ingested_at"],
                    managed_trust_state="TIME_UNPROVEN",
                )
            )
            if len(rows) > 100000:
                raise BacktestBudgetExceeded("calendar session budget exhausted")
    return tuple(rows)


def snapshot_trust(connection, row, evidence, checkpoint):
    """Reconstruct accepted authority state, not today's revocation interpretation."""
    decisions = tuple(
        connection.execute(
            text(
                "SELECT * FROM research.trust_decisions WHERE version<=:version "
                "ORDER BY version LIMIT 100001"
            ),
            {"version": row["trust_version"]},
        ).mappings()
    )
    if len(decisions) + len(evidence) > 100000:
        raise BacktestBudgetExceeded("trust metadata budget exhausted")
    authorities = {}
    for entry in decisions:
        checkpoint()
        decision = entry["body"]
        if datetime.fromisoformat(decision["effectiveAt"]) > row["snapshot_at"]:
            continue
        key = base64.b64decode(decision["publicKey"], validate=True)
        authorities[
            (
                decision["sourceId"],
                decision["keyId"],
                decision["authorityType"],
                decision.get("witnessId"),
            )
        ] = {
            **decision,
            "pinned_fingerprint": hashlib.sha256(key).hexdigest() if len(key) == 32 else None,
        }
    records = []
    for entry in evidence:
        checkpoint()
        records.append(
            RetrospectiveTrustRecord(
                str(entry["evidence_id"]),
                tuple(
                    (name, entry["trust"][name]["status"])
                    for name in (
                        "integrity",
                        "sourceAuthenticity",
                        "temporalAuthenticity",
                        "completeness",
                        "domainQuality",
                    )
                ),
                _currently_invalid(entry, authorities, row["snapshot_at"]),
            )
        )
    return dict(
        version=fingerprint((row["trust_version"], row["trust_policy_version"], tuple(records))),
        records=[
            dict(reference=r.reference, dimensions=list(r.dimensions), revoked=r.revoked)
            for r in records
        ],
    )


def restore_trust(snapshot, content, evidence):
    return RetrospectiveTrustContext(
        snapshot,
        content["version"],
        tuple(
            RetrospectiveTrustRecord(
                r["reference"], tuple(tuple(v) for v in r["dimensions"]), r["revoked"]
            )
            for r in content["records"]
        ),
        tuple(evidence_reference(r) for r in evidence),
    )


def public_manifest(manifest, request, report, receipt, checkpoint=lambda: None):
    """Canonical retrospective wire binding, with no synthetic receipt deadlines."""
    folds, decisions, outcomes = [], [], []
    count = 0
    for index, dataset in enumerate(manifest.datasets):
        checkpoint()
        binding = dataset.warmup_binding
        folds.append(
            dict(
                foldIndex=index,
                window=request["folds"][index],
                warmupStart=utc_string(binding.warmup_start),
                warmupEnd=utc_string(binding.warmup_end),
                warmupRuleVersion=binding.rule_version,
                expectedWarmupBars=[bar_identity(bar) for bar in dataset.warmup],
            )
        )
        for step in dataset.steps:
            checkpoint()
            count += len(step.input_bars)
            if count > 10000:
                raise BacktestBudgetExceeded("manifest evidence byte budget exhausted")
            decisions.append(
                dict(
                    valuationAt=utc_string(step.signal_bar.identity.bar_end),
                    knowledgeAt=utc_string(step.knowledge_at),
                    inputHash=fingerprint(
                        tuple((b.revision_id, b.fingerprint) for b in step.input_bars)
                    ),
                    revisions=[
                        RetrospectiveRevisionReference.from_bar(b).to_dict()
                        for b in step.input_bars
                    ],
                )
            )
        outcomes.extend(
            dict(
                revision=RetrospectiveRevisionReference.from_bar(b).to_dict(),
                simulatedMarketCutoff=utc_string(b.identity.bar_end),
            )
            for b in dataset.outcome_bars
        )
    calendars = sorted(
        {ref for d in manifest.datasets for ref in d.calendar_references},
        key=lambda ref: (str(ref.session_id), ref.version),
    )
    calendar_revisions = [
        dict(
            evidenceId=str(r.evidence_id or r.session_id),
            fingerprint=r.version,
            revision=max(1, r.commit_version),
            ingestedAt=utc_string(r.ingested_at),
        )
        for r in calendars
    ]
    body = dict(
        manifestHash=manifest.manifest_hash,
        inputHash=manifest.input_hash,
        request=request,
        symbol=manifest.config.symbol,
        strategyVersion=manifest.config.strategy_version,
        simulatorVersion=manifest.simulator_version,
        metricsVersion=manifest.metrics_version,
        codeVersion=manifest.code_version,
        snapshot=dict(
            version=manifest.snapshot.version, committedAt=utc_string(manifest.snapshot.at)
        ),
        assumptions=report["methods"]["assumptions"],
        folds=folds,
        decisionInputs=decisions,
        outcomeInputs=outcomes,
        calendarRevisions=calendar_revisions,
        qualityRevisions=[r.to_dict() for r in manifest.quality_references],
        corporateActionCoverage=[r.to_dict() for r in manifest.corporate_action_references],
        researchMode="RETROSPECTIVE_EXPLORATORY",
        selectionPolicyVersion="retrospective-v1",
        experimentVersion="retrospective-experiment-v1",
        snapshotReference=snapshot_reference(manifest.snapshot),
        calendarReferences=[
            dict(
                revision=ref,
                source=raw.source,
                version=raw.version,
                purpose="REGULAR_SESSION",
                historicalAvailability="NOT_PROVEN",
            )
            for raw, ref in zip(calendars, calendar_revisions, strict=True)
        ],
        trustReference=manifest.trust_context.to_dict(),
        licenseReceipt=dict(
            receiptId=str(receipt.receipt_id),
            provider="ALPACA",
            status=receipt.status,
            recordedAt=utc_string(receipt.recorded_at),
            fingerprint=receipt.fingerprint,
        ),
        qualityPolicyVersion=manifest.quality_policy_version,
        resultLimitations=list(RESULT_LIMITATIONS),
    )
    encoded(body)
    return body


def retrospective_fold_public(evaluation, dataset, window, corporate_status):
    public = fold_public(evaluation, dataset, window)
    coverage = public["summary"]["coverage"]
    del coverage["corporateActions"]
    coverage.update(
        corporateActionStatus=corporate_status, historicalAvailabilityStatus="NOT_PROVEN"
    )
    for fill in public["fills"]:
        fill["simulatedMarketCutoff"] = fill.pop("observationCutoff")
    return public


class RetrospectiveWorker:
    def __init__(self, engine, *, owner, code_version, timer=monotonic):
        self.storage = RetrospectiveStorage(engine)
        self.owner, self.code_version, self.timer = owner, code_version, timer

    def tick(self):
        lease = self.storage.claim(self.owner)
        if lease is None:
            lease = self.storage.claim(self.owner, run=True)
        if lease is None:
            return None
        return self.process(lease)

    def _read_inputs(self, lease, config, checkpoint):
        snapshot = Snapshot(lease.row["snapshot_version"], lease.row["snapshot_at"])
        try:
            dataset, rows = self.storage.load_dataset(lease.assessment_id, checkpoint=checkpoint)
            return dataset["body"]["content"], rows
        except BacktestError as error:
            if error.status != 404 or lease.run_id is not None:
                raise
        start, end = min(f.training_start for f in config.folds), config.folds[-1].test_end
        with self.storage.transaction(repeatable=True, lease=lease) as connection:
            self.storage.fenced(connection, lease)
            rows = []
            queries = (
                (
                    "BAR",
                    "SELECT * FROM research.bar_revisions WHERE instrument_id=:instrument "
                    "AND bar_start>=:start AND bar_end<=:end",
                ),
                ("CALENDAR", "SELECT * FROM research.calendar_versions WHERE true"),
                (
                    "QUALITY",
                    "SELECT r.*,encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') "
                    "AS fingerprint FROM research.quality_observations r "
                    "WHERE instrument_id=:instrument "
                    "AND start_at<:end AND end_at>:start",
                ),
                (
                    "EVIDENCE",
                    "SELECT * FROM research.evidence_revisions WHERE "
                    "(instrument_id=:instrument OR kind='CALENDAR') "
                    "AND start_at<:end AND end_at>:start",
                ),
            )
            for kind, query in queries:
                checkpoint(fence=False)
                result = connection.execute(
                    text(
                        query + " AND commit_version<=:version "
                        "AND ingested_at<=:at ORDER BY commit_version,ingested_at LIMIT 100001"
                    ),
                    dict(
                        instrument=config.instrument_id,
                        start=start,
                        end=end,
                        version=snapshot.version,
                        at=snapshot.at,
                    ),
                ).mappings()
                rows.extend((kind, dict(row)) for row in result)
                if len(rows) > 100000:
                    raise BacktestBudgetExceeded("combined revision/metadata budget exhausted")
            evidence = tuple(row for kind, row in rows if kind == "EVIDENCE")
            trust = snapshot_trust(connection, lease.row, evidence, lambda: checkpoint(fence=False))
        content = dict(trustState=trust)
        references = tuple(
            (
                kind,
                row[
                    {
                        "BAR": "revision_id",
                        "CALENDAR": "session_id",
                        "QUALITY": "observation_id",
                        "EVIDENCE": "evidence_id",
                    }[kind]
                ],
                row["version"] if kind == "CALENDAR" else row["fingerprint"],
            )
            for kind, row in rows
        )
        self.storage.bind_dataset(
            lease, content, references, checkpoint=lambda: checkpoint(fence=False)
        )
        bound, restored = self.storage.load_dataset(lease.assessment_id, checkpoint=checkpoint)
        return bound["body"]["content"], restored

    def process(self, lease):
        started, last_fence = self.timer(), None

        def checkpoint(*, fence=True):
            nonlocal last_fence
            now = self.timer()
            if now - started >= 295:
                raise BacktestBudgetExceeded("retrospective CPU deadline exhausted")
            if fence and (last_fence is None or now - last_fence >= 1):
                self.storage.checkpoint(lease)
                last_fence = now

        try:
            checkpoint()
            if lease.row["code_version"] != self.code_version:
                raise ValueError("build version mismatch")
            if lease.run_id is not None:
                code = self.storage.current_gate(lease)
                if code is not None:
                    self.storage.block_current_gate(lease, code)
                    return True
            request = {
                k: v
                for k, v in lease.row["request"].items()
                if k not in ("assessmentId", "assessmentHash")
            }
            config = parse_request(
                {
                    k: v
                    for k, v in request.items()
                    if k not in ("researchMode", "selectionPolicyVersion")
                },
                lease.row["snapshot_at"],
            )
            content, rows = self._read_inputs(lease, config, checkpoint)
            snapshot = Snapshot(lease.row["snapshot_version"], lease.row["snapshot_at"])
            bars = tuple(bar_from_row(r) for kind, r in rows if kind == "BAR")
            evidence = tuple(r for kind, r in rows if kind == "EVIDENCE")
            calendars = tuple(r for kind, r in rows if kind == "CALENDAR") + managed_calendars(
                evidence
            )
            qualities = tuple(r for kind, r in rows if kind == "QUALITY") + tuple(
                r for r in evidence if r["kind"] == "QUALITY"
            )
            actions = tuple(r for r in evidence if r["kind"] == "CORPORATE_ACTION")
            trust = restore_trust(snapshot, content["trustState"], evidence)
            receipt = ReliabilityLicenseReceipt.from_row(
                self.storage.license_receipt(lease.assessment_id)
            )
            dataset, manifest = RetrospectiveDataset(snapshot, bars), None
            try:
                folds = tuple(
                    materialize_retrospective_fold(
                        dataset,
                        fold,
                        config.instrument_id,
                        warmup_count(config.strategy_id, config.parameters),
                        calendars,
                        trust_context=trust,
                        checkpoint=checkpoint,
                    )
                    for fold in config.folds
                )
                manifest = RetrospectiveManifest(
                    config,
                    snapshot,
                    folds,
                    trust,
                    self.code_version,
                    receipt.fingerprint,
                    quality_references=tuple(
                        evidence_reference(r)
                        for r in evidence
                        if r["kind"] == "QUALITY"
                        and any(
                            r["start_at"] < d.fold.test_end
                            and r["end_at"] > d.warmup_binding.warmup_start
                            for d in folds
                        )
                    ),
                    corporate_action_references=tuple(
                        evidence_reference(r)
                        for r in actions
                        if any(
                            r["start_at"] < d.fold.test_end
                            and r["end_at"] > d.warmup_binding.warmup_start
                            for d in folds
                        )
                    ),
                )
            except DatasetBlocked:
                pass
            assessment = assess_reliability(
                config,
                dataset,
                calendars,
                trust_context=trust,
                license_receipt=receipt,
                code_version=self.code_version,
                quality_rows=qualities,
                corporate_action_rows=actions,
                manifest=manifest,
                checkpoint=checkpoint,
            )
            report = assessment.to_dict(
                report_id=uuid4(),
                assessment_id=lease.assessment_id,
                phase="FINAL" if lease.run_id else "PREVIEW",
                run_id=lease.run_id,
            )
            findings = tuple(f.to_dict() for f in assessment.findings)
            references = tuple(r.to_dict() for r in assessment.revision_references)
            checkpoint()
            if lease.run_id is None:
                self.storage.publish_assessment(
                    lease, report, findings=findings, revision_references=references
                )
                return True
            preview = self.storage.preview_report(lease.assessment_id)
            if (
                report["reportHash"] != preview["body"]["reportHash"]
                or findings != tuple(preview["findings"])
                or references != tuple(preview["revision_references"])
            ):
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
            code = self.storage.current_gate(lease)
            if code is not None:
                self.storage.block_current_gate(lease, code)
                return True
            public = public_manifest(manifest, request, report, receipt, checkpoint)
            projections = []

            def completed(fold):
                checkpoint()
                projection = retrospective_fold_public(
                    fold,
                    manifest.datasets[fold.fold_index],
                    request["folds"][fold.fold_index],
                    assessment.corporate_action_status,
                )
                self.storage.checkpoint_run(lease, fold.fold_index, projection)
                projections.append(projection)

            result = evaluate(
                config,
                manifest.datasets,
                checkpoint=checkpoint,
                on_fold=completed,
                validation_context=manifest.validation_context(),
            )
            if result.status != "SUCCEEDED":
                raise ValueError("simulation did not complete")
            checkpoint()
            code = self.storage.current_gate(lease)
            if code is not None:
                self.storage.block_current_gate(lease, code)
                return True
            body = {
                kind: [
                    item
                    for fold in projections
                    for item in ([fold[kind]] if kind == "summary" else fold[kind])
                ]
                for kind in ("summary", "equity", "fills")
            }
            self.storage.publish_run(
                lease,
                body,
                report,
                manifest=public,
                findings=findings,
                revision_references=references,
            )
            return True
        except BacktestError as error:
            if error.code == "LEASE_LOST":
                return False
            if lease.run_id is not None:
                code = self.storage.current_gate(lease)
                if code is not None:
                    self.storage.block_current_gate(lease, code)
                    return True
            return self.fail(lease, "INTERNAL_FAILURE")
        except BacktestBudgetExceeded:
            return self.fail(lease, "BUDGET_EXHAUSTED")
        except Exception:
            return self.fail(lease, "INTERNAL_FAILURE")

    def fail(self, lease, code):
        try:
            if lease.run_id is None:
                self.storage.publish_assessment(lease, error_code=code)
            else:
                self.storage.fail_run(lease, code)
            return True
        except BacktestError:
            return False
