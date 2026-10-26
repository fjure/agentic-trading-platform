"""Bounded persistent simulations. No transaction spans simulation CPU work."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from packages.backtesting.models import BacktestBudgetExceeded
from packages.research.models import _canonical, content_fingerprint


class BacktestError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass(frozen=True)
class Lease:
    run_id: UUID
    owner: UUID
    fence: int
    row: dict


def encoded(value):
    result = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(result.encode("utf-8")) > 12 * 1024 * 1024:
        raise BacktestBudgetExceeded("serialized publication budget exhausted")
    return result


class BacktestJobs:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self):
        failure = None
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout='2s'"))
                connection.execute(text("SET LOCAL lock_timeout='1s'"))
                yield connection
        except SQLAlchemyError as error:
            message = getattr(
                getattr(getattr(error, "orig", None), "diag", None), "message_primary", None
            )
            failure = (
                BacktestError(409, "IDEMPOTENCY_CONFLICT")
                if (message == "backtest idempotency conflict")
                else BacktestError(503, "UNAVAILABLE")
            )
        if failure:
            raise failure from None

    def enqueue(self, request, actor, key, trace, code_version):
        # Domain validation precedes this port; no client-supplied snapshot is accepted.
        if (
            actor != "local-operator"
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or not re.fullmatch(r"[0-9a-f]{32}", trace)
            or not re.fullmatch(r"[0-9a-f]{40}", code_version)
        ):
            raise BacktestError(400, "INVALID_REQUEST")
        with self.transaction() as connection:
            run_id = connection.execute(
                text("""
              SELECT backtest.request_run(:id,:actor,:key,
                CAST(:request AS jsonb),:hash,:code,:trace)
            """),
                dict(
                    id=uuid4(),
                    actor=actor,
                    key=key,
                    request=encoded(request),
                    hash=content_fingerprint(request),
                    code=code_version,
                    trace=trace,
                ),
            ).scalar_one()
            return dict(
                connection.execute(
                    text("SELECT * FROM backtest.runs WHERE run_id=:id"), dict(id=run_id)
                )
                .mappings()
                .one()
            )

    def claim(self, owner):
        if not isinstance(owner, UUID):
            raise ValueError("typed owner required")
        with self.transaction() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(894217346)"))
            # Attempts are bounded even if the final worker dies before publication.
            exhausted = (
                connection.execute(
                    text("""
              UPDATE backtest.runs SET status='FAILED',error_code='BUDGET_EXHAUSTED',
                owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
              WHERE status='RUNNING' AND lease_until<=clock_timestamp() AND attempts>=3
              RETURNING run_id,fence,trace_id
            """)
                )
                .mappings()
                .all()
            )
            for row in exhausted:
                self.audit(connection, row, "FAILED")
            if connection.execute(
                text("""SELECT EXISTS(SELECT FROM backtest.runs
              WHERE status='RUNNING' AND lease_until>clock_timestamp())""")
            ).scalar_one():
                return None
            row = (
                connection.execute(
                    text("""
              WITH candidate AS (SELECT run_id FROM backtest.runs
                WHERE status='ACCEPTED' OR
                  (status='RUNNING' AND lease_until<=clock_timestamp() AND attempts<3)
                ORDER BY accepted_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1)
              UPDATE backtest.runs r SET status='RUNNING',owner=:owner,fence=fence+1,
                attempts=attempts+1,started_at=clock_timestamp(),
                lease_until=clock_timestamp()+interval '300 seconds',updated_at=clock_timestamp()
              FROM candidate c WHERE r.run_id=c.run_id RETURNING r.*
            """),
                    dict(owner=owner),
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            self.audit(connection, row, "RUNNING")
            return Lease(row["run_id"], owner, row["fence"], dict(row))

    @staticmethod
    def audit(connection, row, event):
        connection.execute(
            text("""INSERT INTO backtest.audit(run_id,event,fence,trace_id)
          VALUES(:run_id,:event,:fence,:trace_id)"""),
            dict(run_id=row["run_id"], fence=row["fence"], trace_id=row["trace_id"], event=event),
        )

    @staticmethod
    def fenced(connection, lease):
        row = (
            connection.execute(
                text("""SELECT * FROM backtest.runs WHERE run_id=:id
          AND owner=:owner AND fence=:fence AND status='RUNNING'
          AND lease_until>clock_timestamp()
          AND started_at>clock_timestamp()-interval '300 seconds' FOR UPDATE"""),
                dict(id=lease.run_id, owner=lease.owner, fence=lease.fence),
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BacktestError(409, "LEASE_LOST")
        return row

    def bind_manifest(self, lease, manifest):
        fingerprint = content_fingerprint(manifest)
        with self.transaction() as connection:
            self.fenced(connection, lease)
            previous = connection.execute(
                text("SELECT fingerprint FROM backtest.manifests WHERE run_id=:id"),
                dict(id=lease.run_id),
            ).scalar_one_or_none()
            if previous is not None:
                if previous != fingerprint:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                return
            connection.execute(
                text("""INSERT INTO backtest.manifests(run_id,fingerprint,body)
              VALUES(:id,:hash,CAST(:body AS jsonb))"""),
                dict(id=lease.run_id, hash=fingerprint, body=encoded(manifest)),
            )

    def checkpoint(self, lease, index, fold):
        with self.transaction() as connection:
            row = self.fenced(connection, lease)
            previous = connection.execute(
                text("""SELECT body FROM backtest.fold_results
              WHERE run_id=:id AND fold_index=:index"""),
                dict(id=lease.run_id, index=index),
            ).scalar_one_or_none()
            if previous is not None:
                if content_fingerprint(previous) != content_fingerprint(fold):
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                return
            connection.execute(
                text("""INSERT INTO backtest.fold_results(run_id,fold_index,body)
              VALUES(:id,:index,CAST(:body AS jsonb))"""),
                dict(id=lease.run_id, index=index, body=encoded(fold)),
            )
            connection.execute(
                text(
                    "UPDATE backtest.runs SET checkpoint=checkpoint+1,"
                    "updated_at=clock_timestamp() WHERE run_id=:id"
                ),
                dict(id=lease.run_id),
            )
            self.audit(connection, row, "CHECKPOINT")

    def publish(self, lease, result, status="SUCCEEDED", error_code=None):
        if status not in {"SUCCEEDED", "BLOCKED", "FAILED"}:
            raise ValueError("terminal status required")
        if (status == "SUCCEEDED") != (error_code is None):
            raise ValueError("explicit failure reason required")
        with self.transaction() as connection:
            row = self.fenced(connection, lease)
            connection.execute(
                text("""INSERT INTO backtest.results(run_id,fingerprint,body)
              VALUES(:id,:hash,CAST(:body AS jsonb))"""),
                dict(id=lease.run_id, hash=content_fingerprint(result), body=encoded(result)),
            )
            connection.execute(
                text("""UPDATE backtest.runs SET status=:status,error_code=:error,
              owner=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=:id"""),
                dict(id=lease.run_id, status=status, error=error_code),
            )
            self.audit(connection, row, status)
