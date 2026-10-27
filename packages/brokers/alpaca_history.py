"""Execution-owned, bounded GET-only historical research adapter.

Only normalized values cross this boundary. Private pagination state never belongs
in public source DTOs. Calendar responses prove returned sessions, not holidays.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from packages.domain.models import utc_string, utc_time
from packages.research.models import (
    Adjustment,
    BarIdentity,
    BarInterval,
    BarRevision,
    EvidenceType,
    content_fingerprint,
)
from packages.research.ports import (
    HistoricalBarsRequest,
    LicenseStatus,
    SourceCapability,
    SourceError,
    SourceErrorCode,
    SourceStatus,
)

_DATA = "https://data.alpaca.markets/v2/stocks/bars"
_CALENDAR = "https://paper-api.alpaca.markets/v2/calendar"
_EASTERN = ZoneInfo("America/New_York")
_MAX_BODY = 2 * 1024 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9_./+=-]{1,1024}\Z")


@dataclass(frozen=True)
class ProviderPolicy:
    license_status: LicenseStatus
    policy_version: str | None
    scope: str = "LOCAL_RESEARCH"
    max_gets_per_minute: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.license_status, LicenseStatus) or self.scope != "LOCAL_RESEARCH":
            raise ValueError("invalid local research policy")
        if self.policy_version is not None and not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", self.policy_version
        ):
            raise ValueError("invalid policy version")
        if self.max_gets_per_minute is not None and (
            type(self.max_gets_per_minute) is not int or self.max_gets_per_minute < 1
        ):
            raise ValueError("invalid entitlement limit")


@dataclass(frozen=True)
class CalendarSession:
    session_date: date
    opens_at: datetime
    closes_at: datetime
    version: str
    source: str = "alpaca-v2-calendar"

    def __post_init__(self) -> None:
        utc_time(self.opens_at)
        utc_time(self.closes_at)
        if type(self.session_date) is not date or self.opens_at >= self.closes_at:
            raise ValueError("invalid calendar session")
        if (
            self.opens_at.astimezone(_EASTERN).date() != self.session_date
            or self.closes_at.astimezone(_EASTERN).date() != self.session_date
        ):
            raise ValueError("calendar session must belong to its New York date")
        if not re.fullmatch(r"[0-9a-f]{64}", self.version) or self.source != "alpaca-v2-calendar":
            raise ValueError("invalid calendar provenance")


@dataclass(frozen=True)
class CalendarPage:
    sessions: tuple[CalendarSession, ...]
    received_at: datetime | None = None

    @property
    def coverage_known(self) -> bool:
        """At least one returned session is proven; absent dates remain unproven."""
        return bool(self.sessions)


@dataclass(frozen=True)
class HistoryPage:
    items: tuple[BarRevision, ...]
    next_page_token: str | None = field(default=None, repr=False)
    received_at: datetime | None = None


class HistorySourceError(SourceError):
    def __init__(self, code: SourceErrorCode, retry_after: int | None = None) -> None:
        super().__init__(code)
        if retry_after is not None and (type(retry_after) is not int or not 0 <= retry_after <= 60):
            raise ValueError("retry delay must be an integer in [0,60]")
        self.retry_after = retry_after


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("nonfinite JSON value")


def _decode(body: bytes) -> object:
    failed = False
    try:
        value = json.loads(
            body,
            object_pairs_hook=_pairs,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise HistorySourceError(SourceErrorCode.INVALID_RESPONSE)
    return value


def _timestamp(raw: object) -> datetime:
    if not isinstance(raw, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", raw
    ):
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)


def _token(value: object) -> str | None:
    if value is not None and (not isinstance(value, str) or not _TOKEN.fullmatch(value)):
        raise HistorySourceError(SourceErrorCode.INVALID_CURSOR)
    return value


def _retry_delay(raw: str, now: datetime) -> int | None:
    if re.fullmatch(r"\d{1,9}", raw):
        return min(int(raw), 60)
    try:
        target = parsedate_to_datetime(raw)
        if target.tzinfo is not None:
            return max(0, min(60, int((target.astimezone(UTC) - now).total_seconds()) + 1))
    except (ValueError, TypeError, OverflowError):
        pass
    return None


class AlpacaHistorySource:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        enabled: bool = False,
        policy: ProviderPolicy,
        transport: httpx.AsyncBaseTransport | httpx.MockTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        egress_proxy: bool = False,
    ) -> None:
        if type(enabled) is not bool or not isinstance(policy, ProviderPolicy):
            raise ValueError("invalid provider configuration")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only exact MockTransport may be injected")
        if type(egress_proxy) is not bool or (egress_proxy and transport is not None):
            raise ValueError("invalid research egress configuration")
        self._api_key, self._api_secret = api_key, api_secret
        self._enabled, self._policy = enabled, policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(5),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            proxy="http://paper-execution-egress:8080" if egress_proxy else None,
        )

    def _check(self) -> None:
        if not self._enabled:
            raise HistorySourceError(SourceErrorCode.UNAVAILABLE)
        if (
            self._policy.license_status != LicenseStatus.CONFIRMED
            or self._policy.policy_version is None
        ):
            raise HistorySourceError(SourceErrorCode.BLOCKED_LICENSE)
        if not self._api_key or not self._api_secret:
            raise HistorySourceError(SourceErrorCode.BLOCKED_CREDENTIALS)

    def capability(self) -> SourceCapability:
        status = SourceStatus.DISABLED
        if self._enabled:
            status = (
                SourceStatus.IMPLEMENTED
                if (
                    self._policy.license_status == LicenseStatus.CONFIRMED
                    and self._policy.policy_version
                    and self._api_key
                    and self._api_secret
                )
                else SourceStatus.BLOCKED
            )
        return SourceCapability(
            "ALPACA",
            EvidenceType.BAR,
            status,
            self._policy.license_status,
            feeds=("iex",),
            intervals=(BarInterval.FIFTEEN_MINUTES, BarInterval.ONE_DAY),
            quality_limits=(
                "FIRST_OBSERVED_ONLY",
                "IEX_NOT_FULL_MARKET",
                "NO_HISTORICAL_PUBLICATION",
            ),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, endpoint: str, parameters: dict) -> object:
        self._check()
        if endpoint not in {_DATA, _CALENDAR}:
            raise HistorySourceError(SourceErrorCode.INVALID_REQUEST)
        code, retry_after, received_at = None, None, None
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                async with self._client.stream(
                    "GET",
                    endpoint,
                    params=parameters,
                    headers={
                        "APCA-API-KEY-ID": self._api_key,
                        "APCA-API-SECRET-KEY": self._api_secret,
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                ) as response:
                    if response.status_code in {401, 403}:
                        code = SourceErrorCode.BLOCKED_ENTITLEMENT
                    elif response.status_code == 429:
                        code = SourceErrorCode.RATE_LIMITED
                        raw = response.headers.get("retry-after", "")
                        retry_after = _retry_delay(raw, utc_time(self._clock()))
                    elif 500 <= response.status_code <= 599:
                        code = SourceErrorCode.UNAVAILABLE
                    elif (
                        response.status_code in {400, 422}
                        and endpoint == _DATA
                        and parameters.get("page_token") is not None
                    ):
                        # Do not inspect or retain provider error payloads. A
                        # rejected continuation restarts its fixed partition
                        # under a bounded new receipt generation, never a fallback.
                        code = SourceErrorCode.INVALID_CURSOR
                    elif response.status_code != 200:
                        code = SourceErrorCode.INVALID_RESPONSE
                    elif response.headers.get("content-encoding", "identity").lower() != "identity":
                        # Do not run an unbounded decompressor on attacker-controlled data.
                        code = SourceErrorCode.INVALID_RESPONSE
                    else:
                        length = response.headers.get("content-length")
                        if length and (
                            not length.isascii()
                            or not length.isdigit()
                            or len(length) > 12
                            or int(length) > _MAX_BODY
                        ):
                            code = SourceErrorCode.RESPONSE_TOO_LARGE
                        else:
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                if len(body) + len(chunk) > _MAX_BODY:
                                    code = SourceErrorCode.RESPONSE_TOO_LARGE
                                    break
                                body.extend(chunk)
                            if code is None:
                                # Vollständiger Empfang vor Parsing/Normalisierung.
                                received_at = utc_time(self._clock())
        except (TimeoutError, httpx.TimeoutException):
            code = SourceErrorCode.TIMEOUT
        except (httpx.HTTPError, ValueError):
            code = SourceErrorCode.UNAVAILABLE
        if code is not None:
            # Raise outside handlers: no HTTPX exception context retaining private request headers.
            raise HistorySourceError(code, retry_after)
        return _decode(bytes(body)), received_at

    async def fetch_calendar(self, start: date, end: date) -> CalendarPage:
        self._check()
        if type(start) is not date or type(end) is not date or not 0 <= (end - start).days <= 366:
            raise HistorySourceError(SourceErrorCode.INVALID_REQUEST)
        payload, received = await self._get(
            _CALENDAR, {"start": start.isoformat(), "end": end.isoformat()},
        )
        failed = False
        try:
            if not isinstance(payload, list) or len(payload) > 367:
                raise ValueError("invalid calendar")
            sessions = []
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError("invalid calendar session")
                day = date.fromisoformat(item["date"])
                if not start <= day <= end:
                    raise ValueError("calendar outside request")
                times = []
                for name in ("open", "close"):
                    raw = item[name]
                    if not isinstance(raw, str) or not re.fullmatch(r"\d{2}:\d{2}", raw):
                        raise ValueError("invalid session time")
                    times.append(
                        datetime.combine(day, time.fromisoformat(raw), _EASTERN).astimezone(UTC)
                    )
                version = content_fingerprint(("alpaca-v2-calendar", day.isoformat(), tuple(times)))
                sessions.append(CalendarSession(day, times[0], times[1], version))
            if len({session.session_date for session in sessions}) != len(sessions):
                raise ValueError("duplicate calendar day")
        except (ValueError, KeyError, TypeError):
            failed = True
        if failed:
            raise HistorySourceError(SourceErrorCode.INVALID_RESPONSE)
        return CalendarPage(
            tuple(sorted(sessions, key=lambda session: session.session_date)), received,
        )

    async def fetch_page(
        self,
        request: HistoricalBarsRequest,
        private_page_token: str | None = None,
        *,
        sessions: tuple[CalendarSession, ...] = (),
    ) -> HistoryPage:
        self._check()
        if (
            not isinstance(request, HistoricalBarsRequest)
            or request.symbol not in {"SPY", "QQQ"}
            or request.feed != "iex"
            or request.adjustment != Adjustment.RAW
            or not isinstance(sessions, tuple)
            or not sessions
            or any(not isinstance(session, CalendarSession) for session in sessions)
            or len({session.session_date for session in sessions}) != len(sessions)
        ):
            raise HistorySourceError(SourceErrorCode.INVALID_REQUEST)
        token = _token(private_page_token)
        parameters = {
            "symbols": request.symbol,
            "timeframe": request.interval.value,
            "start": utc_string(request.start),
            "end": utc_string(request.end),
            "limit": request.limit,
            "feed": "iex",
            "adjustment": "raw",
            "currency": "USD",
            "sort": "asc",
            "asof": "-",
        }
        if request.interval == BarInterval.ONE_DAY:
            day = request.start.astimezone(_EASTERN).date()
            parameters["start"] = utc_string(
                datetime.combine(day, time(0), _EASTERN).astimezone(UTC)
            )
        if token is not None:
            parameters["page_token"] = token
        payload, received = await self._get(_DATA, parameters)
        failed = False
        try:
            page = self._normalize(payload, request, sessions, received_at=received)
        except (ValueError, KeyError, TypeError):
            failed = True
        if failed:
            raise HistorySourceError(SourceErrorCode.INVALID_RESPONSE)
        if token is not None and page.next_page_token == token:
            raise HistorySourceError(SourceErrorCode.PAGINATION_CYCLE)
        return page

    def _normalize(
        self, payload: object, request: HistoricalBarsRequest,
        sessions: tuple[CalendarSession, ...],
        *, received_at: datetime | None = None,
    ) -> HistoryPage:
        if not isinstance(payload, dict) or not isinstance(payload.get("bars"), dict):
            raise ValueError("invalid bars envelope")
        source = payload["bars"]
        if set(source) - {request.symbol}:
            raise ValueError("unexpected instrument")
        values = source.get(request.symbol, [])
        if not isinstance(values, list) or len(values) > request.limit:
            raise ValueError("invalid page size")
        next_token = _token(payload.get("next_page_token"))
        observed = utc_time(received_at if received_at is not None else self._clock())
        by_day = {session.session_date: session for session in sessions}
        bars = []
        previous_start = None
        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError("invalid bar")
            start = _timestamp(raw["t"])
            if previous_start is not None and start <= previous_start:
                raise ValueError("unordered or duplicate bar")
            previous_start = start
            # The upstream end boundary is inclusive. Exclude it before requiring calendar proof.
            if request.interval != BarInterval.ONE_DAY and not request.start <= start < request.end:
                continue
            session = by_day.get(start.astimezone(_EASTERN).date())
            if session is None:
                raise ValueError("calendar proof missing")
            if request.interval == BarInterval.ONE_DAY:
                if start.astimezone(_EASTERN).time() != time(0):
                    raise ValueError("daily timestamp must identify New York midnight")
                start = session.opens_at
                end = session.closes_at
            else:
                end = start + timedelta(minutes=15)
                if start < session.opens_at or end > session.closes_at:
                    # Provider history may include extended hours; the certified dataset does not.
                    continue
                if (start - session.opens_at) % timedelta(minutes=15):
                    raise ValueError("bar off session grid")
            if not request.start <= start < request.end:
                continue
            if end > request.end:
                continue
            bars.append(
                BarRevision(
                    uuid4(),
                    BarIdentity(
                        request.instrument_id,
                        "alpaca",
                        "iex",
                        request.interval,
                        start,
                        end,
                        Adjustment.RAW,
                    ),
                    raw["o"],
                    raw["h"],
                    raw["l"],
                    raw["c"],
                    raw["v"],
                    observed,
                    observed,
                )
            )
        return HistoryPage(tuple(bars), next_token, received_at)
