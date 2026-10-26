"""Immutable offline corporate-action certification and PIT quality proofs.

There is deliberately no runtime writer. Certificates are installed by an
offline certifier using a migration owner, never inferred from raw bar prices.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from packages.backtesting.models import BacktestBudgetExceeded
from packages.domain.models import utc_time
from packages.research.models import content_fingerprint


@dataclass(frozen=True)
class CoverageProof:
    coverage_id: UUID
    instrument_id: UUID
    start_at: datetime
    end_at: datetime
    certified_at: datetime
    source_reference: str
    fingerprint: str
    commit_version: int
    ingested_at: datetime
    no_actions: bool = True

    def __post_init__(self):
        for value in (self.start_at, self.end_at, self.certified_at, self.ingested_at):
            utc_time(value)
        if self.end_at <= self.start_at or self.certified_at > self.ingested_at:
            raise ValueError("invalid offline coverage bounds")
        if not isinstance(self.no_actions, bool):
            raise ValueError("typed corporate action conclusion required")


def read_coverage(connection, instrument_id, snapshot):
    rows = tuple(
        connection.execute(
            text("""
        SELECT * FROM backtest.corporate_action_coverage
        WHERE instrument_id=:instrument AND commit_version<=:version
          AND ingested_at<=:snapshot ORDER BY commit_version,coverage_id LIMIT 100001
    """),
            dict(instrument=instrument_id, version=snapshot.version, snapshot=snapshot.at),
        ).mappings()
    )
    if len(rows) > 100000:
        raise BacktestBudgetExceeded("coverage metadata budget exhausted")
    return tuple(
        CoverageProof(**{name: row[name] for name in CoverageProof.__dataclass_fields__})
        for row in rows
    )


def covering_proof(proofs, instrument_id, start, end, knowledge_at, snapshot):
    visible = tuple(
        p
        for p in proofs
        if p.instrument_id == instrument_id
        and p.start_at < end
        and p.end_at > start
        and p.certified_at <= knowledge_at
        and p.ingested_at <= min(knowledge_at, snapshot.at)
        and p.commit_version <= snapshot.version
    )
    # A later negative certificate cannot be bypassed with an older positive one.
    covering = tuple(p for p in visible if p.start_at <= start and p.end_at >= end)
    if not covering:
        return None
    newest = max(p.commit_version for p in covering)
    heads = tuple(p for p in covering if p.commit_version == newest)
    if not all(p.no_actions for p in heads):
        return None
    if any(not p.no_actions and p.commit_version >= newest for p in visible):
        return None
    return min(heads, key=lambda p: p.coverage_id)


def read_quality(connection, instrument_id, start, end, snapshot):
    rows = tuple(
        connection.execute(
            text("""
        SELECT * FROM research.quality_observations
        WHERE instrument_id=:instrument AND interval='15Min'
          AND start_at<:end AND end_at>:start
          AND commit_version<=:version AND ingested_at<=:snapshot
        ORDER BY ingested_at,commit_version,observation_id LIMIT 100001
    """),
            dict(
                instrument=instrument_id,
                start=start,
                end=end,
                version=snapshot.version,
                snapshot=snapshot.at,
            ),
        ).mappings()
    )
    if len(rows) > 100000:
        raise BacktestBudgetExceeded("quality metadata budget exhausted")
    return rows


def quality_at(rows, start, end, knowledge_at, snapshot):
    visible = tuple(
        row
        for row in rows
        if row["ingested_at"] <= min(knowledge_at, snapshot.at)
        and row["commit_version"] <= snapshot.version
    )
    for rejected in visible:
        if rejected["quality"] == "VALID" or not (
            rejected["start_at"] < end and rejected["end_at"] > start
        ):
            continue
        resolved = any(
            v["quality"] == "VALID"
            and v["start_at"] <= rejected["start_at"]
            and v["end_at"] >= rejected["end_at"]
            and v["commit_version"] > rejected["commit_version"]
            for v in visible
        )
        if not resolved:
            return False, ()
    # Valid bar revisions are quality evidence even without an ingestion job.
    refs = tuple(
        str(row["observation_id"])
        for row in visible
        if row["start_at"] < end and row["end_at"] > start
    )
    return True, tuple(sorted(refs)) or (content_fingerprint(("valid-bar-quality-v1",)),)
