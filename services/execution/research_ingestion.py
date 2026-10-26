"""Execution-only, one-GET research ticks. Provider awaits never hold SQL locks."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from packages.brokers.alpaca_history import AlpacaHistorySource, CalendarSession
from packages.domain.models import utc_time
from packages.research.calendar import NEW_YORK, ResearchSession
from packages.research.calendar_storage import append_sessions, read_calendar
from packages.research.jobs import Lease, ResearchJobError, ResearchJobs
from packages.research.limits import SharedAccountHostLimiter
from packages.research.models import (
    Adjustment,
    BarInterval,
    QualityStatus,
    SymbolMapping,
    content_fingerprint,
)
from packages.research.ports import (
    HistoricalBarsRequest,
    LicenseStatus,
    SourceError,
    SourceErrorCode,
    SourceStatus,
)
from packages.research.query import ResearchQuery
from packages.research.storage import ResearchStore

_DATA_HOST = "data.alpaca.markets"
_CALENDAR_HOST = "paper-api.alpaca.markets"


@dataclass(frozen=True)
class SymbolPartition:
    symbol: str
    start: datetime
    end: datetime


def symbol_partitions(
    instrument_id: UUID,
    start: datetime,
    end: datetime,
    mappings: tuple[SymbolMapping, ...],
    *,
    interval: BarInterval,
) -> tuple[SymbolPartition, ...]:
    """Partition continuous, unambiguous symbol validity; never silently skip a range."""
    utc_time(start)
    utc_time(end)
    if end <= start or not isinstance(interval, BarInterval) or not isinstance(mappings, tuple):
        raise SourceError(SourceErrorCode.INVALID_REQUEST)
    selected = sorted(
        (
            item
            for item in mappings
            if item.instrument_id == instrument_id
            and item.provider == "alpaca"
            and item.valid_from < end
            and (item.valid_to is None or item.valid_to > start)
        ),
        key=lambda item: item.valid_from,
    )
    cursor, partitions = start, []
    for item in selected:
        left, right = max(start, item.valid_from), min(end, item.valid_to or end)
        if item.symbol not in {"SPY", "QQQ"} or left != cursor:
            raise SourceError(SourceErrorCode.INVALID_REQUEST)
        while left < right:
            boundary = left + timedelta(days=365)
            if interval == BarInterval.ONE_DAY:
                # Split on a New York day boundary, leaving room for the provider's daily bucket.
                boundary = datetime.combine(
                    boundary.astimezone(NEW_YORK).date(), datetime.min.time(), NEW_YORK
                ).astimezone(UTC)
            stop = min(right, boundary)
            partitions.append(SymbolPartition(item.symbol, left, stop))
            left = stop
        cursor = right
    if cursor != end or not partitions or len(partitions) > 1000:
        raise SourceError(SourceErrorCode.INVALID_REQUEST)
    return tuple(partitions)


@dataclass(frozen=True)
class TickPlan:
    job_id: UUID
    request: HistoricalBarsRequest
    sessions: tuple[CalendarSession, ...]
    more_partitions: bool
    cursor_hashes: frozenset[str]

    @property
    def host(self) -> str:
        return _DATA_HOST if self.sessions else _CALENDAR_HOST


@dataclass(frozen=True)
class _BlockedPreflight:
    job_id: UUID
    host: str = _DATA_HOST


def cursor_hash(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


class ResearchIngestion:
    def __init__(
        self,
        jobs: ResearchJobs,
        source_factory: Callable[[], Awaitable[AlpacaHistorySource]],
        limiter: SharedAccountHostLimiter,
        *,
        enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
        source_observer: Callable | None = None,
        quality_observer: Callable | None = None,
        error_observer: Callable | None = None,
    ) -> None:
        if type(enabled) is not bool or not callable(source_factory):
            raise ValueError("invalid research dispatch configuration")
        self.jobs, self._source_factory, self.limiter = jobs, source_factory, limiter
        self.enabled, self._clock = enabled, clock or (lambda: datetime.now(UTC))
        self._owner, self._store = uuid4(), ResearchStore()
        self._source_observer = source_observer
        self._quality_observer = quality_observer
        self._error_observer = error_observer

    def _load_plan(self, connection, row: Mapping, lease: Lease | None = None) -> TickPlan:
        request = (
            lease.request
            if lease is not None
            else {
                "instrumentId": str(row["instrument_id"]),
                "start": row["start_at"].isoformat(),
                "end": row["end_at"].isoformat(),
                "interval": row["interval"],
                "mode": row["mode"],
            }
        )
        instrument = UUID(request["instrumentId"])
        start = datetime.fromisoformat(request["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(request["end"].replace("Z", "+00:00"))
        interval = BarInterval(request["interval"])
        if request["mode"] == "UPDATE":
            frozen = lease.overlap_start_at if lease is not None else row.get("overlap_start_at")
            previous = (
                frozen
                if frozen is not None
                else connection.execute(
                    text("""
                SELECT bar_start FROM research.bar_revisions WHERE instrument_id=:instrument
                  AND interval=:interval AND feed='iex' AND adjustment='raw' AND provider='alpaca'
                  AND quality='VALID' AND bar_end<=:start
                ORDER BY bar_end DESC,local_revision DESC LIMIT 1
            """),
                    {"instrument": instrument, "interval": interval.value, "start": start},
                ).scalar_one_or_none()
            )
            if previous is not None:
                start = previous
        rows = (
            connection.execute(
                text("""
            SELECT instrument_id,provider,symbol,valid_from,valid_to FROM research.symbol_mappings
            WHERE instrument_id=:instrument AND provider='alpaca' AND valid_from<:end
              AND (valid_to IS NULL OR valid_to>:start) ORDER BY valid_from LIMIT 1001
        """),
                {"instrument": instrument, "start": start, "end": end},
            )
            .mappings()
            .all()
        )
        mappings = tuple(SymbolMapping(**dict(item)) for item in rows)
        partitions = symbol_partitions(instrument, start, end, mappings, interval=interval)
        partition = lease.partition if lease is not None else row["partition"]
        if partition >= len(partitions):
            raise SourceError(SourceErrorCode.INVALID_REQUEST)
        selected = partitions[partition]
        now = utc_time(self._clock())
        # Ingestion needs already-known session schedules, including a session not yet closed.
        valuation = max(now, selected.end) + timedelta(days=2)
        query = ResearchQuery().snapshot(connection, valuation, now)
        calendar = read_calendar(connection, selected.start, selected.end, query)
        sessions = tuple(
            CalendarSession(
                item.session_date, item.opens_at, item.closes_at, item.version, item.source
            )
            for item in calendar.sessions
        )
        generation = (
            getattr(lease, "cursor_generation", 0)
            if lease is not None
            else row.get("cursor_generation", 0)
        )
        hashes = (
            connection.execute(
                text("""
            SELECT cursor_hash FROM research.page_receipts
            WHERE job_id=:job AND partition=:partition AND cursor_generation=:generation
        """),
                {"job": row["job_id"], "partition": partition, "generation": generation},
            )
            .scalars()
            .all()
        )
        return TickPlan(
            row["job_id"],
            HistoricalBarsRequest(
                instrument,
                selected.start,
                selected.end,
                limit=1000,
                symbol=selected.symbol,
                feed="iex",
                interval=interval,
                adjustment=Adjustment.RAW,
            ),
            sessions,
            partition + 1 < len(partitions),
            frozenset(hashes),
        )

    def _preflight(self) -> TickPlan | _BlockedPreflight | None:
        with self.jobs.engine.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT * FROM research.ingestion_jobs WHERE
                  status='ACCEPTED' OR (status='RETRY_WAIT' AND retry_at<=clock_timestamp())
                  OR (status='RUNNING' AND lease_until<=clock_timestamp())
                ORDER BY accepted_at,job_id LIMIT 1
            """)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            try:
                return self._load_plan(connection, row)
            except SourceError:
                # Claim only after an admitted slot, then persist the invalid partition as blocked.
                return _BlockedPreflight(row["job_id"])

    def _claimed_plan(self, lease: Lease) -> TickPlan:
        with self.jobs.engine.connect() as connection:
            return self._load_plan(connection, {"job_id": lease.job_id}, lease)

    def _handle(self, lease: Lease, error: SourceError, host: str) -> None:
        if error.code == SourceErrorCode.INVALID_CURSOR and hasattr(self.jobs, "restart_partition"):
            self.jobs.restart_partition(lease)
        elif error.code in {
            SourceErrorCode.RATE_LIMITED,
            SourceErrorCode.TIMEOUT,
            SourceErrorCode.UNAVAILABLE,
        }:
            delay = getattr(error, "retry_after", None)
            if error.code == SourceErrorCode.RATE_LIMITED:
                self.limiter.block_research(host, delay if delay is not None else 1)
            self.jobs.retry(lease, error.code, delay)
        else:
            if self._quality_observer is not None or self._error_observer is not None:

                def observe_error(connection):
                    if (
                        error.code == SourceErrorCode.INVALID_RESPONSE
                        and self._quality_observer is not None
                    ):
                        self._quality_observer(connection, lease, QualityStatus.INVALID)
                    if self._error_observer is not None:
                        self._error_observer(connection, lease, error.code)

                self.jobs.block(lease, error.code, write_callback=observe_error)
            else:
                self.jobs.block(lease, error.code)

    async def tick(self, safety_busy: bool = False) -> bool:
        """Make at most one admitted GET, then atomically commit normalized progress."""
        if not self.enabled or safety_busy:
            return False
        lease, source, host = None, None, _DATA_HOST
        try:
            planned = self._preflight()
            if planned is None:
                return False
            host = planned.host
            if not self.limiter.allow(host, research=True, safety_busy=safety_busy):
                return False
            lease = self.jobs.claim(self._owner)
            if lease is None:
                return False
            plan = self._claimed_plan(lease)
            if plan.host != host:
                self.jobs.commit_metadata(lease, lambda connection: None)
                return True
            source = await self._source_factory()
            capability = source.capability()
            if capability.license_status != LicenseStatus.CONFIRMED:
                raise SourceError(SourceErrorCode.BLOCKED_LICENSE)
            if capability.status != SourceStatus.IMPLEMENTED:
                raise SourceError(SourceErrorCode.BLOCKED_CREDENTIALS)
            if not plan.sessions:
                start_day = plan.request.start.astimezone(NEW_YORK).date()
                end_day = (plan.request.end - timedelta(microseconds=1)).astimezone(NEW_YORK).date()
                calendar = await source.fetch_calendar(start_day, end_day)
                if not calendar.sessions:
                    raise SourceError(SourceErrorCode.INVALID_RESPONSE)
                sessions = tuple(
                    ResearchSession(
                        item.session_date, item.opens_at, item.closes_at, item.version, item.source
                    )
                    for item in calendar.sessions
                )
                self.jobs.commit_metadata(
                    lease, lambda connection: append_sessions(connection, sessions)
                )
                return True
            current_hash = cursor_hash(lease.private_cursor)
            if current_hash in plan.cursor_hashes:
                raise SourceError(SourceErrorCode.PAGINATION_CYCLE)
            page = await source.fetch_page(
                plan.request, lease.private_cursor, sessions=plan.sessions
            )
            if page.next_page_token is not None and (
                cursor_hash(page.next_page_token) == current_hash
                or cursor_hash(page.next_page_token) in plan.cursor_hashes
            ):
                raise SourceError(SourceErrorCode.PAGINATION_CYCLE)
            bars = tuple(
                bar
                for bar in page.items
                if bar.identity.bar_start >= plan.request.start
                and bar.identity.bar_end <= plan.request.end
            )
            page_hash = content_fingerprint(
                (
                    "normalized-bars-page-v1",
                    plan.request,
                    tuple(bar.fingerprint for bar in bars),
                    cursor_hash(page.next_page_token),
                )
            )

            def write(connection):
                for bar in bars:
                    self._store.append_bar(connection, bar, symbol=plan.request.symbol)
                if self._source_observer is not None:
                    self._source_observer(connection, lease, len(bars))
                if (
                    self._quality_observer is not None
                    and page.next_page_token is None
                    and not plan.more_partitions
                ):
                    self._quality_observer(connection, lease, QualityStatus.VALID)
                return len(bars)

            self.jobs.commit_page(
                lease,
                page_hash,
                current_hash,
                page.next_page_token,
                write,
                more_partitions=plan.more_partitions,
            )
            return True
        except SourceError as error:
            if lease is not None:
                try:
                    self._handle(lease, error, host)
                except ResearchJobError:
                    pass  # A new lease owner is the only party allowed to persist progress.
            return lease is not None
        except ResearchJobError as error:
            if (
                lease is not None
                and error.code == "PROVIDER_RESPONSE_INVALID"
                and self._quality_observer is not None
            ):
                try:
                    self.jobs.block(
                        lease,
                        SourceErrorCode.INVALID_RESPONSE,
                        write_callback=lambda connection: self._quality_observer(
                            connection, lease, QualityStatus.CONFLICT
                        ),
                    )
                except ResearchJobError:
                    pass
            return lease is not None
        except Exception:
            if lease is not None:
                try:
                    self.jobs.block(lease, SourceErrorCode.INVALID_RESPONSE)
                except Exception:
                    pass
            return lease is not None
        finally:
            if source is not None:
                try:
                    await source.close()
                except Exception:
                    pass
