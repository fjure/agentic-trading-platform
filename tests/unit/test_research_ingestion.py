"""Coordinator state planning against minimal job/source ports; no simulated SQL."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.brokers.alpaca_history import (
    CalendarPage,
    CalendarSession,
    HistoryPage,
    HistorySourceError,
)
from packages.domain.models import utc_string
from packages.research.jobs import Lease, ResearchJobError
from packages.research.limits import SharedAccountHostLimiter
from packages.research.models import (
    Adjustment,
    BarIdentity,
    BarInterval,
    BarRevision,
    EvidenceType,
    SymbolMapping,
)
from packages.research.ports import (
    HistoricalBarsRequest,
    LicenseStatus,
    SourceCapability,
    SourceError,
    SourceErrorCode,
    SourceStatus,
)
from services.execution.research_ingestion import (
    ResearchIngestion,
    TickPlan,
    cursor_hash,
    symbol_partitions,
)

INSTRUMENT = uuid4()
START = datetime(2026, 10, 22, 13, 30, tzinfo=UTC)
END = START + timedelta(minutes=45)
NOW = datetime(2026, 10, 25, tzinfo=UTC)
SESSION = CalendarSession(
    date(2026, 10, 22), START, START + timedelta(hours=6, minutes=30), "0" * 64
)


def drive(coroutine):
    """These port doubles never suspend; no event loop or sockets are required."""
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    raise AssertionError("unexpected asynchronous I/O in pure coordinator test")


def lease():
    request = dict(
        instrumentId=str(INSTRUMENT),
        start=utc_string(START),
        end=utc_string(END),
        interval="15Min",
        mode="BACKFILL",
        feed="iex",
        adjustment="raw",
    )
    return Lease(uuid4(), uuid4(), 1, request, 0, 0, 0, 0, NOW)


def plan(claim, sessions=(SESSION,), hashes=frozenset()):
    return TickPlan(
        claim.job_id,
        HistoricalBarsRequest(
            INSTRUMENT,
            START,
            END,
            symbol="SPY",
            feed="iex",
            interval=BarInterval.FIFTEEN_MINUTES,
            adjustment=Adjustment.RAW,
        ),
        sessions,
        False,
        hashes,
    )


class Jobs:
    def __init__(self):
        self.lease = lease()
        self.actions = []
        self.stale = False

    def claim(self, owner):
        self.actions.append("claim")
        return self.lease

    def commit_metadata(self, claim, callback):
        self.actions.append("metadata")
        # SQL correctness is covered in the independent PostgreSQL certification.
        assert callable(callback)

    def commit_page(self, claim, page_hash, source_hash, next_token, callback, **keywords):
        if self.stale:
            raise ResearchJobError(409, "LEASE_LOST")
        count = callback(None)
        self.actions.append(("page", count, source_hash, next_token, keywords))
        assert len(page_hash) == 64

    def block(self, claim, code):
        self.actions.append(("block", code))

    def retry(self, claim, code, delay):
        self.actions.append(("retry", code, delay))

    def restart_partition(self, claim):
        self.actions.append("restart")


class Source:
    def __init__(self):
        self.calls = []
        self.error = None
        self.page = HistoryPage(())
        self.calendar = CalendarPage((SESSION,))
        self.license = LicenseStatus.CONFIRMED

    def capability(self):
        return SourceCapability(
            "alpaca-iex-bars", EvidenceType.BAR, SourceStatus.IMPLEMENTED, self.license
        )

    async def fetch_calendar(self, start, end):
        self.calls.append("calendar")
        if self.error:
            raise self.error
        return self.calendar

    async def fetch_page(self, request, token, *, sessions):
        self.calls.append("bars")
        if self.error:
            raise self.error
        return self.page

    async def close(self):
        self.calls.append("closed")


class Coordinator(ResearchIngestion):
    def __init__(self, *, enabled=True, entitlement=120):
        self.fake_jobs, self.source = Jobs(), Source()
        self.factory_calls, self.writes = 0, []
        self.monotonic = 0.0

        async def factory():
            self.factory_calls += 1
            return self.source

        super().__init__(
            self.fake_jobs,
            factory,
            SharedAccountHostLimiter("offline", entitlement, clock=lambda: self.monotonic),
            enabled=enabled,
            clock=lambda: NOW,
        )
        self.current = plan(self.fake_jobs.lease)
        self._store = self

    def _preflight(self):
        return self.current

    def _claimed_plan(self, claim):
        return self.current

    def append_bar(self, connection, bar, *, symbol):
        self.writes.append((bar, symbol))


@pytest.mark.parametrize(
    "configuration,busy", [({"enabled": False}, False), ({}, True), ({"entitlement": None}, False)]
)
def test_disabled_safety_and_unknown_entitlement_never_claim_or_create_source(configuration, busy):
    coordinator = Coordinator(**configuration)
    assert drive(coordinator.tick(safety_busy=busy)) is False
    assert coordinator.fake_jobs.actions == [] and coordinator.factory_calls == 0


def test_one_calendar_get_is_a_separate_fenced_metadata_tick():
    coordinator = Coordinator()
    coordinator.current = plan(coordinator.fake_jobs.lease, sessions=())
    assert drive(coordinator.tick())
    assert coordinator.source.calls == ["calendar", "closed"]
    assert coordinator.fake_jobs.actions == ["claim", "metadata"]
    assert coordinator.writes == []


def test_empty_calendar_blocks_without_fabricating_holiday_coverage():
    coordinator = Coordinator()
    coordinator.current = plan(coordinator.fake_jobs.lease, sessions=())
    coordinator.source.calendar = CalendarPage(())
    assert drive(coordinator.tick())
    assert coordinator.fake_jobs.actions[-1] == ("block", SourceErrorCode.INVALID_RESPONSE)
    assert "metadata" not in coordinator.fake_jobs.actions


def test_policy_denies_io_even_after_an_admitted_job_lease():
    coordinator = Coordinator()
    coordinator.source.license = LicenseStatus.UNKNOWN
    assert drive(coordinator.tick())
    assert coordinator.source.calls == ["closed"]
    assert coordinator.fake_jobs.actions[-1] == ("block", SourceErrorCode.BLOCKED_LICENSE)


def test_bars_commit_only_normalized_values_once_then_rate_limit_prevents_claim():
    coordinator = Coordinator()
    bar = BarRevision(
        uuid4(),
        BarIdentity(
            INSTRUMENT,
            "alpaca",
            "iex",
            BarInterval.FIFTEEN_MINUTES,
            START,
            START + timedelta(minutes=15),
        ),
        Decimal("100"),
        Decimal("105"),
        Decimal("95"),
        Decimal("101"),
        Decimal("2"),
        NOW,
        NOW,
    )
    coordinator.source.page = HistoryPage((bar,), "next-page")
    assert drive(coordinator.tick())
    assert coordinator.writes == [(bar, "SPY")]
    assert coordinator.source.calls == ["bars", "closed"]
    assert coordinator.fake_jobs.actions[-1][0:3] == ("page", 1, cursor_hash(None))
    before = tuple(coordinator.fake_jobs.actions)
    assert drive(coordinator.tick()) is False
    assert tuple(coordinator.fake_jobs.actions) == before


@pytest.mark.parametrize(
    "code,delay,expected",
    [
        (SourceErrorCode.RATE_LIMITED, 40, "retry"),
        (SourceErrorCode.TIMEOUT, None, "retry"),
        (SourceErrorCode.UNAVAILABLE, None, "retry"),
        (SourceErrorCode.BLOCKED_ENTITLEMENT, None, "block"),
        (SourceErrorCode.INVALID_RESPONSE, None, "block"),
    ],
)
def test_provider_errors_schedule_durable_actions_without_sleep(code, delay, expected):
    coordinator = Coordinator()
    coordinator.source.error = HistorySourceError(code, delay)
    assert drive(coordinator.tick())
    assert coordinator.fake_jobs.actions[-1][:2] == (expected, code)
    assert coordinator.writes == []
    if code == SourceErrorCode.RATE_LIMITED:
        coordinator.monotonic = 2
        assert drive(coordinator.tick()) is False


def test_nonimmediate_pagination_cycle_is_rejected_before_mutation():
    coordinator = Coordinator()
    coordinator.fake_jobs.lease = replace(coordinator.fake_jobs.lease, private_cursor="B")
    coordinator.current = plan(coordinator.fake_jobs.lease, hashes=frozenset({cursor_hash("A")}))
    coordinator.source.page = HistoryPage((), "A")
    assert drive(coordinator.tick())
    assert coordinator.fake_jobs.actions[-1] == ("block", SourceErrorCode.PAGINATION_CYCLE)
    assert coordinator.writes == []


def test_invalid_cursor_uses_explicit_partition_restart_port():
    coordinator = Coordinator()
    coordinator.source.error = SourceError(SourceErrorCode.INVALID_CURSOR)
    assert drive(coordinator.tick())
    assert coordinator.fake_jobs.actions[-1] == "restart"


def test_stale_lease_commit_error_is_redacted_and_not_retried_by_an_old_owner():
    coordinator = Coordinator()
    coordinator.fake_jobs.stale = True
    assert drive(coordinator.tick())
    assert coordinator.fake_jobs.actions == ["claim"]
    assert coordinator.writes == []


def test_symbol_partitions_cover_renames_and_reject_gaps_overlaps_and_unsupported_symbols():
    middle = START + timedelta(minutes=15)
    old = SymbolMapping(INSTRUMENT, "alpaca", "SPY", START, middle)
    new = SymbolMapping(INSTRUMENT, "alpaca", "QQQ", middle, END)
    result = symbol_partitions(
        INSTRUMENT, START, END, (new, old), interval=BarInterval.FIFTEEN_MINUTES
    )
    assert [(part.symbol, part.start, part.end) for part in result] == [
        ("SPY", START, middle),
        ("QQQ", middle, END),
    ]
    for mappings in [
        (old,),
        (old, replace(new, valid_from=START)),
        (old, replace(new, symbol="AAPL")),
    ]:
        with pytest.raises(SourceError):
            symbol_partitions(
                INSTRUMENT, START, END, mappings, interval=BarInterval.FIFTEEN_MINUTES
            )


def test_large_overlap_window_is_partitioned_without_dropping_requested_history():
    beginning = START - timedelta(minutes=15)
    ending = START + timedelta(days=366)
    mapping = SymbolMapping(INSTRUMENT, "alpaca", "SPY", beginning)
    result = symbol_partitions(
        INSTRUMENT, beginning, ending, (mapping,), interval=BarInterval.FIFTEEN_MINUTES
    )
    assert result[0].start == beginning and result[-1].end == ending
    assert all(left.end == right.start for left, right in zip(result, result[1:], strict=False))
    assert all(part.end - part.start <= timedelta(days=366) for part in result)
