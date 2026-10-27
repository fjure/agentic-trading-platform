"""Credential-free optional local dispatcher with fenced, bounded publication."""

import re
from dataclasses import replace
from time import monotonic
from uuid import uuid4

from packages.backtesting.calendar import read_schedule_rows
from packages.backtesting.coverage import read_coverage, read_quality
from packages.backtesting.dataset import (
    DatasetBudgetExceeded,
    DatasetPort,
    Snapshot,
    materialize_fold,
)
from packages.backtesting.evaluation import evaluate
from packages.backtesting.jobs import BacktestError, BacktestJobs
from packages.backtesting.models import BacktestBlocked, DataReference, Manifest
from packages.backtesting.readiness import TrustSnapshot, assess
from packages.backtesting.readiness_jobs import ReadinessError, ReadinessJobs, load_observations
from packages.backtesting.request import parse_request
from packages.backtesting.storage import fold_public, manifest_public
from packages.backtesting.strategies import warmup_count
from packages.research.evidence_storage import EvidenceBudgetExceeded, read_trust_snapshot
from packages.research.models import content_fingerprint


class TrackedTrustSnapshot(TrustSnapshot):
    """Unveränderlicher Truststand mit separatem internem Nutzungsreceipt."""

    def __init__(self, value):
        super().__init__(value.version, value.policy_version, value.records)
        object.__setattr__(self, "references", set())

    def gate(self, kind, reference, knowledge_at):
        if self.record(kind, reference) is not None:
            self.references.add((kind, reference))
        return super().gate(kind, reference, knowledge_at)


class BacktestWorker:
    def __init__(self, engine, *, enabled=False, timer=monotonic, code_version=None):
        if type(enabled) is not bool:
            raise ValueError("explicit dispatch state required")
        self.jobs, self.enabled, self.timer = BacktestJobs(engine), enabled, timer
        self.readiness_jobs = ReadinessJobs(engine)
        if enabled and (
            not isinstance(code_version, str) or not re.fullmatch(r"[0-9a-f]{40}", code_version)
        ):
            raise ValueError("actual code version required")
        self.code_version = code_version
        self.owner = uuid4()

    def tick(self):
        if not self.enabled:
            return False
        try:
            readiness = self.readiness_jobs.claim(self.owner)
            if readiness is not None:
                return self._assess(readiness)
            lease = self.jobs.claim(self.owner)
        except BacktestError:
            return False
        if lease is None:
            return False
        if lease.row["code_version"] != self.code_version:
            self.jobs.publish(
                lease, {"summary": [], "equity": [], "fills": []}, "FAILED", "CODE_VERSION_MISMATCH"
            )
            return True
        started = self.timer()
        projections = []

        def budget():
            if self.timer() - started >= 295:
                raise DatasetBudgetExceeded()

        try:
            request = lease.row["request"]
            config = parse_request(request, lease.row["snapshot_at"])
            snapshot = Snapshot(lease.row["snapshot_version"], lease.row["snapshot_at"])
            start = min(f.training_start for f in config.folds)
            end = config.folds[-1].test_end
            calendars, qualities, proofs, trust, checked_at = self._metadata(config, snapshot)
            # Pages use independent short transactions and the same committed cap.
            port = DatasetPort()
            rows, after = [], None
            while True:
                budget()
                with self.jobs.transaction() as connection:
                    page = port.page(
                        connection, config.instrument_id, start, end, snapshot, after=after
                    )
                rows.extend(page.items)
                if len(rows) > 100000:
                    raise DatasetBudgetExceeded()
                after = page.next_position
                if after is None:
                    break
            from packages.backtesting.dataset import FrozenDataset

            frozen = FrozenDataset(snapshot, tuple(rows))
            datasets, blocked = [], None
            for fold in config.folds:
                try:
                    datasets.append(
                        materialize_fold(
                            frozen,
                            fold,
                            config.instrument_id,
                            warmup_count(config.strategy_id, config.parameters),
                            calendars,
                            qualities,
                            proofs,
                            budget,
                            trust_check=trust.gate,
                        )
                    )
                except BacktestBlocked as error:
                    blocked = error
                    break
            datasets = tuple(datasets)
            decision_refs, outcome_refs = set(), set()
            for data in datasets:
                for step in data.steps:
                    decision_refs.update(
                        DataReference(b.revision_id, b.fingerprint, step.knowledge_at)
                        for b in step.input_bars
                    )
                cutoffs = dict(data.outcome_cutoffs)
                outcome_refs.update(
                    DataReference(b.revision_id, b.fingerprint, cutoffs[b.revision_id])
                    for b in data.outcome_bars
                )
            if blocked is not None:
                # Bind the complete frozen input and the precise missing-data boundary.
                # Successful fold results remain available without claiming a complete manifest.
                envelope = dict(
                    domain=dict(
                        config=config,
                        snapshot=snapshot,
                        code_version=lease.row["code_version"],
                        revisions=tuple(sorted((str(b.revision_id), b.fingerprint) for b in rows)),
                        calendar=calendars,
                        quality=qualities,
                        coverage=proofs,
                        warmups=tuple(d.warmup_binding for d in datasets),
                        blocker=dict(code=blocked.code, reference=blocked.reference),
                    ),
                    public=None,
                )
            else:
                manifest = Manifest(
                    config,
                    snapshot.version,
                    snapshot.at,
                    tuple(sorted(decision_refs)),
                    tuple(sorted(outcome_refs)),
                    tuple(sorted({r for d in datasets for r in d.calendar_revision_ids})),
                    tuple(sorted({r for d in datasets for r in d.quality_revision_ids})),
                    tuple(sorted({r for d in datasets for r in d.coverage_ids})),
                    tuple(d.warmup_binding for d in datasets),
                    lease.row["code_version"],
                )
                envelope = dict(
                    domain=manifest,
                    public=manifest_public(
                        manifest,
                        request,
                        datasets,
                        calendars,
                        qualities,
                        proofs,
                        budget,
                    ),
                )
            envelope["trust"] = dict(version=trust.version, policy_version=trust.policy_version)
            self.jobs.bind_manifest(lease, envelope)

            def completed(fold):
                budget()
                public = fold_public(
                    fold, datasets[fold.fold_index], request["folds"][fold.fold_index]
                )
                self.jobs.checkpoint(lease, fold.fold_index, public)
                projections.append(public)

            def fold_inputs():
                yield from datasets
                if blocked is not None:
                    raise blocked

            result = evaluate(config, fold_inputs(), checkpoint=budget, on_fold=completed)
            body = {
                kind: [
                    item
                    for fold in projections
                    for item in ([fold[kind]] if kind == "summary" else fold[kind])
                ]
                for kind in ("summary", "equity", "fills")
            }
            if result.status == "BLOCKED":
                body["_blocker"] = dict(
                    code=result.blocked_code, reference=result.blocked_reference
                )
            self.jobs.publish(lease, body, result.status, result.blocked_code)
        except BacktestBlocked as error:
            self.jobs.publish(
                lease, {"summary": [], "equity": [], "fills": []}, "BLOCKED", error.code
            )
        except DatasetBudgetExceeded:
            self.jobs.publish(
                lease, {"summary": [], "equity": [], "fills": []}, "FAILED", "BUDGET_EXHAUSTED"
            )
        except BacktestError:
            # A lost lease must never be published or claimed successful.
            return False
        except Exception:
            self.jobs.publish(
                lease, {"summary": [], "equity": [], "fills": []}, "FAILED", "UNAVAILABLE"
            )
        return True

    def _metadata(self, config, snapshot):
        # All metadata readers see ONE repeatable committed trust state, while
        # data is additionally capped by the original immutable data snapshot.
        with self.readiness_jobs.transaction(repeatable=True) as connection:
            try:
                state = read_trust_snapshot(connection, snapshot.at, snapshot.version)
            except EvidenceBudgetExceeded:
                raise DatasetBudgetExceeded() from None
            trust = TrackedTrustSnapshot(TrustSnapshot.from_mapping(state))
            calendars = read_schedule_rows(connection, snapshot)
            start = min(f.training_start for f in config.folds)
            end = config.folds[-1].test_end
            qualities = read_quality(connection, config.instrument_id, start, end, snapshot)
            proofs = read_coverage(connection, config.instrument_id, snapshot)
        return calendars, qualities, proofs, trust, state["snapshot_at"]

    def _assess(self, lease):
        if lease.row["code_version"] != self.code_version:
            return self._assessment_failure(lease, "INTERNAL_FAILURE")
        started, last_fence = self.timer(), None

        def budget():
            nonlocal last_fence
            now = self.timer()
            if now - started >= 295:
                raise DatasetBudgetExceeded()
            if last_fence is None or now - last_fence >= 1:
                self.readiness_jobs.checkpoint(lease)
                last_fence = now

        try:
            budget()
            config = parse_request(lease.row["request"], lease.row["snapshot_at"])
            snapshot = Snapshot(lease.row["snapshot_version"], lease.row["snapshot_at"])
            calendars, qualities, proofs, trust, checked_at = self._metadata(config, snapshot)
            self.readiness_jobs.bind_trust(lease, trust)
            port, after, rows = DatasetPort(), None, []
            start, end = min(f.training_start for f in config.folds), config.folds[-1].test_end
            while True:
                budget()
                with self.readiness_jobs.transaction() as connection:
                    page = port.page(
                        connection, config.instrument_id, start, end, snapshot, after=after
                    )
                rows.extend(page.items)
                if len(rows) > 100000:
                    raise DatasetBudgetExceeded()
                after = page.next_position
                if after is None:
                    break
            from packages.backtesting.dataset import FrozenDataset

            frozen = FrozenDataset(snapshot, tuple(rows))
            with self.readiness_jobs.transaction() as connection:
                observations = load_observations(
                    connection, config.instrument_id, start, end, snapshot, frozen.revisions
                )
            result = assess(
                config,
                frozen,
                calendars,
                qualities,
                proofs,
                trust=trust,
                observations=observations,
                checkpoint=budget,
                clock=self.timer,
            )
            if not result.publication_complete:
                return self._assessment_failure(lease, result.failure_code or "INTERNAL_FAILURE")
            result = replace(result, code_version=lease.row["code_version"])
            report = result.report_dict(lease.assessment_id, checked_at)
            report["requestHash"] = lease.row["request_hash"]
            del report["reportHash"]
            report["reportHash"] = content_fingerprint(report)
            findings = tuple(finding.to_dict() for finding in result.findings)
            budget()
            self.readiness_jobs.publish(lease, report, findings, references=trust.references)
            return True
        except ReadinessError:
            return False  # A lost fence or interrupted SQL transaction cannot publish.
        except DatasetBudgetExceeded:
            return self._assessment_failure(lease, "BUDGET_EXHAUSTED")
        except Exception:
            return self._assessment_failure(lease, "INTERNAL_FAILURE")

    def _assessment_failure(self, lease, code):
        try:
            self.readiness_jobs.publish(lease, error_code=code)
            return True
        except BacktestError:
            return False
