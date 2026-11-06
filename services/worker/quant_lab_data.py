"""Accepted-snapshot data and evidence ports for the Quant Lab worker."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text

from packages.backtesting.calendar import read_schedule_rows, schedule_windows
from packages.backtesting.coverage import read_coverage, read_quality
from packages.backtesting.dataset import (
    FrozenDataset,
    Snapshot,
    materialize_quant_phases,
    quant_phase_history_start,
)
from packages.backtesting.experiment import QuantExperimentManifest
from packages.backtesting.models import BacktestBlocked, BacktestBudgetExceeded
from packages.backtesting.readiness import TrustSnapshot
from packages.backtesting.retrospective import (
    RetrospectiveDataset,
    materialize_quant_retrospective_phases,
    restore_retrospective_calendar,
)
from packages.domain.models import utc_string
from packages.research.evidence_storage import read_trust_snapshot
from packages.research.storage import bar_from_row

MAX_ROWS = 100_000


def _read_rows(connection, query, params, room):
    if room < 0:
        raise BacktestBudgetExceeded("quant snapshot row budget exhausted")
    rows = tuple(
        connection.execute(text(query + " LIMIT :limit"), dict(params, limit=room + 1)).mappings()
    )
    if len(rows) > room:
        raise BacktestBudgetExceeded("quant snapshot row budget exhausted")
    return rows


@dataclass(frozen=True)
class QuantInputs:
    manifest: QuantExperimentManifest
    phases: tuple
    calendars: tuple
    references: dict
    license_status: str


def load_quant_inputs(storage, lease, definition, receipt, checkpoint, deadline):
    """Fetch one pinned DB cap. Resolve/materialize all chronological phases outside SQL."""
    from services.worker.retrospective_backtesting import (
        managed_calendars,
        restore_trust,
        snapshot_trust,
    )

    snapshot = Snapshot(lease.row["snapshot_version"], lease.row["snapshot_at"])
    strict = definition.research_mode == "STRICT_PIT"
    end = definition.protocol.holdout.end
    params = dict(
        instrument=definition.instrument_id,
        version=snapshot.version,
        at=snapshot.at,
        earliest=end - timedelta(days=366),
        end=end,
    )
    accepted_trust = receipt["trust_body"]
    license_status = receipt["license_body"]["status"]
    if license_status in ("DENIED", "BLOCKED"):
        raise BacktestBlocked("LICENSE_DENIED")
    with storage.transaction() as connection:
        calendars = (
            read_schedule_rows(connection, snapshot)
            if strict
            else _read_rows(
                connection,
                "SELECT * FROM research.calendar_versions WHERE commit_version<=:version "
                "AND ingested_at<=:at ORDER BY commit_version,session_id",
                params,
                MAX_ROWS,
            )
        )
        evidence = _read_rows(
            connection,
            "SELECT * FROM research.evidence_revisions WHERE (instrument_id=:instrument OR "
            "kind='CALENDAR') AND (kind='CALENDAR' OR (start_at<:end AND end_at>:earliest)) "
            "AND commit_version<=:version AND ingested_at<=:at ORDER BY commit_version,evidence_id",
            params,
            MAX_ROWS - len(calendars) - len(accepted_trust["decisions"]),
        )
        if not strict:
            state = snapshot_trust(
                connection,
                dict(
                    trust_version=accepted_trust["version"],
                    trust_policy_version=accepted_trust["policyVersion"],
                    snapshot_at=snapshot.at,
                ),
                evidence,
                deadline,
            )
    if not strict:
        trust = restore_trust(snapshot, state, evidence)
        imported = managed_calendars(evidence)
        if any(not trust.permits_content(r["evidence_id"]) for r in imported):
            raise BacktestBlocked("BLOCKED_CALENDAR_UNKNOWN")
        calendars += tuple(dict(r, managed_trust_state=None) for r in imported)
    starts = tuple(
        quant_phase_history_start(
            snapshot, window, definition.warmup_count, calendars, strict=strict
        )
        for _, _, _, window in definition.protocol.phases
    )
    params["start"] = max(params["earliest"], min(starts))
    with storage.transaction() as connection:
        if strict:
            trust = TrustSnapshot.from_mapping(
                read_trust_snapshot(connection, snapshot.at, snapshot.version)
            )
            quality = read_quality(
                connection, definition.instrument_id, params["start"], end, snapshot
            )
            proofs = read_coverage(connection, definition.instrument_id, snapshot)
        else:
            quality, proofs = (), ()
        raw_quality = _read_rows(
            connection,
            "SELECT q.*,encode(sha256(convert_to(to_jsonb(q)::text,'UTF8')),'hex') "
            "AS fingerprint "
            "FROM research.quality_observations q WHERE instrument_id=:instrument AND "
            "interval='15Min' AND start_at<:end AND end_at>:start AND commit_version<=:version "
            "AND ingested_at<=:at ORDER BY commit_version,observation_id",
            params,
            MAX_ROWS,
        )
        rows = _read_rows(
            connection,
            "SELECT * FROM research.bar_revisions WHERE instrument_id=:instrument AND "
            "provider='alpaca' AND feed='iex' AND interval='15Min' AND adjustment='raw' "
            "AND bar_start>=:start AND bar_end<=:end AND commit_version<=:version "
            "AND ingested_at<=:at ORDER BY bar_start,local_revision,revision_id",
            params,
            MAX_ROWS
            - sum(
                map(
                    len,
                    (
                        calendars,
                        evidence,
                        quality if strict else raw_quality,
                        proofs,
                        accepted_trust["decisions"],
                    ),
                )
            ),
        )
    bars = tuple(bar_from_row(row) for row in rows)
    if strict:
        phases = materialize_quant_phases(
            FrozenDataset(snapshot, bars),
            definition,
            calendars,
            quality,
            proofs,
            checkpoint,
            trust.gate,
        )
    else:
        periods = tuple(
            (start if definition.warmup_count else window.start, window.end)
            for start, (_, _, _, window) in zip(starts, definition.protocol.phases, strict=True)
        )

        def overlaps(row):
            return any(row["start_at"] < end and row["end_at"] > start for start, end in periods)

        evidence = tuple(r for r in evidence if r["kind"] == "CALENDAR" or overlaps(r))
        raw_quality = tuple(r for r in raw_quality if overlaps(r))
        relevant = {str(r["evidence_id"]) for r in evidence}
        # The exploratory mode still blocks explicitly rejected or revoked inputs.
        if any(
            r.reference in relevant
            and (r.revoked or any(value == "REJECTED" for _, value in r.dimensions))
            for r in trust.records
        ):
            raise BacktestBlocked("EVIDENCE_REVOKED")
        if any(r["quality"] != "VALID" for r in raw_quality) or any(
            r["kind"] == "QUALITY"
            and r["statement"].get("quality") in ("INVALID", "CONFLICT", "MISSING_EXPECTED")
            for r in evidence
        ):
            raise BacktestBlocked("BLOCKED_QUALITY")
        phases = materialize_quant_retrospective_phases(
            RetrospectiveDataset(snapshot, bars),
            definition,
            calendars,
            trust_context=trust,
            checkpoint=checkpoint,
        )
    manifest = QuantExperimentManifest.from_phase_datasets(
        definition,
        phases,
        snapshot,
        code_version=lease.row["code_version"],
        trust_fingerprint=receipt["trust_fingerprint"],
        license_receipt_fingerprint=receipt["license_fingerprint"],
        quality_revisions=(
            tuple(str(r["evidence_id"]) for r in evidence if r["kind"] == "QUALITY")
            + tuple(str(r["observation_id"]) for r in raw_quality)
        )
        if not strict
        else (),
        coverage_revisions=tuple(
            str(r["evidence_id"]) for r in evidence if r["kind"] == "CORPORATE_ACTION"
        )
        if not strict
        else (),
    )
    references = _references(manifest, rows, calendars, raw_quality, proofs, evidence)
    phase_calendars = tuple(
        schedule_windows(
            calendars,
            p.binding.window.start,
            p.binding.window.end,
            p.dataset.steps[0].knowledge_at,
            snapshot,
        )[0]
        if strict
        else restore_retrospective_calendar(
            calendars, p.dataset.calendar_references, snapshot, trust
        )[0]
        for p in phases
    )
    return QuantInputs(manifest, phases, phase_calendars, references, license_status)


def _references(manifest, rows, calendars, quality, proofs, evidence):
    bar_ids = {r.revision_id for r in (*manifest.decision_data, *manifest.outcome_data)}
    calendar_ids = set(manifest.calendar_revisions)
    quality_ids = set(manifest.quality_revisions)
    coverage_ids = set(manifest.coverage_revisions)
    references = {}

    def add(kind, identifier, fingerprint, recorded, start, end, revision=None, public_kind=None):
        references[kind, identifier] = dict(
            kind=public_kind or kind,
            referenceId=str(identifier),
            fingerprint=fingerprint,
            localRevision=revision,
            recordedAt=utc_string(recorded),
            period=dict(start=utc_string(start), end=utc_string(end)),
        )

    for r in rows:
        if r["revision_id"] in bar_ids:
            add(
                "BAR",
                r["revision_id"],
                r["fingerprint"],
                r["ingested_at"],
                r["bar_start"],
                r["bar_end"],
                r["local_revision"],
            )
    for r in calendars:
        if "evidence_id" not in r and (
            r["version"] in calendar_ids or str(r["session_id"]) in calendar_ids
        ):
            add(
                "CALENDAR",
                r["session_id"],
                r["version"],
                r["ingested_at"],
                r["opens_at"],
                r["closes_at"],
            )
    for r in quality:
        if str(r["observation_id"]) in quality_ids:
            add(
                "QUALITY",
                r["observation_id"],
                r["fingerprint"],
                r["ingested_at"],
                r["start_at"],
                r["end_at"],
            )
    for r in proofs:
        if r.managed_trust_state is None and str(r.coverage_id) in coverage_ids:
            add(
                "COVERAGE",
                r.coverage_id,
                r.fingerprint,
                r.ingested_at,
                r.start_at,
                r.end_at,
                public_kind="CORPORATE_ACTION",
            )
    for r in evidence:
        if str(r["evidence_id"]) in calendar_ids | quality_ids | coverage_ids or (
            r["kind"] == "CALENDAR" and r["fingerprint"] in calendar_ids
        ):
            add(
                "EVIDENCE",
                r["evidence_id"],
                r["fingerprint"],
                r["ingested_at"],
                r["start_at"],
                r["end_at"],
                r["revision"],
                r["kind"],
            )
    return references
