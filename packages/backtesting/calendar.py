"""Observation-bound schedule port; planned sessions need not have closed."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from packages.backtesting.coverage import managed_evidence_installed
from packages.backtesting.models import BacktestBudgetExceeded
from packages.research.calendar import NEW_YORK, expected_windows
from packages.research.calendar_storage import coverage_from_rows
from packages.research.models import BarInterval, PointInTimeQuery


def read_schedule_rows(connection, snapshot):
    rows = tuple(
        connection.execute(
            text("""
        SELECT * FROM research.calendar_versions
        WHERE commit_version<=:version AND ingested_at<=:snapshot
        ORDER BY ingested_at,commit_version,session_id LIMIT 100001
    """),
            dict(version=snapshot.version, snapshot=snapshot.at),
        ).mappings()
    )
    imported = ()
    if managed_evidence_installed(connection):
        from packages.research.calendar_storage import read_evidence_calendar_rows

        imported = read_evidence_calendar_rows(connection, snapshot.version, snapshot.at)
    if len(rows) + len(imported) > 100000:
        raise BacktestBudgetExceeded("calendar metadata budget exhausted")
    return tuple(
        sorted(
            (*rows, *imported),
            key=lambda row: (row["ingested_at"], row["commit_version"], row["session_id"]),
        )
    )


def calendar_reference(row):
    return str(row.get("evidence_id", row["version"]))


def schedule_at(rows, knowledge_at, snapshot):
    # Reuse same-response holiday/ambiguity proofs, with an unbounded *planned*
    # validity horizon. Knowledge and commit visibility remain conservative.
    query = PointInTimeQuery(
        datetime.max.replace(tzinfo=UTC), knowledge_at, snapshot.at, snapshot.version
    )
    usable = tuple(row for row in rows if row.get("managed_trust_state") in (None, "VERIFIED"))
    return coverage_from_rows(usable, query)


def relevant_schedule_rows(rows, start, end, knowledge_at, snapshot):
    # Keep complete response groups relevant to this window. Unrelated historical
    # response gaps must not invalidate a locally bracketed holiday proof.
    visible = tuple(
        row
        for row in rows
        if row["ingested_at"] <= min(knowledge_at, snapshot.at)
        and row["commit_version"] <= snapshot.version
    )
    groups = {}
    for row in visible:
        groups.setdefault(row["version"], []).append(row)
    first_day = start.astimezone(NEW_YORK).date()
    last_day = end.astimezone(NEW_YORK).date()
    return tuple(
        row
        for group in groups.values()
        if min(r["session_date"] for r in group) <= last_day
        and max(r["session_date"] for r in group) >= first_day
        for row in group
    )


def schedule_windows(rows, start, end, knowledge_at, snapshot):
    relevant = relevant_schedule_rows(rows, start, end, knowledge_at, snapshot)
    coverage = schedule_at(relevant, knowledge_at, snapshot)
    windows = expected_windows(coverage, BarInterval.FIFTEEN_MINUTES, start, end, end)
    return coverage, windows


def first_signal_knowledge(rows, start, end, snapshot, checkpoint=lambda: None):
    """Find the first regular signal from an actually observed session plan.

    Each response proposes only its first complete window inside the test, never
    a later bar to recover from a missing first observation. Re-select the actual
    schedule at that signal time: a superseded plan cannot skip the earlier first
    window of the then-visible schedule or backdate a later calendar correction.
    """
    step, delay = timedelta(minutes=15), timedelta(seconds=60)
    candidates = set()
    for row in rows:
        checkpoint()
        opening = row["opens_at"]
        if opening < start:
            intervals = (start - opening + step - timedelta(microseconds=1)) // step
            opening += intervals * step
        closing = opening + step
        knowledge = closing + delay
        if (
            closing <= min(row["closes_at"], end)
            and row["ingested_at"] <= min(knowledge, snapshot.at)
            and row["commit_version"] <= snapshot.version
        ):
            candidates.add(knowledge)
    for knowledge in sorted(candidates):
        checkpoint()
        _, windows = schedule_windows(rows, start, end, knowledge, snapshot)
        if windows.windows and windows.windows[0][1] + delay == knowledge:
            return knowledge
    return None
