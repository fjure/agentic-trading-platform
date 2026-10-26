"""Separate typed source boundaries. No transport clients, credentials or raw responses."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from packages.domain.models import utc_time
from packages.research.models import (
    Adjustment,
    BarInterval,
    BarRevision,
    EvidenceType,
    FundamentalContent,
    MacroEventContent,
    NewsContent,
    ResearchEvidence,
    _label,
    _uuid,
)


class SourceStatus(StrEnum):
    IMPLEMENTED = "IMPLEMENTED"
    DISABLED = "DISABLED"
    BLOCKED = "BLOCKED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class LicenseStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONFIRMED = "CONFIRMED"
    BLOCKED = "BLOCKED"


class SourceErrorCode(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    BLOCKED_CREDENTIALS = "BLOCKED_CREDENTIALS"
    BLOCKED_ENTITLEMENT = "BLOCKED_ENTITLEMENT"
    BLOCKED_LICENSE = "BLOCKED_LICENSE"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    PAGINATION_CYCLE = "PAGINATION_CYCLE"
    INVALID_CURSOR = "INVALID_CURSOR"


class SourceError(Exception):
    """Stable redacted code only; provider messages and tokens are never retained."""

    def __init__(self, code: SourceErrorCode) -> None:
        if not isinstance(code, SourceErrorCode):
            raise ValueError("typed source error required")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class SourceCapability:
    source: str
    evidence_type: EvidenceType
    status: SourceStatus
    license_status: LicenseStatus = LicenseStatus.UNKNOWN
    feeds: tuple[str, ...] = ()
    intervals: tuple[BarInterval, ...] = ()
    history_start: datetime | None = None
    history_end: datetime | None = None
    quality_limits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _label(self.source)
        if not isinstance(self.evidence_type, EvidenceType) or not isinstance(
            self.status, SourceStatus
        ):
            raise ValueError("unknown capability")
        if not isinstance(self.license_status, LicenseStatus):
            raise ValueError("unknown license status")
        for values in (self.feeds, self.intervals, self.quality_limits):
            if not isinstance(values, tuple):
                raise ValueError("capability collections must be immutable")
        for value in (*self.feeds, *self.quality_limits):
            _label(value)
        if any(not isinstance(value, BarInterval) for value in self.intervals):
            raise ValueError("unknown interval")
        for value in (self.history_start, self.history_end):
            if value is not None:
                utc_time(value)
        if (self.history_start is None) != (self.history_end is None):
            raise ValueError("history requires both bounds")
        if self.history_start is not None and self.history_end <= self.history_start:
            raise ValueError("invalid history bounds")


@dataclass(frozen=True)
class SourceRequest:
    instrument_id: UUID
    start: datetime
    end: datetime
    limit: int = 200

    def __post_init__(self) -> None:
        _uuid(self.instrument_id)
        utc_time(self.start)
        utc_time(self.end)
        if not timedelta(0) < self.end - self.start <= timedelta(days=366):
            raise ValueError("request range must be nonempty and at most 366 days")
        if type(self.limit) is not int or not 1 <= self.limit <= 1000:
            raise ValueError("source page limit outside [1,1000]")


@dataclass(frozen=True, kw_only=True)
class HistoricalBarsRequest(SourceRequest):
    symbol: str
    feed: str
    interval: BarInterval
    adjustment: Adjustment

    def __post_init__(self) -> None:
        super().__post_init__()
        _label(self.symbol)
        if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,15}", self.symbol):
            raise ValueError("invalid provider symbol")
        _label(self.feed)
        if not isinstance(self.interval, BarInterval) or not isinstance(
            self.adjustment, Adjustment
        ):
            raise ValueError("unsupported bar configuration")


@dataclass(frozen=True)
class NewsRequest(SourceRequest):
    pass


@dataclass(frozen=True)
class FundamentalsRequest(SourceRequest):
    pass


@dataclass(frozen=True)
class MacroEventsRequest(SourceRequest):
    pass


@dataclass(frozen=True)
class HistoricalBarsPage:
    items: tuple[BarRevision, ...]
    has_more: bool = False

    def __post_init__(self) -> None:
        _page(self.items, BarRevision, self.has_more)


def _page(items: tuple, expected: type, has_more: bool) -> None:
    if not isinstance(items, tuple) or len(items) > 1000 or type(has_more) is not bool:
        raise ValueError("invalid immutable bounded page")
    if any(not isinstance(item, expected) for item in items):
        raise ValueError("page item type mismatch")


@dataclass(frozen=True)
class NewsPage:
    items: tuple[ResearchEvidence, ...]
    has_more: bool = False

    def __post_init__(self) -> None:
        _evidence_page(self.items, NewsContent, self.has_more)


@dataclass(frozen=True)
class FundamentalsPage:
    items: tuple[ResearchEvidence, ...]
    has_more: bool = False

    def __post_init__(self) -> None:
        _evidence_page(self.items, FundamentalContent, self.has_more)


@dataclass(frozen=True)
class MacroEventsPage:
    items: tuple[ResearchEvidence, ...]
    has_more: bool = False

    def __post_init__(self) -> None:
        _evidence_page(self.items, MacroEventContent, self.has_more)


def _evidence_page(items: tuple, expected: type, has_more: bool) -> None:
    _page(items, ResearchEvidence, has_more)
    if any(not isinstance(item.content, expected) for item in items):
        raise ValueError("evidence page content mismatch")


class HistoricalBarsSource(Protocol):
    def capability(self) -> SourceCapability: ...
    async def fetch(self, request: HistoricalBarsRequest) -> HistoricalBarsPage: ...


class NewsSource(Protocol):
    def capability(self) -> SourceCapability: ...
    async def fetch(self, request: NewsRequest) -> NewsPage: ...


class FundamentalsSource(Protocol):
    def capability(self) -> SourceCapability: ...
    async def fetch(self, request: FundamentalsRequest) -> FundamentalsPage: ...


class MacroEventsSource(Protocol):
    def capability(self) -> SourceCapability: ...
    async def fetch(self, request: MacroEventsRequest) -> MacroEventsPage: ...


class UnavailableNewsSource:
    def capability(self) -> SourceCapability:
        return SourceCapability("news", EvidenceType.NEWS, SourceStatus.NOT_IMPLEMENTED)

    async def fetch(self, request: NewsRequest) -> NewsPage:
        raise SourceError(SourceErrorCode.NOT_IMPLEMENTED)


class UnavailableFundamentalsSource:
    def capability(self) -> SourceCapability:
        return SourceCapability(
            "fundamentals", EvidenceType.FUNDAMENTAL, SourceStatus.NOT_IMPLEMENTED
        )

    async def fetch(self, request: FundamentalsRequest) -> FundamentalsPage:
        raise SourceError(SourceErrorCode.NOT_IMPLEMENTED)


class UnavailableMacroEventsSource:
    def capability(self) -> SourceCapability:
        return SourceCapability("macro", EvidenceType.MACRO_EVENT, SourceStatus.NOT_IMPLEMENTED)

    async def fetch(self, request: MacroEventsRequest) -> MacroEventsPage:
        raise SourceError(SourceErrorCode.NOT_IMPLEMENTED)
