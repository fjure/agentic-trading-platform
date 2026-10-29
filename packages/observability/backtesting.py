"""Fixed-cardinality local backtest counters and bounded sanitized audit observations."""

import math
import re
from collections import Counter, deque
from threading import Lock

OPERATIONS = frozenset(
    {
        "read_strategies",
        "create_run",
        "read_runs",
        "read_run",
        "read_results",
        "create_assessment",
        "read_assessment",
        "read_reliability_report",
    }
)
CODES = frozenset(
    {
        "INVALID_REQUEST",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "CURSOR_INVALID",
        "SNAPSHOT_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "UNAVAILABLE",
    }
)


class BacktestMetrics:
    def __init__(self):
        self._lock = Lock()
        self._counters = Counter()
        self._audit = deque(maxlen=256)

    def observe(self, operation, duration, *, rows=0, code=None, trace=None):
        if (
            operation not in OPERATIONS
            or type(rows) is not int
            or not 0 <= rows <= 200
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or not 0 <= duration <= 86400
            or code is not None
            and code not in CODES
        ):
            raise ValueError("bounded observation required")
        if trace is not None and (
            not isinstance(trace, str) or not re.fullmatch(r"[0-9a-f]{32}", trace)
        ):
            raise ValueError("sanitized trace required")
        with self._lock:
            self._counters[f"backtest_{operation}_count"] += 1
            self._counters[f"backtest_{operation}_rows"] += rows
            self._counters[f"backtest_{operation}_seconds"] += duration
            if code:
                self._counters[f"backtest_error_{code.lower()}_count"] += 1
            self._audit.append(dict(operation=operation, code=code, traceId=trace))

    def snapshot(self):
        with self._lock:
            return dict(self._counters)

    def audit_snapshot(self):
        with self._lock:
            return [dict(item) for item in self._audit]
