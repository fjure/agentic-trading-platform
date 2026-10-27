"""Execution-eigener interner Vorabkalenderjob ohne neue öffentliche Requestart."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from packages.research.calendar import NEW_YORK, ResearchSession
from packages.research.calendar_storage import append_sessions
from packages.research.collection import CollectionStore, Dispatch
from packages.research.models import content_fingerprint
from packages.research.ports import LicenseStatus, SourceError, SourceErrorCode, SourceStatus


class CalendarCollection:
    def __init__(self, engine, source_factory, limiter, *, enabled=False, clock=None,
                 error_observer=None):
        if type(enabled) is not bool:
            raise ValueError("INVALID_COLLECTION_ENABLE")
        self.engine, self.source_factory, self.limiter = engine, source_factory, limiter
        self.enabled = enabled
        self.clock = clock or (lambda: datetime.now(UTC))
        self.store = CollectionStore(engine)
        self.error_observer = error_observer

    async def tick(self, safety_busy=False):
        if not self.enabled or safety_busy:
            return False
        with self.engine.connect() as connection:
            due = connection.execute(text("""
              SELECT d.* FROM research.due_jobs d
              JOIN research.collection_plans p USING(instrument_id)
              WHERE d.purpose='CALENDAR' AND d.state='PENDING' AND p.enabled
                AND d.generation=p.generation AND d.due_at<=clock_timestamp()
                AND (d.lease_until IS NULL OR d.lease_until<=clock_timestamp())
              ORDER BY d.due_at,d.due_id LIMIT 1
            """)).mappings().first()
        if due is None or not self.limiter.allow(
            "paper-api.alpaca.markets", research=True, safety_busy=safety_busy,
        ):
            return False
        source, dispatch, state, retry_after = None, None, "UNKNOWN", None
        try:
            source = await self.source_factory()
            capability = source.capability()
            if capability.license_status != LicenseStatus.CONFIRMED:
                raise SourceError(SourceErrorCode.BLOCKED_LICENSE)
            if capability.status != SourceStatus.IMPLEMENTED:
                raise SourceError(SourceErrorCode.BLOCKED_CREDENTIALS)
            dispatch = self.store.admit(
                None, due["fence"] + 1, due_id=due["due_id"], global_enabled=self.enabled,
                safety_busy=safety_busy or self.limiter.safety_busy(),
            )
            if not isinstance(dispatch, Dispatch):
                return False
            start = due["start_at"].astimezone(NEW_YORK).date()
            end = (due["end_at"] - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
            calendar = await source.fetch_calendar(start, end)
            received = getattr(calendar, "received_at", None)
            sessions = tuple(ResearchSession(
                item.session_date, item.opens_at, item.closes_at, item.version, item.source,
            ) for item in calendar.sessions)
            if not sessions:
                state = "BLOCKED"
                return True
            with self.engine.begin() as connection:
                # Kein Planlock nach I/O: bereits zugelassene Antworten dürfen auch
                # nach bestätigter Pause ihre tatsächliche Beobachtung persistieren.
                append_sessions(connection, sessions)
                receipt = self.store.record(
                    connection, dispatch, state="COMPLETED", first_observed_at=received,
                    body={"calendarResponseFingerprint": content_fingerprint(sessions)},
                )
            with self.engine.begin() as connection:
                connection.execute(text(
                    "SELECT research.confirm_receipt(:id,'OBSERVATION')"
                ), dict(id=receipt))
            state = "COMPLETED"
            return True
        except SourceError as error:
            if error.code == SourceErrorCode.RATE_LIMITED:
                retry_after = getattr(error, "retry_after", None) or 1
                self.limiter.block_research("paper-api.alpaca.markets", retry_after)
            elif error.code not in {SourceErrorCode.TIMEOUT, SourceErrorCode.UNAVAILABLE}:
                state = "BLOCKED"
                if self.error_observer is not None:
                    with self.engine.begin() as connection:
                        self.error_observer(connection, None, error.code)
            return isinstance(dispatch, Dispatch)
        except Exception:
            return isinstance(dispatch, Dispatch)
        finally:
            if isinstance(dispatch, Dispatch):
                try:
                    self.store.finish(dispatch, state=state, retry_after=retry_after)
                except Exception:
                    pass  # Unbestätigte Messung bleibt UNKNOWN.
            if source is not None:
                await source.close()
