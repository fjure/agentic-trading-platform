"""RDR-04: Kalenderfälligkeiten, Fristunterschiede und ehrliche Empfangsmessung."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from packages.brokers.alpaca_history import AlpacaHistorySource, ProviderPolicy
from packages.research.calendar import NEW_YORK, ResearchSession
from packages.research.collection import DueWork, due_work, registered_instruments
from packages.research.ports import LicenseStatus


def stamp(day=26, hour=13, minute=30, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def session(day=26):
    return ResearchSession(date(2026, 10, day), stamp(day), stamp(day, 20), "test-calendar-v1")


def test_unknown_calendar_never_fabricates_bar_jobs():
    works = due_work(stamp(hour=0), stamp(hour=20), ())
    assert works and all(work.purpose == "CALENDAR" for work in works)


@pytest.mark.parametrize("seconds,initial,reobserve", [
    (4, False, False), (5, True, False), (239, True, False), (240, True, True),
])
def test_exact_initial_and_reobservation_due_boundaries(seconds, initial, reobserve):
    opening = stamp()
    works = due_work(opening, opening + timedelta(minutes=15, seconds=seconds), (session(),))
    purposes = {work.purpose for work in works}
    assert ("INITIAL_BAR" in purposes) is initial
    assert ("REOBSERVE_BAR" in purposes) is reobserve


def test_closed_session_has_two_distinct_observations_per_expected_bar():
    works = due_work(stamp(), stamp(hour=20, minute=4), (session(),))
    bars = [work for work in works if work.purpose != "CALENDAR"]
    assert len(bars) == 52
    assert sum(work.purpose == "INITIAL_BAR" for work in bars) == 26
    assert sum(work.purpose == "REOBSERVE_BAR" for work in bars) == 26
    assert all(work.start_at >= stamp() for work in bars)
    assert all(work.end_at - work.start_at <= timedelta(minutes=45) for work in bars)


def test_outage_over_24h_is_explicit_gap_and_never_backdated_reception():
    old, now = stamp(day=23), stamp(day=26, hour=20)
    works = due_work(old, now, (session(),), scheduled_through=old)
    gap, = [work for work in works if work.reason == "RECOVERY_WINDOW_EXCEEDED"]
    assert gap.start_at == old and gap.end_at == now - timedelta(hours=24)
    assert all(work.due_at >= now - timedelta(hours=24) for work in works)


def test_due_identity_binds_plan_generation_purpose_and_instrument():
    instrument, other = [__import__("uuid").UUID(value) for value in registered_instruments()]
    one = DueWork("INITIAL_BAR", stamp(hour=14), stamp(), stamp(hour=14))
    two = DueWork("REOBSERVE_BAR", one.due_at, one.start_at, one.end_at)
    assert len({one.identity(instrument, 1), one.identity(instrument, 2),
                one.identity(other, 1), two.identity(instrument, 1)}) == 4
    assert one.identity(instrument, 1) == one.identity(instrument, 1)


def test_dst_calendar_lookahead_uses_new_york_dates_and_utc_storage():
    activated = datetime(2026, 10, 30, 23, tzinfo=UTC)
    calendar = next(work for work in due_work(activated, activated, ())
                    if work.purpose == "CALENDAR")
    assert calendar.start_at.astimezone(NEW_YORK).date() == date(2026, 10, 30)
    assert calendar.end_at.astimezone(NEW_YORK).date() == date(2026, 11, 7)
    assert calendar.end_at - calendar.start_at == timedelta(days=8, hours=1)


def test_complete_http_reception_is_measured_before_payload_normalization(monkeypatch):
    received = stamp(hour=20)
    later = received + timedelta(seconds=55)
    clock = [received]
    source = AlpacaHistorySource(
        api_key="synthetic", api_secret="synthetic", enabled=True,
        policy=ProviderPolicy(LicenseStatus.CONFIRMED, "synthetic-v1"),
        clock=lambda: clock[0],
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json=[{"date": "2026-10-26", "open": "09:30", "close": "16:00"}],
            headers={"date": "Mon, 26 Oct 2026 00:00:00 GMT"},
        )),
    )
    import packages.brokers.alpaca_history as module

    original = module._decode

    def slow_decode(value):
        clock[0] = later
        return original(value)

    monkeypatch.setattr(module, "_decode", slow_decode)

    async def run():
        try:
            page = await source.fetch_calendar(date(2026, 10, 26), date(2026, 10, 26))
            assert page.received_at == received
            assert clock[0] == later
        finally:
            await source.close()

    asyncio.run(run())
