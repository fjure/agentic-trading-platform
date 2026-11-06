"""Shared read projection of a bound Quant Lab exposure-register prefix."""

import json
from bisect import bisect_left

from sqlalchemy import text

from packages.backtesting.experiment import PHASE_ROLES, SCHEMA_VERSION
from packages.domain.models import utc_string

HISTORY_LIMITATIONS = [
    "PRICE_ONLY",
    "IEX_PARTIAL_MARKET",
    "NO_STATISTICAL_GUARANTEE",
    "NO_TRADING_AUTHORIZATION",
    "EXTERNAL_KNOWLEDGE_UNKNOWN",
]


def empty_counts(planned):
    return dict(planned=planned, started=0, complete=0, failed=0, blocked=0, notEvaluated=0)


def history_bound(connection, *, at=None):
    return (
        connection.execute(
            text("""
        SELECT coalesce(max(exposure_id),0) AS mark,
          encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(e) ORDER BY exposure_id)::text,
            '[]'),'UTF8')),'hex') AS registry_hash,
          coalesce(CAST(:at AS timestamptz),clock_timestamp()) AS bound_at
        FROM backtest.test_exposures e
        WHERE CAST(:at AS timestamptz) IS NULL OR observed_at<=:at
    """),
            {"at": at},
        )
        .mappings()
        .one()
    )


def _interval(start, end):
    return dict(start=utc_string(start), end=utc_string(end))


def _windows(definition):
    return [
        (phase, fold, period[phase])
        for fold, period in enumerate(definition["protocol"]["folds"])
        for phase in ("training", "validation", "test")
    ] + [("holdout", None, definition["protocol"]["holdout"])]


def project_history(
    connection,
    definition,
    *,
    mark=None,
    registry_hash=None,
    bound_at=None,
    candidate_counts=None,
    reference_counts=None,
    phase_bar_starts=None,
    seed_bar_starts=None,
    offset=0,
    limit=200,
):
    """Return page and exact totals; unresolved legacy rows still count as rounds."""
    if mark is None:
        receipt = history_bound(connection)
        mark, registry_hash, bound_at = (
            receipt["mark"],
            receipt["registry_hash"],
            receipt["bound_at"],
        )
    definition_body = definition["body"]
    values, params = (
        [],
        dict(
            mark=mark,
            instrument=definition_body["instrumentId"],
            offset=offset,
            limit=limit,
            seeds=json.dumps(
                [
                    [utc_string(start) for start in starts]
                    for starts in (seed_bar_starts or {}).values()
                    if starts
                ]
            ),
        ),
    )
    roles = dict(PHASE_ROLES)
    windows = _windows(definition_body)
    for index, (phase, _fold, window) in enumerate(windows):
        values.append(
            f"({index},CAST(:start{index} AS timestamptz),"
            f"CAST(:end{index} AS timestamptz),CAST(:role{index} AS text))"
        )
        params.update(
            {
                f"start{index}": window["start"],
                f"end{index}": window["end"],
                f"role{index}": roles[phase],
            }
        )
    phase_keys = [(phase, fold) for phase, fold, _ in windows]
    base = (
        """
        WITH latest AS (
          SELECT DISTINCT ON(source_kind,source_id,phase_key) e.*,
            bool_or(knowledge_status='RESULT_AVAILABLE') OVER
            (PARTITION BY source_kind,source_id,phase_key) AS available
          FROM backtest.test_exposures e WHERE exposure_id<=:mark
          ORDER BY source_kind,source_id,phase_key,exposure_id DESC
        ), rounds AS (
          SELECT DISTINCT ON(source_kind,source_id) source_kind,source_id,status
          FROM latest ORDER BY source_kind,source_id,exposure_id DESC
        ), phase(ordinal,start_at,end_at,role) AS (VALUES """
        + ",".join(values)
        + """), intersections AS (
          SELECT e.*,p.ordinal,p.role,p.start_at AS current_start,p.end_at AS current_end,
            greatest(e.start_at,p.start_at) AS overlap_start,
            least(e.end_at,p.end_at) AS overlap_end,d.fingerprint AS definition_hash
          FROM latest e CROSS JOIN phase p LEFT JOIN backtest.experiment_batches b
            ON e.source_kind='BATCH' AND b.batch_id=e.source_id
          LEFT JOIN backtest.experiment_definitions d ON d.definition_id=b.definition_id
          WHERE e.instrument_id=CAST(:instrument AS uuid) AND e.feed='iex' AND e.interval='15Min'
            AND e.adjustment='raw' AND e.start_at<p.end_at AND p.start_at<e.end_at
        ) """
    )
    totals = (
        connection.execute(
            text(
                base
                + """
        SELECT count(*) AS registered,count(*) FILTER (WHERE status='FAILED') AS failed,
          count(*) FILTER (WHERE status='BLOCKED') AS blocked,
          (SELECT count(DISTINCT d.supersedes_batch_id) FROM rounds q
            JOIN backtest.experiment_batches b ON q.source_kind='BATCH' AND q.source_id=b.batch_id
            JOIN backtest.experiment_definitions d USING(definition_id)
            JOIN rounds prior ON prior.source_kind='BATCH' AND prior.source_id=d.supersedes_batch_id
          ) AS superseded,
          (SELECT count(*) FROM intersections) AS overlap,
          (SELECT count(*) FROM intersections WHERE role='PARAMETER_SELECTION'
            AND research_role='PARAMETER_SELECTION') AS repeated,
          (SELECT count(*) FROM latest e
            CROSS JOIN jsonb_array_elements(CAST(:seeds AS jsonb)) AS phase_seeds(value)
            WHERE e.instrument_id=CAST(:instrument AS uuid) AND e.feed='iex'
              AND e.interval='15Min' AND e.adjustment='raw' AND EXISTS(
                SELECT FROM jsonb_array_elements_text(phase_seeds.value) AS seed(moment)
                WHERE CAST(seed.moment AS timestamptz)>=e.start_at
                  AND CAST(seed.moment AS timestamptz)<e.end_at)) AS warmup
        FROM rounds
    """
            ),
            params,
        )
        .mappings()
        .one()
    )
    rows = connection.execute(
        text(
            base
            + """
        SELECT * FROM intersections
        ORDER BY source_kind COLLATE "C",source_id,phase_key COLLATE "C",ordinal
        LIMIT :limit OFFSET :offset
    """
        ),
        params,
    ).mappings()
    overlaps = []
    for row in rows:
        starts = (phase_bar_starts or {}).get(phase_keys[row["ordinal"]], ())
        count = bisect_left(starts, row["overlap_end"]) - bisect_left(starts, row["overlap_start"])
        overlaps.append(
            dict(
                schemaVersion=SCHEMA_VERSION,
                currentRole=row["role"],
                priorRole=row["research_role"],
                currentInterval=_interval(row["current_start"], row["current_end"]),
                priorInterval=_interval(row["start_at"], row["end_at"]),
                overlapInterval=_interval(row["overlap_start"], row["overlap_end"]),
                priorBatchId=str(row["source_id"]) if row["source_kind"] == "BATCH" else None,
                priorRunId=None if row["source_kind"] == "BATCH" else str(row["source_id"]),
                priorDefinitionHash=row["definition_hash"],
                priorStatus=row["status"],
                knowledgeStatus="RESULT_AVAILABLE" if row["available"] else "REQUESTED",
                overlapBarCount=count,
            )
        )
    return dict(
        schemaVersion=SCHEMA_VERSION,
        registryVersion="quant-exposure-v1",
        registryHash=registry_hash,
        boundAt=utc_string(bound_at),
        registeredRoundCount=totals["registered"],
        priorFailedRoundCount=totals["failed"],
        priorBlockedRoundCount=totals["blocked"],
        supersededRoundCount=totals["superseded"],
        overlapCount=totals["overlap"],
        repeatedValidationCount=totals["repeated"],
        reuseStatus="REGISTERED_REUSE" if totals["overlap"] else "NO_REGISTERED_PRIOR_USE",
        externalKnowledgeStatus="EXTERNAL_KNOWLEDGE_UNKNOWN",
        candidateCounts=candidate_counts or empty_counts(definition["candidate_count"]),
        referenceCounts=reference_counts or empty_counts(2),
        overlaps=overlaps,
        nextOverlapCursor=None,
        warmupOverlapCount=totals["warmup"],
        limitations=HISTORY_LIMITATIONS,
    )
