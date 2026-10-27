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
    managed_trust_state: str | None = None

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
    managed = read_managed_evidence(connection, instrument_id, snapshot, "CORPORATE_ACTION")
    if len(rows) + len(managed) > 100000:
        raise BacktestBudgetExceeded("coverage metadata budget exhausted")
    legacy = tuple(
        CoverageProof(
            **{name: row[name] for name in CoverageProof.__dataclass_fields__ if name in row}
        )
        for row in rows
    )
    imported = tuple(
        CoverageProof(
            row["evidence_id"],
            row["instrument_id"],
            row["start_at"],
            row["end_at"],
            row["source_observed_at"],
            str(row["evidence_id"]),
            row["fingerprint"],
            row["commit_version"],
            row["ingested_at"],
            row["statement"]["conclusion"] == "NO_ACTIONS",
            row["managed_trust_state"],
        )
        for row in managed
    )
    return tuple(sorted((*legacy, *imported), key=lambda p: (p.commit_version, p.coverage_id)))


def managed_trust_state(record):
    """Fail closed for normalized imported evidence, even without a caller hook."""
    if record is None:
        return "TRUST_UNKNOWN"
    if record.get("revoked", False):
        return "EVIDENCE_REVOKED"
    dimensions = record["dimensions"]
    if any(item["status"] == "REJECTED" for item in dimensions.values()):
        return "TRUST_REJECTED"
    if dimensions["temporalAuthenticity"]["status"] == "UNKNOWN":
        return "TIME_UNPROVEN"
    if any(item["status"] == "UNKNOWN" for item in dimensions.values()):
        return "TRUST_UNKNOWN"
    if any(item["status"] == "NOT_EVALUATED" for item in dimensions.values()):
        return "TRUST_NOT_EVALUATED"
    return "VERIFIED"


def read_managed_evidence(connection, instrument_id, snapshot, kind, start=None, end=None):
    if not managed_evidence_installed(connection):
        return ()
    from packages.research.evidence_storage import EvidenceBudgetExceeded, read_trust_snapshot

    try:
        state = read_trust_snapshot(connection, snapshot.at, snapshot.version)
    except EvidenceBudgetExceeded as error:
        raise BacktestBudgetExceeded("trust metadata budget exhausted") from error
    trust = {record["reference"]: record for record in state["records"]}
    rows = tuple(
        connection.execute(
            text("""
        SELECT * FROM research.evidence_revisions
        WHERE instrument_id=:instrument AND kind=:kind
          AND commit_version<=:version AND ingested_at<=:snapshot
          AND (CAST(:start AS timestamptz) IS NULL OR end_at>:start)
          AND (CAST(:end AS timestamptz) IS NULL OR start_at<:end)
        ORDER BY commit_version,evidence_id LIMIT 100001
    """),
            dict(
                instrument=instrument_id,
                kind=kind,
                version=snapshot.version,
                snapshot=snapshot.at,
                start=start,
                end=end,
            ),
        ).mappings()
    )
    if len(rows) > MAX_METADATA_ROWS:
        raise BacktestBudgetExceeded("managed evidence metadata budget exhausted")
    return tuple(
        {**row, "managed_trust_state": managed_trust_state(trust.get(str(row["evidence_id"])))}
        for row in rows
    )


MAX_METADATA_ROWS = 100000


def managed_evidence_installed(connection):
    """RDR-02 remains usable before the additive RDR-03 migration/module."""
    return connection.execute(
        text("SELECT to_regclass('research.evidence_revisions') IS NOT NULL")
    ).scalar_one()


@dataclass(frozen=True)
class CoverageResult:
    """Explain a proof without changing the legacy optional-proof boundary."""

    state: str
    proof: CoverageProof | None
    references: tuple[str, ...] = ()


def coverage_result(proofs, instrument_id, start, end, knowledge_at, snapshot, trust_check=None):
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
        return CoverageResult(
            "NEGATIVE" if any(not p.no_actions for p in visible) else "UNKNOWN",
            None,
            tuple(str(p.coverage_id) for p in visible),
        )
    newest = max(p.commit_version for p in covering)
    heads = tuple(p for p in covering if p.commit_version == newest)
    if not all(p.no_actions for p in heads):
        state = "CONFLICT" if any(p.no_actions for p in heads) else "NEGATIVE"
        return CoverageResult(state, None, tuple(str(p.coverage_id) for p in heads))
    if any(not p.no_actions and p.commit_version >= newest for p in visible):
        return CoverageResult("CONFLICT", None, tuple(str(p.coverage_id) for p in visible))
    selected = min(heads, key=lambda p: p.coverage_id)
    if selected.managed_trust_state not in (None, "VERIFIED"):
        return CoverageResult(selected.managed_trust_state, None, (str(selected.coverage_id),))
    if trust_check is not None:
        reason = trust_check("CORPORATE_ACTIONS_REVISION", str(selected.coverage_id), knowledge_at)
        if reason is not None:
            return CoverageResult(reason, None, (str(selected.coverage_id),))
    return CoverageResult("VERIFIED", selected, (str(selected.coverage_id),))


def covering_proof(proofs, instrument_id, start, end, knowledge_at, snapshot, trust_check=None):
    return coverage_result(
        proofs, instrument_id, start, end, knowledge_at, snapshot, trust_check
    ).proof


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
    managed = read_managed_evidence(connection, instrument_id, snapshot, "QUALITY", start, end)
    if len(rows) + len(managed) > 100000:
        raise BacktestBudgetExceeded("quality metadata budget exhausted")
    imported = tuple(
        {
            "observation_id": row["evidence_id"],
            "start_at": row["start_at"],
            "end_at": row["end_at"],
            "quality": row["statement"]["quality"],
            "ingested_at": row["ingested_at"],
            "commit_version": row["commit_version"],
            "managed_trust_state": row["managed_trust_state"],
        }
        for row in managed
    )
    return tuple(
        sorted(
            (*rows, *imported),
            key=lambda row: (row["ingested_at"], row["commit_version"], row["observation_id"]),
        )
    )


@dataclass(frozen=True)
class QualityResult:
    state: str
    references: tuple[str, ...]

    @property
    def okay(self):
        return self.state == "VERIFIED"


def quality_result(rows, start, end, knowledge_at, snapshot, trust_check=None):
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
            and v.get("managed_trust_state") in (None, "VERIFIED")
            and (
                trust_check is None
                or trust_check("QUALITY_REVISION", str(v["observation_id"]), knowledge_at) is None
            )
            and v["start_at"] <= rejected["start_at"]
            and v["end_at"] >= rejected["end_at"]
            and v["commit_version"] > rejected["commit_version"]
            for v in visible
        )
        if not resolved:
            return QualityResult("REJECTED", (str(rejected["observation_id"]),))
    for row in visible:
        if (
            row.get("managed_trust_state") not in (None, "VERIFIED")
            and row["start_at"] < end
            and row["end_at"] > start
        ):
            return QualityResult(row["managed_trust_state"], (str(row["observation_id"]),))
    # Valid bar revisions are quality evidence even without an ingestion job.
    refs = tuple(
        str(row["observation_id"])
        for row in visible
        if row["start_at"] < end and row["end_at"] > start
    )
    if trust_check is not None:
        for reference in sorted(refs):
            reason = trust_check("QUALITY_REVISION", reference, knowledge_at)
            if reason is not None:
                return QualityResult(reason, (reference,))
    return QualityResult(
        "VERIFIED", tuple(sorted(refs)) or (content_fingerprint(("valid-bar-quality-v1",)),)
    )


def quality_at(rows, start, end, knowledge_at, snapshot, trust_check=None):
    result = quality_result(rows, start, end, knowledge_at, snapshot, trust_check)
    return result.okay, result.references if result.okay else ()
