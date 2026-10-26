"""PIT-bound rejection metadata. Rejected provider values are never retained."""

from uuid import UUID, uuid5

from sqlalchemy import text

from packages.research.models import QualityStatus, content_fingerprint
from packages.research.quality import QUALITY_RULE_VERSION

_NAMESPACE = UUID("45d14744-f9ef-4c7c-9bdb-661d19adcd6e")
_CODES = {
    QualityStatus.VALID: "NORMALIZED_JOB_COMPLETED",
    QualityStatus.INVALID: "REJECTED_PROVIDER_RESPONSE",
    QualityStatus.CONFLICT: "PROVIDER_REVISION_CONFLICT",
}


def observe(connection, lease, quality):
    if quality not in _CODES:
        raise ValueError("unsupported quality observation")
    request = lease.request
    # Repeated error handling under one fence is idempotent. Later attempts are
    # separate immutable observations; no provider token or rejected value enters SQL.
    observation = uuid5(_NAMESPACE, content_fingerprint((lease.job_id, lease.fence, quality)))
    connection.execute(text("SELECT research.reserve_version()"))
    connection.execute(
        text("""
      INSERT INTO research.quality_observations(observation_id,job_id,instrument_id,
        start_at,end_at,interval,quality,rule_version,code)
      VALUES(:id,:job,:instrument,:start,:end,:interval,:quality,:rule,:code)
      ON CONFLICT(observation_id) DO NOTHING
    """),
        dict(
            id=observation,
            job=lease.job_id,
            instrument=UUID(request["instrumentId"]),
            start=request["start"],
            end=request["end"],
            interval=request["interval"],
            quality=quality.value,
            rule=QUALITY_RULE_VERSION,
            code=_CODES[quality],
        ),
    )


def rejected_quality(connection, instrument, start, end, interval, query):
    """A later completed rerun resolves its range; old snapshots retain the rejection.

    A completion must cover the entire rejected range. An unrelated successful
    window cannot rehabilitate a rejected response. No fabricated bars are stored.
    """
    result = connection.execute(
        text("""
      SELECT q.quality FROM research.quality_observations q
      WHERE q.instrument_id=:instrument AND q.interval=:interval
        AND q.start_at<:end AND q.end_at>:start AND q.quality<>'VALID'
        AND q.ingested_at<=:knowledge AND q.ingested_at<=:snapshot
        AND q.commit_version<=:version
        AND NOT EXISTS(SELECT 1 FROM research.quality_observations v
          WHERE v.instrument_id=q.instrument_id AND v.interval=q.interval AND v.quality='VALID'
            AND v.start_at<=q.start_at AND v.end_at>=q.end_at
            AND v.commit_version>q.commit_version AND v.commit_version<=:version
            AND v.ingested_at<=:knowledge AND v.ingested_at<=:snapshot)
      ORDER BY CASE q.quality WHEN 'CONFLICT' THEN 0 ELSE 1 END LIMIT 1
    """),
        dict(
            instrument=instrument,
            start=start,
            end=end,
            interval=interval.value,
            knowledge=query.knowledge_at,
            snapshot=query.snapshot_at,
            version=query.snapshot_version,
        ),
    ).scalar_one_or_none()
    return QualityStatus(result) if result else None
