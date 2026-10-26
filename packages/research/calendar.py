"""Pure regular-session windows backed by explicit historical calendar evidence."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from packages.domain.models import utc_time
from packages.research.models import BarInterval, _label

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ResearchSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime
    version: str
    source: str = "alpaca-v2-calendar"

    def __post_init__(self) -> None:
        _label(self.version)
        _label(self.source)
        utc_time(self.opens_at)
        utc_time(self.closes_at)
        if type(self.session_date) is not date or self.closes_at <= self.opens_at:
            raise ValueError("invalid session")
        if any(
            value.astimezone(NEW_YORK).date() != self.session_date
            for value in (self.opens_at, self.closes_at)
        ):
            raise ValueError("session boundaries must belong to its New York date")
        if (self.closes_at - self.opens_at) % timedelta(minutes=15):
            raise ValueError("session does not align to supported interval")


@dataclass(frozen=True)
class CalendarCoverage:
    """Inclusive proven date bounds; empty responses cannot prove holidays.

    Authoritative coverage additionally needs observed sessions at both bounds.
    This deliberately leaves response-edge holidays unknown until bracketed by
    evidence. A request range or HTTP success alone is never coverage evidence.
    """

    start_date: date | None
    end_date: date | None
    sessions: tuple[ResearchSession, ...]
    version: str
    authoritative: bool = False

    def __post_init__(self) -> None:
        _label(self.version)
        if type(self.authoritative) is not bool or not isinstance(self.sessions, tuple):
            raise ValueError("immutable typed calendar evidence required")
        if any(not isinstance(session, ResearchSession) for session in self.sessions):
            raise ValueError("normalized sessions required")
        dates = tuple(session.session_date for session in self.sessions)
        if len(set(dates)) != len(dates) or dates != tuple(sorted(dates)):
            raise ValueError("calendar sessions must have unique ordered dates")
        if any(session.version != self.version for session in self.sessions):
            raise ValueError("calendar evidence versions differ")
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("calendar requires both bounds")
        if self.start_date is not None:
            if type(self.start_date) is not date or type(self.end_date) is not date:
                raise ValueError("calendar bounds must be dates")
            if self.end_date < self.start_date:
                raise ValueError("invalid calendar bounds")
            if any(not self.start_date <= day <= self.end_date for day in dates):
                raise ValueError("sessions outside calendar bounds")
        if self.authoritative and (
            not dates or dates[0] != self.start_date or dates[-1] != self.end_date
        ):
            raise ValueError("authoritative bounds require observed boundary sessions")


@dataclass(frozen=True)
class CalendarWindows:
    windows: tuple[tuple[datetime, datetime], ...]
    unknown_dates: tuple[date, ...]


def expected_windows(
    coverage: CalendarCoverage,
    interval: BarInterval,
    start: datetime,
    end: datetime,
    valuation_at: datetime,
) -> CalendarWindows:
    """Select fully contained windows whose close is no later than valuation.

    Daily windows use regular-session open/close, never a fabricated 24h bar.
    An unbounded calendar still proves individual returned sessions, not holidays.
    """
    for value in (start, end, valuation_at):
        utc_time(value)
    if end <= start or not isinstance(interval, BarInterval):
        raise ValueError("invalid calendar query")
    sessions = {session.session_date: session for session in coverage.sessions}
    windows = []
    unknown = []
    day = start.astimezone(NEW_YORK).date()
    last = (min(end, valuation_at) - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
    while day <= last:
        session = sessions.get(day)
        if session is None:
            if not (coverage.authoritative and coverage.start_date <= day <= coverage.end_date):
                unknown.append(day)
        else:
            window_start = session.opens_at
            step = (
                timedelta(minutes=15)
                if interval == BarInterval.FIFTEEN_MINUTES
                else session.closes_at - session.opens_at
            )
            while window_start < session.closes_at:
                window_end = window_start + step
                if start <= window_start < window_end <= min(end, valuation_at):
                    windows.append((window_start, window_end))
                window_start = window_end
        day += timedelta(days=1)
    return CalendarWindows(tuple(windows), tuple(unknown))
