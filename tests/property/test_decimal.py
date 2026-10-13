"""Exact decimal and UTC boundary properties for PTV-02 (T-MKT-003)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packages.domain.models import decimal_string, decimal_value, utc_string, utc_time


@given(
    st.integers(min_value=-(10**20 - 1), max_value=10**20 - 1),
    st.integers(min_value=0, max_value=10**18 - 1),
)
def test_numeric_38_18_round_trip_without_rounding(whole: int, fraction: int) -> None:
    raw = f"{whole}.{fraction:018d}"
    if whole == 0 and raw.startswith("-0"):
        raw = raw[1:]
    value = decimal_value(raw)
    assert decimal_string(value) == raw
    assert Decimal(decimal_string(value)) == value


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        1.25,
        "NaN",
        "Infinity",
        "1e3",
        "1E+3",
        Decimal("1E+3"),
        "01",
        "1.0000000000000000001",
        "100000000000000000000",
        "1.0.1",
        "",
        True,
    ],
)
def test_nonfinite_float_exponent_and_out_of_range_rejected(bad: object) -> None:
    with pytest.raises(ValueError):
        decimal_value(bad)


def test_utc_requires_aware_zero_offset_and_renders_z() -> None:
    now = datetime(2026, 10, 12, 12, 30, tzinfo=UTC)
    assert utc_string(now) == "2026-10-12T12:30:00Z"
    assert utc_time(now).tzinfo is UTC
    with pytest.raises(ValueError):
        utc_time(now.replace(tzinfo=None))
    with pytest.raises(ValueError):
        utc_time(now.replace(tzinfo=timezone(timedelta(hours=2))))
