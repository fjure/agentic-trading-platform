"""Dauerhafte begrenzte Diagnosen; kein SQLlock während Assessor-CPU-Arbeit."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from packages.backtesting.jobs import BacktestError, encoded
from packages.backtesting.models import BacktestBudgetExceeded
from packages.backtesting.readiness import POLICY_VERSION, TimelinessObservation
from packages.research.models import content_fingerprint


class ReadinessError(BacktestError):
    pass


@dataclass(frozen=True)
class ReadinessLease:
    assessment_id: UUID
    owner: UUID
    fence: int
    row: dict


class ReadinessJobs:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, *, repeatable=False):
        failure = None
        try:
            with self.engine.begin() as connection:
                if repeatable:
                    connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                connection.execute(text("SET LOCAL statement_timeout='2s'"))
                connection.execute(text("SET LOCAL lock_timeout='1s'"))
                yield connection
        except SQLAlchemyError as error:
            message = getattr(
                getattr(getattr(error, "orig", None), "diag", None), "message_primary", None
            )
            status, code = {
                "readiness idempotency conflict": (409, "IDEMPOTENCY_CONFLICT"),
                "readiness queue full": (429, "QUEUE_EXHAUSTED"),
                "invalid readiness request": (400, "INVALID_REQUEST"),
            }.get(message, (503, "UNAVAILABLE"))
            failure = ReadinessError(status, code)
        if failure is not None:
            raise failure from None

    def enqueue(self, request, actor, key, trace, code_version):
        if actor != "local-operator":
            raise ReadinessError(403, "FORBIDDEN")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or not isinstance(trace, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace)
            or not isinstance(code_version, str)
            or not re.fullmatch(r"[0-9a-f]{40}", code_version)
        ):
            raise ReadinessError(400, "INVALID_REQUEST")
        with self.transaction() as connection:
            identifier = connection.execute(
                text("""
                SELECT backtest.request_readiness(:id,:actor,:key,CAST(:request AS jsonb),
                  :hash,:code,:policy,:trace)
            """),
                dict(
                    id=uuid4(),
                    actor=actor,
                    key=key,
                    request=encoded(request),
                    hash=content_fingerprint(request),
                    code=code_version,
                    policy=POLICY_VERSION,
                    trace=trace,
                ),
            ).scalar_one()
            return dict(
                connection.execute(
                    text("SELECT * FROM backtest.readiness_jobs WHERE assessment_id=:id"),
                    dict(id=identifier),
                )
                .mappings()
                .one()
            )

    @staticmethod
    def audit(connection, row, event):
        connection.execute(
            text("""
            INSERT INTO backtest.readiness_audit(assessment_id,event,fence,trace_id)
            VALUES(:id,:event,:fence,:trace)
        """),
            dict(id=row["assessment_id"], event=event, fence=row["fence"], trace=row["trace_id"]),
        )

    def claim(self, owner):
        if not isinstance(owner, UUID):
            raise ValueError("typed owner required")
        with self.transaction() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(894217346)"))
            exhausted = (
                connection.execute(
                    text("""
                UPDATE backtest.readiness_jobs SET status='FAILED',eligibility='UNKNOWN',
                  error_code='ATTEMPTS_EXHAUSTED',owner=NULL,lease_until=NULL,
                  updated_at=clock_timestamp()
                WHERE status='RUNNING' AND lease_until<=clock_timestamp() AND attempts>=3
                RETURNING *
            """)
                )
                .mappings()
                .all()
            )
            for row in exhausted:
                self.audit(connection, row, "FAILED")
            if connection.execute(
                text("""
                SELECT EXISTS(SELECT FROM backtest.runs WHERE status='RUNNING'
                  AND lease_until>clock_timestamp())
                  OR EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING'
                  AND lease_until>clock_timestamp())
            """)
            ).scalar_one():
                return None
            # Admission uses DB-clock order across both bounded queues. The
            # same advisory lock plus SQL trigger also protects legacy claimers.
            first = connection.execute(
                text("""
                SELECT kind FROM (
                  SELECT 'readiness' AS kind,accepted_at,assessment_id AS id
                    FROM backtest.readiness_jobs WHERE status='ACCEPTED'
                    OR(status='RUNNING' AND lease_until<=clock_timestamp() AND attempts<3)
                  UNION ALL SELECT 'backtest',accepted_at,run_id FROM backtest.runs
                    WHERE status='ACCEPTED' OR(status='RUNNING'
                    AND lease_until<=clock_timestamp() AND attempts<3)
                ) q ORDER BY accepted_at,id LIMIT 1
            """)
            ).scalar_one_or_none()
            if first != "readiness":
                return None
            row = (
                connection.execute(
                    text("""
                WITH candidate AS(SELECT assessment_id FROM backtest.readiness_jobs
                  WHERE status='ACCEPTED' OR(status='RUNNING'
                    AND lease_until<=clock_timestamp() AND attempts<3)
                  ORDER BY accepted_at,assessment_id FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE backtest.readiness_jobs r SET status='RUNNING',eligibility='NOT_EVALUATED',
                  error_code=NULL,owner=:owner,fence=fence+1,attempts=attempts+1,
                  started_at=clock_timestamp(),lease_until=clock_timestamp()+interval '300 seconds',
                  updated_at=clock_timestamp()
                FROM candidate c WHERE r.assessment_id=c.assessment_id RETURNING r.*
            """),
                    dict(owner=owner),
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            self.audit(connection, row, "RUNNING")
            return ReadinessLease(row["assessment_id"], owner, row["fence"], dict(row))

    @staticmethod
    def fenced(connection, lease):
        row = (
            connection.execute(
                text("""
            SELECT * FROM backtest.readiness_jobs WHERE assessment_id=:id AND owner=:owner
              AND fence=:fence AND status='RUNNING' AND lease_until>clock_timestamp()
              AND started_at>clock_timestamp()-interval '300 seconds' FOR UPDATE
        """),
                dict(id=lease.assessment_id, owner=lease.owner, fence=lease.fence),
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ReadinessError(409, "LEASE_LOST")
        return row

    def checkpoint(self, lease):
        with self.transaction() as connection:
            self.fenced(connection, lease)

    def bind_trust(self, lease, state):
        with self.transaction() as connection:
            row = self.fenced(connection, lease)
            connection.execute(
                text("""
                UPDATE backtest.readiness_jobs SET trust_version=:version,
                  trust_policy_version=:policy,updated_at=clock_timestamp() WHERE assessment_id=:id
            """),
                dict(id=lease.assessment_id, version=state.version, policy=state.policy_version),
            )
            self.audit(connection, row, "TRUST_BOUND")

    def publish(self, lease, report=None, findings=(), *, error_code=None, references=()):
        if error_code not in (None, "BUDGET_EXHAUSTED", "ATTEMPTS_EXHAUSTED", "INTERNAL_FAILURE"):
            raise ValueError("closed failure reason required")
        if (report is None) != (error_code is not None):
            raise ValueError("complete report or explicit failure required")
        if report is None and findings:
            raise ValueError("failure cannot publish partial findings")
        if len(findings) > 10000:
            raise BacktestBudgetExceeded("finding publication budget exhausted")
        if report is not None:
            if report["findingCount"] != len(findings) or report["assessmentId"] != str(
                lease.assessment_id
            ):
                raise ValueError("publication identity/count mismatch")
            # Include JSONB's whitespace expansion in the conservative byte cap.
            if len(json.dumps(dict(report=report, findings=findings)).encode()) > 12 * 1024 * 1024:
                raise BacktestBudgetExceeded("publication byte budget exhausted")
        with self.transaction() as connection:
            row = self.fenced(connection, lease)
            if report is not None:
                if (
                    report["requestHash"] != row["request_hash"]
                    or report["snapshot"]["snapshotVersion"] != row["snapshot_version"]
                    or report["snapshot"]["trustVersion"] != row["trust_version"]
                ):
                    raise ReadinessError(409, "LEASE_LOST")
                connection.execute(
                    text("""
                    INSERT INTO backtest.readiness_reports(assessment_id,fingerprint,body)
                    VALUES(:id,:hash,CAST(:body AS jsonb))
                """),
                    dict(id=lease.assessment_id, hash=report["reportHash"], body=encoded(report)),
                )
                for index, finding in enumerate(findings):
                    connection.execute(
                        text("""
                        INSERT INTO backtest.readiness_findings
                          (assessment_id,finding_index,category,body)
                        VALUES(:id,:index,:category,CAST(:body AS jsonb))
                    """),
                        dict(
                            id=lease.assessment_id,
                            index=index,
                            category=finding["category"],
                            body=encoded(finding),
                        ),
                    )
                for kind, reference in sorted(set(references)):
                    connection.execute(
                        text("""
                        INSERT INTO backtest.readiness_bindings(assessment_id,kind,reference)
                        VALUES(:id,:kind,:reference)
                    """),
                        dict(id=lease.assessment_id, kind=kind, reference=reference),
                    )
            # Publication must still own an unexpired fence at the END of the
            # atomic transaction. Expiry rolls back every report/finding write.
            self.fenced(connection, lease)
            connection.execute(
                text("""
                UPDATE backtest.readiness_jobs SET status=:status,eligibility=:eligibility,
                  error_code=:error,owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE assessment_id=:id
            """),
                dict(
                    id=lease.assessment_id,
                    status="SUCCEEDED" if report else "FAILED",
                    eligibility=report["eligibility"] if report else "UNKNOWN",
                    error=error_code,
                ),
            )
            self.audit(connection, row, "SUCCEEDED" if report else "FAILED")


def load_observations(connection, instrument_id, start, end, snapshot, revisions):
    """Nur tatsächlich gemessene, im selben Datensnapshot sichtbare Receipts.

    Die Ingestion gehört zur Inhaltsrevision, nicht zum späteren unveränderten
    Abruf. Bestätigung bleibt eine konservative obere Sichtbarkeitsschranke.
    Unbelegte Providerpublikation oder Uhrenvergleichbarkeit bleiben unbekannt.
    """
    known = {str(bar.revision_id): bar for bar in revisions}
    rows = (
        connection.execute(
            text("""
        SELECT r.*,CASE WHEN c.commit_version<=:version AND c.confirmed_at<=:snapshot
          THEN c.confirmed_at ELSE NULL END AS confirmed_at
        FROM research.observation_receipts r
        LEFT JOIN research.commit_confirmations c USING(receipt_id)
        WHERE r.instrument_id=:instrument AND r.state='COMPLETED'
          AND r.event_at>=:start AND r.bar_close_at<=:end
          AND r.commit_version<=:version AND r.ingested_at<=:snapshot
        ORDER BY r.first_observed_at NULLS LAST,r.receipt_id LIMIT 100001
    """),
            dict(
                instrument=instrument_id,
                start=start,
                end=end,
                version=snapshot.version,
                snapshot=snapshot.at,
            ),
        )
        .mappings()
        .all()
    )
    if len(rows) > 100000:
        raise BacktestBudgetExceeded("observation metadata budget exhausted")
    result = []
    for row in rows:
        body = row["body"]
        bar = known.get(body.get("revisionId"))
        if (
            bar is None
            or body.get("contentFingerprint") != bar.fingerprint
            or row["event_at"] != bar.identity.bar_start
            or row["bar_close_at"] != bar.identity.bar_end
        ):
            continue
        first = row["first_observed_at"]
        duplicate = body.get("duplicate", first is not None and first > bar.ingested_at)
        clock_status = body.get("clockStatus", "UNKNOWN")
        if clock_status not in ("COMPARABLE", "UNKNOWN", "INVALID") or type(duplicate) is not bool:
            clock_status, duplicate = "INVALID", False
        result.append(
            TimelinessObservation(
                bar.identity,
                bar.revision_id,
                first,
                bar.ingested_at,
                row["confirmed_at"],
                bar.commit_version,
                None,
                clock_status,
                duplicate,
                row["receipt_id"],
            )
        )
    return tuple(result)
