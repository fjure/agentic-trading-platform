"""Bounded, network-free diagnosis over the materializer's immutable inputs.

No result is an execution permission. Local visibility is distinguished from
external publication; managed evidence always uses the supplied current trust
snapshot, including revocation decisions made after the data snapshot.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from types import MappingProxyType
from uuid import UUID, uuid5

from packages.backtesting.calendar import (
    calendar_reference,
    relevant_schedule_rows,
    schedule_windows,
)
from packages.backtesting.coverage import coverage_result, quality_result
from packages.backtesting.dataset import (
    MAX_REVISIONS,
    OUTCOME_DELAY,
    SIGNAL_DELAY,
    DatasetBlocked,
    FrozenDataset,
    Snapshot,
    input_candidates,
    outcome_candidates,
    plan_fold,
)
from packages.backtesting.models import BacktestBudgetExceeded, ExperimentConfig
from packages.backtesting.strategies import warmup_count
from packages.domain.models import utc_string, utc_time
from packages.research.calendar import NEW_YORK
from packages.research.models import BarIdentity, BarInterval, QualityStatus, content_fingerprint

POLICY_VERSION = "backtest-data-readiness-v1"
CODE_VERSION = "readiness-assessor-v1"
TRUST_POLICY_VERSION = "local-observation-trust-v1"
_NAMESPACE = UUID("02020202-0202-4202-8202-020202020202")
_DIMENSIONS = (
    "integrity",
    "sourceAuthenticity",
    "temporalAuthenticity",
    "completeness",
    "domainQuality",
)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


class Eligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"
    NOT_EVALUATED = "NOT_EVALUATED"


class TrustStatus(StrEnum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    NOT_EVALUATED = "NOT_EVALUATED"


def aggregate(states):
    """Required checks only; known rejection wins over unknown and unchecked."""
    values = frozenset(Eligibility(value) for value in states)
    for value in (Eligibility.BLOCKED, Eligibility.UNKNOWN, Eligibility.NOT_EVALUATED):
        if value in values:
            return value
    return Eligibility.ELIGIBLE


def _code(value, maximum=80):
    if not isinstance(value, str) or not re.fullmatch(
        rf"[A-Za-z0-9][A-Za-z0-9._:-]{{0,{maximum - 1}}}", value
    ):
        raise ValueError("safe opaque code required")
    return value


@dataclass(frozen=True)
class SafeReference:
    kind: str
    id: str
    revision: str | None = None
    fingerprint: str | None = None

    def __post_init__(self):
        if self.kind not in (
            "BAR_REVISION",
            "CALENDAR_REVISION",
            "QUALITY_REVISION",
            "CORPORATE_ACTIONS_REVISION",
            "SOURCE",
            "KEY",
            "TRUST_DECISION",
            "TEMPORAL_PROOF",
            "OBSERVATION_RECEIPT",
        ):
            raise ValueError("unknown reference kind")
        _code(self.id, 128)
        if self.revision is not None:
            _code(self.revision)
        if self.fingerprint is not None and not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("SHA256 reference required")

    def to_dict(self):
        return dict(
            kind=self.kind, id=self.id, revision=self.revision, fingerprint=self.fingerprint
        )


@dataclass(frozen=True)
class TrustResult:
    status: TrustStatus
    method: str
    rule_version: str
    reason: str
    references: tuple[SafeReference, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "status", TrustStatus(self.status))
        for value in (self.method, self.rule_version, self.reason):
            _code(value)
        if type(self.references) is not tuple or len(self.references) > 200:
            raise ValueError("bounded immutable trust references required")
        if any(not isinstance(ref, SafeReference) for ref in self.references):
            raise ValueError("typed safe references required")

    def to_dict(self):
        return dict(
            status=self.status.value,
            method=self.method,
            ruleVersion=self.rule_version,
            reason=self.reason,
            references=[r.to_dict() for r in self.references],
        )


def _local_result(dimension=0, references=()):
    # These are five DIFFERENT local statements, never provider publication or
    # external source certification. Managed imports use their stored results.
    method, reason = (
        ("NORMALIZED_CONTENT", "LOCAL_CONTENT_INTEGRITY"),
        ("DB_INGESTION_PROVENANCE", "LOCAL_SOURCE_PORT_ONLY"),
        ("DB_COMMITTED_VISIBILITY", "LOCAL_VISIBILITY_NOT_PROVIDER_PUBLICATION"),
        ("SHARED_CALENDAR_IDENTITIES", "EXPECTED_LOCAL_IDENTITIES_CHECKED"),
        ("SHARED_OHLC_QUALITY", "LOCAL_DOMAIN_RULES_CHECKED"),
    )[dimension]
    return TrustResult(TrustStatus.VERIFIED, method, TRUST_POLICY_VERSION, reason, references)


@dataclass(frozen=True)
class TrustDimensions:
    integrity: TrustResult = field(default_factory=_local_result)
    source_authenticity: TrustResult = field(default_factory=lambda: _local_result(1))
    temporal_authenticity: TrustResult = field(default_factory=lambda: _local_result(2))
    completeness: TrustResult = field(default_factory=lambda: _local_result(3))
    domain_quality: TrustResult = field(default_factory=lambda: _local_result(4))

    def values(self):
        return (
            self.integrity,
            self.source_authenticity,
            self.temporal_authenticity,
            self.completeness,
            self.domain_quality,
        )

    def to_dict(self):
        return {
            name: result.to_dict() for name, result in zip(_DIMENSIONS, self.values(), strict=True)
        }

    @classmethod
    def from_mapping(cls, value):
        results = []
        for name in _DIMENSIONS:
            item = value.get(
                name, value.get("semanticQuality") if name == "domainQuality" else None
            )
            if item is None:
                results.append(
                    TrustResult(
                        TrustStatus.UNKNOWN,
                        "NOT_AVAILABLE",
                        TRUST_POLICY_VERSION,
                        "EVIDENCE_MISSING",
                    )
                )
                continue
            references = []
            for reference in item.get("references", item.get("evidenceRefs", ())):
                if isinstance(reference, SafeReference):
                    references.append(reference)
                elif isinstance(reference, Mapping):
                    references.append(SafeReference(**reference))
                else:
                    references.append(SafeReference("TRUST_DECISION", str(reference)))
            results.append(
                TrustResult(
                    item["status"],
                    item["method"],
                    item["ruleVersion"],
                    item["reason"],
                    tuple(references),
                )
            )
        return cls(*results)


@dataclass(frozen=True)
class EvidenceTrust:
    reference: str
    kind: str
    dimensions: TrustDimensions
    revoked: bool = False
    ingested_at: datetime | None = None
    commit_version: int | None = None

    def __post_init__(self):
        _code(self.reference)
        if not isinstance(self.dimensions, TrustDimensions) or type(self.revoked) is not bool:
            raise ValueError("typed evidence trust required")
        if self.ingested_at is not None:
            utc_time(self.ingested_at)
        if self.commit_version is not None and (
            type(self.commit_version) is not int or not 0 <= self.commit_version <= 9007199254740991
        ):
            raise ValueError("typed commit version required")


@dataclass(frozen=True)
class TrustSnapshot:
    version: int = 0
    policy_version: str = TRUST_POLICY_VERSION
    records: tuple[EvidenceTrust, ...] = ()
    _index: Mapping = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if type(self.version) is not int or not 0 <= self.version <= 9007199254740991:
            raise ValueError("committed trust version required")
        _code(self.policy_version)
        if type(self.records) is not tuple or len(self.records) > MAX_REVISIONS:
            raise BacktestBudgetExceeded("trust metadata budget exhausted")
        if any(not isinstance(record, EvidenceTrust) for record in self.records):
            raise ValueError("typed immutable trust records required")
        if len({(r.kind, r.reference) for r in self.records}) != len(self.records):
            raise ValueError("trust snapshot must contain one current head per reference")
        object.__setattr__(
            self,
            "_index",
            MappingProxyType({(self._kind(r.kind), r.reference): r for r in self.records}),
        )

    @staticmethod
    def _kind(value):
        return {
            "CALENDAR": "CALENDAR_REVISION",
            "QUALITY": "QUALITY_REVISION",
            "CORPORATE_ACTION": "CORPORATE_ACTIONS_REVISION",
            "BAR": "BAR_REVISION",
        }.get(value, value)

    @classmethod
    def from_mapping(cls, value):
        return cls(
            value["version"],
            value["policy_version"],
            tuple(
                EvidenceTrust(
                    str(r["reference"]),
                    r["kind"],
                    TrustDimensions.from_mapping(r["dimensions"]),
                    r.get("revoked", False),
                    r.get("ingested_at"),
                    r.get("commit_version"),
                )
                for r in value["records"]
            ),
        )

    def record(self, kind, reference):
        return self._index.get((self._kind(kind), reference))

    def gate(self, kind, reference, knowledge_at):
        record = self.record(kind, reference)
        if record is None:
            # Existing local evidence semantics are not reinterpreted as external certification.
            return None
        if record.revoked:
            return "EVIDENCE_REVOKED"
        if record.ingested_at is not None and record.ingested_at > knowledge_at:
            return "TIME_UNPROVEN"
        if any(d.status == TrustStatus.REJECTED for d in record.dimensions.values()):
            return "TRUST_REJECTED"
        if record.dimensions.temporal_authenticity.status == TrustStatus.UNKNOWN:
            return "TIME_UNPROVEN"
        if any(d.status == TrustStatus.UNKNOWN for d in record.dimensions.values()):
            return "TRUST_UNKNOWN"
        if any(d.status == TrustStatus.NOT_EVALUATED for d in record.dimensions.values()):
            return "TRUST_NOT_EVALUATED"
        return None


@dataclass(frozen=True)
class TimelinessObservation:
    """A measured receipt, never reconstructed from interval or ingestion time."""

    identity: BarIdentity
    revision_id: UUID | None = None
    first_observed_at: datetime | None = None
    ingested_at: datetime | None = None
    commit_confirmed_at: datetime | None = None
    commit_version: int | None = None
    provider_published_at: datetime | None = None
    clock_status: str = "UNKNOWN"
    duplicate: bool = False
    receipt_id: UUID | None = None
    publication_evidence: SafeReference | None = None

    def __post_init__(self):
        if not isinstance(self.identity, BarIdentity):
            raise ValueError("typed bar identity required")
        for value in (
            self.first_observed_at,
            self.ingested_at,
            self.commit_confirmed_at,
            self.provider_published_at,
        ):
            if value is not None:
                utc_time(value)
        if self.clock_status not in ("COMPARABLE", "UNKNOWN", "INVALID"):
            raise ValueError("explicit clock comparability required")
        if type(self.duplicate) is not bool:
            raise ValueError("typed receipt required")
        if self.commit_version is not None and (
            type(self.commit_version) is not int or not 0 <= self.commit_version <= 9007199254740991
        ):
            raise ValueError("typed commit version required")
        if any(
            value is not None and not isinstance(value, UUID)
            for value in (self.revision_id, self.receipt_id)
        ):
            raise ValueError("typed receipt and revision UUID required")
        if self.provider_published_at is not None and not isinstance(
            self.publication_evidence, SafeReference
        ):
            raise ValueError("independent publication evidence reference required")


@dataclass(frozen=True)
class Finding:
    finding_id: UUID
    fold_index: int
    area: str
    category: str
    eligibility: Eligibility
    start: datetime
    end: datetime
    knowledge_at: datetime
    backtest_reason: str | None
    references: tuple[SafeReference, ...]
    trust: TrustDimensions

    def to_dict(self):
        return dict(
            findingId=str(self.finding_id),
            foldIndex=self.fold_index,
            area=self.area,
            category=self.category,
            eligibility=self.eligibility.value,
            backtestReason=_public_reason(self.backtest_reason),
            period=dict(start=utc_string(self.start), end=utc_string(self.end)),
            knowledgeAt=utc_string(self.knowledge_at),
            references=[r.to_dict() for r in self.references],
            trust=self.trust.to_dict(),
            observation=None,
            remedy="REVIEW_REQUIRED_EVIDENCE",
        )


def _public_reason(code):
    # Established materializer codes stay unchanged internally. The HTTP contract
    # has an older public enum; expose its existing mapped reason, never a new code.
    return {
        "BLOCKED_MISSING_DATA": "BLOCKED_DATA_UNAVAILABLE",
        "BLOCKED_OUTCOME_MISSING": "BLOCKED_OUTCOME_UNAVAILABLE",
        "BLOCKED_QUALITY": "BLOCKED_QUALITY_UNKNOWN",
        "BLOCKED_CALENDAR_CHANGED": "BLOCKED_CALENDAR_UNKNOWN",
        "BLOCKED_AMBIGUOUS_DATA": "BLOCKED_DATA_UNAVAILABLE",
    }.get(code, code)


@dataclass(frozen=True)
class Window:
    fold_index: int
    kind: str
    scope: str
    deadline_seconds: int
    bars: tuple[BarIdentity, ...]
    left_reason: str
    right_reason: str

    def to_dict(self, snapshot, trust):
        return dict(
            kind=self.kind,
            scope=self.scope,
            deadlineSeconds=self.deadline_seconds,
            firstBar=_identity(self.bars[0]),
            lastBar=_identity(self.bars[-1]),
            period=dict(
                start=utc_string(self.bars[0].bar_start), end=utc_string(self.bars[-1].bar_end)
            ),
            expectedBars=len(self.bars),
            foldIndex=self.fold_index,
            snapshot=_snapshot(snapshot, trust),
            policyVersion=POLICY_VERSION,
            leftBoundary=dict(reason=self.left_reason, references=[]),
            rightBoundary=dict(reason=self.right_reason, references=[]),
            references=[],
        )


def _identity(identity):
    return dict(
        instrumentId=str(identity.instrument_id),
        provider=identity.provider.upper(),
        feed=identity.feed,
        interval=identity.interval.value,
        adjustment=identity.adjustment.value,
        start=utc_string(identity.bar_start),
        end=utc_string(identity.bar_end),
    )


def _snapshot(snapshot, trust):
    return dict(
        snapshotAt=utc_string(snapshot.at),
        snapshotVersion=snapshot.version,
        trustVersion=trust.version,
        trustPolicyVersion=trust.policy_version,
    )


def maximal_windows(
    identities, accepted, reasons, *, fold_index, kind, scope, deadline_seconds, break_before=()
):
    """Consecutive EXPECTED calendar identities, including proved overnight gaps."""
    runs, begin = [], None
    for index in range(len(identities) + 1):
        okay = index < len(identities) and accepted[index]
        boundary = index in break_before
        if (not okay or boundary) and begin is not None:
            runs.append(
                Window(
                    fold_index,
                    kind,
                    scope,
                    deadline_seconds,
                    tuple(identities[begin:index]),
                    "RANGE_START"
                    if begin == 0
                    else "CALENDAR_UNKNOWN"
                    if begin in break_before
                    else reasons[begin - 1],
                    "CALENDAR_UNKNOWN"
                    if boundary
                    else "RANGE_END"
                    if index == len(identities)
                    else reasons[index],
                )
            )
            begin = None
        if okay and begin is None:
            begin = index
    return tuple(runs)


def latency_metric(pairs):
    """Exact nearest-rank; unavailable and invalid clock pairs never improve it."""
    samples, unknown, invalid = [], 0, 0
    for left, right, clock_status in pairs:
        if left is None or right is None or clock_status == "UNKNOWN":
            unknown += 1
        elif clock_status != "COMPARABLE" or right < left:
            invalid += 1
        else:
            difference = right - left
            samples.append(
                Decimal(difference.days * 86400 + difference.seconds)
                + Decimal(difference.microseconds) / Decimal(1000000)
            )
    samples.sort()

    def quantile(percent):
        return str(samples[(len(samples) * percent + 99) // 100 - 1]) if samples else None

    return dict(
        status="MEASURED" if samples else "UNKNOWN",
        p50Seconds=quantile(50),
        p95Seconds=quantile(95),
        maximumSeconds=str(samples[-1]) if samples else None,
        sampleCount=len(samples),
        unknownPairCount=unknown,
        invalidPairCount=invalid,
    )


def _ratio(numerator, denominator, basis="EXPECTED_BAR_IDENTITIES", unknown=0):
    return dict(
        status="N/A" if denominator == 0 else "UNKNOWN" if unknown else "MEASURED",
        numerator=numerator,
        denominator=denominator,
        denominatorBasis=basis,
        unknownCount=unknown,
    )


@dataclass(frozen=True)
class AssessmentBudget:
    max_findings: int = 10000
    max_publication_bytes: int = 12 * 1024 * 1024
    max_seconds: int = 300
    max_windows_per_kind: int = 200

    def __post_init__(self):
        for value, maximum in (
            (self.max_findings, 10000),
            (self.max_publication_bytes, 12 * 1024 * 1024),
            (self.max_seconds, 300),
            (self.max_windows_per_kind, 200),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("budget must be bounded by the protocol maximum")


@dataclass(frozen=True)
class AssessmentResult:
    config: ExperimentConfig
    snapshot: Snapshot
    trust_snapshot: TrustSnapshot
    status: str
    eligibility: Eligibility
    evaluation_complete: bool
    findings: tuple[Finding, ...]
    windows: tuple[Window, ...]
    metrics: tuple[dict, ...]
    trust: TrustDimensions
    failure_code: str | None = None
    policy_version: str = POLICY_VERSION
    code_version: str = CODE_VERSION

    def __post_init__(self):
        object.__setattr__(self, "metrics", tuple(_freeze(metric) for metric in self.metrics))

    @property
    def publication_complete(self):
        return self.status == "SUCCEEDED" and self.evaluation_complete

    def windows_dict(self):
        result = {
            name: []
            for name in (
                "barSignal",
                "barOutcome",
                "strictSignal",
                "strictOutcome",
                "completeExperiment",
            )
        }
        keys = {
            ("BAR_OBSERVATION", "SIGNAL"): "barSignal",
            ("BAR_OBSERVATION", "OUTCOME"): "barOutcome",
            ("STRICT_PIT", "SIGNAL"): "strictSignal",
            ("STRICT_PIT", "OUTCOME"): "strictOutcome",
            ("COMPLETE_EXPERIMENT", "SIGNAL_AND_OUTCOME"): "completeExperiment",
        }
        for window in self.windows:
            result[keys[window.kind, window.scope]].append(
                window.to_dict(self.snapshot, self.trust_snapshot)
            )
        result["complete"] = self.evaluation_complete
        return result

    def report_dict(self, assessment_id, created_at=None):
        if not self.publication_complete:
            raise ValueError("incomplete assessment cannot publish a report")
        utc_time(created_at or self.snapshot.at)
        summaries, overlaps = summarize_findings(self.findings)
        result = dict(
            assessmentId=str(assessment_id),
            requestHash=self.config.input_hash,
            policyVersion=self.policy_version,
            codeVersion=self.code_version,
            snapshot=_snapshot(self.snapshot, self.trust_snapshot),
            createdAt=utc_string(created_at or self.snapshot.at),
            eligibility=self.eligibility.value,
            evaluationComplete=True,
            trust=self.trust.to_dict(),
            findingCount=len(self.findings),
            categories=summaries,
            categoryOverlaps=overlaps,
            metrics=[_plain(metric) for metric in self.metrics],
            windows=self.windows_dict(),
            providerRights=dict(
                licenseStatus="UNKNOWN",
                providerVerification="UNVERIFIED",
                attestationRef=None,
                reason="RIGHTS_UNVERIFIED",
            ),
            limits=dict(
                maxQueued=32,
                maxFolds=12,
                maxDays=366,
                maxRevisionRows=100000,
                maxCalendarRows=100000,
                maxQualityRows=100000,
                maxCoverageRows=100000,
                maxAttemptSeconds=300,
                maxAttempts=3,
                maxFindings=10000,
                maxPublicationBytes=12582912,
                maxPageSize=200,
            ),
        )
        result["reportHash"] = content_fingerprint(result)
        return result


def summarize_findings(findings):
    grouped = defaultdict(list)
    for finding in findings:
        grouped[finding.category].append((finding.start, finding.end))

    def union(intervals):
        merged = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    disjoint = {category: union(intervals) for category, intervals in grouped.items()}
    summaries = [
        dict(
            category=category,
            findingCount=sum(f.category == category for f in findings),
            disjointIntervalCount=len(disjoint[category]),
        )
        for category in sorted(grouped)
    ]
    overlaps = []
    categories = sorted(grouped)
    for index, first in enumerate(categories):
        for second in categories[index + 1 :]:
            intersections = [
                (max(a, c), min(b, d))
                for a, b in disjoint[first]
                for c, d in disjoint[second]
                if a < d and c < b
            ]
            if intersections:
                overlaps.append(
                    dict(first=first, second=second, overlapIntervalCount=len(union(intersections)))
                )
    return summaries, overlaps


class _Assessor:
    def __init__(
        self,
        config,
        frozen,
        calendar,
        quality,
        coverage,
        trust,
        observations,
        budget,
        checkpoint,
        clock,
    ):
        self.config, self.frozen = config, frozen
        self.calendar, self.quality, self.coverage = calendar, quality, coverage
        self.trust, self.observations = trust, observations
        self.budget, self.checkpoint, self.clock = budget, checkpoint, clock
        self.started = clock()
        self.findings, self.windows, self.metrics = [], [], []
        self.finding_ids = set()
        self.used_trust = {}
        self.by_start = defaultdict(list)
        for bar in frozen.revisions:
            if (
                bar.identity.instrument_id == config.instrument_id
                and bar.identity.provider == "alpaca"
                and bar.identity.feed == "iex"
            ):
                self.by_start[bar.identity.bar_start].append(bar)

    def check(self):
        self.checkpoint()
        if self.clock() - self.started >= self.budget.max_seconds:
            raise BacktestBudgetExceeded("assessment deadline exhausted")
        if len(self.findings) > self.budget.max_findings:
            raise BacktestBudgetExceeded("finding publication budget exhausted")

    def gate(self, kind, reference, knowledge):
        record = self.trust.record(kind, reference)
        if record is not None:
            self.used_trust[kind, reference] = record
        return self.trust.gate(kind, reference, knowledge)

    def add(
        self,
        fold_index,
        area,
        category,
        eligibility,
        start,
        end,
        knowledge,
        code=None,
        references=(),
        trust=None,
    ):
        self.check()
        if len(references) > 200:
            raise BacktestBudgetExceeded("finding reference budget exhausted")
        identity = content_fingerprint(
            (
                self.config.input_hash,
                self.frozen.snapshot,
                self.trust.version,
                fold_index,
                area,
                category,
                start,
                end,
                knowledge,
                references,
            )
        )
        finding = Finding(
            uuid5(_NAMESPACE, identity),
            fold_index,
            area,
            category,
            Eligibility(eligibility),
            start,
            end,
            knowledge,
            code,
            tuple(references),
            trust or TrustDimensions(),
        )
        if finding.finding_id not in self.finding_ids:
            self.findings.append(finding)
            self.finding_ids.add(finding.finding_id)
        self.check()

    def trust_finding(self, index, area, reason, start, end, knowledge, code, refs):
        if reason is None:
            return False
        category = (
            "EVIDENCE_REVOKED"
            if reason == "EVIDENCE_REVOKED"
            else "NOT_EVALUATED"
            if reason == "TRUST_NOT_EVALUATED"
            else "TIME_UNPROVEN"
        )
        eligibility = (
            Eligibility.BLOCKED
            if reason in ("EVIDENCE_REVOKED", "TRUST_REJECTED")
            else Eligibility.UNKNOWN
        )
        if reason == "TRUST_NOT_EVALUATED":
            eligibility = Eligibility.NOT_EVALUATED
        for reference in refs:
            self.gate(reference.kind, reference.id, knowledge)
        dimensions = next(
            (
                self.trust.record(ref.kind, ref.id).dimensions
                for ref in refs
                if self.trust.record(ref.kind, ref.id) is not None
            ),
            TrustDimensions(),
        )
        self.add(index, area, category, eligibility, start, end, knowledge, code, refs, dimensions)
        return True

    def missing(self, index, area, start, end, knowledge, code):
        invalid = tuple(
            bar
            for bar in self.by_start[start]
            if bar.quality != QualityStatus.VALID
            and max(bar.ingested_at, bar.available_at) <= knowledge
        )
        if invalid:
            self.add(
                index,
                area,
                "QUALITY_REJECTED",
                Eligibility.BLOCKED,
                start,
                end,
                knowledge,
                "BLOCKED_QUALITY",
                tuple(
                    SafeReference(
                        "BAR_REVISION",
                        str(bar.revision_id),
                        str(bar.local_revision),
                        bar.fingerprint,
                    )
                    for bar in invalid
                ),
            )
        revisions = tuple(
            bar
            for bar in self.by_start[start]
            if bar.quality == QualityStatus.VALID and bar.identity.bar_end <= end
        )
        late = tuple(bar for bar in revisions if max(bar.available_at, bar.ingested_at) > knowledge)
        category = (
            "OBSERVED_TOO_LATE"
            if late
            else ("WARMUP_MISSING" if area == "WARMUP" else "BAR_MISSING")
        )
        refs = tuple(
            SafeReference("BAR_REVISION", str(b.revision_id), str(b.local_revision), b.fingerprint)
            for b in late[:200]
        )
        self.add(index, area, category, Eligibility.BLOCKED, start, end, knowledge, code, refs)

    def explain_coverage(self, index, proof, start, end, knowledge):
        references = tuple(SafeReference("CORPORATE_ACTIONS_REVISION", r) for r in proof.references)
        if proof.state in ("UNKNOWN", "NEGATIVE", "CONFLICT"):
            unknown = proof.state == "UNKNOWN"
            self.add(
                index,
                "CORPORATE_ACTIONS",
                "CORPORATE_ACTIONS_UNKNOWN" if unknown else "CORPORATE_ACTIONS_CONFLICT",
                Eligibility.UNKNOWN if unknown else Eligibility.BLOCKED,
                start,
                end,
                knowledge,
                "BLOCKED_CORPORATE_ACTIONS_UNKNOWN",
                references,
            )
        elif proof.proof is None:
            self.trust_finding(
                index,
                "CORPORATE_ACTIONS",
                proof.state,
                start,
                end,
                knowledge,
                "BLOCKED_CORPORATE_ACTIONS_UNKNOWN",
                references,
            )

    def fold(self, fold_index, fold):
        self.check()
        required = warmup_count(self.config.strategy_id, self.config.parameters)
        try:
            plan = plan_fold(self.frozen, fold, required, self.calendar, self.check, strict=False)
        except DatasetBlocked as error:
            calendar_unknown = error.code == "BLOCKED_CALENDAR_UNKNOWN"
            self.add(
                fold_index,
                "CALENDAR" if calendar_unknown else "SIGNAL",
                "CALENDAR_UNKNOWN" if calendar_unknown else "BAR_MISSING",
                Eligibility.UNKNOWN if calendar_unknown else Eligibility.BLOCKED,
                fold.test_start,
                fold.test_end,
                fold.test_start,
                error.code,
            )
            for row in relevant_schedule_rows(
                self.calendar,
                fold.test_start,
                fold.test_end,
                self.frozen.snapshot.at,
                self.frozen.snapshot,
            ):
                reference = SafeReference("CALENDAR_REVISION", calendar_reference(row))
                reason = self.gate(reference.kind, reference.id, self.frozen.snapshot.at)
                reason = reason or (
                    row.get("managed_trust_state")
                    if row.get("managed_trust_state") not in (None, "VERIFIED")
                    else None
                )
                self.trust_finding(
                    fold_index,
                    "CALENDAR",
                    reason,
                    fold.test_start,
                    fold.test_end,
                    fold.test_start,
                    "BLOCKED_CALENDAR_UNKNOWN",
                    (reference,),
                )
            proof = coverage_result(
                self.coverage,
                self.config.instrument_id,
                fold.training_start if required else fold.test_start,
                fold.test_end,
                fold.test_start,
                self.frozen.snapshot,
                self.gate,
            )
            self.explain_coverage(
                fold_index,
                proof,
                fold.training_start if required else fold.test_start,
                fold.test_end,
                fold.test_start,
            )
            for area in ("WARMUP", "SIGNAL", "OUTCOME", "QUALITY"):
                self.add(
                    fold_index,
                    area,
                    "NOT_EVALUATED",
                    Eligibility.NOT_EVALUATED,
                    fold.test_start,
                    fold.test_end,
                    fold.test_start,
                )
            self.metrics.append(
                self.metric(fold, (), (), (), (), (), unknown_calendar=calendar_unknown)
            )
            return
        snapshot = self.frozen.snapshot
        history = tuple(w[0] for w in plan.warm_windows + plan.test_windows)
        expected = tuple(w[0] for w in plan.test_windows)
        signal_sweep, outcome_sweep = self.frozen.sweep(), self.frozen.sweep()
        bar_signal, bar_outcome, strict_signal, strict_outcome = [], [], [], []
        signal_reasons, outcome_reasons = [], []
        identities = tuple(
            BarIdentity(
                self.config.instrument_id, "alpaca", "iex", BarInterval.FIFTEEN_MINUTES, start, end
            )
            for start, end in plan.test_windows
        )
        for day in plan.unknown_dates:
            opening = datetime.combine(day, datetime.min.time(), NEW_YORK).astimezone(
                fold.test_start.tzinfo
            )
            closing = datetime.combine(
                day + timedelta(days=1), datetime.min.time(), NEW_YORK
            ).astimezone(fold.test_start.tzinfo)
            scope_start = fold.training_start if required else fold.test_start
            if opening < fold.test_end and closing > scope_start:
                self.add(
                    fold_index,
                    "CALENDAR",
                    "CALENDAR_UNKNOWN",
                    Eligibility.UNKNOWN,
                    max(opening, scope_start),
                    min(closing, fold.test_end),
                    plan.test_windows[0][1] + SIGNAL_DELAY,
                    "BLOCKED_CALENDAR_UNKNOWN",
                )
        if plan.warmup_missing:
            self.add(
                fold_index,
                "WARMUP",
                "NOT_EVALUATED" if plan.unknown_dates else "WARMUP_MISSING",
                Eligibility.NOT_EVALUATED if plan.unknown_dates else Eligibility.BLOCKED,
                fold.training_start,
                fold.test_start,
                plan.test_windows[0][1] + SIGNAL_DELAY,
                "BLOCKED_WARMUP_MISSING",
            )
        for index, (start, end) in enumerate(plan.test_windows):
            self.check()
            knowledge, outcome_at = end + SIGNAL_DELAY, end + OUTCOME_DELAY
            _, planned_now = schedule_windows(
                self.calendar, start, fold.test_end, knowledge, snapshot
            )
            calendar_ok = not planned_now.unknown_dates and (start, end) in planned_now.windows
            if not calendar_ok:
                self.add(
                    fold_index,
                    "CALENDAR",
                    "CALENDAR_UNKNOWN",
                    Eligibility.UNKNOWN,
                    start,
                    end,
                    knowledge,
                    "BLOCKED_CALENDAR_UNKNOWN",
                )
            for row in relevant_schedule_rows(
                self.calendar, start, fold.test_end, knowledge, snapshot
            ):
                ref = SafeReference("CALENDAR_REVISION", calendar_reference(row))
                if self.trust_finding(
                    fold_index,
                    "CALENDAR",
                    self.gate(ref.kind, ref.id, knowledge),
                    start,
                    end,
                    knowledge,
                    "BLOCKED_CALENDAR_UNKNOWN",
                    (ref,),
                ):
                    calendar_ok = False
            target = next((w[0] for w in planned_now.windows if w[0] >= knowledge), None)
            changed = target is not None and target not in expected
            if changed:
                self.add(
                    fold_index,
                    "CALENDAR",
                    "CALENDAR_CHANGED",
                    Eligibility.BLOCKED,
                    start,
                    end,
                    knowledge,
                    "BLOCKED_CALENDAR_CHANGED",
                )
            proof = coverage_result(
                self.coverage,
                self.config.instrument_id,
                plan.warm_start,
                fold.test_end,
                knowledge,
                snapshot,
                self.gate,
            )
            self.explain_coverage(fold_index, proof, plan.warm_start, fold.test_end, knowledge)
            quality = quality_result(
                self.quality, plan.warm_start, end, knowledge, snapshot, self.gate
            )
            outcome_quality = quality_result(
                self.quality, start, end, outcome_at, snapshot, self.gate
            )
            for area, checked, at in (
                ("SIGNAL", quality, knowledge),
                ("OUTCOME", outcome_quality, outcome_at),
            ):
                if not checked.okay:
                    refs = tuple(SafeReference("QUALITY_REVISION", r) for r in checked.references)
                    if checked.state == "REJECTED":
                        self.add(
                            fold_index,
                            area,
                            "QUALITY_REJECTED",
                            Eligibility.BLOCKED,
                            start,
                            end,
                            at,
                            "BLOCKED_QUALITY",
                            refs,
                        )
                    else:
                        self.trust_finding(
                            fold_index, area, checked.state, start, end, at, "BLOCKED_QUALITY", refs
                        )
            selected, ambiguous = input_candidates(
                signal_sweep.visible(end, knowledge), self.config.instrument_id, plan.warm_start
            )
            if ambiguous:
                self.add(
                    fold_index,
                    "SIGNAL",
                    "AMBIGUOUS_DATA",
                    Eligibility.BLOCKED,
                    start,
                    end,
                    knowledge,
                    "BLOCKED_AMBIGUOUS_DATA",
                )
            needed = () if plan.warmup_missing else history[index : index + required + 1]
            inputs_ok = not plan.warmup_missing and not ambiguous
            for needed_start in needed:
                chosen = selected.get(needed_start)
                area = "WARMUP" if needed_start < fold.test_start else "SIGNAL"
                if chosen is None:
                    self.missing(
                        fold_index,
                        area,
                        needed_start,
                        needed_start + timedelta(minutes=15),
                        knowledge,
                        "BLOCKED_WARMUP_MISSING" if area == "WARMUP" else "BLOCKED_MISSING_DATA",
                    )
                    inputs_ok = False
                else:
                    ref = SafeReference(
                        "BAR_REVISION",
                        str(chosen.revision_id),
                        str(chosen.local_revision),
                        chosen.fingerprint,
                    )
                    if self.trust_finding(
                        fold_index,
                        area,
                        self.gate(ref.kind, ref.id, knowledge),
                        needed_start,
                        chosen.identity.bar_end,
                        knowledge,
                        "BLOCKED_MISSING_DATA",
                        (ref,),
                    ):
                        inputs_ok = False
            eligible = outcome_candidates(
                outcome_sweep.visible(end, outcome_at), self.config.instrument_id, start
            )
            outcome_ok = len(eligible) == 1
            if not outcome_ok:
                self.missing(
                    fold_index, "OUTCOME", start, end, outcome_at, "BLOCKED_OUTCOME_MISSING"
                )
            else:
                ref = SafeReference(
                    "BAR_REVISION",
                    str(eligible[0].revision_id),
                    str(eligible[0].local_revision),
                    eligible[0].fingerprint,
                )
                if self.trust_finding(
                    fold_index,
                    "OUTCOME",
                    self.gate(ref.kind, ref.id, outcome_at),
                    start,
                    end,
                    outcome_at,
                    "BLOCKED_OUTCOME_MISSING",
                    (ref,),
                ):
                    outcome_ok = False
            own_quality = quality_result(self.quality, start, end, knowledge, snapshot, self.gate)
            _, own_plan = schedule_windows(self.calendar, start, end, knowledge, snapshot)
            own_calendar = not own_plan.unknown_dates and (start, end) in own_plan.windows
            own = selected.get(start)
            own_ok = (
                own is not None
                and not ambiguous
                and own_calendar
                and own_quality.okay
                and self.gate("BAR_REVISION", str(own.revision_id), knowledge) is None
            )
            # Outcome's own then-visible calendar must independently prove the bar.
            _, outcome_plan = schedule_windows(self.calendar, start, end, outcome_at, snapshot)
            outcome_calendar = (
                not outcome_plan.unknown_dates and (start, end) in outcome_plan.windows
            )
            bar_signal.append(own_ok)
            bar_outcome.append(outcome_ok and outcome_quality.okay and outcome_calendar)
            strict_signal.append(
                inputs_ok
                and quality.okay
                and calendar_ok
                and not changed
                and proof.proof is not None
                and not plan.unknown_dates
            )
            strict_outcome.append(
                bar_outcome[-1] and proof.proof is not None and not plan.unknown_dates
            )
            signal_reasons.append(
                "CALENDAR_UNKNOWN"
                if not calendar_ok
                else "QUALITY_REJECTED"
                if not quality.okay
                else "BAR_MISSING"
                if own is None
                else "REQUIRED_EVIDENCE_BLOCKED"
            )
            outcome_reasons.append(
                "CALENDAR_UNKNOWN"
                if not outcome_calendar
                else "QUALITY_REJECTED"
                if not outcome_quality.okay
                else "BAR_MISSING"
                if not outcome_ok
                else "REQUIRED_EVIDENCE_BLOCKED"
            )
        for flags, kind, scope, deadline, reasons in (
            (bar_signal, "BAR_OBSERVATION", "SIGNAL", 60, signal_reasons),
            (bar_outcome, "BAR_OBSERVATION", "OUTCOME", 300, outcome_reasons),
            (strict_signal, "STRICT_PIT", "SIGNAL", 60, signal_reasons),
            (strict_outcome, "STRICT_PIT", "OUTCOME", 300, outcome_reasons),
        ):
            breaks = tuple(
                index
                for index in range(1, len(identities))
                if any(
                    identities[index - 1].bar_end.astimezone(NEW_YORK).date()
                    <= day
                    <= identities[index].bar_start.astimezone(NEW_YORK).date()
                    for day in plan.unknown_dates
                )
            )
            self.windows.extend(
                maximal_windows(
                    identities,
                    flags,
                    reasons,
                    fold_index=fold_index,
                    kind=kind,
                    scope=scope,
                    deadline_seconds=deadline,
                    break_before=breaks,
                )
            )
        complete = all(strict_signal) and all(strict_outcome) and not plan.unknown_dates
        if complete:
            self.windows.extend(
                maximal_windows(
                    identities,
                    [True] * len(identities),
                    signal_reasons,
                    fold_index=fold_index,
                    kind="COMPLETE_EXPERIMENT",
                    scope="SIGNAL_AND_OUTCOME",
                    deadline_seconds=60,
                )
            )
        self.metrics.append(
            self.metric(
                fold,
                identities,
                bar_signal,
                bar_outcome,
                signal_reasons,
                outcome_reasons,
                unknown_calendar=sum(
                    fold.test_start.astimezone(NEW_YORK).date()
                    <= day
                    <= (fold.test_end - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
                    for day in plan.unknown_dates
                ),
            )
        )

    def metric(
        self,
        fold,
        identities,
        signal,
        outcome,
        signal_reasons,
        outcome_reasons,
        unknown_calendar=False,
    ):
        del signal_reasons, outcome_reasons
        expected = len(identities)
        by_identity = {
            identity: tuple(
                bar
                for bar in self.by_start[identity.bar_start]
                if bar.identity == identity and bar.quality == QualityStatus.VALID
            )
            for identity in identities
        }
        bars = tuple(bar for revisions in by_identity.values() for bar in revisions)
        revision_ids = {bar.revision_id for bar in bars}
        observed = {identity for identity, revisions in by_identity.items() if revisions}
        receipts = tuple(
            o
            for o in self.observations
            if o.identity in by_identity
            and o.revision_id in revision_ids
            and (o.ingested_at is None or o.ingested_at <= self.frozen.snapshot.at)
            and (o.first_observed_at is None or o.first_observed_at <= self.frozen.snapshot.at)
            and (o.commit_version is None or o.commit_version <= self.frozen.snapshot.version)
        )
        receipt_by_revision = defaultdict(list)
        for receipt in receipts:
            receipt_by_revision[receipt.revision_id].append(receipt)
        first = {
            revision: min(
                items,
                key=lambda o: (
                    o.first_observed_at is None,
                    o.first_observed_at or self.frozen.snapshot.at,
                    str(o.receipt_id),
                ),
            )
            for revision, items in receipt_by_revision.items()
        }

        def confirmation(receipt):
            if receipt is None or receipt.commit_confirmed_at is None:
                return None
            return (
                receipt.commit_confirmed_at
                if receipt.commit_confirmed_at <= self.frozen.snapshot.at
                else None
            )

        def pairs(kind):
            for bar in bars:
                receipt = first.get(bar.revision_id)
                status = receipt.clock_status if receipt else "UNKNOWN"
                if receipt and receipt.ingested_at != bar.ingested_at:
                    status = "INVALID" if receipt.ingested_at is not None else "UNKNOWN"
                if kind == "reception":
                    yield (
                        bar.identity.bar_end,
                        receipt.first_observed_at if receipt else None,
                        status,
                    )
                elif kind == "ingestion":
                    yield receipt.first_observed_at if receipt else None, bar.ingested_at, status
                else:
                    yield bar.ingested_at, confirmation(receipt), status

        received, usable, usable_unknown, received_unknown, late = [], [], [], [], []
        for delay, flags in ((SIGNAL_DELAY, signal), (OUTCOME_DELAY, outcome)):
            reception_count = usable_count = unknown_count = unknown_reception = late_count = 0
            for index, identity in enumerate(identities):
                cutoff = identity.bar_end + delay
                revisions = by_identity[identity]
                entries = tuple(
                    o for bar in revisions for o in receipt_by_revision[bar.revision_id]
                )
                reception_count += any(
                    o.clock_status == "COMPARABLE"
                    and o.first_observed_at is not None
                    and identity.bar_end <= o.first_observed_at <= cutoff
                    for o in entries
                )
                unknown_reception += bool(revisions) and not any(
                    o.clock_status == "COMPARABLE"
                    and o.first_observed_at is not None
                    and o.first_observed_at >= identity.bar_end
                    for o in entries
                )
                visible = tuple(
                    bar for bar in revisions if max(bar.ingested_at, bar.available_at) <= cutoff
                )
                head = max(visible, key=lambda bar: bar.local_revision) if visible else None
                metadata = receipt_by_revision[head.revision_id] if head else ()
                measured_visible = bool(head) and any(
                    o.clock_status == "COMPARABLE"
                    and o.commit_version == head.commit_version
                    and o.ingested_at == head.ingested_at
                    and confirmation(o) is not None
                    and head.ingested_at <= confirmation(o) <= cutoff
                    for o in metadata
                )
                # Commit measurement never changes the existing materializer rule.
                # It can only support the measured quota or leave it UNKNOWN.
                if flags[index]:
                    usable_count += measured_visible
                    unknown_count += not measured_visible
                late_count += bool(revisions) and not visible
            received.append(reception_count)
            usable.append(usable_count)
            usable_unknown.append(unknown_count)
            received_unknown.append(unknown_reception)
            late.append(late_count)
        if isinstance(unknown_calendar, int) and not isinstance(unknown_calendar, bool):
            unknown_days = unknown_calendar
        elif unknown_calendar:
            first_day = fold.test_start.astimezone(NEW_YORK).date()
            last_day = (fold.test_end - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
            unknown_days = (last_day - first_day).days + 1
        else:
            unknown_days = 0
        calendar_version = (
            content_fingerprint(
                tuple(
                    sorted(
                        {
                            row["version"]
                            for row in relevant_schedule_rows(
                                self.calendar,
                                fold.test_start,
                                fold.test_end,
                                self.frozen.snapshot.at,
                                self.frozen.snapshot,
                            )
                        }
                    )
                )
            )
            if self.calendar
            else None
        )
        corrected = sum(
            len({bar.revision_id for bar in revisions}) > 1 for revisions in by_identity.values()
        )
        return dict(
            sourceId="alpaca",
            instrumentId=str(self.config.instrument_id),
            feed="iex",
            interval="15Min",
            adjustment="raw",
            calendarVersion=calendar_version,
            period=dict(start=utc_string(fold.test_start), end=utc_string(fold.test_end)),
            snapshot=_snapshot(self.frozen.snapshot, self.trust),
            expectedBars=expected,
            observedBars=len(observed),
            unknownCalendarDays=unknown_days,
            receptionMinusClose=latency_metric(pairs("reception")),
            ingestionMinusReception=latency_metric(pairs("ingestion")),
            confirmationMinusIngestion=latency_metric(pairs("confirmation")),
            receivedBySignalDeadline=_ratio(received[0], expected, unknown=received_unknown[0]),
            receivedByOutcomeDeadline=_ratio(received[1], expected, unknown=received_unknown[1]),
            usableBySignalDeadline=_ratio(usable[0], expected, unknown=usable_unknown[0]),
            usableByOutcomeDeadline=_ratio(usable[1], expected, unknown=usable_unknown[1]),
            missingBars=_ratio(expected - len(observed), expected),
            lateForSignal=_ratio(late[0], expected),
            lateForOutcome=_ratio(late[1], expected),
            correctedBars=_ratio(corrected, len(observed), "OBSERVED_BAR_IDENTITIES"),
            duplicateResponses=_ratio(
                sum(o.duplicate for o in receipts), len(receipts), "USABLE_BAR_OCCURRENCES"
            ),
            limitations=[
                "RIGHTS_UNVERIFIED",
                "PROVIDER_LATENCY_UNVERIFIED",
                "PROVIDER_PUBLICATION_UNKNOWN",
                "CROSS_CATEGORY_OVERLAP_POSSIBLE",
            ],
        )

    def result(self):
        dimensions = []
        local_reference = SafeReference(
            "SOURCE",
            f"local-snapshot-{self.frozen.snapshot.version}",
            fingerprint=content_fingerprint((self.config.input_hash, self.frozen.snapshot)),
        )
        for index, _ in enumerate(_DIMENSIONS):
            results = [record.dimensions.values()[index] for record in self.used_trust.values()]
            if not results:
                dimensions.append(_local_result(index, (local_reference,)))
                continue
            priority = {
                TrustStatus.VERIFIED: 0,
                TrustStatus.NOT_EVALUATED: 1,
                TrustStatus.UNKNOWN: 2,
                TrustStatus.REJECTED: 3,
            }
            dimensions.append(max(results, key=lambda r: priority[r.status]))
        if any(record.revoked for record in self.used_trust.values()):
            dimensions[1] = TrustResult(
                TrustStatus.REJECTED,
                "CURRENT_TRUST_DECISIONS",
                self.trust.policy_version,
                "EVIDENCE_REVOKED",
            )
        # Completeness and domain-quality are content checks, independent of signatures.
        for dimension, categories in (
            (3, {"BAR_MISSING", "WARMUP_MISSING"}),
            (4, {"QUALITY_REJECTED", "AMBIGUOUS_DATA"}),
        ):
            rejected = next((f for f in self.findings if f.category in categories), None)
            if rejected:
                dimensions[dimension] = TrustResult(
                    TrustStatus.REJECTED,
                    "SHARED_BACKTEST_RULES",
                    POLICY_VERSION,
                    rejected.category,
                    rejected.references,
                )
        if any(f.category == "CALENDAR_UNKNOWN" for f in self.findings):
            dimensions[3] = TrustResult(
                TrustStatus.UNKNOWN, "OBSERVED_CALENDAR", POLICY_VERSION, "CALENDAR_UNKNOWN"
            )
        trust = TrustDimensions(*dimensions)
        eligibility = aggregate(f.eligibility for f in self.findings)
        ordered = tuple(
            sorted(
                self.findings,
                key=lambda f: (f.fold_index, f.area, f.start, f.category, f.finding_id),
            )
        )
        for kind, scope in {(w.kind, w.scope) for w in self.windows}:
            if (
                sum(w.kind == kind and w.scope == scope for w in self.windows)
                > self.budget.max_windows_per_kind
            ):
                raise BacktestBudgetExceeded("window publication budget exhausted")
        result = AssessmentResult(
            self.config,
            self.frozen.snapshot,
            self.trust,
            "SUCCEEDED",
            eligibility,
            True,
            ordered,
            tuple(self.windows),
            tuple(self.metrics),
            trust,
        )
        publication = dict(
            report=result.report_dict(_NAMESPACE), findings=[f.to_dict() for f in ordered]
        )
        if (
            len(json.dumps(publication, separators=(",", ":")).encode())
            > self.budget.max_publication_bytes
        ):
            raise BacktestBudgetExceeded("byte publication budget exhausted")
        self.check()
        return result


def assess(
    config,
    frozen,
    calendar_rows,
    quality_rows=(),
    coverage_proofs=(),
    *,
    trust=None,
    observations=(),
    budget=None,
    checkpoint=lambda: None,
    clock=monotonic,
):
    """Assess an immutable experiment; no acquisition, simulation or persistence.

    ``trust`` is the CURRENT trust snapshot, not one reconstructed at the old
    market event time. RDR05 passes its ``gate`` to materialize_fold as well.
    Budget exhaustion returns FAILED/UNKNOWN with no publishable partial rows.
    Validation errors remain caller errors and cannot become a positive report.
    """
    if not isinstance(config, ExperimentConfig) or not isinstance(frozen, FrozenDataset):
        raise ValueError("typed experiment and frozen dataset required")
    if config.folds[-1].test_end > frozen.snapshot.at:
        raise ValueError("future evaluation window")
    trust = trust or TrustSnapshot()
    budget = budget or AssessmentBudget()
    if not isinstance(trust, TrustSnapshot) or not isinstance(budget, AssessmentBudget):
        raise ValueError("typed trust snapshot and budget required")
    inputs = (calendar_rows, quality_rows, coverage_proofs, observations)
    if any(type(rows) is not tuple for rows in inputs):
        raise ValueError("immutable snapshot-bound input tuples required")
    if any(not isinstance(o, TimelinessObservation) for o in observations):
        raise ValueError("typed observation metadata required")
    try:
        if any(len(rows) > MAX_REVISIONS for rows in inputs):
            raise BacktestBudgetExceeded("metadata row budget exhausted")
        runner = _Assessor(
            config,
            frozen,
            tuple(_freeze(row) for row in calendar_rows),
            tuple(_freeze(row) for row in quality_rows),
            coverage_proofs,
            trust,
            observations,
            budget,
            checkpoint,
            clock,
        )
        for index, fold in enumerate(config.folds):
            runner.fold(index, fold)
        return runner.result()
    except BacktestBudgetExceeded:
        return AssessmentResult(
            config,
            frozen.snapshot,
            trust,
            "FAILED",
            Eligibility.UNKNOWN,
            False,
            (),
            (),
            (),
            TrustDimensions(),
            "BUDGET_EXHAUSTED",
        )
