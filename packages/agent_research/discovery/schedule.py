"""Deterministic civil-time occurrences and transactional schedule admission."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Protocol
from uuid import UUID
from zoneinfo import TZPATH, ZoneInfo

from jsonschema import Draft202012Validator
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from packages.domain.models import utc_string, utc_time

_CONTRACT = Path(__file__).resolve().parents[3] / "contracts/schemas/v1/agent-research.schema.json"
_ZONE_KEY = re.compile(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*")
_ONE_DAY = timedelta(days=1)
_ONE_SECOND = timedelta(seconds=1)


class ScheduleDenied(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ScheduleState(StrEnum):
    DISABLED = "DISABLED"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class TimeResolution(StrEnum):
    EXACT = "EXACT"
    GAP_FORWARD = "GAP_FORWARD"
    FOLD_EARLIER = "FOLD_EARLIER"


@dataclass(frozen=True, slots=True)
class ScheduledOccurrence:
    local_date: date
    scheduled_at: datetime
    resolution: TimeResolution


@lru_cache(maxsize=1)
def _schedule_validator() -> Draft202012Validator:
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema | {"$ref": "#/$defs/AgentScheduleV1"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _tzdata_version(data: str) -> str:
    header = data.splitlines()[0].split()
    if len(header) < 3 or header[:2] != ["#", "version"]:
        raise ValueError
    return header[2]


@lru_cache(maxsize=64)
def _load_zone(key: str) -> tuple[ZoneInfo, str]:
    if not _ZONE_KEY.fullmatch(key) or key.split("/")[0] in {"posix", "right"}:
        raise ScheduleDenied("INVALID_TIME_ZONE")
    try:
        for directory in TZPATH:
            root = Path(directory)
            path = root.joinpath(*key.split("/"))
            if path.is_file():
                version = _tzdata_version((root / "tzdata.zi").read_text(encoding="utf-8"))
                with path.open("rb") as data:
                    return ZoneInfo.from_file(data, key=key), version
        bundled = resources.files("tzdata.zoneinfo")
        version = _tzdata_version(bundled.joinpath("tzdata.zi").read_text(encoding="utf-8"))
        with bundled.joinpath(*key.split("/")).open("rb") as data:
            return ZoneInfo.from_file(data, key=key), version
    except (OSError, ValueError, IndexError, ModuleNotFoundError):
        raise ScheduleDenied("INVALID_TIME_ZONE") from None


def tzdata_version(key: str) -> str:
    """Identify the actual time-zone database used to interpret a configured key."""
    return _load_zone(key)[1]


def _candidates(local: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    possible = {local.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)}
    return tuple(sorted(
        instant for instant in possible if instant.astimezone(zone).replace(tzinfo=None) == local
    ))


@dataclass(frozen=True, slots=True, init=False)
class DailySchedule:
    schedule_id: str
    version: int
    state: ScheduleState
    _local_time: time = field(repr=False)
    _zone: ZoneInfo = field(repr=False, compare=False)
    _body: str = field(repr=False)

    def __init__(self, document: dict):
        if type(document) is not dict or not _schedule_validator().is_valid(document):
            raise ScheduleDenied("INVALID_SCHEDULE")
        if (
            type(document["version"]) is not int
            or type(document["triggerPolicyVersion"]) is not int
            or Decimal(document["limits"]["maxCost"]) < 0
            or any(type(value) is float for value in document["limits"].values())
        ):
            raise ScheduleDenied("INVALID_SCHEDULE")
        zone, version = _load_zone(document["timeZone"])
        if version != document["tzdataVersion"]:
            raise ScheduleDenied("TIME_ZONE_DATA_CHANGED")
        object.__setattr__(self, "schedule_id", document["scheduleId"])
        object.__setattr__(self, "version", document["version"])
        object.__setattr__(self, "state", ScheduleState(document["state"]))
        object.__setattr__(self, "_local_time", time.fromisoformat(document["localTime"]))
        object.__setattr__(self, "_zone", zone)
        object.__setattr__(self, "_body", json.dumps(document, sort_keys=True, allow_nan=False))

    @classmethod
    def parse(cls, document: dict) -> "DailySchedule":
        return cls(document)

    def to_document(self) -> dict:
        return json.loads(self._body)

    def resolve(self, local_date: date) -> ScheduledOccurrence:
        local = datetime.combine(local_date, self._local_time)
        candidates = _candidates(local, self._zone)
        if candidates:
            resolution = (
                TimeResolution.FOLD_EARLIER if len(candidates) == 2 else TimeResolution.EXACT
            )
            return ScheduledOccurrence(local_date, candidates[0], resolution)

        # A nonexistent local time has a round trip on each side of the gap.
        # Find the exact first valid second, including non-hour and date-line jumps.
        upper = max(
            local.replace(tzinfo=self._zone, fold=fold).astimezone(UTC)
            .astimezone(self._zone).replace(tzinfo=None)
            for fold in (0, 1)
        )
        seconds = (upper - local) // _ONE_SECOND
        if not 1 <= seconds <= 86400 or not _candidates(upper, self._zone):
            raise ScheduleDenied("INVALID_TIME_ZONE_GAP")
        lower = 0
        while seconds - lower > 1:
            middle = (lower + seconds) // 2
            if _candidates(local + middle * _ONE_SECOND, self._zone):
                seconds = middle
            else:
                lower = middle
        first = _candidates(local + seconds * _ONE_SECOND, self._zone)[0]
        return ScheduledOccurrence(local_date, first, TimeResolution.GAP_FORWARD)

    def latest_due(
        self, now: datetime, *, last_local_date: date | None = None,
        not_before: datetime | None = None,
    ) -> ScheduledOccurrence | None:
        now = utc_time(now)
        if self.state is not ScheduleState.ACTIVE:
            return None
        day = now.astimezone(self._zone).date()
        occurrence = self.resolve(day)
        if occurrence.scheduled_at > now:
            occurrence = self.resolve(day - _ONE_DAY)
        if last_local_date is not None and occurrence.local_date <= last_local_date:
            return None
        if not_before is not None and occurrence.scheduled_at < utc_time(not_before):
            return None
        return occurrence

    def next_after(self, now: datetime) -> ScheduledOccurrence:
        now = utc_time(now)
        day = now.astimezone(self._zone).date()
        current = self.resolve(day)
        return current if current.scheduled_at > now else self.resolve(day + _ONE_DAY)


class Enqueue(Protocol):
    def __call__(
        self, connection: Connection, schedule: DailySchedule, occurrence: ScheduledOccurrence,
        *, epoch: int,
    ) -> UUID: ...


class ScheduleStore:
    """Own durable calendar claims; enqueue work inside the same transaction."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _lock(connection: Connection, schedule_id: str):
        return connection.execute(text("""
            SELECT * FROM agent_research.lock_discovery_schedule(:id)
        """), {"id": schedule_id}).mappings().one_or_none()

    @classmethod
    def require_active(cls, connection: Connection, schedule_id: str, epoch: int) -> None:
        row = cls._lock(connection, schedule_id)
        if (
            type(epoch) is not int or row is None
            or row["state"] != ScheduleState.ACTIVE or row["epoch"] != epoch
        ):
            raise ScheduleDenied("DISCOVERY_SCHEDULE_STALE")

    def create(self, schedule: DailySchedule) -> None:
        if schedule.version != 1 or schedule.state is not ScheduleState.DISABLED:
            raise ScheduleDenied("INVALID_SCHEDULE")
        with self.engine.begin() as connection:
            inserted = connection.execute(text("""
                INSERT INTO agent_research.discovery_schedules
                    (schedule_id,version,epoch,state,configuration)
                VALUES (:id,1,1,'DISABLED',CAST(:configuration AS jsonb))
                ON CONFLICT (schedule_id) DO NOTHING
                RETURNING schedule_id
            """), {"id": schedule.schedule_id, "configuration": _configuration(schedule)}).scalar()
            if inserted is None:
                raise ScheduleDenied("SCHEDULE_EXISTS")
            self._audit(connection, schedule.schedule_id, "CREATE")

    def replace(self, schedule: DailySchedule, *, expected_version: int) -> None:
        """Update settings or apply a pause/resume by compare-and-swap."""
        if type(expected_version) is not int or schedule.version != expected_version + 1:
            raise ScheduleDenied("STALE_SCHEDULE_VERSION")
        with self.engine.begin() as connection:
            row = self._lock(connection, schedule.schedule_id)
            if row is None or row["version"] != expected_version:
                raise ScheduleDenied("STALE_SCHEDULE_VERSION")
            prior = ScheduleState(row["state"])
            if prior is not schedule.state and schedule.state is ScheduleState.DISABLED:
                raise ScheduleDenied("INVALID_SCHEDULE_TRANSITION")
            if prior is ScheduleState.DISABLED and schedule.state is ScheduleState.PAUSED:
                raise ScheduleDenied("INVALID_SCHEDULE_TRANSITION")
            configuration = _configuration(schedule)
            if prior is not schedule.state and row["configuration"] != json.loads(configuration):
                raise ScheduleDenied("INVALID_SCHEDULE_TRANSITION")
            connection.execute(text("""
                UPDATE agent_research.discovery_schedules
                SET version=:version,epoch=epoch+1,state=:state,
                    configuration=CAST(:configuration AS jsonb),
                    active_since=CASE WHEN :state='ACTIVE' THEN clock_timestamp() END
                WHERE schedule_id=:id
            """), {
                "version": schedule.version, "state": schedule.state,
                "configuration": configuration, "id": schedule.schedule_id,
            })
            action = (
                "UPDATE" if prior is schedule.state else
                "RESUME" if schedule.state is ScheduleState.ACTIVE else "PAUSE"
            )
            self._audit(connection, schedule.schedule_id, action)

    @staticmethod
    def _audit(connection: Connection, schedule_id: str, action: str) -> None:
        connection.execute(text("""
            INSERT INTO agent_research.discovery_schedule_events
                (schedule_id,version,epoch,state,configuration,action)
            SELECT schedule_id,version,epoch,state,configuration,:action
            FROM agent_research.discovery_schedules WHERE schedule_id=:id
        """), {"id": schedule_id, "action": action})

    def tick(self, schedule_id: str, *, enqueue: Enqueue) -> ScheduledOccurrence | None:
        with self.engine.begin() as connection:
            row = self._lock(connection, schedule_id)
            if row is None or row["state"] != ScheduleState.ACTIVE:
                return None
            schedule = _schedule(row)
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)
            last_day = connection.execute(text("""
                SELECT max(local_date) FROM agent_research.discovery_occurrences
                WHERE schedule_id=:id
            """), {"id": schedule_id}).scalar_one()
            occurrence = schedule.latest_due(
                now, last_local_date=last_day, not_before=row["active_since"].astimezone(UTC),
            )
            if occurrence is None:
                return None
            run_id = enqueue(connection, schedule, occurrence, epoch=row["epoch"])
            connection.execute(text("""
                INSERT INTO agent_research.discovery_occurrences
                    (schedule_id,local_date,scheduled_at,schedule_version,schedule_epoch,resolution,run_id)
                VALUES (:id,:day,:scheduled_at,:version,:epoch,:resolution,:run_id)
            """), {
                "id": schedule_id, "day": occurrence.local_date,
                "scheduled_at": occurrence.scheduled_at, "version": schedule.version,
                "epoch": row["epoch"], "resolution": occurrence.resolution,
                "run_id": run_id,
            })
            return occurrence

    def read(self, schedule_id: str) -> DailySchedule | None:
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT s.*,l.local_date,l.scheduled_at,clock_timestamp() AS now
                FROM agent_research.discovery_schedules s
                LEFT JOIN LATERAL (
                  SELECT local_date,scheduled_at FROM agent_research.discovery_occurrences
                  WHERE schedule_id=s.schedule_id ORDER BY local_date DESC LIMIT 1
                ) l ON true WHERE s.schedule_id=:id
            """), {"id": schedule_id}).mappings().one_or_none()
        if row is None:
            return None
        schedule = _schedule(row)
        document = schedule.to_document()
        if row["scheduled_at"] is not None:
            document["lastExecutionAt"] = utc_string(row["scheduled_at"].astimezone(UTC))
        if schedule.state is ScheduleState.ACTIVE:
            now = row["now"].astimezone(UTC)
            due = schedule.latest_due(
                now, last_local_date=row["local_date"],
                not_before=row["active_since"].astimezone(UTC),
            )
            following = due or schedule.next_after(now)
            if row["local_date"] is not None and following.local_date <= row["local_date"]:
                following = schedule.resolve(row["local_date"] + _ONE_DAY)
            document["nextExecutionAt"] = utc_string(following.scheduled_at)
        return DailySchedule.parse(document)


def _configuration(schedule: DailySchedule) -> str:
    document = schedule.to_document()
    for key in (
        "scheduleId", "version", "state", "lastExecutionAt", "nextExecutionAt", "checkpointId",
    ):
        document.pop(key, None)
    return json.dumps(document, sort_keys=True)


def _schedule(row) -> DailySchedule:
    return DailySchedule.parse(row["configuration"] | {
        "scheduleId": row["schedule_id"], "version": row["version"], "state": row["state"],
    })
