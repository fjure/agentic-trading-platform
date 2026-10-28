"""Pure snapshot-bound raw-price research diagnostics, never trading permission.

Inputs are the local Research row mappings (calendar/quality/managed evidence),
not provider payloads or CoverageProof.no_actions. Receipt rows come from the
separate immutable backtest license receipt port. Runtime/current warnings are
separate from the historical report and its deterministic hash.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, localcontext
from time import monotonic
from uuid import UUID, uuid5

from packages.backtesting.calendar import relevant_schedule_rows
from packages.backtesting.dataset import DatasetBlocked, Snapshot, revision_key
from packages.backtesting.models import (
    METRICS_VERSION,
    SIMULATOR_VERSION,
    BacktestBudgetExceeded,
    ExperimentConfig,
    RetrospectivePolicyV1,
    fingerprint,
)
from packages.backtesting.retrospective import (
    DISCONTINUITY_POLICY_VERSION,
    MAX_PUBLICATION_BYTES,
    MAX_REVISIONS,
    QUALITY_POLICY_VERSION,
    RESULT_LIMITATIONS,
    RetrospectiveDataset,
    RetrospectiveEvidenceReference,
    RetrospectiveManifest,
    RetrospectiveTrustContext,
    materialize_retrospective_fold,
    restore_retrospective_calendar,
    retrospective_calendar,
    snapshot_reference,
)
from packages.backtesting.strategies import warmup_count
from packages.domain.models import decimal_string, utc_string, utc_time
from packages.research.calendar import CalendarCoverage, expected_windows
from packages.research.models import BarInterval, QualityStatus, _canonical
from packages.research.quality import validate_ohlcv

VERSION = "reliability-report-v1"
DIMENSIONS = (
    "INTEGRITY",
    "CALENDAR",
    "CORPORATE_ACTIONS",
    "HISTORICAL_AVAILABILITY",
    "FEED",
    "LICENSE",
)
_NAMESPACE = UUID("fc00b602-838b-4e35-bdf4-293cd8bb4228")
_EXPLORATORY = "EXPLORATORY_WITH_LIMITATIONS"


def _period(start, end):
    return dict(start=utc_string(start), end=utc_string(end))


def _window(fold):
    return dict(
        zip(
            ("trainingStart", "trainingEnd", "testStart", "testEnd"),
            (
                utc_string(fold.training_start),
                utc_string(fold.training_end),
                utc_string(fold.test_start),
                utc_string(fold.test_end),
            ),
            strict=True,
        )
    )


def _protocol(config):
    return dict(
        instrumentId=str(config.instrument_id),
        strategyId=config.strategy_id,
        version=config.strategy_version,
        parameters=dict(config.parameters),
        startCash=decimal_string(config.start_cash),
        feeUsd=decimal_string(config.fee_usd),
        slippageBps=decimal_string(config.slippage_bps),
        quantityStep=decimal_string(config.quantity_step),
        folds=[_window(f) for f in config.folds],
        researchMode="RETROSPECTIVE_EXPLORATORY",
        selectionPolicyVersion="retrospective-v1",
    )


def _license_status(value):
    result = {
        "UNKNOWN": "UNKNOWN",
        "CONFIRMED": "DECLARED_CONFIRMED",
        "DECLARED_CONFIRMED": "DECLARED_CONFIRMED",
        "BLOCKED": "DENIED",
        "DENIED": "DENIED",
    }.get(value)
    if result is None:
        raise ValueError("closed license status required")
    return result


@dataclass(frozen=True)
class ReliabilityLicenseReceipt:
    receipt_id: UUID
    status: str
    recorded_at: datetime
    fingerprint: str
    provider: str = "alpaca"

    def __post_init__(self):
        if not isinstance(self.receipt_id, UUID) or self.provider != "alpaca":
            raise ValueError("local Alpaca receipt required")
        object.__setattr__(self, "status", _license_status(self.status))
        utc_time(self.recorded_at)
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("receipt fingerprint required")

    @classmethod
    def from_row(cls, row):
        """RRB03 rows use assessment_id as the immutable receipt identity."""
        return cls(
            row["assessment_id"],
            row["status"],
            row["recorded_at"],
            row["fingerprint"],
            row["provider"],
        )


@dataclass(frozen=True)
class CurrentLicenseDecision:
    status: str
    code: str | None
    blocks_new_execution: bool


def check_current_license(status: str | None) -> CurrentLicenseDecision:
    """None denotes unavailable current port, separate from historical receipts."""
    if status is None:
        return CurrentLicenseDecision("UNKNOWN", "TRUST_UNAVAILABLE", True)
    status = _license_status(status)
    return CurrentLicenseDecision(
        status,
        "LICENSE_DENIED"
        if status == "DENIED"
        else "LICENSE_UNKNOWN"
        if status == "UNKNOWN"
        else None,
        status == "DENIED",
    )


@dataclass(frozen=True)
class ReliabilityBudget:
    max_rows: int = MAX_REVISIONS
    max_findings: int = 10000
    max_publication_bytes: int = MAX_PUBLICATION_BYTES
    max_seconds: int = 295

    def __post_init__(self):
        for value, limit in (
            (self.max_rows, MAX_REVISIONS),
            (self.max_findings, MAX_REVISIONS),
            (self.max_publication_bytes, MAX_PUBLICATION_BYTES),
            (self.max_seconds, 295),
        ):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("bounded reliability budget required")


_DEFAULT_BUDGET = ReliabilityBudget()


@dataclass(frozen=True)
class ReliabilityReference:
    kind: str
    reference_id: UUID
    fingerprint: str
    local_revision: int | None
    recorded_at: datetime
    period: tuple[datetime, datetime] | None = None

    def to_dict(self):
        return dict(
            kind=self.kind,
            referenceId=str(self.reference_id),
            fingerprint=self.fingerprint,
            localRevision=self.local_revision,
            recordedAt=utc_string(self.recorded_at),
            period=_period(*self.period) if self.period else None,
        )


@dataclass(frozen=True)
class ReliabilityFinding:
    category: str
    code: str
    severity: str
    fold_index: int | None = None
    period: tuple[datetime, datetime] | None = None
    references: tuple[ReliabilityReference, ...] = ()

    def to_dict(self):
        return dict(
            findingId=str(uuid5(_NAMESPACE, fingerprint(self))),
            category=self.category,
            code=self.code,
            severity=self.severity,
            foldIndex=self.fold_index,
            period=_period(*self.period) if self.period else None,
            references=[r.to_dict() for r in self.references],
        )


@dataclass(frozen=True)
class ReliabilityBarCounts:
    expected: int | None
    present: int
    missing: int | None
    unexpected: int

    def to_dict(self):
        return dict(
            expected=self.expected,
            present=self.present,
            missing=self.missing,
            unexpected=self.unexpected,
        )


@dataclass(frozen=True)
class ReliabilityFold:
    index: int
    warmup_period: tuple[datetime, datetime]
    counts: tuple[tuple[str, ReliabilityBarCounts], ...]
    eligibility: str

    def to_dict(self, config):
        return dict(
            foldIndex=self.index,
            window=_window(config.folds[self.index]),
            warmupPeriod=_period(*self.warmup_period),
            barCounts={name: counts.to_dict() for name, counts in self.counts},
            eligibility=self.eligibility,
        )


@dataclass(frozen=True)
class ReliabilityAssessment:
    config: ExperimentConfig
    snapshot: Snapshot
    code_version: str
    trust_version: str
    manifest_hash: str | None
    license_receipt: ReliabilityLicenseReceipt | None
    dimensions: tuple[tuple[str, str, tuple[str, ...]], ...]
    folds: tuple[ReliabilityFold, ...]
    findings: tuple[ReliabilityFinding, ...]
    revision_references: tuple[ReliabilityReference, ...]
    corporate_action_status: str
    evaluation_complete: bool

    @property
    def overall_status(self):
        return (
            "BLOCKED"
            if (
                not self.evaluation_complete
                or self.manifest_hash is None
                or any(f.severity == "BLOCKER" for f in self.findings)
            )
            else _EXPLORATORY
        )

    @property
    def report_hash(self):
        """Semantic hash excludes report/assessment/run IDs, phase and current warnings.

        Storage separately fingerprints the complete published body. This preimage
        includes the bound config, snapshot, build, trust, references and findings.
        """
        return fingerprint(self)

    def to_dict(
        self, *, report_id: UUID, assessment_id: UUID, phase="PREVIEW", run_id: UUID | None = None
    ):
        if (
            not isinstance(report_id, UUID)
            or not isinstance(assessment_id, UUID)
            or phase not in ("PREVIEW", "FINAL")
        ):
            raise ValueError("typed report identity and phase required")
        if (
            phase == "FINAL"
            and not isinstance(run_id, UUID)
            or phase == "PREVIEW"
            and run_id is not None
        ):
            raise ValueError("phase/run binding required")
        assumptions = dict(
            simulation="LOCAL_SIMULATION",
            provider="ALPACA",
            feed="iex",
            interval="15Min",
            adjustment="raw",
            currency="USD",
            positionPolicy="LONG_FLAT",
            fillModel="next-regular-bar-open-v1",
            signalDelaySeconds=60,
            decimalPrecision=38,
            rounding="ROUND_HALF_EVEN",
            moneyScale=8,
            returnScale=12,
            dividendsIncluded=False,
            forceLiquidation=False,
            outcomeSelection="SNAPSHOT_BOUND_MARKET_EVENT",
            qualityPolicyVersion=QUALITY_POLICY_VERSION,
            discontinuityPolicyVersion=DISCONTINUITY_POLICY_VERSION,
        )
        protocol = _protocol(self.config)
        return dict(
            reportId=str(report_id),
            version=VERSION,
            reportHash=self.report_hash,
            phase=phase,
            assessmentId=str(assessment_id),
            runId=str(run_id) if run_id else None,
            protocolHash=fingerprint(protocol),
            snapshotReference=snapshot_reference(self.snapshot),
            researchMode="RETROSPECTIVE_EXPLORATORY",
            selectionPolicyVersion="retrospective-v1",
            experimentVersion="retrospective-experiment-v1",
            instrumentId=str(self.config.instrument_id),
            symbol=self.config.symbol,
            provider="ALPACA",
            feed="iex",
            interval="15Min",
            adjustment="raw",
            period=_period(
                min(f.training_start for f in self.config.folds), self.config.folds[-1].test_end
            ),
            folds=[f.to_dict(self.config) for f in self.folds],
            dimensions={
                name: dict(rating=rating, reasonCodes=list(codes))
                for name, rating, codes in self.dimensions
            },
            overallStatus=self.overall_status,
            corporateActionStatus=self.corporate_action_status,
            licenseStatus=self.license_receipt.status if self.license_receipt else "UNKNOWN",
            historicalAvailabilityStatus="NOT_PROVEN",
            methods=dict(
                manifestHash=self.manifest_hash,
                protocol=protocol,
                experimentVersion="retrospective-experiment-v1",
                strategyVersion="1",
                simulatorVersion=SIMULATOR_VERSION,
                metricsVersion=METRICS_VERSION,
                codeVersion=self.code_version,
                assumptions=assumptions,
                benchmarks=["flat-v1", "buy-hold-v1"],
                qualityPolicyVersion=QUALITY_POLICY_VERSION,
            ),
            resultLimitations=list(RESULT_LIMITATIONS),
            findingsCount=len(self.findings),
            revisionReferenceCount=len(self.revision_references),
        )


class _Assessor:
    def __init__(
        self, config, dataset, calendar_rows, trust, receipt, code_version, budget, checkpoint
    ):
        self.config, self.dataset, self.snapshot = config, dataset, dataset.snapshot
        self.calendar_rows, self.trust, self.receipt = calendar_rows, trust, receipt
        self.code_version, self.budget, self.checkpoint = code_version, budget, checkpoint
        self.started = monotonic()
        self.findings, self.references, self.folds, self.required = [], {}, [], []
        self.corporate_status, self.manifest_hash = "UNKNOWN", None
        self.complete = True
        self.unevaluated_dimensions = set()

    def check(self):
        self.checkpoint()
        if monotonic() - self.started >= self.budget.max_seconds:
            raise BacktestBudgetExceeded("reliability budget exhausted")

    def add(self, category, code, severity="BLOCKER", fold=None, period=None, refs=()):
        if len(self.findings) >= self.budget.max_findings:
            raise BacktestBudgetExceeded("reliability finding budget exhausted")
        self.findings.append(
            ReliabilityFinding(category, code, severity, fold, period, tuple(refs))
        )

    def reference(self, reference):
        self.references[(reference.kind, reference.reference_id)] = reference
        return reference

    def row_reference(self, row, kind):
        identity = row.get("evidence_id", row.get("observation_id", row.get("session_id")))
        digest = row.get("fingerprint", row.get("version")) or fingerprint(dict(row))
        reference = ReliabilityReference(
            kind,
            identity,
            digest,
            row.get("revision") or max(1, row["commit_version"]),
            row["ingested_at"],
            (row["start_at"], row["end_at"]) if "start_at" in row else None,
        )
        return self.reference(reference)

    def visible(self, row):
        return (
            row["commit_version"] <= self.snapshot.version
            and row["ingested_at"] <= self.snapshot.at
        )

    def resolve(self):
        heads, seen = {}, set()
        for bar in self.dataset.revisions:
            self.check()
            key = bar.identity, bar.local_revision
            if key in seen or bar.quality == QualityStatus.CONFLICT:
                self.add("INTEGRITY", "REVISION_CONFLICT")
            seen.add(key)
            head = heads.get(bar.identity)
            if head is None or bar.local_revision > head.local_revision:
                heads[bar.identity] = bar
        return tuple(sorted(heads.values(), key=revision_key))

    def counts(self, bars, windows, start, end, unknown):
        present = {
            (b.identity.bar_start, b.identity.bar_end)
            for b in bars
            if start <= b.identity.bar_start < end
        }
        expected = set(windows)
        if unknown:
            return ReliabilityBarCounts(None, len(present), None, 0)
        return ReliabilityBarCounts(
            len(expected), len(present & expected), len(expected - present), len(present - expected)
        )

    def scan_fold(self, index, fold, bars):
        self.check()
        rows = relevant_schedule_rows(
            self.calendar_rows, fold.training_start, fold.test_end, self.snapshot.at, self.snapshot
        )
        try:
            calendar, references = retrospective_calendar(rows, self.snapshot, self.trust)
        except (DatasetBlocked, ValueError):
            calendar = CalendarCoverage(None, None, (), "unknown")
            self.add("CALENDAR", "SESSION_CONFLICT", fold=index)
            references = ()
        for row in references:
            self.reference(
                ReliabilityReference(
                    "CALENDAR",
                    row.session_id,
                    row.version,
                    None,
                    row.ingested_at,
                    (row.opens_at, row.closes_at),
                )
            )
        count = warmup_count(self.config.strategy_id, self.config.parameters)
        planning = expected_windows(
            calendar,
            BarInterval.FIFTEEN_MINUTES,
            fold.training_start if count else fold.test_start,
            fold.test_end,
            fold.test_end,
        )
        prior = tuple(w for w in planning.windows if w[1] <= fold.test_start)
        test = tuple(w for w in planning.windows if w[0] >= fold.test_start)
        warm = prior[-count:] if count else ()
        warm_start = warm[0][0] if warm else fold.test_start
        unknown = bool(planning.unknown_dates)
        if unknown:
            self.add("CALENDAR", "CALENDAR_UNKNOWN", fold=index)
            # Missing session bounds prevent proving the required warm-up domain.
            self.unevaluated_dimensions.update(("INTEGRITY", "CORPORATE_ACTIONS", "FEED"))
        elif len(warm) < count or not test:
            self.add("INTEGRITY", "MISSING_BARS", fold=index)
        training = expected_windows(
            calendar,
            BarInterval.FIFTEEN_MINUTES,
            fold.training_start,
            fold.training_end,
            fold.training_end,
        )
        counts = (
            ("warmup", self.counts(bars, warm, warm_start, fold.test_start, unknown)),
            (
                "training",
                self.counts(
                    bars,
                    training.windows,
                    fold.training_start,
                    fold.training_end,
                    bool(training.unknown_dates),
                ),
            ),
            ("test", self.counts(bars, test, fold.test_start, fold.test_end, unknown)),
        )
        for name, value in counts:
            if name != "training" and value.missing:
                self.add("INTEGRITY", "MISSING_BARS", fold=index)
            if name != "training" and value.unexpected:
                self.add("INTEGRITY", "UNEXPECTED_BARS", fold=index)
        self.required.append((warm_start, fold.test_end))
        needed = tuple(b for b in bars if warm_start <= b.identity.bar_start < fold.test_end)
        for bar in needed:
            self.check()
            reference = self.reference(
                ReliabilityReference(
                    "BAR",
                    bar.revision_id,
                    bar.fingerprint,
                    bar.local_revision,
                    bar.ingested_at,
                    (bar.identity.bar_start, bar.identity.bar_end),
                )
            )
            identity = bar.identity
            if identity.instrument_id != self.config.instrument_id:
                self.add("INTEGRITY", "INSTRUMENT_UNRESOLVED", fold=index, refs=(reference,))
            if (
                identity.provider != "alpaca"
                or identity.feed != "iex"
                or identity.interval != BarInterval.FIFTEEN_MINUTES
            ):
                self.add("FEED", "MIXED_FEED", fold=index, refs=(reference,))
            if identity.adjustment != "raw":
                self.add("FEED", "MIXED_ADJUSTMENT", fold=index, refs=(reference,))
            if bar.quality != QualityStatus.VALID or validate_ohlcv(
                bar.open, bar.high, bar.low, bar.close, bar.volume
            ):
                self.add("INTEGRITY", "INVALID_OHLCV", fold=index, refs=(reference,))
        if not unknown:
            by_window = {(b.identity.bar_start, b.identity.bar_end): b for b in needed}
            for previous, current in zip(warm + test, (warm + test)[1:], strict=False):
                self.check()
                before, after = by_window.get(previous), by_window.get(current)
                if before is not None and after is not None and before.close > 0:
                    # Exact cross-multiplication avoids rounded 19.999... ->20% decisions.
                    with localcontext() as ctx:
                        ctx.prec = 80
                        discontinuity = abs(after.open - before.close) >= before.close * Decimal(
                            "0.20"
                        )
                    if discontinuity:
                        self.add(
                            "INTEGRITY",
                            "RAW_DISCONTINUITY",
                            fold=index,
                            period=(previous[0], current[1]),
                        )
        self.folds.append(
            ReliabilityFold(index, (warm_start, fold.test_start), counts, _EXPLORATORY)
        )

    def evidence_rows(self, rows, kind):
        result = []
        for row in rows:
            self.check()
            if not isinstance(row, Mapping):
                raise ValueError("local immutable evidence row mapping required")
            if not self.visible(row):
                continue
            if row["instrument_id"] != self.config.instrument_id or not any(
                row["start_at"] < end and row["end_at"] > start for start, end in self.required
            ):
                continue
            reference = self.row_reference(row, kind)
            result.append((row, reference))
        return result

    def scan_quality(self, rows):
        for row, reference in self.evidence_rows(rows, "QUALITY"):
            statement = row.get("statement", row)
            quality = statement.get("quality")
            if quality in ("INVALID", "CONFLICT", "MISSING_EXPECTED"):
                self.add(
                    "INTEGRITY",
                    "REVISION_CONFLICT"
                    if quality == "CONFLICT"
                    else "MISSING_BARS"
                    if quality == "MISSING_EXPECTED"
                    else "INVALID_OHLCV",
                    refs=(reference,),
                )

    def scan_actions(self, rows):
        records = self.evidence_rows(rows, "CORPORATE_ACTION")
        coverage, actions, claims = [], {}, []
        uncertain, dividend, structural, conflict = False, False, False, False
        seen = {}
        action_count = 0
        for row, reference in records:
            statement = row.get("statement")
            if not isinstance(statement, Mapping) or not isinstance(
                statement.get("actions"), (tuple, list)
            ):
                self.add("CORPORATE_ACTIONS", "CORPORATE_ACTION_CONFLICT", refs=(reference,))
                conflict = True
                continue
            key = row.get("source_id"), row.get("evidence_key"), row.get("revision", 1)
            if key in seen and seen[key] != row["fingerprint"]:
                conflict = True
            seen[key] = row["fingerprint"]
            conclusion, entries = statement.get("conclusion"), statement["actions"]
            if conclusion not in ("NO_ACTIONS", "ACTIONS_PRESENT", "UNKNOWN") or bool(entries) != (
                conclusion == "ACTIONS_PRESENT"
            ):
                conflict = True
            trusted = self.trust.permits_content(row["evidence_id"])
            if trusted and conclusion in ("NO_ACTIONS", "ACTIONS_PRESENT"):
                coverage.append((row["start_at"], row["end_at"]))
            else:
                uncertain = True
            claims.append((row["start_at"], row["end_at"], conclusion))
            for action in entries:
                self.check()
                action_count += 1
                if action_count > self.budget.max_rows:
                    raise BacktestBudgetExceeded("corporate action budget exhausted")
                try:
                    effective = utc_time(
                        datetime.fromisoformat(action["effectiveAt"].replace("Z", "+00:00"))
                    )
                    if not row["start_at"] <= effective < row["end_at"]:
                        raise ValueError("action outside evidence")
                    if not any(start <= effective < end for start, end in self.required):
                        continue
                    value = Decimal(action.get("amount", action.get("ratio", "1")))
                    if (
                        not value.is_finite()
                        or value < 0
                        or action["type"] == "SPLIT"
                        and value <= 0
                    ):
                        raise ValueError("invalid action value")
                    key = effective, action["type"]
                    if key in actions and actions[key] != value:
                        conflict = True
                    actions[key] = value
                    if action["type"] == "CASH_DIVIDEND" and action.get("currency") == "USD":
                        dividend = True
                    else:
                        structural = True
                except (ValueError, TypeError, KeyError):
                    conflict = True
        # Prefix maxima answer overlapping NO_ACTIONS claims without an actions² scan.
        no_actions = sorted((start, end) for start, end, claim in claims if claim == "NO_ACTIONS")
        starts, ends = [], []
        for start, end in no_actions:
            self.check()
            starts.append(start)
            ends.append(max(ends[-1], end) if ends else end)
        for effective, _action_type in actions:
            self.check()
            position = bisect_right(starts, effective) - 1
            if position >= 0 and effective < ends[position]:
                conflict = True

        def covered(start, end):
            edge = start
            for first, last in sorted(coverage):
                self.check()
                if first <= edge:
                    edge = max(edge, last)
            return edge >= end

        complete = not uncertain and all(covered(start, end) for start, end in self.required)
        if structural:
            self.add("CORPORATE_ACTIONS", "STRUCTURAL_CORPORATE_ACTION")
        if conflict:
            self.add("CORPORATE_ACTIONS", "CORPORATE_ACTION_CONFLICT")
        if dividend:
            self.add("CORPORATE_ACTIONS", "DIVIDENDS_EXCLUDED", "LIMITATION")
        if not complete:
            self.add("CORPORATE_ACTIONS", "CORPORATE_ACTIONS_UNKNOWN", "LIMITATION")
        self.corporate_status = (
            "CONFLICT"
            if conflict
            else "STRUCTURAL_ACTION"
            if structural
            else "UNKNOWN"
            if not complete
            else "DIVIDENDS_EXCLUDED"
            if dividend
            else "NO_ACTIONS"
        )

    def bind_manifest(self, manifest, qualities, actions):
        if manifest is None:
            self.add("INTEGRITY", "REFERENCE_MISSING")
            return
        if type(manifest) is not RetrospectiveManifest:
            raise ValueError("closed retrospective manifest required")
        manifest.__post_init__()
        if (manifest.config, manifest.snapshot, manifest.code_version, manifest.trust_context) != (
            self.config,
            self.snapshot,
            self.code_version,
            self.trust,
        ):
            self.add("INTEGRITY", "SNAPSHOT_BOUNDARY")
            return
        if self.receipt is None or manifest.license_receipt_fingerprint != self.receipt.fingerprint:
            self.add("LICENSE", "REFERENCE_MISSING")
            return
        for fold in manifest.datasets:
            self.check()
            self.dataset.restore(fold.revision_references)
            restore_retrospective_calendar(
                self.calendar_rows, fold.calendar_references, self.snapshot, self.trust
            )
            selected = materialize_retrospective_fold(
                self.dataset,
                fold.fold,
                self.config.instrument_id,
                warmup_count(self.config.strategy_id, self.config.parameters),
                self.calendar_rows,
                trust_context=self.trust,
                checkpoint=self.check,
            )
            if selected != fold:
                self.add("INTEGRITY", "REFERENCE_MISSING")
                return
        for bound, rows in (
            (manifest.quality_references, qualities),
            (manifest.corporate_action_references, actions),
        ):
            actual = {
                row["evidence_id"]: RetrospectiveEvidenceReference(
                    row["evidence_id"],
                    row["fingerprint"],
                    row["revision"],
                    row["commit_version"],
                    row["ingested_at"],
                )
                for row in rows
                if "evidence_id" in row
                and self.visible(row)
                and row["instrument_id"] == self.config.instrument_id
                and any(
                    row["start_at"] < end and row["end_at"] > start for start, end in self.required
                )
            }
            if set(bound) != set(actual.values()):
                self.add("INTEGRITY", "REFERENCE_MISSING")
                return
        self.manifest_hash = manifest.manifest_hash

    def result(self):
        findings = tuple(sorted(set(self.findings), key=lambda f: fingerprint(f)))
        dimensions = []
        for name in DIMENSIONS:
            items = tuple(f for f in findings if f.category == name)
            codes = tuple(sorted({f.code for f in items}))
            rating = (
                "UNKNOWN"
                if name == "HISTORICAL_AVAILABILITY"
                or name == "LICENSE"
                and (self.receipt is None or self.receipt.status == "UNKNOWN")
                else "BLOCKED"
                if name == "LICENSE" and self.receipt.status == "DENIED"
                else "NOT_EVALUATED"
                if not self.complete or name in self.unevaluated_dimensions
                else "BLOCKED"
                if any(f.severity == "BLOCKER" for f in items)
                else "UNKNOWN"
                if any(
                    f.code
                    in (
                        "CORPORATE_ACTIONS_UNKNOWN",
                        "HISTORICAL_AVAILABILITY_NOT_PROVEN",
                        "LICENSE_UNKNOWN",
                    )
                    for f in items
                )
                else "LIMITED"
                if items
                else "PASS"
            )
            dimensions.append((name, rating, codes))
        blocked = (
            not self.complete
            or self.manifest_hash is None
            or any(f.severity == "BLOCKER" for f in findings)
        )
        folds = tuple(
            ReliabilityFold(
                f.index, f.warmup_period, f.counts, "BLOCKED" if blocked else _EXPLORATORY
            )
            for f in self.folds
        )
        if not folds:
            unknown = ReliabilityBarCounts(None, 0, None, 0)
            folds = tuple(
                ReliabilityFold(
                    n,
                    (f.test_start, f.test_start),
                    tuple((name, unknown) for name in ("warmup", "training", "test")),
                    "BLOCKED",
                )
                for n, f in enumerate(self.config.folds)
            )
        return ReliabilityAssessment(
            self.config,
            self.snapshot,
            self.code_version,
            self.trust.version,
            self.manifest_hash,
            self.receipt,
            tuple(dimensions),
            folds,
            findings,
            tuple(sorted(self.references.values(), key=lambda r: (r.kind, str(r.reference_id)))),
            self.corporate_status
            if self.complete and "CORPORATE_ACTIONS" not in self.unevaluated_dimensions
            else "NOT_EVALUATED",
            self.complete,
        )


def assess_reliability(
    config,
    dataset,
    calendar_rows,
    *,
    trust_context,
    license_receipt,
    code_version,
    quality_rows=(),
    corporate_action_rows=(),
    manifest=None,
    budget=_DEFAULT_BUDGET,
    checkpoint=lambda: None,
):
    """Assess stored snapshots only; positive preview requires a complete manifest.

    quality_rows are quality_observations or evidence_revisions mappings;
    corporate_action_rows must be evidence_revisions with statement.actions.
    Calendar rows have the exact RRB02 read_schedule_rows shape. No port I/O.
    """
    if (
        type(config) is not ExperimentConfig
        or type(dataset) is not RetrospectiveDataset
        or type(dataset.policy) is not RetrospectivePolicyV1
        or type(trust_context) is not RetrospectiveTrustContext
        or trust_context.snapshot != dataset.snapshot
        or type(budget) is not ReliabilityBudget
    ):
        raise ValueError("closed snapshot-bound retrospective inputs required")
    if license_receipt is not None and type(license_receipt) is not ReliabilityLicenseReceipt:
        raise ValueError("closed immutable license receipt required")
    if not isinstance(code_version, str) or not re.fullmatch(r"[0-9a-f]{40}", code_version):
        raise ValueError("build fingerprint required")
    if any(
        type(rows) is not tuple for rows in (calendar_rows, quality_rows, corporate_action_rows)
    ):
        raise ValueError("immutable row collections required")
    assessor = _Assessor(
        config,
        dataset,
        calendar_rows,
        trust_context,
        license_receipt,
        code_version,
        budget,
        checkpoint,
    )
    try:
        assessor.check()
        if (
            sum(
                map(
                    len,
                    (
                        dataset.revisions,
                        calendar_rows,
                        quality_rows,
                        corporate_action_rows,
                        trust_context.records,
                        trust_context.evidence_references,
                    ),
                )
            )
            > budget.max_rows
        ):
            raise BacktestBudgetExceeded("combined reliability row budget exhausted")
        dataset.__post_init__()
        trust_context.__post_init__()
        if license_receipt is None:
            assessor.add("LICENSE", "TRUST_UNAVAILABLE")
        elif license_receipt.recorded_at != dataset.snapshot.at:
            assessor.add("LICENSE", "SNAPSHOT_BOUNDARY")
        else:
            assessor.reference(
                ReliabilityReference(
                    "LICENSE",
                    license_receipt.receipt_id,
                    license_receipt.fingerprint,
                    None,
                    license_receipt.recorded_at,
                )
            )
            if license_receipt.status == "DENIED":
                assessor.add("LICENSE", "LICENSE_DENIED")
            elif license_receipt.status == "UNKNOWN":
                assessor.add("LICENSE", "LICENSE_UNKNOWN", "LIMITATION")
        for record in trust_context.records:
            if record.revoked or any(value == "REJECTED" for _, value in record.dimensions):
                assessor.add("INTEGRITY", "TRUST_REVOKED")
        bars = assessor.resolve()
        for index, fold in enumerate(config.folds):
            assessor.scan_fold(index, fold, bars)
        assessor.scan_quality(quality_rows)
        assessor.scan_actions(corporate_action_rows)
        assessor.bind_manifest(manifest, quality_rows, corporate_action_rows)
        for category, code in (
            ("HISTORICAL_AVAILABILITY", "HISTORICAL_AVAILABILITY_NOT_PROVEN"),
            ("HISTORICAL_AVAILABILITY", "EX_POST_REVISIONS"),
            ("CALENDAR", "EX_POST_CALENDAR"),
            ("FEED", "IEX_PARTIAL_MARKET"),
            ("FEED", "SURVIVORSHIP_BIAS"),
        ):
            assessor.add(category, code, "LIMITATION")
        result = assessor.result()
        size = sum(
            len(part.encode("utf-8")) for part in json.JSONEncoder().iterencode(_canonical(result))
        )
        if size > budget.max_publication_bytes:
            raise BacktestBudgetExceeded("reliability publication budget exhausted")
        return result
    except BacktestBudgetExceeded:
        assessor.complete = False
        # Keep this diagnostic bounded even when the configured finding cap is exhausted.
        assessor.findings = [ReliabilityFinding("INTEGRITY", "BUDGET_EXHAUSTED", "BLOCKER")]
        assessor.references = {}
        return assessor.result()
    except DatasetBlocked as error:
        code = (
            "REFERENCE_MISSING"
            if "MISSING" in error.code
            else "SNAPSHOT_BOUNDARY"
            if "SNAPSHOT" in error.code
            else "REVISION_CONFLICT"
        )
        assessor.add("INTEGRITY", code)
        return assessor.result()
