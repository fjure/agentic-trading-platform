"""Pure snapshot-bound retrospective selection, separate from the Strict PIT port.

Market events, actual receipt, snapshot creation and simulated knowledge remain
distinct. No bar is cloned and no historical observation time is synthesized.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from packages.backtesting.calendar import relevant_schedule_rows
from packages.backtesting.dataset import (
    MAX_REVISIONS,
    SIGNAL_DELAY,
    DatasetBlocked,
    DatasetBudgetExceeded,
    DecisionStep,
    Snapshot,
    revision_key,
)
from packages.backtesting.models import (
    METRICS_VERSION,
    SIMULATOR_VERSION,
    ExperimentConfig,
    Fold,
    RetrospectivePolicyV1,
    WarmupBinding,
    digest,
    fingerprint,
    label,
)
from packages.domain.models import utc_string, utc_time
from packages.research.calendar import expected_windows
from packages.research.calendar_storage import coverage_from_rows
from packages.research.models import (
    AvailabilityBasis,
    BarIdentity,
    BarInterval,
    BarRevision,
    PointInTimeQuery,
    QualityStatus,
    _canonical,
)

QUALITY_POLICY_VERSION = "retrospective-quality-v1"
DISCONTINUITY_POLICY_VERSION = "raw-discontinuity-v1"
MAX_PUBLICATION_BYTES = 12 * 1024 * 1024
RESULT_LIMITATIONS = (
    "NOT_STRICT_PIT",
    "PRICE_ONLY",
    "DIVIDENDS_EXCLUDED",
    "IEX_PARTIAL_MARKET",
    "SURVIVORSHIP_BIAS",
    "EX_POST_REVISIONS",
    "NO_TRADING_AUTHORIZATION",
)
_TRUST_DIMENSIONS = (
    "integrity",
    "sourceAuthenticity",
    "temporalAuthenticity",
    "completeness",
    "domainQuality",
)


def _budget(*collections):
    if sum(len(rows) for rows in collections) > MAX_REVISIONS:
        raise DatasetBudgetExceeded("combined revision/metadata row budget exhausted")


def _policy(policy):
    if type(policy) is not RetrospectivePolicyV1:
        raise ValueError("retrospective policy required")


def _inside(snapshot, commit_version, ingested_at):
    return commit_version <= snapshot.version and ingested_at <= snapshot.at


def snapshot_reference(snapshot):
    return {"snapshotVersion": snapshot.version, "snapshotAt": utc_string(snapshot.at)}


def _publication_budget(value):
    """Bound encoded bytes without joining an oversized JSON string.

    The compact reference graph is canonicalized eagerly under the row budget;
    the 12MiB output cap is not a bound on that preliminary allocation.
    """
    size = 0
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    for piece in encoder.iterencode(_canonical(value)):
        size += len(piece.encode("utf-8"))
        if size > MAX_PUBLICATION_BYTES:
            raise DatasetBudgetExceeded("manifest publication byte budget exhausted")


@dataclass(frozen=True)
class RetrospectiveRevisionReference:
    identity: BarIdentity
    revision_id: UUID
    fingerprint: str
    local_revision: int
    commit_version: int
    ingested_at: datetime
    available_at: datetime
    published_at: datetime | None
    availability_basis: AvailabilityBasis

    def __post_init__(self):
        if type(self.identity) is not BarIdentity or not isinstance(self.revision_id, UUID):
            raise ValueError("complete typed bar reference required")
        digest(self.fingerprint)
        for at in (self.ingested_at, self.available_at):
            utc_time(at)
        if self.published_at is not None:
            utc_time(self.published_at)
        if (
            type(self.local_revision) is not int
            or self.local_revision < 1
            or type(self.commit_version) is not int
            or self.commit_version < 0
            or type(self.availability_basis) is not AvailabilityBasis
        ):
            raise ValueError("invalid revision evidence")

    @classmethod
    def from_bar(cls, bar):
        return cls(
            bar.identity,
            bar.revision_id,
            bar.fingerprint,
            bar.local_revision,
            bar.commit_version,
            bar.ingested_at,
            bar.available_at,
            bar.published_at,
            bar.availability_basis,
        )

    def verify(self, bar, snapshot):
        if type(bar) is not BarRevision or self != self.from_bar(bar):
            raise DatasetBlocked("BLOCKED_REVISION_MISMATCH", self.revision_id)
        if (
            not _inside(snapshot, bar.commit_version, bar.ingested_at)
            or bar.available_at > snapshot.at
        ):
            raise DatasetBlocked("BLOCKED_SNAPSHOT_MISMATCH", self.revision_id)

    def to_dict(self):
        i = self.identity
        return {
            "bar": {
                "instrumentId": str(i.instrument_id),
                "provider": i.provider.upper(),
                "feed": i.feed,
                "interval": i.interval.value,
                "start": utc_string(i.bar_start),
                "end": utc_string(i.bar_end),
                "adjustment": i.adjustment.value,
            },
            "revisionId": str(self.revision_id),
            "fingerprint": self.fingerprint,
            "localRevision": self.local_revision,
            "ingestedAt": utc_string(self.ingested_at),
            "availableAt": utc_string(self.available_at),
            # FIRST_OBSERVED is the conservative availability basis, not an
            # independently preserved provider/local receipt timestamp.
            "firstObservedAt": None,
            "publishedAt": utc_string(self.published_at) if self.published_at else None,
        }


@dataclass(frozen=True)
class RetrospectiveEvidenceReference:
    evidence_id: UUID
    fingerprint: str
    revision: int
    commit_version: int
    ingested_at: datetime

    def __post_init__(self):
        if not isinstance(self.evidence_id, UUID):
            raise ValueError("evidence UUID required")
        digest(self.fingerprint)
        utc_time(self.ingested_at)
        if (
            type(self.revision) is not int
            or self.revision < 1
            or type(self.commit_version) is not int
            or self.commit_version < 0
        ):
            raise ValueError("typed evidence version required")

    def to_dict(self):
        return {
            "evidenceId": str(self.evidence_id),
            "fingerprint": self.fingerprint,
            "revision": self.revision,
            "ingestedAt": utc_string(self.ingested_at),
        }


@dataclass(frozen=True)
class RetrospectiveTrustRecord:
    reference: str
    dimensions: tuple[tuple[str, str], ...]
    revoked: bool = False

    def __post_init__(self):
        label(self.reference)
        if (
            type(self.revoked) is not bool
            or type(self.dimensions) is not tuple
            or tuple(name for name, _ in self.dimensions) != _TRUST_DIMENSIONS
            or any(
                status not in ("VERIFIED", "REJECTED", "UNKNOWN", "NOT_EVALUATED")
                for _, status in self.dimensions
            )
        ):
            raise ValueError("five closed immutable trust dimensions required")

    @property
    def content_verified(self):
        # Temporal authenticity is honestly unproven in retrospective research.
        # A rejection in any dimension is nevertheless never bypassed.
        return (
            not self.revoked
            and all(status != "REJECTED" for _, status in self.dimensions)
            and all(
                status == "VERIFIED"
                for name, status in self.dimensions
                if name != "temporalAuthenticity"
            )
        )


@dataclass(frozen=True)
class RetrospectiveTrustContext:
    snapshot: Snapshot
    version: str
    records: tuple[RetrospectiveTrustRecord, ...] = ()
    evidence_references: tuple[RetrospectiveEvidenceReference, ...] = ()
    recorded_at: datetime | None = None

    def __post_init__(self):
        if type(self.snapshot) is not Snapshot:
            raise ValueError("typed trust snapshot required")
        digest(self.version)
        if self.recorded_at is None:
            object.__setattr__(self, "recorded_at", self.snapshot.at)
        utc_time(self.recorded_at)
        _budget(self.records, self.evidence_references)
        if (
            type(self.records) is not tuple
            or any(type(r) is not RetrospectiveTrustRecord for r in self.records)
            or len({r.reference for r in self.records}) != len(self.records)
            or type(self.evidence_references) is not tuple
            or any(
                type(r) is not RetrospectiveEvidenceReference
                or not _inside(self.snapshot, r.commit_version, r.ingested_at)
                for r in self.evidence_references
            )
        ):
            raise ValueError("immutable snapshot-bound trust required")

    def permits_content(self, reference):
        matches = tuple(r for r in self.records if r.reference == str(reference))
        return len(matches) == 1 and matches[0].content_verified

    @classmethod
    def from_state(cls, snapshot, state, evidence_references=()):
        records = tuple(
            RetrospectiveTrustRecord(
                record["reference"],
                tuple((name, record["dimensions"][name]["status"]) for name in _TRUST_DIMENSIONS),
                record["revoked"],
            )
            for record in state["records"]
        )
        return cls(
            snapshot,
            state["version"],
            records,
            evidence_references,
            state.get("snapshot_at", snapshot.at),
        )

    def to_dict(self):
        return {
            "version": self.version,
            "recordedAt": utc_string(self.recorded_at),
            "evidenceRevisions": [r.to_dict() for r in self.evidence_references],
        }


@dataclass(frozen=True)
class RetrospectiveCalendarReference:
    session_id: UUID
    session_date: date
    opens_at: datetime
    closes_at: datetime
    version: str
    source: str
    commit_version: int
    ingested_at: datetime
    evidence_id: UUID | None = None
    purpose: str = "REGULAR_SESSION"

    def __post_init__(self):
        if not isinstance(self.session_id, UUID) or type(self.session_date) is not date:
            raise ValueError("complete calendar identity required")
        for value in (self.opens_at, self.closes_at, self.ingested_at):
            utc_time(value)
        label(self.version)
        label(self.source)
        if (
            self.closes_at <= self.opens_at
            or type(self.commit_version) is not int
            or self.commit_version < 0
            or self.purpose not in ("REGULAR_SESSION", "HOLIDAY", "EARLY_CLOSE", "WARMUP")
        ):
            raise ValueError("invalid calendar revision")
        if self.evidence_id is not None and not isinstance(self.evidence_id, UUID):
            raise ValueError("calendar evidence UUID required")

    @classmethod
    def from_row(cls, row):
        return cls(
            *(
                row[name]
                for name in (
                    "session_id",
                    "session_date",
                    "opens_at",
                    "closes_at",
                    "version",
                    "source",
                    "commit_version",
                    "ingested_at",
                )
            ),
            evidence_id=row.get("evidence_id"),
        )

    @property
    def fingerprint(self):
        # The response's fingerprint is its persisted reference; bind the actual
        # selected session fields separately in the enclosing manifest hash.
        return self.version

    def row(self):
        return {
            name: getattr(self, name)
            for name in (
                "session_id",
                "session_date",
                "opens_at",
                "closes_at",
                "version",
                "source",
                "commit_version",
                "ingested_at",
            )
        }


@dataclass(frozen=True)
class RetrospectiveDataset:
    snapshot: Snapshot
    revisions: tuple[BarRevision, ...]
    policy: RetrospectivePolicyV1 = RetrospectivePolicyV1()

    def __post_init__(self):
        _policy(self.policy)
        if type(self.snapshot) is not Snapshot or type(self.revisions) is not tuple:
            raise ValueError("immutable typed retrospective snapshot required")
        _budget(self.revisions)
        if any(type(r) is not BarRevision for r in self.revisions):
            raise ValueError("typed bar revisions required")
        if len({r.revision_id for r in self.revisions}) != len(self.revisions):
            raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA")
        if any(
            not _inside(self.snapshot, r.commit_version, r.ingested_at)
            or r.available_at > self.snapshot.at
            for r in self.revisions
        ):
            raise DatasetBlocked("BLOCKED_SNAPSHOT_MISMATCH")

    def resolve(self, checkpoint=lambda: None):
        heads, revisions = {}, {}
        for bar in self.revisions:
            checkpoint()
            key = bar.identity, bar.local_revision
            previous = revisions.get(key)
            if previous is not None:
                # Never resolve a tied local revision by its UUID.
                raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA", bar.identity.bar_start)
            revisions[key] = bar
            if bar.quality == QualityStatus.CONFLICT:
                raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA", bar.identity.bar_start)
            head = heads.get(bar.identity)
            if head is None or bar.local_revision > head.local_revision:
                heads[bar.identity] = bar
        for head in heads.values():
            if head.quality != QualityStatus.VALID:
                raise DatasetBlocked("BLOCKED_QUALITY", head.revision_id)
        return tuple(sorted(heads.values(), key=revision_key))

    def restore(self, references):
        """Read exact immutable bindings; never reselect the current highest head."""
        if type(references) is not tuple or any(
            type(r) is not RetrospectiveRevisionReference for r in references
        ):
            raise ValueError("typed immutable retrospective references required")
        _budget(references)
        if len({r.revision_id for r in references}) != len(references):
            raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA")
        by_id = {bar.revision_id: bar for bar in self.revisions}
        restored = []
        for reference in references:
            bar = by_id.get(reference.revision_id)
            if bar is None:
                raise DatasetBlocked("BLOCKED_REVISION_MISSING", reference.revision_id)
            reference.verify(bar, self.snapshot)
            restored.append(bar)
        return tuple(restored)


def retrospective_calendar(calendar_rows, snapshot, trust_context=None):
    """Use real snapshot visibility, without a historical receipt deadline.

    Same-response bracketing and conflict rules are reused unchanged. Removing
    the managed marker follows explicit content verification; actual dates and
    receipt times are preserved in every row and reference.
    """
    if type(calendar_rows) is not tuple or type(snapshot) is not Snapshot:
        raise ValueError("immutable calendar rows and snapshot required")
    _budget(calendar_rows)
    if trust_context is not None and (
        type(trust_context) is not RetrospectiveTrustContext or trust_context.snapshot != snapshot
    ):
        raise ValueError("retrospective trust snapshot mismatch")
    visible, references = [], []
    for row in calendar_rows:
        if not isinstance(row, Mapping):
            raise ValueError("calendar revision mapping required")
        if not _inside(snapshot, row["commit_version"], row["ingested_at"]):
            continue
        managed = row.get("managed_trust_state")
        reference = row.get("evidence_id", row["version"])
        if managed in ("EVIDENCE_REVOKED", "TRUST_REJECTED"):
            raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", reference)
        if managed is not None and (
            trust_context is None or not trust_context.permits_content(reference)
        ):
            raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", reference)
        if trust_context is not None:
            records = tuple(r for r in trust_context.records if r.reference == str(reference))
            if records and not records[0].content_verified:
                raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", reference)
        bound = RetrospectiveCalendarReference.from_row(row)
        references.append(bound)
        visible.append(bound.row())
    query = PointInTimeQuery(
        datetime.max.replace(tzinfo=UTC), snapshot.at, snapshot.at, snapshot.version
    )
    try:
        coverage = coverage_from_rows(tuple(visible), query)
    except ValueError as exc:
        raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN") from exc
    return coverage, tuple(
        sorted(references, key=lambda r: (r.version, r.session_date, r.session_id))
    )


def restore_retrospective_calendar(calendar_rows, references, snapshot, trust_context=None):
    """Reopen only the bound calendar sessions, never a newer response/version."""
    if (
        type(calendar_rows) is not tuple
        or type(references) is not tuple
        or any(type(r) is not RetrospectiveCalendarReference for r in references)
    ):
        raise ValueError("immutable typed calendar replay required")
    _budget(calendar_rows, references)
    by_id = {}
    for row in calendar_rows:
        key = row["session_id"], row.get("evidence_id")
        if key in by_id:
            raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA", key[0])
        by_id[key] = row
    selected = []
    for reference in references:
        row = by_id.get((reference.session_id, reference.evidence_id))
        if row is None:
            raise DatasetBlocked("BLOCKED_REVISION_MISSING", reference.session_id)
        if RetrospectiveCalendarReference.from_row(row) != reference:
            raise DatasetBlocked("BLOCKED_REVISION_MISMATCH", reference.session_id)
        if not _inside(snapshot, reference.commit_version, reference.ingested_at):
            raise DatasetBlocked("BLOCKED_SNAPSHOT_MISMATCH", reference.session_id)
        selected.append(row)
    return retrospective_calendar(tuple(selected), snapshot, trust_context)


@dataclass(frozen=True)
class RetrospectiveFoldDataset:
    fold: Fold
    warmup: tuple[BarRevision, ...]
    steps: tuple[DecisionStep, ...]
    outcome_bars: tuple[BarRevision, ...]
    expected_bar_starts: tuple[datetime, ...]
    warmup_binding: WarmupBinding
    snapshot: Snapshot
    revision_references: tuple[RetrospectiveRevisionReference, ...]
    calendar_references: tuple[RetrospectiveCalendarReference, ...]
    policy: RetrospectivePolicyV1 = RetrospectivePolicyV1()

    def __post_init__(self):
        _policy(self.policy)
        if type(self.fold) is not Fold or type(self.snapshot) is not Snapshot:
            raise ValueError("typed retrospective fold and snapshot required")
        for rows in (
            self.warmup,
            self.steps,
            self.outcome_bars,
            self.expected_bar_starts,
            self.revision_references,
            self.calendar_references,
        ):
            if type(rows) is not tuple:
                raise ValueError("immutable retrospective fold required")
        _budget(self.revision_references, self.calendar_references)
        self.validate()

    @property
    def dataset_hash(self):
        return fingerprint(self.binding())

    def binding(self):
        """Reference-based preimage; price content is already fingerprint-bound."""
        return (
            self.policy.research_mode,
            self.policy.selection_policy_version,
            self.fold,
            self.snapshot,
            self.warmup_binding,
            self.revision_references,
            self.calendar_references,
            tuple(
                (
                    s.signal_bar.revision_id,
                    tuple(b.revision_id for b in s.input_bars),
                    s.knowledge_at,
                    s.fill_target,
                )
                for s in self.steps
            ),
            tuple(b.revision_id for b in self.outcome_bars),
        )

    @property
    def calendar_revision_ids(self):
        return tuple(sorted({r.version for r in self.calendar_references}))

    def validate(self):
        _policy(self.policy)
        if any(type(bar) is not BarRevision for bar in (*self.warmup, *self.outcome_bars)) or any(
            type(step) is not DecisionStep for step in self.steps
        ):
            raise ValueError("typed retrospective bars and decisions required")
        expected = self.expected_bar_starts
        if (
            not expected
            or tuple(sorted(set(expected))) != expected
            or tuple(b.identity.bar_start for b in self.outcome_bars) != expected
            or tuple(s.signal_bar.identity.bar_start for s in self.steps) != expected
            or self.fold.test_end > self.snapshot.at
        ):
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")
        if any(type(r) is not RetrospectiveRevisionReference for r in self.revision_references):
            raise ValueError("typed retrospective references required")
        refs = {r.revision_id: r for r in self.revision_references}
        if len(refs) != len(self.revision_references):
            raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA")
        used = {}

        def bind(bar):
            if type(bar) is not BarRevision:
                raise ValueError("typed retrospective bar required")
            previous = used.get(bar.revision_id)
            if previous is not None and previous != bar:
                raise DatasetBlocked("BLOCKED_REVISION_MISMATCH", bar.revision_id)
            used[bar.revision_id] = bar

        if type(self.warmup_binding) is not WarmupBinding:
            raise ValueError("typed warmup binding required")
        required_history = self.warmup_binding.expected_bar_starts + expected
        count = len(self.warmup_binding.expected_bar_starts)
        if (
            self.warmup_binding.warmup_end != self.fold.test_start
            or self.warmup_binding.warmup_start < self.fold.training_start
            or tuple(b.identity.bar_start for b in self.warmup) != required_history[:count]
        ):
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")
        for n, step in enumerate(self.steps):
            if (
                type(step) is not DecisionStep
                or type(step.input_bars) is not tuple
                or not step.input_bars
                or step.input_bars[-1] != step.signal_bar
                or tuple(b.identity.bar_start for b in step.input_bars)
                != required_history[n : n + count + 1]
                or any(
                    b.identity.bar_end > step.signal_bar.identity.bar_end for b in step.input_bars
                )
            ):
                raise DatasetBlocked("BLOCKED_POINT_IN_TIME_INPUT")
            target_index = bisect_left(expected, step.knowledge_at)
            target = expected[target_index] if target_index < len(expected) else None
            if step.fill_target != target:
                raise DatasetBlocked("BLOCKED_FILL_SCHEDULE_MISMATCH")
            for bar in step.input_bars:
                bind(bar)
        for bar in (*self.warmup, *self.outcome_bars):
            bind(bar)
        if set(refs) != set(used):
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")
        for revision_id, bar in used.items():
            refs[revision_id].verify(bar, self.snapshot)
            if bar.quality != QualityStatus.VALID:
                raise DatasetBlocked("BLOCKED_QUALITY", revision_id)
            if (
                bar.identity.provider,
                bar.identity.feed,
                bar.identity.interval,
                bar.identity.adjustment.value,
            ) != ("alpaca", "iex", "15Min", "raw"):
                raise DatasetBlocked("BLOCKED_DATASET_BINDING", revision_id)
        if len({b.identity.instrument_id for b in used.values()}) != 1:
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")
        if any(
            type(r) is not RetrospectiveCalendarReference
            or not _inside(self.snapshot, r.commit_version, r.ingested_at)
            for r in self.calendar_references
        ):
            raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN")
        # Revalidate actual snapshot-bound sessions, not a caller's claimed list
        # of expected starts. Dropping a missing bar cannot create a valid fold.
        calendar, _ = retrospective_calendar(
            tuple(r.row() for r in self.calendar_references), self.snapshot
        )
        planned = expected_windows(
            calendar,
            BarInterval.FIFTEEN_MINUTES,
            self.fold.training_start if count else self.fold.test_start,
            self.fold.test_end,
            self.fold.test_end,
        )
        test = tuple(w for w in planned.windows if w[0] >= self.fold.test_start)
        prior = tuple(w for w in planned.windows if w[1] <= self.fold.test_start)
        warm = prior[-count:] if count else ()
        if (
            planned.unknown_dates
            or expected != tuple(w[0] for w in test)
            or self.warmup_binding.expected_bar_starts != tuple(w[0] for w in warm)
            or len(warm) != count
        ):
            raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN")
        ends = dict(warm + test)
        if any(bar.identity.bar_end != ends.get(bar.identity.bar_start) for bar in used.values()):
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")


def materialize_retrospective_fold(
    dataset,
    fold,
    instrument_id,
    warmup_count,
    calendar_rows,
    *,
    trust_context=None,
    checkpoint=lambda: None,
):
    if type(dataset) is not RetrospectiveDataset:
        raise ValueError("retrospective dataset required")
    _policy(dataset.policy)
    if (
        type(fold) is not Fold
        or not isinstance(instrument_id, UUID)
        or type(warmup_count) is not int
        or not 0 <= warmup_count < 100
    ):
        raise ValueError("typed fold/instrument and bounded warmup required")
    if fold.test_end > dataset.snapshot.at:
        raise ValueError("future evaluation window")
    if trust_context is not None and type(trust_context) is not RetrospectiveTrustContext:
        raise ValueError("typed retrospective trust context required")
    _budget(
        dataset.revisions,
        calendar_rows,
        trust_context.records if trust_context else (),
        trust_context.evidence_references if trust_context else (),
    )
    relevant = relevant_schedule_rows(
        calendar_rows,
        fold.training_start if warmup_count else fold.test_start,
        fold.test_end,
        dataset.snapshot.at,
        dataset.snapshot,
    )
    coverage, calendar_refs = retrospective_calendar(relevant, dataset.snapshot, trust_context)
    planned = expected_windows(
        coverage,
        BarInterval.FIFTEEN_MINUTES,
        fold.training_start if warmup_count else fold.test_start,
        fold.test_end,
        fold.test_end,
    )
    if planned.unknown_dates:
        raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", planned.unknown_dates[0])
    test = tuple(w for w in planned.windows if w[0] >= fold.test_start)
    prior = tuple(w for w in planned.windows if w[1] <= fold.test_start)
    if not test:
        raise DatasetBlocked("BLOCKED_MISSING_DATA", fold.test_start)
    if len(prior) < warmup_count:
        raise DatasetBlocked("BLOCKED_WARMUP_MISSING", fold.test_start)
    warm = prior[-warmup_count:] if warmup_count else ()
    warm_start = warm[0][0] if warm else fold.test_start
    binding = WarmupBinding(warm_start, fold.test_start, tuple(w[0] for w in warm))
    by_start = {}
    trust_records = {r.reference: r for r in trust_context.records} if trust_context else {}
    for bar in dataset.resolve(checkpoint):
        i = bar.identity
        if (
            i.instrument_id != instrument_id
            or i.provider != "alpaca"
            or i.feed != "iex"
            or i.interval != BarInterval.FIFTEEN_MINUTES
            or i.adjustment.value != "raw"
            or not warm_start <= i.bar_start < fold.test_end
        ):
            continue
        if i.bar_start in by_start:
            raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA", i.bar_start)
        if trust_context is not None:
            record = trust_records.get(str(bar.revision_id))
            if record is not None and not record.content_verified:
                raise DatasetBlocked("BLOCKED_MISSING_DATA", bar.revision_id)
        by_start[i.bar_start] = bar
    history = tuple(w[0] for w in warm + test)
    expected = tuple(w[0] for w in test)
    for start, end in warm + test:
        checkpoint()
        if start not in by_start or by_start[start].identity.bar_end != end:
            raise DatasetBlocked(
                "BLOCKED_WARMUP_MISSING" if start < fold.test_start else "BLOCKED_MISSING_DATA",
                start,
            )
    steps = []
    for n, (start, end) in enumerate(test):
        checkpoint()
        target_index = bisect_left(expected, end + SIGNAL_DELAY)
        steps.append(
            DecisionStep(
                by_start[start],
                tuple(by_start[s] for s in history[n : n + warmup_count + 1]),
                expected[target_index] if target_index < len(expected) else None,
            )
        )
    selected = tuple(by_start[start] for start in history)
    return RetrospectiveFoldDataset(
        fold,
        tuple(by_start[w[0]] for w in warm),
        tuple(steps),
        tuple(by_start[s] for s in expected),
        expected,
        binding,
        dataset.snapshot,
        tuple(
            RetrospectiveRevisionReference.from_bar(b) for b in sorted(selected, key=revision_key)
        ),
        calendar_refs,
    )


@dataclass(frozen=True)
class RetrospectiveManifest:
    config: ExperimentConfig
    snapshot: Snapshot
    datasets: tuple[RetrospectiveFoldDataset, ...]
    trust_context: RetrospectiveTrustContext
    code_version: str
    license_receipt_fingerprint: str
    quality_references: tuple[RetrospectiveEvidenceReference, ...] = ()
    corporate_action_references: tuple[RetrospectiveEvidenceReference, ...] = ()
    policy: RetrospectivePolicyV1 = RetrospectivePolicyV1()
    quality_policy_version: str = QUALITY_POLICY_VERSION
    discontinuity_policy_version: str = DISCONTINUITY_POLICY_VERSION
    simulator_version: str = SIMULATOR_VERSION
    metrics_version: str = METRICS_VERSION

    def __post_init__(self):
        _policy(self.policy)
        if (
            type(self.config) is not ExperimentConfig
            or type(self.snapshot) is not Snapshot
            or type(self.trust_context) is not RetrospectiveTrustContext
            or self.trust_context.snapshot != self.snapshot
        ):
            raise ValueError("typed manifest and trust binding required")
        if not isinstance(self.code_version, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.code_version
        ):
            raise ValueError("build commit fingerprint required")
        if (
            self.quality_policy_version,
            self.discontinuity_policy_version,
            self.simulator_version,
            self.metrics_version,
        ) != (
            QUALITY_POLICY_VERSION,
            DISCONTINUITY_POLICY_VERSION,
            SIMULATOR_VERSION,
            METRICS_VERSION,
        ):
            raise ValueError("unsupported retrospective model versions")
        digest(self.license_receipt_fingerprint)
        if (
            type(self.datasets) is not tuple
            or len(self.datasets) != len(self.config.folds)
            or any(type(d) is not RetrospectiveFoldDataset for d in self.datasets)
        ):
            raise ValueError("one typed dataset per configured fold required")
        for dataset, fold in zip(self.datasets, self.config.folds, strict=True):
            if dataset.snapshot != self.snapshot or dataset.fold != fold:
                raise DatasetBlocked("BLOCKED_DATASET_BINDING")
            dataset.validate()
            if len(dataset.warmup) != dict(self.config.parameters).get("slow", 1) - 1:
                raise DatasetBlocked("BLOCKED_WARMUP_MISSING")
            if any(
                r.identity.instrument_id != self.config.instrument_id
                for r in dataset.revision_references
            ):
                raise DatasetBlocked("BLOCKED_DATASET_BINDING")
            trust_records = {r.reference: r for r in self.trust_context.records}
            for reference in dataset.calendar_references:
                key = str(reference.evidence_id or reference.version)
                record = trust_records.get(key)
                if (
                    reference.evidence_id is not None
                    and record is None
                    or record is not None
                    and not record.content_verified
                ):
                    raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", key)
            for reference in dataset.revision_references:
                record = trust_records.get(str(reference.revision_id))
                if record is not None and not record.content_verified:
                    raise DatasetBlocked("BLOCKED_MISSING_DATA", reference.revision_id)
        for refs in (self.quality_references, self.corporate_action_references):
            if type(refs) is not tuple or any(
                type(r) is not RetrospectiveEvidenceReference
                or not _inside(self.snapshot, r.commit_version, r.ingested_at)
                for r in refs
            ):
                raise ValueError("immutable snapshot-bound metadata required")
        _budget(
            tuple({r.revision_id for d in self.datasets for r in d.revision_references}),
            tuple({r for d in self.datasets for r in d.calendar_references}),
            self.trust_context.records,
            self.trust_context.evidence_references,
            self.quality_references,
            self.corporate_action_references,
        )
        _publication_budget(self.binding())

    @property
    def input_hash(self):
        return fingerprint(
            (self.policy.research_mode, self.policy.selection_policy_version, self.config)
        )

    @property
    def manifest_hash(self):
        return fingerprint(self.binding())

    def binding(self):
        return (
            self.policy.experiment_version,
            self.policy.research_mode,
            self.policy.selection_policy_version,
            self.config,
            self.snapshot,
            tuple(dataset.binding() for dataset in self.datasets),
            self.trust_context,
            self.code_version,
            self.license_receipt_fingerprint,
            self.quality_references,
            self.corporate_action_references,
            self.quality_policy_version,
            self.discontinuity_policy_version,
            self.simulator_version,
            self.metrics_version,
        )

    def validation_context(self):
        return RetrospectiveValidationContext(self)


@dataclass(frozen=True)
class RetrospectiveValidationContext:
    manifest: RetrospectiveManifest

    def __post_init__(self):
        if type(self.manifest) is not RetrospectiveManifest:
            raise ValueError("retrospective manifest required")

    def validate(self, config, dataset):
        if type(config) is not ExperimentConfig or type(dataset) is not RetrospectiveFoldDataset:
            raise ValueError("retrospective policy/dataset mismatch")
        _policy(dataset.policy)
        if (
            config != self.manifest.config
            or dataset.snapshot != self.manifest.snapshot
            or not any(dataset == bound for bound in self.manifest.datasets)
        ):
            raise DatasetBlocked("BLOCKED_DATASET_BINDING")
        dataset.validate()
