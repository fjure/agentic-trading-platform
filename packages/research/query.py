"""Read-only committed research snapshots and bounded keyset queries."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.domain.models import utc_time
from packages.research.models import (
    BarInterval,
    BarRevision,
    PointInTimeQuery,
    ResearchEvidence,
    content_fingerprint,
)
from packages.research.storage import bar_from_row, evidence_for_bar

# All raw IEX natural identities are retained in the partition, including provider.
_PARTITION = "instrument_id,provider,feed,interval,bar_start,bar_end,adjustment"
_ELIGIBLE = """bar_end<=:valuation_at AND ingested_at<=:knowledge_at
 AND ingested_at<=:snapshot_at AND commit_version<=:snapshot_version AND quality='VALID'
 AND adjustment='raw' AND feed='iex'"""


@dataclass(frozen=True)
class BarPosition:
    """Internal keyset only; an HTTP layer must authenticate and bind filters/snapshot."""

    bar_start: datetime
    bar_end: datetime
    provider: str
    revision_id: UUID
    filter_fingerprint: str

    def __post_init__(self) -> None:
        utc_time(self.bar_start)
        utc_time(self.bar_end)
        if not isinstance(self.provider, str) or not self.provider:
            raise ValueError("provider required")
        if not isinstance(self.revision_id, UUID):
            raise ValueError("revision UUID required")
        if not isinstance(self.filter_fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.filter_fingerprint
        ):
            raise ValueError("bound filter fingerprint required")


@dataclass(frozen=True)
class BarPage:
    items: tuple[BarRevision, ...]
    next_position: BarPosition | None


def _bounds(query: PointInTimeQuery) -> dict:
    return {
        "valuation_at": query.valuation_at,
        "knowledge_at": query.knowledge_at,
        "snapshot_at": query.snapshot_at,
        "snapshot_version": query.snapshot_version,
    }


def filter_fingerprint(
    instrument_id: UUID,
    start: datetime,
    end: datetime,
    interval: BarInterval,
    query: PointInTimeQuery,
) -> str:
    """Bind an internal position to every selection boundary and fixed feed/adjustment."""
    utc_time(start)
    utc_time(end)
    if not isinstance(instrument_id, UUID) or not isinstance(interval, BarInterval):
        raise ValueError("typed instrument and interval required")
    if not isinstance(query, PointInTimeQuery):
        raise ValueError("typed PIT query required")
    return content_fingerprint(
        ("research-bars-v1", instrument_id, start, end, interval, "iex", "raw", query)
    )


def latest_visible(
    bars: tuple[BarRevision, ...], query: PointInTimeQuery
) -> tuple[BarRevision, ...]:
    """Pure selection oracle shared by offline PIT certification, independent of SQL."""
    latest: dict = {}
    for bar in bars:
        if query.includes(bar):
            current = latest.get(bar.identity)
            if current is None or bar.local_revision > current.local_revision:
                latest[bar.identity] = bar
    return tuple(
        sorted(
            latest.values(),
            key=lambda bar: (
                bar.identity.bar_start,
                bar.identity.bar_end,
                bar.identity.provider,
                bar.revision_id,
            ),
        )
    )


class ResearchQuery:
    def snapshot(
        self,
        connection: Connection,
        valuation_at: datetime,
        knowledge_at: datetime,
    ) -> PointInTimeQuery:
        # One read sees only committed version_state, including during another writer's lock.
        row = (
            connection.execute(
                text(
                    "SELECT version,clock_timestamp() AS snapshot_at FROM research.version_state "
                    "WHERE singleton"
                )
            )
            .mappings()
            .one()
        )
        return PointInTimeQuery(valuation_at, knowledge_at, row["snapshot_at"], row["version"])

    def list_bars(
        self,
        connection: Connection,
        instrument_id: UUID,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        query: PointInTimeQuery,
        limit: int = 100,
        after: BarPosition | None = None,
    ) -> BarPage:
        utc_time(start)
        utc_time(end)
        if not timedelta(0) < end - start <= timedelta(days=366):
            raise ValueError("nonempty query range is limited to 366 days")
        if not isinstance(instrument_id, UUID) or not isinstance(interval, BarInterval):
            raise ValueError("typed instrument and interval required")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("query page limit outside [1,200]")
        fingerprint = filter_fingerprint(instrument_id, start, end, interval, query)
        parameters = {
            **_bounds(query),
            "instrument_id": instrument_id,
            "start": start,
            "end": end,
            "interval": interval.value,
            "limit": limit + 1,
        }
        position = ""
        if after is not None:
            if not isinstance(after, BarPosition):
                raise ValueError("typed internal keyset position required")
            if after.filter_fingerprint != fingerprint:
                raise ValueError("keyset position does not match query filters or snapshot")
            parameters.update(
                after_start=after.bar_start,
                after_end=after.bar_end,
                after_provider=after.provider,
                after_id=after.revision_id,
            )
            position = (
                " AND (bar_start,bar_end,provider,revision_id) > "
                "(:after_start,:after_end,:after_provider,:after_id)"
            )
        rows = (
            connection.execute(
                text(f"""
            WITH ranked AS (
              SELECT *,row_number() OVER (PARTITION BY {_PARTITION}
                ORDER BY local_revision DESC) AS rank
              FROM research.bar_revisions WHERE {_ELIGIBLE}
                AND instrument_id=:instrument_id AND interval=:interval
                AND bar_start>=:start AND bar_start<:end AND bar_end<=:end)
            SELECT * FROM ranked WHERE rank=1 {position}
            ORDER BY bar_start,bar_end,provider,revision_id LIMIT :limit
        """),
                parameters,
            )
            .mappings()
            .all()
        )
        items = tuple(bar_from_row(row) for row in rows[:limit])
        next_position = None
        if len(rows) > limit:
            last = items[-1]
            next_position = BarPosition(
                last.identity.bar_start,
                last.identity.bar_end,
                last.identity.provider,
                last.revision_id,
                fingerprint,
            )
        return BarPage(items, next_position)

    def get_evidence(
        self,
        connection: Connection,
        evidence_id: UUID,
        query: PointInTimeQuery,
    ) -> ResearchEvidence | None:
        if not isinstance(evidence_id, UUID):
            raise ValueError("evidence UUID required")
        parameters = {**_bounds(query), "evidence_id": evidence_id}
        row = (
            connection.execute(
                text(f"""
            WITH ranked AS (
              SELECT *,row_number() OVER (PARTITION BY {_PARTITION}
                ORDER BY local_revision DESC) AS rank
              FROM research.bar_revisions WHERE {_ELIGIBLE})
            SELECT b.*,e.evidence_id,e.source_reference,
              e.ingested_at AS evidence_ingested_at,e.commit_version AS evidence_commit_version
            FROM ranked b JOIN research.evidence e ON e.revision_id=b.revision_id
            WHERE b.rank=1 AND e.evidence_id=:evidence_id
              AND e.ingested_at<=:knowledge_at AND e.ingested_at<=:snapshot_at
              AND e.commit_version<=:snapshot_version
        """),
                parameters,
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        evidence = evidence_for_bar(
            bar_from_row(row),
            row["source_reference"],
            ingested_at=row["evidence_ingested_at"],
            commit_version=row["evidence_commit_version"],
        )
        if evidence.evidence_id != row["evidence_id"]:
            raise ValueError("stored evidence identity is inconsistent")
        return evidence
