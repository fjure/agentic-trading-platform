"""Immutable response proofs distinguish bracketed holidays from missing calendar knowledge."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from packages.research.calendar import ResearchSession, expected_windows
from packages.research.calendar_storage import (
    append_sessions,
    calendar_response,
    coverage_from_rows,
)
from packages.research.models import BarInterval, PointInTimeQuery

FRIDAY = date(2026, 10, 23)
MONDAY = date(2026, 10, 26)
OBSERVED = datetime(2026, 10, 28, tzinfo=UTC)
QUERY = PointInTimeQuery(OBSERVED, OBSERVED, OBSERVED, 20)


def session(day, version="0" * 64):
    opens = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    return ResearchSession(day, opens, opens + timedelta(hours=6, minutes=30), version)


def rows(sessions, commit=1, observed=OBSERVED):
    response, ordered = calendar_response(tuple(sessions))
    return tuple(
        {
            "session_date": item.session_date,
            "opens_at": item.opens_at,
            "closes_at": item.closes_at,
            "source": item.source,
            "version": response,
            "commit_version": commit,
            "ingested_at": observed,
        }
        for item in ordered
    )


def weekend(coverage):
    return expected_windows(
        coverage,
        BarInterval.ONE_DAY,
        datetime(2026, 10, 24, 4, tzinfo=UTC),
        datetime(2026, 10, 26, 4, tzinfo=UTC),
        OBSERVED,
    )


def test_one_response_proves_only_its_bracketed_interior_dates():
    coverage = coverage_from_rows(rows((session(FRIDAY), session(MONDAY))), QUERY)
    assert coverage.authoritative
    assert coverage.start_date == FRIDAY and coverage.end_date == MONDAY
    assert weekend(coverage).unknown_dates == ()
    assert weekend(coverage).windows == ()
    edge = expected_windows(
        coverage,
        BarInterval.ONE_DAY,
        datetime(2026, 10, 22, 4, tzinfo=UTC),
        datetime(2026, 10, 23, 4, tzinfo=UTC),
        OBSERVED,
    )
    assert edge.unknown_dates == (date(2026, 10, 22),)


def test_independent_responses_cannot_be_combined_into_weekend_proof():
    separate = rows((session(FRIDAY),)) + rows((session(MONDAY),), commit=2)
    coverage = coverage_from_rows(separate, QUERY)
    assert not coverage.authoritative
    assert weekend(coverage).unknown_dates == (date(2026, 10, 24), date(2026, 10, 25))
    assert len(coverage.sessions) == 2


def test_response_provenance_is_order_invariant_but_binds_contents_and_source_revision():
    friday, monday = session(FRIDAY), session(MONDAY)
    first, _ = calendar_response((friday, monday))
    reordered, _ = calendar_response((monday, friday))
    assert first == reordered
    assert calendar_response((friday, replace(monday, version="1" * 64)))[0] != first
    early_close = replace(monday, closes_at=monday.closes_at - timedelta(hours=3))
    assert calendar_response((friday, early_close))[0] != first


def test_newer_response_can_correct_old_session_to_a_bracketed_nontrading_date():
    tuesday = session(date(2026, 10, 27))
    before = rows((session(FRIDAY), session(MONDAY), tuesday), commit=1)
    after = rows((session(FRIDAY, "1" * 64), replace(tuesday, version="1" * 64)), commit=2)
    coverage = coverage_from_rows(before + after, QUERY)
    assert coverage.authoritative
    assert [item.session_date for item in coverage.sessions] == [FRIDAY, tuesday.session_date]


def test_later_session_correction_and_response_proofs_respect_all_pit_bounds():
    original = rows((session(FRIDAY), session(MONDAY)), commit=1)
    changed = replace(session(MONDAY), closes_at=session(MONDAY).closes_at - timedelta(hours=3))
    future = rows((changed,), commit=21)
    coverage = coverage_from_rows(original + future, QUERY)
    assert coverage.sessions[-1].closes_at == session(MONDAY).closes_at
    late_ingestion = rows((changed,), commit=2, observed=OBSERVED + timedelta(microseconds=1))
    assert coverage_from_rows(original + late_ingestion, QUERY) == coverage
    assert (
        coverage_from_rows(
            original, replace(QUERY, snapshot_at=OBSERVED - timedelta(microseconds=1))
        ).sessions
        == ()
    )


def test_same_commit_conflicting_proofs_fail_closed():
    open_monday = rows((session(FRIDAY), session(MONDAY), session(date(2026, 10, 27))), commit=2)
    closed_monday = rows((session(FRIDAY), session(date(2026, 10, 27))), commit=2)
    coverage = coverage_from_rows(open_monday + closed_monday, QUERY)
    assert not coverage.authoritative
    assert MONDAY not in {item.session_date for item in coverage.sessions}


def test_empty_response_proves_no_holidays_and_takes_no_version_lock():
    append_sessions(None, ())
    coverage = coverage_from_rows((), QUERY)
    assert not coverage.authoritative and coverage.sessions == ()
    assert len(weekend(coverage).unknown_dates) == 2


@pytest.mark.parametrize(
    "sessions",
    [
        (session(FRIDAY), session(FRIDAY)),
        (session(FRIDAY), session(date(2028, 1, 1))),
        (replace(session(FRIDAY), source="unverified-calendar"),),
        [session(FRIDAY)],
    ],
)
def test_bad_response_proofs_are_rejected_before_database_access(sessions):
    with pytest.raises(ValueError):
        append_sessions(None, sessions)


def test_persisted_rows_share_the_response_version_and_repeat_identity():
    class Capture:
        def __init__(self):
            self.values = []

        def execute(self, statement, parameters=None):
            if parameters is not None:
                self.values.append(parameters)

    first, second = Capture(), Capture()
    pair = (session(FRIDAY), session(MONDAY))
    append_sessions(first, pair)
    append_sessions(second, tuple(reversed(pair)))
    expected_version = calendar_response(pair)[0]
    assert first.values == second.values
    assert {value["version"] for value in first.values} == {expected_version}
    assert len({value["id"] for value in first.values}) == 2
