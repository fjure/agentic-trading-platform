"""Gateway-private durable one-shot claims, without model or credential payloads."""

import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

_VERSION = 1
_MAX_TTL_SECONDS = 600
_MAX_ENTRIES = 100_000
_HASH = re.compile(r"[0-9a-f]{64}")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class JournalDenied(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DispatchOutcome(StrEnum):
    UNSENT = "UNSENT"
    COMPLETED = "COMPLETED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"


def _utc_micros(value: datetime) -> int:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise JournalDenied("DISPATCH_BINDING_INVALID")
    delta = value - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _attempt_key(attempt_id: UUID) -> str:
    if not isinstance(attempt_id, UUID):
        raise JournalDenied("DISPATCH_BINDING_INVALID")
    return attempt_id.hex


def _binding(attempt_id: UUID, request_hash: str) -> tuple[str, str]:
    if not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash):
        raise JournalDenied("DISPATCH_BINDING_INVALID")
    return _attempt_key(attempt_id), request_hash


class DispatchJournal:
    """Open an explicitly provisioned database. Consumed identities are never evicted."""

    def __init__(self, path: Path, *, max_entries: int = 10_000):
        self._path = Path(path).absolute()
        self._max_entries = max_entries
        if type(max_entries) is not int or not 1 <= max_entries <= _MAX_ENTRIES:
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
        with self._connect():
            pass

    @classmethod
    def provision(cls, path: Path, *, max_entries: int = 10_000) -> "DispatchJournal":
        """Create an empty, private journal once; fail if the destination already exists."""
        path = Path(path).absolute()
        if type(max_entries) is not int or not 1 <= max_entries <= _MAX_ENTRIES:
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
        try:
            cls._check_path(path, exists=False)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(path, flags, 0o600))
            with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as connection:
                connection.execute("PRAGMA synchronous=EXTRA")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("""
                        CREATE TABLE dispatches (
                            attempt_id TEXT PRIMARY KEY NOT NULL CHECK(length(attempt_id)=32),
                            request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
                            expires_at INTEGER NOT NULL,
                            outcome TEXT NOT NULL CHECK(outcome IN (
                                'DISPATCHING', 'UNSENT', 'COMPLETED', 'OUTCOME_UNKNOWN',
                                'RESULT_UNAVAILABLE'
                            ))
                        ) STRICT
                    """)
                    connection.execute(f"PRAGMA user_version={_VERSION}")
        except (OSError, sqlite3.Error, ValueError):
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE") from None
        return cls(path, max_entries=max_entries)

    @staticmethod
    def _check_path(path: Path, *, exists: bool = True) -> None:
        parent = path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or path.parent.resolve() != path.parent:
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
        if os.name == "posix" and (parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
        if exists:
            file = path.lstat()
            if not stat.S_ISREG(file.st_mode) or file.st_nlink != 1:
                raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
            if os.name == "posix" and (file.st_uid != os.geteuid() or file.st_mode & 0o077):
                raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._check_path(self._path)
            uri = self._path.as_uri() + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                connection.execute("PRAGMA synchronous=EXTRA")
                if connection.execute("PRAGMA user_version").fetchone() != (_VERSION,):
                    raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE")
                with connection:
                    yield connection
        except (OSError, sqlite3.Error, ValueError):
            raise JournalDenied("DISPATCH_JOURNAL_UNAVAILABLE") from None

    def claim(self, attempt_id: UUID, request_hash: str, expires_at: datetime) -> None:
        key, digest = _binding(attempt_id, request_hash)
        expiry = _utc_micros(expires_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_micros(datetime.now(UTC))
            if expiry <= now or expiry - now > _MAX_TTL_SECONDS * 1_000_000:
                raise JournalDenied("DISPATCH_BINDING_INVALID")
            previous = connection.execute(
                "SELECT request_hash FROM dispatches WHERE attempt_id=?", (key,),
            ).fetchone()
            if previous is not None:
                if previous[0] != digest:
                    raise JournalDenied("IDEMPOTENCY_CONFLICT")
                raise JournalDenied("DISPATCH_ALREADY_CLAIMED")
            size = connection.execute("SELECT count(*) FROM dispatches").fetchone()[0]
            if size >= self._max_entries:
                raise JournalDenied("DISPATCH_JOURNAL_FULL")
            connection.execute(
                "INSERT INTO dispatches VALUES (?,?,?,'DISPATCHING')", (key, digest, expiry),
            )

    def status(self, attempt_id: UUID, request_hash: str) -> DispatchOutcome | None:
        key, digest = _binding(attempt_id, request_hash)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_hash,outcome FROM dispatches WHERE attempt_id=?", (key,),
            ).fetchone()
            if row is None:
                return None
            if row[0] != digest:
                raise JournalDenied("IDEMPOTENCY_CONFLICT")
            if row[1] == "DISPATCHING":
                return DispatchOutcome.OUTCOME_UNKNOWN
            return DispatchOutcome(row[1])

    def finish(self, attempt_id: UUID, outcome: DispatchOutcome) -> None:
        key = _attempt_key(attempt_id)
        if not isinstance(outcome, DispatchOutcome):
            raise JournalDenied("DISPATCH_BINDING_INVALID")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE dispatches SET outcome=? WHERE attempt_id=? AND outcome='DISPATCHING'",
                (outcome.value, key),
            ).rowcount
            if not changed:
                row = connection.execute(
                    "SELECT outcome FROM dispatches WHERE attempt_id=?", (key,),
                ).fetchone()
                if row is None or row[0] != outcome.value:
                    raise JournalDenied("DISPATCH_BINDING_INVALID")
