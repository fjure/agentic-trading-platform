"""Shared moving UTC offset for disposable, explicitly selected test processes."""

import re
from datetime import UTC, datetime

VERSION = "0.9.13"
COMMIT = "86b37fde2fed7336ea2d0c17928e3015a55d9b4a"
SOURCE_SHA256 = "d98aa7beeb21344b3612f60cbfe08196541a2ef25525b3774b11871493a4726e"
PYTHON_IMAGE = (
    "agentic-trading-lab-paper-test-clock-python@sha256:"
    "aa02183f67a30b2c9757c09486a98d831dea59fbc5886eaea87b25d6179cd84b"
)
POSTGRES_IMAGE = (
    "agentic-trading-lab-paper-test-clock-postgres@sha256:"
    "d1aca1848ba6d3695791d5d3a92662b3547a10eab754bdd7344d87e13a1ca4f0"
)
LIBRARY_SHA256 = {
    "libfaketime.so.1": "e5138d1791a687608bfc9611dbc5327e2c85f85c7cc4bf526038f422baada25b",
    "libfaketimeMT.so.1": "04748d385bd935797e72ff792231f858cf78a3e8081ba2942ba3a8f32872a549",
}
PYTHON_BASE = (
    "agentic-trading-lab-dev-build-smoke@sha256:"
    "c40719cab30222c1dd7032d68efd6b7206834ccfd3cdc3c06034b607248f7f14"
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
