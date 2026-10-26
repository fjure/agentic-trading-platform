"""Synthetic calendar evidence tests; fixtures are not provider certifications."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from packages.research.calendar import CalendarCoverage, ResearchSession, expected_windows
from packages.research.models import (
    Adjustment,
    BarIdentity,
    BarInterval,
    BarRevision,
    QualityStatus,
)
from packages.research.quality import assess_coverage, validate_ohlcv

ID = UUID("00000000-0000-0000-0000-000000000001")
VERSION = "synthetic-calendar-v1"


@pytest.mark.parametrize("interval", [BarInterval.FIFTEEN_MINUTES, BarInterval.ONE_DAY])
def test_query_end_before_window_close_does_not_invent_missing_bar(interval):
    item = session("2026-10-23", opening="13:30", closing="20:00")
    partial_end = item.opens_at + timedelta(minutes=5)
    windows = expected_windows(calendar(item), interval, item.opens_at, partial_end, item.closes_at)
    assert windows.windows == () and windows.unknown_dates == ()


def session(day, opening="14:30", closing="21:00"):
    return ResearchSession(
        date.fromisoformat(day),
        datetime.fromisoformat(f"{day}T{opening}:00+00:00"),
        datetime.fromisoformat(f"{day}T{closing}:00+00:00"),
        VERSION,
        source="synthetic-test-evidence",
    )


def calendar(*sessions, authoritative=True):
    return CalendarCoverage(
        sessions[0].session_date if sessions else None,
        sessions[-1].session_date if sessions else None,
        tuple(sessions),
        VERSION,
        authoritative,
    )


def bar(window, *, feed="iex", provider_revision=None):
    return BarRevision(
        uuid4(),
        BarIdentity(ID, "alpaca", feed, BarInterval.FIFTEEN_MINUTES, *window),
        Decimal("10"),
        Decimal("12"),
        Decimal("9"),
        Decimal("11"),
        Decimal("100"),
        window[1],
        window[1],
        provider_revision=provider_revision,
    )


def coverage(cal, bars=(), *, feed="iex", start=None, end=None, valuation_at=None):
    return assess_coverage(
        instrument_id=ID,
        feed=feed,
        interval=BarInterval.FIFTEEN_MINUTES,
        adjustment=Adjustment.RAW,
        start=start or cal.sessions[0].opens_at,
        end=end or cal.sessions[-1].closes_at,
        valuation_at=valuation_at or cal.sessions[-1].closes_at,
        calendar=cal,
        bars=bars,
    )


def test_interior_holiday_is_proven_only_with_authoritative_bracketing():
    before, after = session("2026-12-24", closing="18:00"), session("2026-12-28")
    start = datetime(2026, 12, 25, 5, tzinfo=UTC)
    end = datetime(2026, 12, 26, 5, tzinfo=UTC)
    proven = coverage(calendar(before, after), start=start, end=end)
    assert (proven.quality, proven.expected, proven.missing) == (QualityStatus.VALID, 0, 0)
    unproven = coverage(calendar(before, after, authoritative=False), start=start, end=end)
    assert unproven.quality == QualityStatus.UNKNOWN_CALENDAR
    assert unproven.expected is None and unproven.missing is None
    assert unproven.unchecked == (date(2026, 12, 25),)


def test_empty_response_cannot_claim_authoritative_holiday():
    with pytest.raises(ValueError, match="boundary sessions"):
        CalendarCoverage(date(2026, 12, 25), date(2026, 12, 25), (), VERSION, True)
    cal = calendar(authoritative=False)
    result = coverage(
        cal,
        start=datetime(2026, 12, 25, 5, tzinfo=UTC),
        end=datetime(2026, 12, 26, 5, tzinfo=UTC),
        valuation_at=datetime(2026, 12, 26, 5, tzinfo=UTC),
    )
    assert result.quality == QualityStatus.UNKNOWN_CALENDAR


@pytest.mark.parametrize("year", [2027, 2030])
def test_no_static_2026_or_2029_calendar_horizon_inference(year):
    cal = calendar(session("2026-12-31"))
    result = coverage(
        cal,
        start=datetime(year, 1, 4, 5, tzinfo=UTC),
        end=datetime(year, 1, 5, 5, tzinfo=UTC),
        valuation_at=datetime(year, 1, 5, 5, tzinfo=UTC),
    )
    assert result.quality == QualityStatus.UNKNOWN_CALENDAR
    assert result.expected is None and result.unchecked == (date(year, 1, 4),)


def test_dst_and_early_close_use_observed_utc_boundaries():
    winter = session("2026-03-06")
    summer = session("2026-03-09", opening="13:30", closing="20:00")
    short = session("2026-11-27", closing="18:00")
    for observed, count in ((winter, 26), (summer, 26), (short, 14)):
        windows = expected_windows(
            calendar(observed),
            BarInterval.FIFTEEN_MINUTES,
            observed.opens_at,
            observed.closes_at,
            observed.closes_at,
        )
        assert len(windows.windows) == count
        assert windows.windows[0][0] == observed.opens_at
        assert windows.windows[-1][1] == observed.closes_at
        assert not windows.unknown_dates


def test_only_closed_windows_starting_in_half_open_query_are_expected():
    observed = session("2026-11-27", closing="18:00")
    windows = expected_windows(
        calendar(observed),
        BarInterval.FIFTEEN_MINUTES,
        observed.opens_at + timedelta(minutes=1),
        observed.opens_at + timedelta(minutes=45),
        observed.opens_at + timedelta(minutes=40),
    )
    assert windows.windows == (
        (observed.opens_at + timedelta(minutes=15), observed.opens_at + timedelta(minutes=30)),
    )


def test_daily_window_is_actual_session_not_midnight_24hours():
    observed = session("2026-11-27", closing="18:00")
    cal = calendar(observed)
    assert (
        expected_windows(
            cal,
            BarInterval.ONE_DAY,
            observed.opens_at,
            observed.closes_at,
            observed.closes_at - timedelta(seconds=1),
        ).windows
        == ()
    )
    assert expected_windows(
        cal,
        BarInterval.ONE_DAY,
        observed.opens_at,
        observed.closes_at,
        observed.closes_at,
    ).windows == ((observed.opens_at, observed.closes_at),)


def test_feed_gap_distinguishes_iex_no_observation_from_expected_missing():
    cal = calendar(session("2026-11-27", closing="14:45"))
    iex, sip = coverage(cal), coverage(cal, feed="sip")
    assert iex.quality == QualityStatus.NO_OBSERVATION
    assert sip.quality == QualityStatus.MISSING_EXPECTED
    assert iex.expected == iex.missing == sip.expected == sip.missing == 1
    assert iex.observed == 0
    assert iex.missing_windows == ((cal.sessions[0].opens_at, cal.sessions[0].closes_at),)
    assert iex.instrument_id == ID and iex.feed == "iex" and iex.adjustment == Adjustment.RAW
    assert iex.rule_version and iex.calendar_version == VERSION


def test_partial_data_keeps_missing_window_without_synthetic_bar():
    observed = session("2026-11-27", closing="15:00")
    cal = calendar(observed)
    first_end = observed.opens_at + timedelta(minutes=15)
    source = (bar((observed.opens_at, first_end)),)
    result = coverage(cal, source)
    assert (result.expected, result.observed, result.missing) == (2, 1, 1)
    assert result.missing_windows == ((first_end, observed.closes_at),)
    assert len(source) == 1


def test_unknown_range_does_not_discard_known_session_missing_windows():
    observed = session("2026-12-31", closing="14:45")
    result = coverage(
        calendar(observed),
        end=datetime(2027, 1, 2, 5, tzinfo=UTC),
        valuation_at=datetime(2027, 1, 2, 5, tzinfo=UTC),
    )
    assert result.quality == QualityStatus.UNKNOWN_CALENDAR
    assert result.expected is None and result.missing is None
    assert result.missing_windows == ((observed.opens_at, observed.closes_at),)
    assert result.unchecked == (date(2027, 1, 1),)


def test_same_declared_revision_different_content_is_conflict_and_duplicates_do_not_heal():
    cal = calendar(session("2026-11-27", closing="14:45"))
    first = bar((cal.sessions[0].opens_at, cal.sessions[0].closes_at), provider_revision="rev1")
    different = replace(first, revision_id=uuid4(), close=Decimal("10.5"))
    result = coverage(cal, (first, different, different))
    assert result.quality == QualityStatus.CONFLICT and result.observed == 0
    assert any(item.code == "PROVIDER_REVISION_CONFLICT" for item in result.findings)
    assert coverage(cal, (first, replace(first, revision_id=uuid4()))).observed == 1
    duplicate = coverage(cal, (first, replace(first, revision_id=uuid4())))
    assert duplicate.quality == QualityStatus.VALID


def test_conflicting_undeclared_revisions_still_require_selection():
    cal = calendar(session("2026-11-27", closing="14:45"))
    first = bar((cal.sessions[0].opens_at, cal.sessions[0].closes_at))
    result = coverage(cal, (first, replace(first, close=Decimal("10"))))
    assert result.quality == QualityStatus.CONFLICT
    assert result.findings[0].code == "AMBIGUOUS_LOCAL_REVISION"


def test_order_and_off_session_validation():
    observed = session("2026-11-27", closing="15:00")
    cal = calendar(observed)
    one = bar((observed.opens_at, observed.opens_at + timedelta(minutes=15)))
    two = bar((one.identity.bar_end, observed.closes_at))
    assert coverage(cal, (two, one)).quality == QualityStatus.INVALID
    outside = bar((observed.closes_at, observed.closes_at + timedelta(minutes=15)))
    result = coverage(
        cal,
        (outside,),
        end=outside.identity.bar_end,
        valuation_at=outside.identity.bar_end,
    )
    assert result.quality == QualityStatus.INVALID
    assert result.findings[0].code == "OUTSIDE_SESSION_WINDOW"


def test_adjustments_are_not_silently_mixed_and_quality_is_immutable():
    observed = session("2026-11-27", closing="14:45")
    cal = calendar(observed)
    with pytest.raises(ValueError, match="raw"):
        assess_coverage(
            instrument_id=ID,
            feed="iex",
            interval=BarInterval.FIFTEEN_MINUTES,
            adjustment="split",
            start=observed.opens_at,
            end=observed.closes_at,
            valuation_at=observed.closes_at,
            calendar=cal,
            bars=(),
        )
    result = coverage(cal)
    with pytest.raises(FrozenInstanceError):
        result.quality = QualityStatus.VALID
    with pytest.raises(FrozenInstanceError):
        result.findings[0].code = "FAKE"


@pytest.mark.parametrize(
    "values,code",
    [
        (("10", "9", "8", "10", "1"), "IMPLAUSIBLE_OHLC"),
        (("10", "12", "9", "11", "-1"), "INVALID_PRICE_OR_VOLUME"),
        (("NaN", "12", "9", "11", "1"), "INVALID_DECIMAL"),
        ((1.2, "12", "9", "11", "1"), "INVALID_DECIMAL"),
    ],
)
def test_invalid_normalized_candidates_have_findings_before_domain_rejection(values, code):
    assert validate_ohlcv(*values)[0].code == code


def test_plausibility_exact_decimal_values_are_not_rounded():
    assert (
        validate_ohlcv(
            Decimal("10.000000000000000001"),
            Decimal("10.000000000000000002"),
            Decimal("10"),
            Decimal("10.000000000000000001"),
            Decimal("0"),
        )
        == ()
    )
