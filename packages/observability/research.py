"""Bounded Research metrics without identifiers, payloads or ambient exporters."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import Counter, deque
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock

from sqlalchemy import text

from packages.research.models import QualityStatus

LOCAL_SNAPSHOT = Path("/tmp/research-telemetry.json")
JOB_METRIC_NAMES = frozenset(
    {
        "research_jobs_count",
        "research_jobs_active",
        "research_jobs_retry_wait",
        "research_jobs_partial",
        "research_jobs_blocked",
        "research_jobs_succeeded",
        "research_jobs_failed",
        "research_pages_count",
        "research_bars_count",
        "research_current_attempts_count",
        "research_terminal_duration_seconds",
        "research_retry_events_count",
    }
)


class ResearchOperation(StrEnum):
    SOURCES = "sources"
    BARS = "bars"
    EVIDENCE = "evidence"
    CONTEXT = "context"
    JOBS = "jobs"
    JOB = "job"
    IMPORT = "import"


class ResearchMetricError(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CURSOR_INVALID = "CURSOR_INVALID"
    SNAPSHOT_CONFLICT = "SNAPSHOT_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    BLOCKED_CREDENTIALS = "BLOCKED_CREDENTIALS"
    BLOCKED_LICENSE = "BLOCKED_LICENSE"
    BLOCKED_ENTITLEMENT = "BLOCKED_ENTITLEMENT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
    PAGINATION_CYCLE = "PAGINATION_CYCLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    LEASE_LOST = "LEASE_LOST"


class ResearchMetrics:
    """Fixed-cardinality counters and bounded trace-correlated normalized audit.

    Call observe_query from a transport finally block with elapsed monotonic
    seconds. Unknown errors must map to UNAVAILABLE before reaching this boundary.
    snapshot returns an independent numeric dictionary suitable for local export.
    """

    def __init__(self):
        self._lock = Lock()
        self._export_lock = Lock()
        self._values: Counter[str] = Counter()
        self._audit: deque[dict] = deque(maxlen=256)

    def observe_query(
        self,
        operation: ResearchOperation,
        duration_seconds: float,
        *,
        rows: int = 0,
        quality: QualityStatus | None = None,
        error: ResearchMetricError | None = None,
        trace_id: str | None = None,
    ) -> None:
        if not isinstance(operation, ResearchOperation):
            raise ValueError("typed Research operation required")
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or not math.isfinite(duration_seconds)
            or not 0 <= duration_seconds <= 86400
            or type(rows) is not int
            or not 0 <= rows <= 200
        ):
            raise ValueError("bounded numeric Research metrics required")
        if quality is not None and not isinstance(quality, QualityStatus):
            raise ValueError("typed Research quality required")
        if error is not None and not isinstance(error, ResearchMetricError):
            raise ValueError("typed Research error required")
        if trace_id is not None and (
            not isinstance(trace_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace_id)
            or trace_id == "0" * 32
        ):
            raise ValueError("nonzero normalized trace required")
        prefix = "research_query_" + operation.value
        with self._lock:
            self._values[prefix + "_count"] += 1
            self._values[prefix + "_duration_seconds"] += duration_seconds
            self._values[prefix + "_rows"] += rows
            if quality is not None:
                self._values["research_quality_" + quality.value.lower() + "_count"] += 1
            if error is not None:
                self._values["research_error_" + error.value.lower() + "_count"] += 1
            if trace_id is not None:
                self._audit.append(
                    {
                        "trace": trace_id,
                        "operation": operation.value,
                        "durationSeconds": duration_seconds,
                        "rows": rows,
                        "quality": None if quality is None else quality.value,
                        "error": None if error is None else error.value,
                    }
                )

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            return dict(self._values)

    def audit_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(record) for record in self._audit]

    def export_local(
        self, path: Path = LOCAL_SNAPSHOT, *, aggregates: dict[str, int | float] | None = None
    ) -> bool:
        """Atomically export sanitized local tmpfs evidence; failure never gates work.

        Only known aggregate metric names may cross this boundary. No arbitrary
        labels, payloads, exception strings, query filters or evidence IDs are
        accepted. A unique private temporary prevents concurrent-writer races.
        """
        with self._export_lock:
            return self._export_local(path, aggregates)

    def _export_local(self, path, aggregates):
        temporary = None
        try:
            if not isinstance(path, Path):
                raise ValueError("local telemetry path required")
            if aggregates is not None and (
                type(aggregates) is not dict
                or not aggregates.keys() <= JOB_METRIC_NAMES
                or any(
                    type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    for value in aggregates.values()
                )
            ):
                raise ValueError("bounded numeric job aggregates required")
            with self._lock:
                values = dict(self._values)
                audit = [dict(record) for record in self._audit]
            values.update(aggregates or {})
            encoded = json.dumps(
                {
                    "checkedAt": datetime.now(UTC).isoformat(),
                    "metrics": values,
                    "queryAudit": audit,
                },
                allow_nan=False,
                separators=(",", ":"),
            )
            descriptor, name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(encoded)
            os.replace(temporary, path)
            return True
        except Exception:
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


# Runtime readers already have SELECT on job_projection and job_audit. This
# aggregate touches no private job table, cursors, credentials or broker state.
_JOB_METRICS = """
SELECT count(*) AS research_jobs_count,
  count(*) FILTER (WHERE status IN ('ACCEPTED','RUNNING','RETRY_WAIT')) AS research_jobs_active,
  count(*) FILTER (WHERE status='RETRY_WAIT') AS research_jobs_retry_wait,
  count(*) FILTER (WHERE status='PARTIAL') AS research_jobs_partial,
  count(*) FILTER (WHERE status='BLOCKED') AS research_jobs_blocked,
  count(*) FILTER (WHERE status='SUCCEEDED') AS research_jobs_succeeded,
  count(*) FILTER (WHERE status='FAILED') AS research_jobs_failed,
  coalesce(sum(pages),0) AS research_pages_count,
  coalesce(sum(bars),0) AS research_bars_count,
  coalesce(sum(attempts),0) AS research_current_attempts_count,
  coalesce(sum(greatest(0,extract(epoch FROM updated_at-accepted_at)))
    FILTER (WHERE status IN ('PARTIAL','BLOCKED','SUCCEEDED','FAILED')),0)
    AS research_terminal_duration_seconds,
  (SELECT count(*) FROM research.job_audit WHERE status='RETRY_WAIT')
    AS research_retry_events_count
FROM research.job_projection
"""


def observe_jobs(connection) -> dict[str, int | float]:
    """Read only aggregate persisted progress using a caller-owned connection.

    The caller owns connection deadlines/admission and decides when to collect;
    this helper never opens another pool or performs background polling.
    """
    row = connection.execute(text(_JOB_METRICS)).mappings().one()
    result = {}
    for name, value in row.items():
        numeric = float(value) if name.endswith("_seconds") else int(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError("invalid aggregate Research metric")
        result[name] = numeric
    return result
