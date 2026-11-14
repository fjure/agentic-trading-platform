"""Durable operator-only authorization, reservations and private evaluation output."""

import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .harness import REPETITIONS, Approval

_VERSION = 1
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_HASH = re.compile(r"[a-f0-9]{64}")
_DATABASE = "evaluation.sqlite"
_STATUSES = ("answered", "refused", "timeout", "provider_blocked")
_RETENTION = timedelta(days=7)
_MAX_UNITS = 10**15
_MAX_RAW_BYTES = 256 * 1024


class OperatorStoreDenied(RuntimeError):
    """Fixed, redacted failures for the operator store."""


def _now() -> datetime:
    return datetime.now(UTC)


def _time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise OperatorStoreDenied("EVALUATION_GRANT_INVALID")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _units(value: Decimal) -> int:
    if not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1000:
        raise OperatorStoreDenied("EVALUATION_COST_INVALID")
    numerator, denominator = value.as_integer_ratio()
    units, remainder = divmod(numerator * 10**12, denominator)
    if remainder or units > _MAX_UNITS:
        raise OperatorStoreDenied("EVALUATION_COST_INVALID")
    return units


def _grant(approval: Approval) -> str:
    binding = asdict(approval.binding)
    if (
        not isinstance(approval.id, str) or not _ID.fullmatch(approval.id)
        or type(approval.max_calls) is not int or not 1 <= approval.max_calls <= 1000
        or approval.binding.repetitions != REPETITIONS
        or any(
            not isinstance(value, str) or not _HASH.fullmatch(value)
            for key, value in binding.items() if key.endswith("_sha256")
        )
        or any(
            not isinstance(value, str) or not value or len(value) > 256
            for value in (approval.binding.profile, approval.binding.model, approval.binding.api)
        )
        or not 0 < _units(approval.cost_bound) <= _units(approval.max_cost)
    ):
        raise OperatorStoreDenied("EVALUATION_GRANT_INVALID")
    return json.dumps({
        "binding": binding,
        "expires": _time(approval.expires_at),
        "calls": approval.max_calls,
        "cost": _units(approval.max_cost),
        "bound": _units(approval.cost_bound),
    }, sort_keys=True, separators=(",", ":"))


def _case_list(cases: tuple[str, ...]) -> str:
    if (
        type(cases) is not tuple or not 1 <= len(cases) <= 1000
        or any(not isinstance(case, str) or not _ID.fullmatch(case) for case in cases)
        or len(set(cases)) != len(cases)
    ):
        raise OperatorStoreDenied("EVALUATION_GRANT_INVALID")
    return json.dumps(cases, separators=(",", ":"))


class OperatorStore:
    """Store grants and raw results under a dedicated POSIX operator identity."""

    def __init__(self, private_dir: Path):
        self._path = private_dir.absolute() / _DATABASE
        with self._connect():
            pass

    @classmethod
    def provision(cls, private_dir: Path) -> "OperatorStore":
        """Initialize an empty database in a pre-created 0700 operator directory."""
        path = private_dir.absolute() / _DATABASE
        try:
            cls._check_path(path, exists=False)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            os.close(os.open(path, flags, 0o600))
            with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as connection:
                connection.execute("PRAGMA synchronous=EXTRA")
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE approvals (
                      id TEXT PRIMARY KEY, binding TEXT NOT NULL, cases TEXT NOT NULL,
                      consumed INTEGER NOT NULL DEFAULT 0
                    ) STRICT;
                    CREATE TABLE attempts (
                      approval TEXT NOT NULL REFERENCES approvals(id), repetition INTEGER NOT NULL,
                      case_id TEXT NOT NULL, status TEXT NOT NULL,
                      cost INTEGER NOT NULL CHECK(cost >= 0), raw BLOB, raw_expires TEXT,
                      PRIMARY KEY (approval, repetition, case_id)
                    ) STRICT;
                    PRAGMA user_version=1;
                    COMMIT;
                """)
        except (OSError, sqlite3.Error, ValueError):
            raise OperatorStoreDenied("EVALUATION_STORE_UNAVAILABLE") from None
        return cls(private_dir)

    @staticmethod
    def _check_path(path: Path, *, exists: bool = True) -> None:
        if os.name != "posix":
            raise OperatorStoreDenied("EVALUATION_PRIVATE_POSIX_STORAGE_REQUIRED")
        directory = path.parent
        info = directory.lstat()
        if (
            directory.resolve() != directory or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise OperatorStoreDenied("EVALUATION_STORE_UNAVAILABLE")

        def check_file(file: Path):
            info = file.lstat()
            if (
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise OperatorStoreDenied("EVALUATION_STORE_UNAVAILABLE")

        if exists:
            check_file(path)
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                check_file(path.with_name(path.name + suffix))
            except FileNotFoundError:
                pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            self._check_path(self._path)
            with closing(sqlite3.connect(
                self._path.as_uri() + "?mode=rw", uri=True, timeout=5,
            )) as connection:
                connection.execute("PRAGMA synchronous=EXTRA")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA secure_delete=ON")
                if connection.execute("PRAGMA user_version").fetchone() != (_VERSION,):
                    raise OperatorStoreDenied("EVALUATION_STORE_UNAVAILABLE")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
        except (OSError, sqlite3.Error, ValueError):
            raise OperatorStoreDenied("EVALUATION_STORE_UNAVAILABLE") from None

    def register(self, approval: Approval, cases: tuple[str, ...]) -> None:
        grant, selected = _grant(approval), _case_list(cases)
        with self._connect() as connection:
            if _time(approval.expires_at) <= _time(_now()):
                raise OperatorStoreDenied("EVALUATION_GRANT_EXPIRED")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO approvals(id,binding,cases) VALUES(?,?,?)",
                (approval.id, grant, selected),
            )
            if inserted.rowcount != 1:
                raise OperatorStoreDenied("EVALUATION_GRANT_EXISTS")

    def consume(self, approval: Approval, cases: tuple[str, ...]) -> bool:
        grant, selected = _grant(approval), _case_list(cases)
        with self._connect() as connection:
            if _time(approval.expires_at) <= _time(_now()):
                return False
            updated = connection.execute(
                "UPDATE approvals SET consumed=1 WHERE id=? AND binding=? "
                "AND cases=? AND consumed=0",
                (approval.id, grant, selected),
            )
            return updated.rowcount == 1

    def reserve(self, approval: Approval, repetition: int, case_id: str) -> bool:
        """Spend one attempt permanently before any dispatch."""
        grant = _grant(approval)
        if type(repetition) is not int or repetition not in REPETITIONS:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cases FROM approvals WHERE id=? AND binding=? AND consumed=1",
                (approval.id, grant),
            ).fetchone()
            if (
                row is None or case_id not in json.loads(row[0])
                or _time(approval.expires_at) <= _time(_now())
            ):
                return False
            used, cost, highest = connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(cost),0),COALESCE(MAX(cost),0) "
                "FROM attempts WHERE approval=?",
                (approval.id,),
            ).fetchone()
            bound = _units(approval.cost_bound)
            if (
                used >= approval.max_calls or highest > bound
                or cost + bound > _units(approval.max_cost)
            ):
                return False
            inserted = connection.execute(
                "INSERT OR IGNORE INTO attempts(approval,repetition,case_id,status,cost) "
                "VALUES(?,?,?,'reserved',?)",
                (approval.id, repetition, case_id, bound),
            )
            return inserted.rowcount == 1

    def authorize(self, approval: Approval, repetition: int, case_id: str) -> bool:
        """Consume the stored I/O reservation once at gateway admission."""
        grant = _grant(approval)
        if type(repetition) is not int or repetition not in REPETITIONS:
            return False
        with self._connect() as connection:
            if _time(approval.expires_at) <= _time(_now()):
                return False
            authorized = connection.execute(
                "UPDATE attempts SET status='authorized' "
                "WHERE approval=? AND repetition=? AND case_id=? AND status='reserved' "
                "AND EXISTS(SELECT 1 FROM approvals WHERE id=? AND binding=? AND consumed=1) "
                "AND NOT EXISTS(SELECT 1 FROM attempts WHERE approval=? AND cost>?)",
                (approval.id, repetition, case_id, approval.id, grant, approval.id,
                 _units(approval.cost_bound)),
            )
            return authorized.rowcount == 1

    def settle(
        self, approval: Approval, repetition: int, case_id: str, status: str,
        confirmed_cost: Decimal | None, raw: dict | None,
    ) -> None:
        """Persist one terminal state. Unknowns retain the reservation."""
        grant = _grant(approval)
        if status not in _STATUSES or (raw is not None and type(raw) is not dict):
            raise OperatorStoreDenied("EVALUATION_OUTCOME_INVALID")
        if type(repetition) is not int or repetition not in REPETITIONS:
            raise OperatorStoreDenied("EVALUATION_OUTCOME_INVALID")
        if status == "timeout":
            confirmed_cost = None
        bound = _units(approval.cost_bound)
        cost = _units(confirmed_cost) if confirmed_cost is not None else bound
        try:
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False).encode() if raw else None
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise OperatorStoreDenied("EVALUATION_OUTCOME_INVALID") from None
        if encoded is not None and len(encoded) > _MAX_RAW_BYTES:
            raise OperatorStoreDenied("EVALUATION_OUTCOME_INVALID")
        expiry = _time(_now() + _RETENTION) if encoded is not None else None
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE attempts SET status=?,cost=?,raw=?,raw_expires=? "
                "WHERE approval=? AND repetition=? AND case_id=? "
                "AND (status='authorized' OR (status='reserved' AND ? IN "
                "('timeout','provider_blocked') AND ? IS NULL AND ?=?)) "
                "AND EXISTS(SELECT 1 FROM approvals WHERE id=? AND binding=? AND consumed=1)",
                (
                    status, cost, encoded, expiry, approval.id, repetition, case_id,
                    status, encoded, cost, bound, approval.id, grant,
                ),
            )
            if updated.rowcount != 1:
                raise OperatorStoreDenied("EVALUATION_ATTEMPT_UNAVAILABLE")

    def states(self, approval: Approval) -> dict[int, dict[str, str]]:
        """Export redacted statuses only. A crash leaves the attempt reserved."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT repetition,case_id,status FROM attempts JOIN approvals "
                "ON approvals.id=attempts.approval WHERE approvals.id=? AND binding=? "
                "ORDER BY repetition,case_id",
                (approval.id, _grant(approval)),
            )
            result: dict[int, dict[str, str]] = {}
            for repetition, case_id, status in rows:
                result.setdefault(repetition, {})[case_id] = status
            return result

    def raw_result(self, approval: Approval, repetition: int, case_id: str) -> dict | None:
        """Read private, unexpired output for independent adjudication."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT raw FROM attempts JOIN approvals ON approvals.id=attempts.approval "
                "WHERE approvals.id=? AND binding=? AND repetition=? AND case_id=? "
                "AND raw_expires>?",
                (approval.id, _grant(approval), repetition, case_id, _time(_now())),
            ).fetchone()
            return json.loads(row[0]) if row is not None else None

    def purge_expired(self) -> int:
        """Run from the operator's maintenance job to remove expired raw values."""
        with self._connect() as connection:
            erased = connection.execute(
                "UPDATE attempts SET raw=NULL,raw_expires=NULL WHERE raw_expires<=?",
                (_time(_now()),),
            )
            return erased.rowcount
