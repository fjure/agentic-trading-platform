"""Begrenzte kalendergebundene Vorwärtsbeobachtung; keine Handelsberechtigung.

SQLtransaktionen enden vor Provider-I/O. STARTED wird unter demselben Planlock
wie die Pause bestätigt und darf nur für genau einen Transport verwendet werden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text

from packages.domain.models import utc_time
from packages.research.calendar import NEW_YORK, ResearchSession
from packages.research.models import content_fingerprint
from packages.research.registry import definitions

STEP = timedelta(minutes=15)
RECOVERY = timedelta(hours=24)
NAMESPACE = UUID("74a02c49-af81-4d01-a775-4963f06f28dd")
PURPOSES = {"CALENDAR", "INITIAL_BAR", "REOBSERVE_BAR", "RECOVERY"}
SCHEDULE = dict(
    calendarRefreshUtc="00:05:00Z", calendarLookaheadDays=7,
    firstBarDelaySeconds=5, reobserveDelaySeconds=240, tickSeconds=5,
    recoveryHours=24, overlapMinutes=30, maxDuePerUtcDay=100,
    maxOpenJobsTotal=2, maxConcurrentHttpGets=1,
)


@dataclass(frozen=True)
class DueWork:
    purpose: str
    due_at: datetime
    start_at: datetime
    end_at: datetime
    reason: str | None = None

    def __post_init__(self):
        for value in (self.due_at, self.start_at, self.end_at):
            utc_time(value)
        if self.purpose not in PURPOSES or self.end_at <= self.start_at:
            raise ValueError("INVALID_COLLECTION_DUE")

    def identity(self, instrument_id: UUID, generation: int) -> UUID:
        if not isinstance(instrument_id, UUID) or type(generation) is not int or generation < 1:
            raise ValueError("INVALID_COLLECTION_GENERATION")
        return uuid5(NAMESPACE, content_fingerprint((
            "collection-due-v1", instrument_id, generation, self.purpose, self.due_at,
        )))


def due_work(
    activated_at: datetime,
    now: datetime,
    sessions: tuple[ResearchSession, ...],
    *,
    scheduled_through: datetime | None = None,
) -> tuple[DueWork, ...]:
    """Höchstens24h nachholen; ältere Bereiche explizit als gesperrte Lücke.

    Sitzungen müssen aus tatsächlich sichtbaren Kalenderversionen stammen.
    Eine unbekannte Sitzung wird nicht durch Werktagsannahmen ersetzt.
    """
    utc_time(activated_at)
    utc_time(now)
    if activated_at > now or not isinstance(sessions, tuple):
        raise ValueError("INVALID_COLLECTION_RANGE")
    if scheduled_through is not None:
        utc_time(scheduled_through)
    if len(sessions) > 10 or any(not isinstance(s, ResearchSession) for s in sessions):
        raise ValueError("INVALID_COLLECTION_CALENDAR")
    lower = max(activated_at, now - RECOVERY)
    result = []

    def calendar_due(due):
        day = due.astimezone(NEW_YORK).date()
        opening = datetime.combine(day, time(), NEW_YORK).astimezone(UTC)
        closing = datetime.combine(day + timedelta(days=8), time(), NEW_YORK).astimezone(UTC)
        return DueWork("CALENDAR", due, opening, closing)

    previous = scheduled_through or activated_at
    if previous < lower:
        result.append(DueWork("RECOVERY", lower, previous, lower, "RECOVERY_WINDOW_EXCEEDED"))
    # Aktivierung und die tägliche UTCfälligkeit sind verschiedene logische Jobs.
    if activated_at >= lower:
        result.append(calendar_due(activated_at))
    day = lower.date()
    while day <= now.date():
        due = datetime.combine(day, time(0, 5), UTC)
        if lower <= due <= now and due != activated_at:
            result.append(calendar_due(due))
        day += timedelta(days=1)
    for session in sessions:
        start = session.opens_at
        while start + STEP <= session.closes_at:
            end = start + STEP
            for purpose, seconds in (("INITIAL_BAR", 5), ("REOBSERVE_BAR", 240)):
                due = end + timedelta(seconds=seconds)
                if lower <= due <= now:
                    # Begrenzter UPDATEbereich mit30min Überlappung; keine Backdatierung.
                    result.append(DueWork(
                        purpose, due, max(session.opens_at, start - 2 * STEP), end,
                    ))
            start = end
    return tuple(sorted(result, key=lambda item: (item.due_at, item.purpose)))


@dataclass(frozen=True)
class Dispatch:
    receipt_id: UUID
    due_id: UUID
    instrument_id: UUID
    generation: int
    purpose: str
    due_at: datetime
    fence: int
    job_id: UUID | None


class CollectionStore:
    """Kurze persistente Plan-/Dispatchbarrieren; ausschließlich Execution.

    HTTPaufrufe dürfen weder in admit noch in finish stattfinden. Der Aufrufer
    muss die HTTPinstanz zeitlich härter begrenzen als die Dispatchlease.
    """

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _lock(connection):
        connection.execute(text("SET LOCAL lock_timeout='1s'"))
        return connection.execute(text(
            "SELECT pg_try_advisory_xact_lock(hashtextextended('research-one-active',0))"
        )).scalar_one()

    def admit(self, job_id: UUID | None, fence: int, *, global_enabled: bool,
              safety_busy: bool = False, due_id: UUID | None = None,
              owner: UUID | None = None) -> Dispatch | bool:
        """False sperrt Sammlung; True bezeichnet einen bestehenden manuellen Job.

        Die Generation wird unter Planlock erneut gelesen. Der committed STARTED-
        Receipt ist der definierte Abrufbeginn, auch vor dem tatsächlichen Socket.
        """
        if not global_enabled or safety_busy:
            return False
        with self.engine.begin() as connection:
            if not self._lock(connection):
                return False
            due = connection.execute(text(
                "SELECT * FROM research.due_jobs WHERE "
                + ("due_id=:id" if due_id is not None else "job_id=:id") + " FOR UPDATE"
            ), dict(id=due_id if due_id is not None else job_id)).mappings().first()
            if connection.execute(text(
                "SELECT count(*) FROM research.due_jobs WHERE state='STARTED' "
                "AND lease_until>clock_timestamp()"
            )).scalar_one():
                return False
            if job_id is None and connection.execute(text(
                "SELECT count(*) FROM research.ingestion_jobs WHERE status='RUNNING' "
                "AND lease_until>clock_timestamp()"
            )).scalar_one():
                return False
            if due is None:
                return job_id is not None
            if due["state"] != "PENDING" or fence <= due["fence"]:
                return False
            if job_id is not None and not connection.execute(text(
                "SELECT 1 FROM research.ingestion_jobs WHERE job_id=:id "
                "AND status='RUNNING' AND fence=:fence AND owner=:owner "
                "AND lease_until>clock_timestamp() FOR UPDATE"
            ), dict(id=job_id, fence=fence, owner=owner)).first():
                return False
            plan = connection.execute(text(
                "SELECT * FROM research.collection_plans WHERE instrument_id=:id FOR UPDATE"
            ), dict(id=due["instrument_id"])).mappings().one()
            if not plan["enabled"] or plan["generation"] != due["generation"]:
                return False
            source = connection.execute(text(
                "SELECT license_status,state FROM research.source_status WHERE singleton"
            )).mappings().one_or_none()
            if (
                source is None or source["license_status"] != "CONFIRMED"
                or source["state"] in {"DISABLED", "BLOCKED"}
            ):
                return False
            # Fences nebst Quelle werden vor jedem einzelnen GET neu zugelassen.
            if connection.execute(text(
                "SELECT count(*) FROM research.observation_receipts WHERE due_id=:id "
                "AND fence=:fence AND state='STARTED'"
            ), dict(id=due["due_id"], fence=fence)).scalar_one():
                return False
            receipt = uuid4()
            connection.execute(text(
                "UPDATE research.due_jobs SET state='STARTED',fence=:fence,"
                "lease_until=clock_timestamp()+interval '30 seconds' WHERE due_id=:due"
            ), dict(fence=fence, due=due["due_id"]))
            connection.execute(text(
                "INSERT INTO research.observation_receipts"
                "(receipt_id,due_id,instrument_id,generation,purpose,due_at,job_id,fence,state) "
                "VALUES(:receipt,:due,:instrument,:generation,:purpose,:at,:job,:fence,'STARTED')"
            ), dict(receipt=receipt, due=due["due_id"], instrument=due["instrument_id"],
                    generation=due["generation"], purpose=due["purpose"], at=due["due_at"],
                    job=job_id, fence=fence))
            return Dispatch(receipt, due["due_id"], due["instrument_id"], due["generation"],
                            due["purpose"], due["due_at"], fence, job_id)

    @staticmethod
    def record(connection, dispatch: Dispatch, *, state: str,
               first_observed_at: datetime | None = None, bar=None, body=None):
        """Append-only Messereignis im selben Commit wie die normalisierten Bars."""
        import json

        from packages.research.models import _canonical

        if state not in {"COMPLETED", "UNKNOWN", "BLOCKED"}:
            raise ValueError("INVALID_COLLECTION_RECEIPT")
        if first_observed_at is not None:
            utc_time(first_observed_at)
        connection.execute(text("SELECT research.reserve_version()"))
        receipt = uuid4()
        connection.execute(text(
            "INSERT INTO research.observation_receipts"
            "(receipt_id,dispatch_id,due_id,instrument_id,generation,purpose,due_at,job_id,"
            "fence,state,event_at,bar_close_at,first_observed_at,body) VALUES"
            "(:receipt,:dispatch,:due,:instrument,:generation,:purpose,:at,:job,:fence,"
            ":state,:event,:close,:observed,CAST(:body AS jsonb))"
        ), dict(receipt=receipt, dispatch=dispatch.receipt_id, due=dispatch.due_id,
                instrument=dispatch.instrument_id, generation=dispatch.generation,
                purpose=dispatch.purpose, at=dispatch.due_at, job=dispatch.job_id,
                fence=dispatch.fence, state=state,
                event=bar.identity.bar_start if bar else None,
                close=bar.identity.bar_end if bar else None, observed=first_observed_at,
                body=json.dumps(_canonical(body or {}), sort_keys=True, separators=(",", ":"))))
        return receipt

    def finish(self, dispatch: Dispatch, *, state="COMPLETED", retry_after=None):
        with self.engine.begin() as connection:
            due = connection.execute(text(
                "SELECT * FROM research.due_jobs WHERE due_id=:id FOR UPDATE"
            ), dict(id=dispatch.due_id)).mappings().one()
            if due["fence"] != dispatch.fence or due["state"] != "STARTED":
                return False
            self.record(connection, dispatch, state=state)
            job_status = connection.execute(text(
                "SELECT status FROM research.ingestion_jobs WHERE job_id=:id"
            ), dict(id=dispatch.job_id)).scalar_one_or_none() if dispatch.job_id else None
            next_state = (
                "PENDING" if job_status in {"ACCEPTED", "RUNNING", "RETRY_WAIT"}
                or (dispatch.job_id is None and state == "UNKNOWN" and dispatch.fence < 3)
                else state
            )
            connection.execute(text(
                "UPDATE research.due_jobs SET state=:state,lease_until="
                "CASE WHEN :delay>0 THEN clock_timestamp()+make_interval(secs=>:delay) "
                "ELSE NULL END WHERE due_id=:id"
            ), dict(state=next_state, id=dispatch.due_id, delay=retry_after or 0))
        return True


class CollectionScheduler:
    def __init__(self, engine, *, enabled=False):
        if type(enabled) is not bool:
            raise ValueError("INVALID_COLLECTION_ENABLE")
        self.engine, self.enabled = engine, enabled

    def tick(self) -> int:
        """Zwei Scheduler serialisieren ausschließlich kurze SQLarbeit, nie I/O."""
        if not self.enabled:
            return 0
        from types import SimpleNamespace

        from packages.backtesting.calendar import read_schedule_rows, schedule_at
        from packages.research.jobs import enqueue_collection_job

        created = 0
        with self.engine.begin() as connection:
            if not CollectionStore._lock(connection):
                return 0
            now = connection.execute(text("SELECT clock_timestamp()" )).scalar_one()
            retired = tuple(connection.execute(text("""
              UPDATE research.due_jobs d SET state='BLOCKED',reason='GENERATION_RETIRED'
              FROM research.collection_plans p WHERE p.instrument_id=d.instrument_id
                AND (NOT p.enabled OR p.generation<>d.generation) AND d.state='PENDING'
              RETURNING d.job_id
            """)).scalars())
            for job_id in retired:
                if job_id is not None:
                    connection.execute(text(
                        "UPDATE research.ingestion_jobs SET status='BLOCKED',"
                        "error_code='INVALID_REQUEST',updated_at=clock_timestamp() "
                        "WHERE job_id=:id AND status IN ('ACCEPTED','RETRY_WAIT')"
                    ), dict(id=job_id))
            # Ein nach Crash verlorener HTTPversuch bleibt als UNKNOWN nachvollziehbar.
            expired = tuple(connection.execute(text(
                "SELECT * FROM research.due_jobs WHERE state='STARTED' "
                "AND lease_until<=clock_timestamp() FOR UPDATE"
            )).mappings())
            for due in expired:
                receipt = connection.execute(text(
                    "SELECT receipt_id FROM research.observation_receipts "
                    "WHERE due_id=:id AND fence=:fence AND state='STARTED'"
                ), dict(id=due["due_id"], fence=due["fence"])).scalar_one()
                CollectionStore.record(connection, Dispatch(
                    receipt, due["due_id"], due["instrument_id"], due["generation"],
                    due["purpose"], due["due_at"], due["fence"], due["job_id"],
                ), state="UNKNOWN")
                connection.execute(text(
                    "UPDATE research.due_jobs SET state=:state,lease_until=NULL "
                    "WHERE due_id=:id"
                ), dict(id=due["due_id"], state=(
                    "BLOCKED" if due["purpose"] == "CALENDAR" and due["fence"] >= 3
                    else "PENDING"
                )))
            version = connection.execute(text(
                "SELECT version FROM research.version_state WHERE singleton"
            )).scalar_one()
            snapshot = SimpleNamespace(at=now, version=version)
            calendar = schedule_at(read_schedule_rows(connection, snapshot), now, snapshot)
            source = connection.execute(text(
                "SELECT state,license_status FROM research.source_status WHERE singleton"
            )).mappings().one_or_none()
            source_ok = (source is not None and source["license_status"] == "CONFIRMED"
                         and source["state"] not in {"DISABLED", "BLOCKED"})
            plans = tuple(connection.execute(text(
                "SELECT * FROM research.collection_plans WHERE enabled "
                "ORDER BY instrument_id FOR UPDATE"
            )).mappings())
            for plan in plans:
                if str(plan["instrument_id"]) not in registered_instruments():
                    raise ValueError("UNREGISTERED_COLLECTION_PLAN")
                if not source_ok:
                    connection.execute(text(
                        "UPDATE research.collection_plans SET status='BLOCKED',"
                        "reason='SOURCE_BLOCKED' WHERE instrument_id=:id"
                    ), dict(id=plan["instrument_id"]))
                    continue
                nearby = tuple(s for s in calendar.sessions
                               if now - timedelta(days=2) <= s.closes_at
                               and s.opens_at <= now + timedelta(days=8))
                candidates = due_work(plan["updated_at"], now, nearby,
                                      scheduled_through=plan["scheduled_through_at"])
                through = now
                blocked_reason = None if nearby else "CALENDAR_UNKNOWN"
                for due in candidates:
                    due_id = due.identity(plan["instrument_id"], plan["generation"])
                    if connection.execute(text(
                        "SELECT count(*) FROM research.due_jobs WHERE due_id=:id"
                    ), dict(id=due_id)).scalar_one():
                        continue
                    open_count = connection.execute(text(
                        "SELECT count(*) FROM research.due_jobs WHERE state IN "
                        "('PENDING','STARTED')"
                    )).scalar_one()
                    day_start = datetime.combine(now.date(), time(), UTC)
                    spent = connection.execute(text(
                        "SELECT count(*) FROM research.due_jobs WHERE instrument_id=:id "
                        "AND created_at>=:day AND reason IS NULL"
                    ), dict(id=plan["instrument_id"], day=day_start)).scalar_one()
                    reason = due.reason
                    if spent >= 100:
                        reason = "DAILY_BUDGET_EXHAUSTED"
                        blocked_reason = reason
                    if open_count >= 2 and reason is None:
                        through = min(through, due.due_at - timedelta(microseconds=1))
                        break
                    job_id = (
                        enqueue_collection_job(connection, due_id, plan["instrument_id"],
                                               due.start_at, due.end_at)
                        if due.purpose != "CALENDAR" and reason is None else None
                    )
                    connection.execute(text("""
                      INSERT INTO research.due_jobs
                        (due_id,instrument_id,generation,purpose,due_at,start_at,end_at,
                         job_id,state,reason)
                      VALUES(:id,:instrument,:generation,:purpose,:due,:start,:end,:job,:state,:reason)
                    """), dict(id=due_id, instrument=plan["instrument_id"],
                               generation=plan["generation"], purpose=due.purpose,
                               due=due.due_at, start=due.start_at, end=due.end_at,
                               job=job_id, state="BLOCKED" if reason else "PENDING", reason=reason))
                    created += 1
                connection.execute(text(
                    "UPDATE research.collection_plans SET status=:status,reason=:reason,"
                    "scheduled_through_at=:through,next_due_at=:due WHERE instrument_id=:id"
                ), dict(id=plan["instrument_id"], through=through,
                        status="BLOCKED" if blocked_reason else "ACTIVE",
                        reason=blocked_reason or "OK",
                        due=min((d.due_at for d in candidates if d.due_at > through),
                                default=now + timedelta(seconds=5))))
        return created


def registered_instruments():
    return {str(instrument.instrument_id): mapping.symbol
            for instrument, mapping in definitions()}
