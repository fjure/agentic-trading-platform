"""Shared moving UTC offset for disposable, explicitly selected test processes."""

import re
from datetime import UTC, datetime

VERSION = "0.9.13"
COMMIT = "86b37fde2fed7336ea2d0c17928e3015a55d9b4a"
SOURCE_SHA256 = "d98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e"
PYTHON_IMAGE = (
    "agentic-trading-lab-paper-test-clock-python@sha256:"
    "dcbaadfd8868e6a760868ee15da5a138483ef768aa3623e2c7dfca8fe93032ea"
)
POSTGRES_IMAGE = (
    "agentic-trading-lab-paper-test-clock-postgres@sha256:"
    "98bc30efae325fe37f8bd4a0d8a465dd84a4a0dbe16427f9babfc2e1b21411c5"
)
LIBRARY_SHA256 = {
    "libfaketime.so.1": "e5138d1791a687608bfc9611dbc5327e2c85f85c7cc4bf526038f422baada25b",
    "libfaketimeMT.so.1": "04748d385bd935797e72ff792231f858cf78a3e8081ba2942ba3a8f32872a549",
}
PYTHON_BASE = (
    "agentic-trading-lab-dev-build-smoke@sha256:"
    "9e72bdc3eee8d7be022e6a9331aea2bc498a5d2cdaf9c084ea523eaa16d78eb1"
)
POSTGRES_BASE = (
    "postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
)
DEFAULT_TARGET = datetime(2026, 10, 26, 14, 30, 5, tzinfo=UTC)


def shared_offset(target_utc=None):
    """Compute once on the unmodified host; reuse the returned numeric offset."""
    target = DEFAULT_TARGET if target_utc is None else target_utc
    if (
        not isinstance(target, datetime)
        or target.tzinfo is None
        or target.utcoffset().total_seconds()
    ):
        raise ValueError("test clock target must be aware UTC")
    return f"{(target - datetime.now(UTC)).total_seconds():+.6f}"


def clock_environment(offset, *, python=False):
    if type(offset) is not str or not re.fullmatch(r"[+-][0-9]{1,10}(?:\.[0-9]{1,6})?", offset):
        raise ValueError("fixed numeric test offset required")
    return {
        "LD_PRELOAD": "/opt/test-clock/libfaketimeMT.so.1"
        if python
        else "/opt/test-clock/libfaketime.so.1",
        "FAKETIME": offset,
        "FAKETIME_DONT_FAKE_MONOTONIC": "1",
        "NO_FAKE_STAT": "1",
        "TZ": "UTC",
        "LC_ALL": "C",
    }
