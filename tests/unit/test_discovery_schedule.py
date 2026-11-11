"""APF-D01 daily UTC identity, controlled zone revisions, and recovery."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from packages.agent_research.discovery.schedule import (
    DailySchedule,
    ScheduleDenied,
    ScheduledOccurrence,
    TimeResolution,
    tzdata_version,
)


def schedule_document(zone="Europe/Berlin", at="02:30", state="ACTIVE"):
    return {
        "scheduleId": "discovery-1", "version": 1, "state": state,
        "timeZone": zone, "localTime": at, "triggerPolicyVersion": 1,
        "tzdataVersion": tzdata_version(zone),
        "scope": {
            "instrumentIds": ["instrument-1"],
            "sourceKinds": ["HISTORICAL_REVISION", "PERSISTED_RESEARCH_RESULT"],
        },
        "limits": {
            "maxInputTokens": 3000, "maxOutputTokens": 400, "maxCost": "0.10",
            "currency": "USD", "maxModelCalls": 1, "maxIdeas": 3,
            "maxSourceItems": 50, "maxPendingItems": 25,
        },
    }


@pytest.mark.parametrize("zone,at,day,instant,resolution", [
    ("Europe/Berlin", "02:30", date(2026, 3, 29), datetime(2026, 3, 29, 1, tzinfo=UTC),
     TimeResolution.GAP_FORWARD),
    ("Europe/Berlin", "02:30", date(2026, 10, 25), datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
     TimeResolution.FOLD_EARLIER),
    ("Australia/Lord_Howe", "02:12", date(2026, 10, 4), datetime(2026, 10, 3, 15, 30, tzinfo=UTC),
     TimeResolution.GAP_FORWARD),
    ("Pacific/Apia", "13:00", date(2011, 12, 30), datetime(2011, 12, 30, 10, tzinfo=UTC),
     TimeResolution.GAP_FORWARD),
    ("Asia/Kathmandu", "09:00", date(2026, 7, 14), datetime(2026, 7, 14, 3, 15, tzinfo=UTC),
     TimeResolution.EXACT),
])
def test_calendar_resolves_first_valid_instant(zone, at, day, instant, resolution):
    schedule = DailySchedule.parse(schedule_document(zone, at))
    assert schedule.resolve(day) == ScheduledOccurrence(day, instant, resolution)


def test_only_latest_missed_local_date_is_eligible():
    schedule = DailySchedule.parse(schedule_document(at="09:00"))
    now = datetime(2026, 7, 14, 6, 59, tzinfo=UTC)
    previous = schedule.latest_due(now, last_local_date=date(2026, 7, 5))
    assert previous == ScheduledOccurrence(
        date(2026, 7, 13), datetime(2026, 7, 13, 7, tzinfo=UTC), TimeResolution.EXACT,
    )
    assert schedule.latest_due(now, last_local_date=previous.local_date) is None
    assert schedule.latest_due(now, not_before=previous.scheduled_at + timedelta(seconds=1)) is None
    today = schedule.next_after(now)
    assert schedule.latest_due(today.scheduled_at) == today
    assert schedule.next_after(today.scheduled_at).local_date == date(2026, 7, 15)


def test_fold_second_occurrence_is_not_scheduled():
    schedule = DailySchedule.parse(schedule_document())
    first = schedule.resolve(date(2026, 10, 25))
    second = first.scheduled_at + timedelta(hours=1)
    assert schedule.latest_due(second, last_local_date=first.local_date) is None
    assert schedule.next_after(first.scheduled_at).local_date == date(2026, 10, 26)


@pytest.mark.parametrize("state", ["DISABLED", "PAUSED"])
def test_paused_and_default_disabled_state_cannot_claim(state):
    schedule = DailySchedule.parse(schedule_document(state=state))
    assert schedule.latest_due(datetime(2026, 7, 14, 20, tzinfo=UTC)) is None


@pytest.mark.parametrize("key,value,code", [
    ("tzdataVersion", "1970a", "TIME_ZONE_DATA_CHANGED"),
    ("timeZone", "Europe/../UTC", "INVALID_TIME_ZONE"),
    ("timeZone", "Asia/DoesNotExist", "INVALID_TIME_ZONE"),
    ("timeZone", "right/Europe/Berlin", "INVALID_TIME_ZONE"),
    ("localTime", "24:00", "INVALID_SCHEDULE"),
    ("version", 1.0, "INVALID_SCHEDULE"),
    ("maxExperiments", 1, "INVALID_SCHEDULE"),
])
def test_invalid_binding_is_rejected(key, value, code):
    with pytest.raises(ScheduleDenied, match=f"^{code}$"):
        DailySchedule.parse(schedule_document() | {key: value})


@pytest.mark.parametrize("key,value", [
    ("maxCost", "-0.1"), ("maxPendingItems", 10001), ("maxIdeas", 101), ("maxModelCalls", 1.0),
])
def test_configured_limits_are_bounded(key, value):
    document = schedule_document()
    document["limits"][key] = value
    with pytest.raises(ScheduleDenied, match="^INVALID_SCHEDULE$"):
        DailySchedule.parse(document)


def test_configuration_does_not_alias_mutable_input_and_utc_is_required():
    document = schedule_document()
    schedule = DailySchedule.parse(document)
    document["scope"]["instrumentIds"].clear()
    schedule.to_document()["scope"]["instrumentIds"].clear()
    assert schedule.to_document()["scope"]["instrumentIds"] == ["instrument-1"]
    with pytest.raises(ValueError, match="UTC"):
        schedule.latest_due(datetime(2026, 7, 14, 12, tzinfo=timezone(timedelta(hours=2))))
