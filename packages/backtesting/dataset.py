"""Read-only bounded snapshots, chronological PIT sweep and separated outcomes."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from packages.backtesting.calendar import relevant_schedule_rows, schedule_windows
from packages.backtesting.coverage import covering_proof, quality_at
from packages.backtesting.models import BacktestBlocked, BacktestBudgetExceeded, Fold, WarmupBinding
from packages.domain.models import utc_time
from packages.research.models import BarRevision, QualityStatus, content_fingerprint
from packages.research.storage import bar_from_row

MAX_REVISIONS = 100000
SIGNAL_DELAY = timedelta(seconds=60)
OUTCOME_DELAY = timedelta(seconds=300)
_KEYS = (
    "instrument_id",
    "provider",
    "feed",
    "interval",
    "bar_start",
    "bar_end",
    "adjustment",
    "local_revision",
    "revision_id",
)
_ORDER = ",".join(_KEYS)


class DatasetBlocked(BacktestBlocked):
    def __init__(self, code, reference=None):
        super().__init__(code, str(reference) if reference is not None else None)


DatasetBudgetExceeded = BacktestBudgetExceeded


@dataclass(frozen=True)
class Snapshot:
    version: int
    at: datetime

    def __post_init__(self):
        utc_time(self.at)
        if type(self.version) is not int or self.version < 0:
            raise ValueError("committed snapshot required")


@dataclass(frozen=True)
class RevisionPosition:
    key: tuple
    binding: str


@dataclass(frozen=True)
class RevisionPage:
    items: tuple[BarRevision, ...]
    next_position: RevisionPosition | None


def revision_key(bar):
    i = bar.identity
    return (
        i.instrument_id,
        i.provider,
        i.feed,
        i.interval.value,
        i.bar_start,
        i.bar_end,
        i.adjustment.value,
        bar.local_revision,
        bar.revision_id,
    )


class DatasetPort:
    """No ingestion/writes, HTTP cursors or changing per-page knowledge cap."""

    def snapshot(self, connection):
        row = (
            connection.execute(
                text(
                    "SELECT version,clock_timestamp() AS at "
                    "FROM research.version_state WHERE singleton"
                )
            )
            .mappings()
            .one()
        )
        return Snapshot(row["version"], row["at"])

    def page(self, connection, instrument_id, start, end, snapshot, limit=200, after=None):
        utc_time(start)
        utc_time(end)
        if not isinstance(instrument_id, UUID) or not isinstance(snapshot, Snapshot):
            raise ValueError("typed identity and snapshot required")
        if not timedelta(0) < end - start <= timedelta(days=366):
            raise ValueError("dataset window exceeds 366 days")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("dataset page limit outside [1,200]")
        binding = content_fingerprint(
            ("backtest-revisions-v1", instrument_id, start, end, snapshot)
        )
        params = dict(
            instrument=instrument_id,
            start=start,
            end=end,
            version=snapshot.version,
            snapshot=snapshot.at,
            limit=limit + 1,
        )
        position = ""
        if after is not None:
            if not isinstance(after, RevisionPosition) or after.binding != binding:
                raise ValueError("keyset snapshot/filter mismatch")
            if len(after.key) != len(_KEYS):
                raise ValueError("invalid revision position")
            params.update({f"after_{k}": v for k, v in zip(_KEYS, after.key, strict=True)})
            placeholders = ",".join(f":after_{key}" for key in _KEYS)
            position = f" AND ({_ORDER}) > ({placeholders})"
        rows = (
            connection.execute(
                text(f"""
            SELECT * FROM research.bar_revisions
            WHERE instrument_id=:instrument AND interval='15Min' AND feed='iex'
              AND adjustment='raw' AND bar_start>=:start AND bar_end<=:end
              AND commit_version<=:version AND ingested_at<=:snapshot {position}
            ORDER BY {_ORDER} LIMIT :limit
        """),
                params,
            )
            .mappings()
            .all()
        )
        items = tuple(bar_from_row(row) for row in rows[:limit])
        cursor = RevisionPosition(revision_key(items[-1]), binding) if len(rows) > limit else None
        return RevisionPage(items, cursor)

    def freeze(
        self,
        connection,
        instrument_id,
        start,
        end,
        snapshot,
        checkpoint: Callable[[], None] = lambda: None,
    ):
        items, after = [], None
        while True:
            checkpoint()
            page = self.page(connection, instrument_id, start, end, snapshot, after=after)
            items.extend(page.items)
            if len(items) > MAX_REVISIONS:
                raise DatasetBudgetExceeded("revision row budget exhausted")
            after = page.next_position
            if after is None:
                return FrozenDataset(snapshot, tuple(items))


@dataclass(frozen=True)
class FrozenDataset:
    snapshot: Snapshot
    revisions: tuple[BarRevision, ...]

    def __post_init__(self):
        if type(self.revisions) is not tuple or len(self.revisions) > MAX_REVISIONS:
            raise DatasetBudgetExceeded("revision row budget exhausted")
        if len({r.revision_id for r in self.revisions}) != len(self.revisions):
            raise ValueError("duplicate frozen revision")
        if any(
            r.commit_version > self.snapshot.version or r.ingested_at > self.snapshot.at
            for r in self.revisions
        ):
            raise ValueError("revision outside committed snapshot")

    def sweep(self):
        return RevisionSweep(self)


class RevisionSweep:
    """Each ingestion/availability event is consumed once; never rewind knowledge."""

    def __init__(self, frozen):
        self.events = sorted(
            frozen.revisions, key=lambda b: (max(b.ingested_at, b.available_at), revision_key(b))
        )
        self.index = 0
        self.heads = {}
        self.last_knowledge = None

    def visible(self, valuation_at, knowledge_at):
        utc_time(valuation_at)
        utc_time(knowledge_at)
        if self.last_knowledge is not None and knowledge_at < self.last_knowledge:
            raise ValueError("PIT sweep cannot rewind")
        self.last_knowledge = knowledge_at
        while self.index < len(self.events):
            bar = self.events[self.index]
            if max(bar.ingested_at, bar.available_at) > knowledge_at:
                break
            self.index += 1
            if bar.quality != QualityStatus.VALID or bar.identity.feed != "iex":
                continue
            head = self.heads.get(bar.identity)
            if head is None or bar.local_revision > head.local_revision:
                self.heads[bar.identity] = bar
        return tuple(
            sorted(
                (b for b in self.heads.values() if b.identity.bar_end <= valuation_at),
                key=lambda b: (
                    b.identity.bar_start,
                    b.identity.bar_end,
                    b.identity.provider,
                    b.revision_id,
                ),
            )
        )


@dataclass(frozen=True)
class DecisionStep:
    signal_bar: BarRevision
    input_bars: tuple[BarRevision, ...]
    fill_target: datetime | None

    @property
    def knowledge_at(self):
        return self.signal_bar.identity.bar_end + SIGNAL_DELAY


@dataclass(frozen=True)
class FoldDataset:
    fold: Fold
    warmup: tuple[BarRevision, ...]
    steps: tuple[DecisionStep, ...]
    outcome_bars: tuple[BarRevision, ...]
    expected_bar_starts: tuple[datetime, ...]
    warmup_binding: WarmupBinding
    calendar_revision_ids: tuple[str, ...] = ()
    quality_revision_ids: tuple[str, ...] = ()
    coverage_ids: tuple[str, ...] = ()
    outcome_cutoffs: tuple[tuple[UUID, datetime], ...] = ()


def materialize_fold(
    frozen,
    fold,
    instrument_id,
    warmup_count,
    calendar_rows,
    quality_rows=(),
    coverage_proofs=(),
    checkpoint=lambda: None,
):
    """Bind complete regular windows; no skipping missing warmup or result bars."""
    if not isinstance(fold, Fold) or type(warmup_count) is not int or not 0 <= warmup_count < 100:
        raise ValueError("typed fold and bounded warmup required")
    if fold.test_end > frozen.snapshot.at:
        raise ValueError("future evaluation window")
    if any(len(rows) > MAX_REVISIONS for rows in (calendar_rows, quality_rows, coverage_proofs)):
        raise DatasetBudgetExceeded("metadata row budget exhausted")
    snapshot = frozen.snapshot
    first_knowledge = fold.test_start + timedelta(minutes=15) + SIGNAL_DELAY
    _, planned = schedule_windows(
        calendar_rows,
        fold.training_start if warmup_count else fold.test_start,
        fold.test_end,
        first_knowledge,
        snapshot,
    )
    if planned.unknown_dates:
        raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", planned.unknown_dates[0])
    test_windows = tuple(w for w in planned.windows if w[0] >= fold.test_start)
    if not test_windows:
        raise DatasetBlocked("BLOCKED_MISSING_DATA", fold.test_start)
    preceding = tuple(w for w in planned.windows if w[1] <= fold.test_start)
    if len(preceding) < warmup_count:
        raise DatasetBlocked("BLOCKED_WARMUP_MISSING", fold.test_start)
    warm_windows = preceding[-warmup_count:] if warmup_count else ()
    warm_start = warm_windows[0][0] if warm_windows else fold.test_start
    binding = WarmupBinding(warm_start, fold.test_start, tuple(w[0] for w in warm_windows))
    expected = tuple(w[0] for w in test_windows)
    signals, outcomes = frozen.sweep(), frozen.sweep()
    steps, result_bars, cutoffs = [], [], []
    calendar_refs, quality_refs, coverage_refs = set(), set(), set()
    warmup = ()
    history = tuple(w[0] for w in warm_windows) + expected
    for index, (start, end) in enumerate(test_windows):
        checkpoint()
        knowledge = end + SIGNAL_DELAY
        coverage, planned_now = schedule_windows(
            calendar_rows, start, fold.test_end, knowledge, snapshot
        )
        if planned_now.unknown_dates or (start, end) not in planned_now.windows:
            raise DatasetBlocked("BLOCKED_CALENDAR_UNKNOWN", start)
        calendar_refs.update(
            row["version"]
            for row in relevant_schedule_rows(
                calendar_rows, start, fold.test_end, knowledge, snapshot
            )
        )
        proof = covering_proof(
            coverage_proofs, instrument_id, warm_start, fold.test_end, knowledge, snapshot
        )
        if proof is None:
            raise DatasetBlocked("BLOCKED_CORPORATE_ACTIONS_UNKNOWN", start)
        coverage_refs.add(str(proof.coverage_id))
        okay, refs = quality_at(quality_rows, warm_start, end, knowledge, snapshot)
        if not okay:
            raise DatasetBlocked("BLOCKED_QUALITY", start)
        quality_refs.update(refs)
        visible = signals.visible(end, knowledge)
        by_start = {}
        for bar in visible:
            if bar.identity.instrument_id != instrument_id or bar.identity.bar_start < warm_start:
                continue
            if bar.identity.bar_start in by_start:
                raise DatasetBlocked("BLOCKED_AMBIGUOUS_DATA", bar.identity.bar_start)
            by_start[bar.identity.bar_start] = bar
        # Every test identity is checked when it first becomes a signal. Keep
        # only the registered strategy's rolling window in each immutable step;
        # copying the complete prefix would make manifest size quadratic.
        needed = history[index : index + warmup_count + 1]
        for required in needed:
            if required not in by_start:
                raise DatasetBlocked(
                    "BLOCKED_WARMUP_MISSING"
                    if required < fold.test_start
                    else "BLOCKED_MISSING_DATA",
                    required,
                )
        input_bars = tuple(by_start[key] for key in needed)
        if index == 0:
            warmup = input_bars[:warmup_count]
        target = next((w[0] for w in planned_now.windows if w[0] >= knowledge), None)
        if target is not None and target not in expected:
            raise DatasetBlocked("BLOCKED_CALENDAR_CHANGED", target)
        steps.append(DecisionStep(by_start[start], input_bars, target))
        outcome_cutoff = end + OUTCOME_DELAY
        okay, refs = quality_at(quality_rows, start, end, outcome_cutoff, snapshot)
        if not okay:
            raise DatasetBlocked("BLOCKED_QUALITY", start)
        quality_refs.update(refs)
        eligible = tuple(
            b
            for b in outcomes.visible(end, outcome_cutoff)
            if b.identity.instrument_id == instrument_id and b.identity.bar_start == start
        )
        if len(eligible) != 1:
            raise DatasetBlocked("BLOCKED_OUTCOME_MISSING", start)
        result_bars.append(eligible[0])
        cutoffs.append((eligible[0].revision_id, outcome_cutoff))
    return FoldDataset(
        fold,
        warmup,
        tuple(steps),
        tuple(result_bars),
        expected,
        binding,
        tuple(sorted(calendar_refs)),
        tuple(sorted(quality_refs)),
        tuple(sorted(coverage_refs)),
        tuple(cutoffs),
    )
