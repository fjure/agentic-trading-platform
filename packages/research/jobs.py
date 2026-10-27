"""Durable credential-free research jobs. Transactions never span provider I/O."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from packages.domain.models import utc_string, utc_time
from packages.research.models import content_fingerprint
from packages.research.ports import SourceError, SourceErrorCode

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_./+=-]{1,1024}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_REQUEST_FIELDS = {"instrumentId", "start", "end", "interval", "mode", "feed", "adjustment"}


def enqueue_collection_job(connection, due_id: UUID, instrument: UUID,
                           start: datetime, end: datetime) -> UUID:
    """Execution-interner UPDATEport; kein zusätzliches öffentliches Abrufprogramm.

    Bestehende Requestnormalisierung, Lease-/Audit-/Revisionsports gelten weiter.
    Der Aufrufer hält den Plan-/Sammlungslock und begrenzt die globale Queue.
    """
    from packages.research.registry import definitions

    if instrument not in {item.instrument_id for item, _ in definitions()}:
        raise ResearchJobError(400, "INVALID_REQUEST")
    payload = validated_request(dict(
        instrumentId=str(instrument), start=utc_string(start), end=utc_string(end),
        interval="15Min", mode="UPDATE", feed="iex", adjustment="raw",
    ))
    connection.execute(text("""
      INSERT INTO research.ingestion_jobs
        (job_id,actor,idempotency_key,request_hash,instrument_id,start_at,end_at,
         interval,mode,status,trace_id,overlap_start_at)
      VALUES(:id,'local-operator',:key,:hash,:instrument,:start,:end,
             '15Min','UPDATE','ACCEPTED',:trace,:start)
      ON CONFLICT(job_id) DO NOTHING
    """), dict(id=due_id, key="collection-" + due_id.hex,
               hash=canonical_request_hash(payload), instrument=instrument,
               start=start, end=end, trace=due_id.hex))
    return due_id


class ResearchJobError(ValueError):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(code)


def _public_errors(method):
    """Detach public errors from database/provider exceptions after transaction exit."""

    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ResearchJobError as error:
            sanitized = ResearchJobError(error.status, error.code)
            sanitized.__context__ = None
            sanitized.__cause__ = None
            sanitized.__suppress_context__ = True
        raise sanitized

    return wrapped


def validated_request(request: Mapping) -> dict:
    """Validate and canonicalize only the public request whitelist."""
    failed = False
    try:
        if not isinstance(request, Mapping) or set(request) != _REQUEST_FIELDS:
            raise ValueError("invalid request fields")
        instrument = UUID(request["instrumentId"])
        if str(instrument) != request["instrumentId"]:
            raise ValueError("noncanonical instrument")
        times = []
        for key in ("start", "end"):
            raw = request[key]
            if not isinstance(raw, str) or not _TIMESTAMP.fullmatch(raw):
                raise ValueError("invalid UTC timestamp")
            times.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        if not timedelta(0) < times[1] - times[0] <= timedelta(days=366):
            raise ValueError("invalid range")
        if (
            request["interval"] not in {"15Min", "1Day"}
            or request["mode"] not in {"BACKFILL", "UPDATE"}
            or request["feed"] != "iex"
            or request["adjustment"] != "raw"
        ):
            raise ValueError("unsupported request")
    except (ValueError, TypeError, AttributeError):
        failed = True
    if failed:
        raise ResearchJobError(400, "INVALID_REQUEST")
    return {**dict(request), "start": utc_string(times[0]), "end": utc_string(times[1])}


def canonical_request_hash(request: Mapping) -> str:
    return content_fingerprint(validated_request(request))


@dataclass(frozen=True)
class RetryPlan:
    status: str
    attempts: int
    delay_seconds: int | None


def budget_exhausted(run_pages: int, started_at: datetime, now: datetime) -> bool:
    if type(run_pages) is not int or run_pages < 0:
        raise ResearchJobError(400, "INVALID_REQUEST")
    utc_time(started_at)
    utc_time(now)
    return run_pages >= 1000 or now - started_at >= timedelta(minutes=15)


def retry_plan(attempts: int, retry_after: int | None = None) -> RetryPlan:
    """Three retries per page, then explicit partial recovery; never sleep in a tick."""
    if type(attempts) is not int or not 0 <= attempts <= 3:
        raise ResearchJobError(400, "INVALID_REQUEST")
    if retry_after is not None and (type(retry_after) is not int or retry_after < 0):
        raise ResearchJobError(400, "INVALID_REQUEST")
    if attempts == 3:
        return RetryPlan("PARTIAL", 3, None)
    delay = max(2**attempts, min(retry_after or 0, 60))
    return RetryPlan("RETRY_WAIT", attempts + 1, delay)


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    owner: UUID
    fence: int
    request: Mapping
    attempts: int
    pages: int
    run_pages: int
    partition: int
    started_at: datetime
    private_cursor: str | None = field(default=None, repr=False)
    cursor_generation: int = 0
    cursor_restarts: int = 0
    overlap_start_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", MappingProxyType(validated_request(self.request)))
        if not isinstance(self.job_id, UUID) or not isinstance(self.owner, UUID):
            raise ResearchJobError(400, "INVALID_REQUEST")
        if (
            type(self.fence) is not int
            or self.fence < 1
            or type(self.attempts) is not int
            or not 0 <= self.attempts <= 3
            or any(
                type(value) is not int or value < 0
                for value in (self.pages, self.run_pages, self.partition)
            )
        ):
            raise ResearchJobError(400, "INVALID_REQUEST")
        utc_time(self.started_at)
        if self.private_cursor is not None and (
            not isinstance(self.private_cursor, str) or not _TOKEN.fullmatch(self.private_cursor)
        ):
            raise ResearchJobError(400, "INVALID_REQUEST")


@dataclass(frozen=True)
class JobPosition:
    accepted_at: datetime
    job_id: UUID


@dataclass(frozen=True)
class JobPage:
    items: tuple[dict, ...]
    next_position: JobPosition | None


def _request_from_row(row: Mapping) -> dict:
    return {
        "instrumentId": str(row["instrument_id"]),
        "start": utc_string(row["start_at"]),
        "end": utc_string(row["end_at"]),
        "interval": row["interval"],
        "mode": row["mode"],
        "feed": "iex",
        "adjustment": "raw",
    }


def _public(row: Mapping) -> dict:
    return {
        "jobId": str(row["job_id"]),
        "request": _request_from_row(row),
        "status": row["status"],
        "acceptedAt": utc_string(row["accepted_at"]),
        "updatedAt": utc_string(row["updated_at"]),
        "retryAt": utc_string(row["retry_at"]) if row["retry_at"] else None,
        "pages": row["pages"],
        "bars": row["bars"],
        "attempts": row["attempts"],
        "errorCode": row["error_code"],
        "traceId": row["trace_id"],
    }


def _error_code(code: SourceErrorCode) -> str:
    if not isinstance(code, SourceErrorCode):
        raise ResearchJobError(400, "INVALID_REQUEST")
    return {
        SourceErrorCode.TIMEOUT: "PROVIDER_TIMEOUT",
        SourceErrorCode.RATE_LIMITED: "PROVIDER_RATE_LIMIT",
        SourceErrorCode.UNAVAILABLE: "PROVIDER_UNAVAILABLE",
        SourceErrorCode.INVALID_RESPONSE: "PROVIDER_RESPONSE_INVALID",
        SourceErrorCode.RESPONSE_TOO_LARGE: "PROVIDER_RESPONSE_INVALID",
        SourceErrorCode.INVALID_CURSOR: "CURSOR_INVALID",
    }.get(code, code.value)


class ResearchJobs:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def _transaction(self):
        failure = None
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError as error:
            # Match only known server messages; no SQL/parameters/driver messages escape.
            original = getattr(error, "orig", None)
            message = getattr(getattr(original, "diag", None), "message_primary", None)
            if message == "research idempotency conflict":
                failure = (409, "IDEMPOTENCY_CONFLICT")
            elif message == "invalid research request":
                failure = (400, "INVALID_REQUEST")
            elif message == "research job not found":
                failure = (404, "NOT_FOUND")
            elif message == "research job cannot be resumed":
                failure = (409, "INVALID_REQUEST")
            else:
                failure = (503, "UNAVAILABLE")
            original, message = None, None
        except ResearchJobError:
            raise
        except SourceError as error:
            failure = (
                (503, "PROVIDER_RESPONSE_INVALID")
                if error.code == SourceErrorCode.INVALID_RESPONSE
                else (503, "UNAVAILABLE")
            )
        except Exception:
            failure = (503, "UNAVAILABLE")
        if failure is not None:
            raise ResearchJobError(*failure)

    @_public_errors
    def enqueue(self, request: Mapping, actor: str, key: str, trace: str) -> dict:
        request = validated_request(request)
        if (
            actor != "local-operator"
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or not isinstance(trace, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace)
        ):
            raise ResearchJobError(400, "INVALID_REQUEST")
        parameters = {
            "id": uuid4(),
            "actor": actor,
            "key": key,
            "hash": canonical_request_hash(request),
            "instrument": UUID(request["instrumentId"]),
            "start": datetime.fromisoformat(request["start"].replace("Z", "+00:00")),
            "end": datetime.fromisoformat(request["end"].replace("Z", "+00:00")),
            "interval": request["interval"],
            "mode": request["mode"],
            "trace": trace,
        }
        with self._transaction() as connection:
            job_id = connection.execute(
                text("""
                SELECT research.request_job(:id,:actor,:key,:hash,:instrument,:start,:end,
                                            :interval,:mode,:trace)
            """),
                parameters,
            ).scalar_one()
            return self._read(connection, job_id)

    @staticmethod
    def _read(connection: Connection, job_id: UUID) -> dict | None:
        row = (
            connection.execute(
                text("SELECT * FROM research.job_projection WHERE job_id=:id"), {"id": job_id}
            )
            .mappings()
            .first()
        )
        return _public(row) if row is not None else None

    @_public_errors
    def read_job(self, job_id: UUID) -> dict | None:
        if not isinstance(job_id, UUID):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            return self._read(connection, job_id)

    @_public_errors
    def list_jobs(self, limit: int = 100, after: JobPosition | None = None) -> JobPage:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ResearchJobError(400, "INVALID_REQUEST")
        if after is not None and not isinstance(after, JobPosition):
            raise ResearchJobError(400, "INVALID_REQUEST")
        predicate = "" if after is None else "WHERE (accepted_at,job_id)>(:accepted,:id)"
        parameters = {"limit": limit + 1}
        if after is not None:
            parameters.update(accepted=after.accepted_at, id=after.job_id)
        with self._transaction() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM research.job_projection "
                        + predicate
                        + " ORDER BY accepted_at,job_id LIMIT :limit"
                    ),
                    parameters,
                )
                .mappings()
                .all()
            )
            position = (
                JobPosition(rows[limit - 1]["accepted_at"], rows[limit - 1]["job_id"])
                if len(rows) > limit
                else None
            )
            return JobPage(tuple(_public(row) for row in rows[:limit]), position)

    @_public_errors
    def claim(self, owner: UUID) -> Lease | None:
        if not isinstance(owner, UUID):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended('research-one-active',0))")
            ).scalar_one()
            if not acquired:
                return None
            row = (
                connection.execute(
                    text("""
                SELECT *,clock_timestamp() AS now FROM research.ingestion_jobs
                WHERE (status='ACCEPTED' OR (status='RETRY_WAIT' AND retry_at<=clock_timestamp())
                   OR (status='RUNNING' AND lease_until<=clock_timestamp()))
                  AND NOT EXISTS(SELECT 1 FROM research.ingestion_jobs
                      WHERE status='RUNNING' AND lease_until>clock_timestamp())
                ORDER BY accepted_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1
            """)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            if budget_exhausted(row["run_pages"], row["started_at"] or row["now"], row["now"]):
                self._finish(connection, row["job_id"], "PARTIAL", "BUDGET_EXHAUSTED")
                return None
            claimed = (
                connection.execute(
                    text("""
                UPDATE research.ingestion_jobs SET status='RUNNING',owner=:owner,fence=fence+1,
                  lease_until=clock_timestamp()+interval '30 seconds',updated_at=clock_timestamp(),
                  overlap_start_at=COALESCE(overlap_start_at,CASE WHEN mode='UPDATE' THEN
                    COALESCE((SELECT b.bar_start FROM research.bar_revisions b
                      WHERE b.instrument_id=research.ingestion_jobs.instrument_id
                        AND b.interval=research.ingestion_jobs.interval AND b.feed='iex'
                        AND b.adjustment='raw' AND b.quality='VALID'
                        AND b.bar_end<=research.ingestion_jobs.start_at
                      ORDER BY b.bar_end DESC,b.local_revision DESC LIMIT 1),start_at)
                    ELSE start_at END),
                  started_at=COALESCE(started_at,clock_timestamp()),retry_at=NULL
                WHERE job_id=:id RETURNING *
            """),
                    {"id": row["job_id"], "owner": owner},
                )
                .mappings()
                .one()
            )
            return Lease(
                claimed["job_id"],
                owner,
                claimed["fence"],
                _request_from_row(claimed),
                claimed["attempts"],
                claimed["pages"],
                claimed["run_pages"],
                claimed["partition"],
                claimed["started_at"],
                claimed["private_cursor"],
                claimed["cursor_generation"],
                claimed["cursor_restarts"],
                claimed["overlap_start_at"],
            )

    @staticmethod
    def _locked(connection: Connection, lease: Lease) -> Mapping:
        row = (
            connection.execute(
                text("""
            SELECT *,clock_timestamp() AS now FROM research.ingestion_jobs
            WHERE job_id=:id FOR UPDATE
        """),
                {"id": lease.job_id},
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["status"] != "RUNNING"
            or row["owner"] != lease.owner
            or row["fence"] != lease.fence
            or row["lease_until"] <= row["now"]
        ):
            raise ResearchJobError(409, "LEASE_LOST")
        return row

    @staticmethod
    def _finish(connection: Connection, job_id: UUID, status: str, code: str | None) -> None:
        connection.execute(
            text("""
            UPDATE research.ingestion_jobs SET status=:status,error_code=:code,
              owner=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE job_id=:id
        """),
            {"id": job_id, "status": status, "code": code},
        )

    @_public_errors
    def commit_page(
        self,
        lease: Lease,
        page_hash: str,
        source_cursor_hash: str,
        next_cursor: str | None,
        write_callback: Callable[[Connection], int],
        *,
        more_partitions: bool = False,
    ) -> dict:
        if (
            not isinstance(lease, Lease)
            or not isinstance(page_hash, str)
            or not _HASH.fullmatch(page_hash)
            or not isinstance(source_cursor_hash, str)
            or not _HASH.fullmatch(source_cursor_hash)
            or type(more_partitions) is not bool
            or (
                next_cursor is not None
                and (not isinstance(next_cursor, str) or not _TOKEN.fullmatch(next_cursor))
            )
        ):
            raise ResearchJobError(400, "INVALID_REQUEST")
        expected_cursor_hash = hashlib.sha256((lease.private_cursor or "").encode()).hexdigest()
        if source_cursor_hash != expected_cursor_hash:
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            row = self._locked(connection, lease)
            if budget_exhausted(row["run_pages"], row["started_at"], row["now"]):
                self._finish(connection, lease.job_id, "PARTIAL", "BUDGET_EXHAUSTED")
                return self._read(connection, lease.job_id)
            receipt = connection.execute(
                text("""
                SELECT content_hash FROM research.page_receipts
                WHERE job_id=:id AND partition=:partition AND cursor_generation=:generation
                  AND cursor_hash=:cursor
            """),
                {
                    "id": lease.job_id,
                    "partition": row["partition"],
                    "generation": row["cursor_generation"],
                    "cursor": source_cursor_hash,
                },
            ).scalar_one_or_none()
            if receipt is not None:
                if receipt != page_hash:
                    raise ResearchJobError(409, "SNAPSHOT_CONFLICT")
                self._finish(
                    connection,
                    lease.job_id,
                    "ACCEPTED" if row["private_cursor"] else "SUCCEEDED",
                    None,
                )
                return self._read(connection, lease.job_id)
            count = write_callback(connection)
            if type(count) is not int or count < 0 or count > 1000:
                raise ResearchJobError(400, "INVALID_REQUEST")
            # A late callback rolls back both normalized writes and progress.
            checked = self._locked(connection, lease)
            if budget_exhausted(checked["run_pages"], checked["started_at"], checked["now"]):
                raise ResearchJobError(409, "BUDGET_EXHAUSTED")
            connection.execute(
                text("""
                INSERT INTO research.page_receipts
                  (job_id,partition,cursor_generation,cursor_hash,content_hash)
                VALUES(:id,:partition,:generation,:cursor,:hash)
            """),
                {
                    "id": lease.job_id,
                    "partition": row["partition"],
                    "generation": row["cursor_generation"],
                    "cursor": source_cursor_hash,
                    "hash": page_hash,
                },
            )
            status = (
                "PARTIAL"
                if row["run_pages"] + 1 >= 1000
                else (
                    "ACCEPTED"
                    if more_partitions and next_cursor is None
                    else ("SUCCEEDED" if next_cursor is None else "ACCEPTED")
                )
            )
            connection.execute(
                text("""
                UPDATE research.ingestion_jobs SET pages=pages+1,run_pages=run_pages+1,
                  bars=bars+:count,attempts=0,
                  private_cursor=:cursor,
                  partition=partition+CASE WHEN :next_partition THEN 1 ELSE 0 END,
                  cursor_restarts=CASE WHEN :next_partition THEN 0 ELSE cursor_restarts END,
                  retry_at=NULL WHERE job_id=:id
            """),
                {
                    "id": lease.job_id,
                    "count": count,
                    "cursor": next_cursor,
                    "next_partition": more_partitions and next_cursor is None,
                },
            )
            self._finish(
                connection,
                lease.job_id,
                status,
                "BUDGET_EXHAUSTED" if status == "PARTIAL" else None,
            )
            return self._read(connection, lease.job_id)

    @_public_errors
    def commit_metadata(self, lease: Lease, write_callback: Callable[[Connection], object]) -> dict:
        """Commit calendar/source metadata under a short fenced transaction."""
        if not isinstance(lease, Lease) or not callable(write_callback):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            self._locked(connection, lease)
            write_callback(connection)
            self._locked(connection, lease)
            self._finish(connection, lease.job_id, "ACCEPTED", None)
            return self._read(connection, lease.job_id)

    @_public_errors
    def retry(
        self,
        lease: Lease,
        code: SourceErrorCode,
        retry_after: int | None = None,
        *,
        write_callback: Callable[[Connection], object] | None = None,
    ) -> dict:
        public_code = _error_code(code)
        if write_callback is not None and not callable(write_callback):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            row = self._locked(connection, lease)
            if write_callback is not None:
                write_callback(connection)
                row = self._locked(connection, lease)
            plan = retry_plan(row["attempts"], retry_after)
            connection.execute(
                text("""
                UPDATE research.ingestion_jobs SET attempts=:attempts,
                  retry_at=CASE WHEN CAST(:delay AS integer) IS NULL THEN NULL ELSE
                    clock_timestamp()+make_interval(secs=>CAST(:delay AS integer)) END
                WHERE job_id=:id
            """),
                {"id": lease.job_id, "attempts": plan.attempts, "delay": plan.delay_seconds},
            )
            self._finish(
                connection,
                lease.job_id,
                plan.status,
                "BUDGET_EXHAUSTED" if plan.status == "PARTIAL" else public_code,
            )
            return self._read(connection, lease.job_id)

    @_public_errors
    def block(
        self,
        lease: Lease,
        code: SourceErrorCode,
        *,
        write_callback: Callable[[Connection], object] | None = None,
    ) -> dict:
        public_code = _error_code(code)
        if write_callback is not None and not callable(write_callback):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            self._locked(connection, lease)
            if write_callback is not None:
                write_callback(connection)
                self._locked(connection, lease)
            self._finish(connection, lease.job_id, "BLOCKED", public_code)
            return self._read(connection, lease.job_id)

    @_public_errors
    def restart_partition(self, lease: Lease) -> dict:
        """Expired provider cursor: keep immutable receipts and restart this partition."""
        with self._transaction() as connection:
            row = self._locked(connection, lease)
            if row["cursor_restarts"] >= 3:
                self._finish(connection, lease.job_id, "BLOCKED", "CURSOR_INVALID")
            else:
                connection.execute(
                    text("""
                  UPDATE research.ingestion_jobs SET private_cursor=NULL,
                    cursor_generation=cursor_generation+1,cursor_restarts=cursor_restarts+1
                  WHERE job_id=:id
                """),
                    {"id": lease.job_id},
                )
                self._finish(connection, lease.job_id, "ACCEPTED", None)
            return self._read(connection, lease.job_id)

    @_public_errors
    def resume(self, job_id: UUID, actor: str, key: str, trace: str) -> dict:
        if (
            not isinstance(job_id, UUID)
            or actor != "local-operator"
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or not isinstance(trace, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace)
        ):
            raise ResearchJobError(400, "INVALID_REQUEST")
        with self._transaction() as connection:
            resumed_id = connection.execute(
                text("SELECT research.resume_job(:id,:actor,:key,:trace)"),
                {"id": job_id, "actor": actor, "key": key, "trace": trace},
            ).scalar_one()
            if resumed_id != job_id:
                raise ResearchJobError(409, "IDEMPOTENCY_CONFLICT")
            result = self._read(connection, job_id)
            if result is None:
                raise ResearchJobError(404, "NOT_FOUND")
            return result
