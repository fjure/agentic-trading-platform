"""Normalized historical sessions; never infer holiday coverage from an empty response."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import text

from packages.research.calendar import CalendarCoverage, ResearchSession
from packages.research.models import PointInTimeQuery, content_fingerprint

_NAMESPACE = UUID("f84512d0-4124-47f6-a682-d14dd9f7677f")


def calendar_response(sessions: tuple[ResearchSession, ...]):
    """Bind one complete normalized response, including its original session provenance."""
    if not isinstance(sessions, tuple) or len(sessions) > 367:
        raise ValueError("bounded normalized sessions required")
    if any(not isinstance(session, ResearchSession) for session in sessions):
        raise ValueError("typed normalized sessions required")
    ordered = tuple(sorted(sessions, key=lambda session: session.session_date))
    if len({session.session_date for session in ordered}) != len(ordered) or any(
        session.source != "alpaca-v2-calendar" for session in ordered
    ):
        raise ValueError("one unambiguous approved calendar response required")
    if ordered and (ordered[-1].session_date - ordered[0].session_date).days > 366:
        raise ValueError("calendar response exceeds the certified date bound")
    return content_fingerprint(("calendar-response-v1", ordered)), ordered


def append_sessions(connection, sessions: tuple[ResearchSession, ...]):
    """Append exactly one complete provider response; empty responses prove no dates.

    The shared immutable version is a response proof. Never pass independently fetched
    or previously selected sessions as if they were one provider response.
    """
    version, sessions = calendar_response(sessions)
    if not sessions:
        return
    connection.execute(text("SELECT research.reserve_version()"))
    for session in sessions:
        connection.execute(
            text("""
          INSERT INTO research.calendar_versions
            (session_id,session_date,opens_at,closes_at,version,source)
          VALUES(:id,:day,:opening,:closing,:version,:source)
          ON CONFLICT(session_id) DO NOTHING
        """),
            dict(
                id=uuid5(
                    _NAMESPACE, content_fingerprint((version, session.session_date.isoformat()))
                ),
                day=session.session_date,
                opening=session.opens_at,
                closing=session.closes_at,
                version=version,
                source=session.source,
            ),
        )


def coverage_from_rows(rows: tuple[Mapping, ...], query: PointInTimeQuery) -> CalendarCoverage:
    """Use same-response bracketing only; disconnected responses never invent holidays."""
    visible = tuple(
        row
        for row in rows
        if row["commit_version"] <= query.snapshot_version
        and row["ingested_at"] <= min(query.knowledge_at, query.snapshot_at)
        and row["closes_at"] <= query.valuation_at
    )
    groups = {}
    for row in visible:
        groups.setdefault(row["version"], []).append(row)
    proofs = []
    for response, records in groups.items():
        by_day = {row["session_date"]: row for row in records}
        if len(by_day) != len(records):
            raise ValueError("duplicate dates in immutable calendar response")
        first, last = min(by_day), max(by_day)
        proofs.append(
            (max(row["commit_version"] for row in records), response, first, last, by_day)
        )
    # Determine a date's latest response before interpreting a missing date as closed.
    selected, ambiguous = {}, set()
    candidates = {row["session_date"] for row in visible}
    for day in candidates:
        covering = [proof for proof in proofs if proof[2] <= day <= proof[3]]
        newest = max(proof[0] for proof in covering)
        heads = [proof for proof in covering if proof[0] == newest]
        values = {
            (record["opens_at"], record["closes_at"], record["source"])
            if (record := proof[4].get(day)) is not None
            else None
            for proof in heads
        }
        if len(values) > 1:
            ambiguous.add(day)
        elif None not in values:
            selected[day] = heads[0][4][day]
    days = tuple(sorted(selected))
    authoritative = bool(days) and not ambiguous
    if days:
        day = days[0]
        while authoritative and day <= days[-1]:
            if day not in selected and not any(proof[2] <= day <= proof[3] for proof in proofs):
                authoritative = False
            day += timedelta(days=1)
    version = content_fingerprint(
        (
            "calendar-response-coverage-v1",
            tuple(
                sorted(
                    (
                        row["version"],
                        row["session_date"].isoformat(),
                        row["opens_at"],
                        row["closes_at"],
                        row["source"],
                        row["commit_version"],
                    )
                    for row in visible
                )
            ),
            authoritative,
        )
    )
    sessions = tuple(
        ResearchSession(
            day,
            selected[day]["opens_at"],
            selected[day]["closes_at"],
            version,
            selected[day]["source"],
        )
        for day in days
    )
    return CalendarCoverage(
        sessions[0].session_date if sessions else None,
        sessions[-1].session_date if sessions else None,
        sessions,
        version,
        authoritative=authoritative,
    )


def read_calendar(connection, start: datetime, end: datetime, query: PointInTimeQuery):
    # Read whole response groups, including endpoints beyond the query's immediate dates.
    # Limiting complete groups loses coverage conservatively; it never fabricates a proof.
    rows = (
        connection.execute(
            text("""
      WITH visible AS (
        SELECT * FROM research.calendar_versions WHERE commit_version<=:version
          AND ingested_at<=:knowledge AND ingested_at<=:snapshot AND closes_at<=:valuation),
      responses AS (
        SELECT version,max(commit_version) AS newest FROM visible GROUP BY version
        HAVING min(session_date)<=CAST(:end AS date)+2
          AND max(session_date)>=CAST(:start AS date)-2
        ORDER BY newest DESC,version LIMIT 64)
      SELECT v.* FROM visible v JOIN responses r USING(version)
      ORDER BY v.version,v.session_date
    """),
            dict(
                version=query.snapshot_version,
                knowledge=query.knowledge_at,
                snapshot=query.snapshot_at,
                valuation=query.valuation_at,
                start=start,
                end=end,
            ),
        )
        .mappings()
        .all()
    )
    return coverage_from_rows(tuple(rows), query)
