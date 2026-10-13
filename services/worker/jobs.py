"""Database leases for local work. No broker or network capability lives here."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection

LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRYABLE_JOB_TYPES = frozenset({"decision", "data", "market"})


class LeaseLost(RuntimeError):
    """The caller must discard all uncommitted work and reacquire the job."""


@dataclass(frozen=True)
class Claim:
    job_id: str
    job_type: str
    input_ref: str
    trace_id: str
    owner: str
    token: int
    attempt: int


def enqueue(
    connection: Connection, *, job_id: str, job_type: str, input_ref: str, trace_id: str
) -> None:
    """Persist a stable job identity in the caller's transaction."""
    if not all((job_id, job_type, input_ref, trace_id)):
        raise ValueError("job identity, type, input reference and trace are required")
    connection.execute(
        text("""
        INSERT INTO public.jobs (id, type, input_ref, trace_id)
        VALUES (:id, :type, :input_ref, :trace_id)
    """),
        {"id": job_id, "type": job_type, "input_ref": input_ref, "trace_id": trace_id},
    )


class JobStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def claim(
        self,
        owner: str,
        *,
        verify_recovery: Callable[[ReadOnlyScopedConnection, str, str], str | None] | None = None,
    ) -> Claim | None:
        """Claim one runnable job. A takeover needs a durable-state evidence reference.

        The verifier runs under the job row lock and must read persisted job/ledger/
        broker-reconciliation state. Returning None leaves the job unavailable.
        Submit jobs with prior attempts are quarantined for reconciliation.
        """
        if not owner:
            raise ValueError("lease owner required")
        with self.engine.begin() as connection:
            skipped: list[str] = []
            while True:
                row = (
                    connection.execute(
                        text("""
                SELECT id, type, input_ref, trace_id, status, attempt, max_attempts,
                       fencing_token
                FROM public.jobs
                WHERE ((status IN ('PENDING','RETRY') AND not_before <= clock_timestamp())
                   OR (status = 'LEASED' AND lease_until <= clock_timestamp()
                       AND :allow_takeover))
                  AND id <> ALL(CAST(:skipped AS text[]))
                ORDER BY not_before, id
                FOR UPDATE SKIP LOCKED LIMIT 1
            """),
                        {"allow_takeover": verify_recovery is not None, "skipped": skipped},
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                if row["type"] not in RETRYABLE_JOB_TYPES and row["attempt"] > 0:
                    connection.execute(
                        text("""
                    UPDATE public.jobs SET status='RECONCILE', lease_owner=NULL,
                        lease_until=NULL, error_code='SUBMIT_RECONCILE_REQUIRED'
                    WHERE id=:id
                """),
                        {"id": row["id"]},
                    )
                    skipped.append(row["id"])
                    continue
                takeover = row["status"] == "LEASED"
                if takeover:
                    evidence_ref = verify_recovery(
                        ReadOnlyScopedConnection(connection), row["id"], row["input_ref"]
                    )
                    if not evidence_ref:
                        skipped.append(row["id"])
                        continue
                attempt = row["attempt"] + 1
                if attempt > row["max_attempts"]:
                    connection.execute(
                        text("""
                    UPDATE public.jobs SET status='DEAD', lease_owner=NULL,
                        lease_until=NULL, error_code='ATTEMPTS_EXHAUSTED' WHERE id=:id
                """),
                        {"id": row["id"]},
                    )
                    skipped.append(row["id"])
                    continue
                token = row["fencing_token"] + 1
                connection.execute(
                    text("""
                UPDATE public.jobs SET status='LEASED', lease_owner=:owner,
                    lease_until=clock_timestamp() + interval '30 seconds',
                    heartbeat=clock_timestamp(), fencing_token=:token, attempt=:attempt,
                    error_code=NULL WHERE id=:id
            """),
                    {"owner": owner, "token": token, "attempt": attempt, "id": row["id"]},
                )
                if takeover:
                    connection.execute(
                        text("""
                    INSERT INTO public.job_recovery_checks (job_id, fencing_token, evidence_ref)
                    VALUES (:id, :token, :evidence)
                """),
                        {"id": row["id"], "token": token, "evidence": evidence_ref},
                    )
                return Claim(
                    row["id"], row["type"], row["input_ref"], row["trace_id"], owner, token, attempt
                )

    def heartbeat(self, claim: Claim) -> bool:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT lease_owner, fencing_token, status FROM public.jobs
                WHERE id=:id FOR UPDATE
            """),
                    {"id": claim.job_id},
                )
                .mappings()
                .first()
            )
            if (
                not row
                or row["status"] != "LEASED"
                or row["lease_owner"] != claim.owner
                or row["fencing_token"] != claim.token
            ):
                return False
            if not connection.execute(
                text("SELECT lease_until > clock_timestamp() FROM public.jobs WHERE id=:id"),
                {"id": claim.job_id},
            ).scalar_one():
                return False
            return (
                connection.execute(
                    text("""
                UPDATE public.jobs SET heartbeat=clock_timestamp(),
                    lease_until=clock_timestamp() + interval '30 seconds'
                WHERE id=:id AND status='LEASED' AND lease_owner=:owner
                  AND fencing_token=:token AND lease_until > clock_timestamp()
            """),
                    {"id": claim.job_id, "owner": claim.owner, "token": claim.token},
                ).rowcount
                == 1
            )

    @contextmanager
    def transaction(self, claim: Claim) -> Iterator[ScopedConnection]:
        """Commit ledger effects only while this token still owns a live lease.

        The row lock prevents a takeover during the body. The final clock check
        rejects work whose lease expired while the body was running.
        """
        with self.engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            row = (
                connection.execute(
                    text("""
                SELECT lease_owner, fencing_token, lease_until, status
                FROM public.jobs WHERE id=:id FOR UPDATE
            """),
                    {"id": claim.job_id},
                )
                .mappings()
                .first()
            )
            if (
                not row
                or row["status"] != "LEASED"
                or row["lease_owner"] != claim.owner
                or row["fencing_token"] != claim.token
            ):
                raise LeaseLost("job token is no longer current")
            if not connection.execute(
                text("SELECT lease_until > clock_timestamp() FROM public.jobs WHERE id=:id"),
                {"id": claim.job_id},
            ).scalar_one():
                raise LeaseLost("job lease expired before work began")
            yield ScopedConnection(connection)
            updated = connection.execute(
                text("""
                UPDATE public.jobs SET status='DONE', lease_owner=NULL, lease_until=NULL,
                    completed_at=clock_timestamp()
                WHERE id=:id AND status='LEASED' AND lease_owner=:owner
                  AND fencing_token=:token AND lease_until > clock_timestamp()
            """),
                {"id": claim.job_id, "owner": claim.owner, "token": claim.token},
            )
            if updated.rowcount != 1:
                raise LeaseLost("job lease expired before commit")

    def fail(self, claim: Claim, error_code: str) -> bool:
        """Retry deterministic work with bounded backoff; quarantine submit work."""
        if not error_code or len(error_code) > 80 or not error_code.replace("_", "").isalnum():
            raise ValueError("redacted symbolic error code required")
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT type, attempt, max_attempts FROM public.jobs
                WHERE id=:id AND status='LEASED' AND lease_owner=:owner
                  AND fencing_token=:token
                FOR UPDATE
            """),
                    {"id": claim.job_id, "owner": claim.owner, "token": claim.token},
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            if not connection.execute(
                text("SELECT lease_until > clock_timestamp() FROM public.jobs WHERE id=:id"),
                {"id": claim.job_id},
            ).scalar_one():
                return False
            status = (
                "RECONCILE"
                if row["type"] not in RETRYABLE_JOB_TYPES
                else "DEAD"
                if row["attempt"] >= row["max_attempts"]
                else "RETRY"
            )
            delay = min(60, 10 * (2 ** (row["attempt"] - 1)))
            connection.execute(
                text("""
                UPDATE public.jobs SET status=:status, lease_owner=NULL, lease_until=NULL,
                    error_code=:code, not_before=clock_timestamp() + (:delay * interval '1 second')
                WHERE id=:id
            """),
                {"status": status, "code": error_code, "delay": delay, "id": claim.job_id},
            )
            return True

    def blocks_new_risk(self, input_ref: str) -> bool:
        """Conservative gate for downstream risk admission on this input."""
        with self.engine.connect() as connection:
            return bool(
                connection.execute(
                    text("""
                        SELECT EXISTS (
                            SELECT 1 FROM public.jobs
                            WHERE input_ref=:input_ref AND status IN ('DEAD','RECONCILE')
                        )
                    """),
                    {"input_ref": input_ref},
                ).scalar_one()
            )
