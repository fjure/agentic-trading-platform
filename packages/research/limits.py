"""Nonblocking shared Alpaca data-GET admission limits.

One instance must be shared by existing and research data readers for the same
opaque account scope. The limiter stores only monotonic timestamps and counters;
it never accepts credentials, request data, or provider responses.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass

_ALLOWED_HOSTS = frozenset({"paper-api.alpaca.markets", "data.alpaca.markets"})
_WINDOW_SECONDS = 60.0
_PLATFORM_TOTAL_PER_MINUTE = 120
_PLATFORM_RESEARCH_PER_MINUTE = 60
_RESEARCH_INTERVAL_SECONDS = 1.0
_MAX_RETRY_AFTER_SECONDS = 60.0
_PROCESS_LIMITER = None


def install_process_limiter(limiter):
    """Execution composition installs its sole account budget; never accepts credentials."""
    global _PROCESS_LIMITER
    if limiter is not None and not isinstance(limiter, SharedAccountHostLimiter):
        raise ValueError("typed shared limiter required")
    _PROCESS_LIMITER = limiter


def existing_get_allowed(host: str) -> bool:
    limiter = _PROCESS_LIMITER
    return limiter is None or limiter.allow(host.removeprefix("https://"), research=False)


def existing_get_rate_limited(host: str, retry_after: str | None) -> None:
    """Forward only a bounded delay; never retain response headers or private data."""
    limiter = _PROCESS_LIMITER
    if limiter is None:
        return
    # A malformed/absent/date-valued header conservatively backs off for a full
    # local window; no provider-controlled string reaches logs or state.
    seconds = 60
    if isinstance(retry_after, str) and re.fullmatch(r"[0-9]{1,6}", retry_after):
        seconds = max(1, min(60, int(retry_after)))
    limiter.block_research(host.removeprefix("https://"), seconds)


def _host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("approved Alpaca host required")
    normalized = value.casefold().removesuffix(".")
    if normalized not in _ALLOWED_HOSTS:
        raise ValueError("host is outside the approved Alpaca data allowlist")
    return normalized


def _scope(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("opaque account scope identifier required")
    return value


@dataclass(frozen=True)
class LimiterStats:
    """Aggregate counters only; never contains account or host identifiers."""

    admitted_existing: int = 0
    admitted_research: int = 0
    denied_total_budget: int = 0
    denied_research_budget: int = 0
    denied_research_rate: int = 0
    denied_unknown_entitlement: int = 0
    denied_safety_busy: int = 0
    denied_provider_backoff: int = 0


class SharedAccountHostLimiter:
    """Atomically admit existing or research GETs under account and host limits.

    `confirmed_entitlement_limit` is the provider-confirmed account-wide GET/minute
    cap. `None` preserves existing-reader admission under the platform ceiling but
    denies every research admission because an unknown entitlement grants no
    research budget. For a confirmed cap below 120/min, research may consume only
    the portion above a 60/min safety reserve; caps of 60/min or less admit no
    research GETs. Every research request also observes a shared 1 GET/second cap.

    All admissions are nonblocking. A host 429 backoff affects research admission
    for that host only; the existing market-data reader keeps its own error gates.
    """

    def __init__(
        self,
        account_scope_id: str,
        confirmed_entitlement_limit: int | None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._account_scope_id = _scope(account_scope_id)
        if confirmed_entitlement_limit is not None and (
            type(confirmed_entitlement_limit) is not int or confirmed_entitlement_limit < 0
        ):
            raise ValueError("confirmed entitlement must be a nonnegative integer or unknown")
        if not callable(clock):
            raise ValueError("monotonic clock callable required")
        self._entitlement_known = confirmed_entitlement_limit is not None
        self._total_limit = min(
            confirmed_entitlement_limit
            if confirmed_entitlement_limit is not None
            else _PLATFORM_TOTAL_PER_MINUTE,
            _PLATFORM_TOTAL_PER_MINUTE,
        )
        self._research_limit = (
            min(
                _PLATFORM_RESEARCH_PER_MINUTE,
                max(0, self._total_limit - _PLATFORM_RESEARCH_PER_MINUTE),
            )
            if self._entitlement_known
            else 0
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._last_now: float | None = None
        self._account_requests: deque[float] = deque()
        self._host_requests: dict[str, deque[float]] = {}
        self._account_research: deque[float] = deque()
        self._host_research: dict[str, deque[float]] = {}
        self._last_research_account: float | None = None
        self._last_research_host: dict[str, float] = {}
        self._research_blocked_until: dict[str, float] = {}
        self._safety_busy = False
        self._stats: Counter[str] = Counter()

    def __repr__(self) -> str:
        return "SharedAccountHostLimiter(account_scope=<opaque>)"

    def _now(self) -> float:
        try:
            now = float(self._clock())
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("monotonic clock must return a finite number") from error
        if not math.isfinite(now):
            raise ValueError("monotonic clock must return a finite number")
        if self._last_now is not None and now < self._last_now:
            now = self._last_now
        self._last_now = now
        return now

    @staticmethod
    def _prune(requests: deque[float], now: float) -> None:
        boundary = now - _WINDOW_SECONDS
        while requests and requests[0] <= boundary:
            requests.popleft()

    def set_safety_busy(self, busy: bool) -> None:
        if type(busy) is not bool:
            raise ValueError("safety-busy state must be boolean")
        with self._lock:
            self._safety_busy = busy

    def safety_busy(self) -> bool:
        """Aktuelle Safetypriorität vor dauerhafter Dispatchzulassung erneut lesen."""
        with self._lock:
            return self._safety_busy

    def block_research(self, host: str, retry_after: float) -> None:
        """Block research GETs to a host for a bounded Retry-After interval."""
        normalized = _host(host)
        if isinstance(retry_after, bool) or not isinstance(retry_after, (int, float)):
            raise ValueError("Retry-After must be numeric seconds")
        seconds = float(retry_after)
        if not math.isfinite(seconds):
            raise ValueError("Retry-After must be finite")
        seconds = min(_MAX_RETRY_AFTER_SECONDS, max(0.0, seconds))
        with self._lock:
            until = self._now() + seconds
            self._research_blocked_until[normalized] = max(
                until, self._research_blocked_until.get(normalized, 0.0)
            )

    def allow(
        self,
        host: str,
        *,
        research: bool,
        safety_busy: bool = False,
    ) -> bool:
        """Atomically reserve one request slot, or return immediately with False."""
        normalized = _host(host)
        if type(research) is not bool or type(safety_busy) is not bool:
            raise ValueError("request class and safety state must be boolean")
        with self._lock:
            now = self._now()
            account_host = self._host_requests.setdefault(normalized, deque())
            research_host = self._host_research.setdefault(normalized, deque())
            self._prune(self._account_requests, now)
            self._prune(account_host, now)
            self._prune(self._account_research, now)
            self._prune(research_host, now)

            if research:
                if not self._entitlement_known:
                    self._stats["denied_unknown_entitlement"] += 1
                    return False
                if self._safety_busy or safety_busy:
                    self._stats["denied_safety_busy"] += 1
                    return False
                if now < self._research_blocked_until.get(normalized, 0.0):
                    self._stats["denied_provider_backoff"] += 1
                    return False
                if (
                    self._research_limit <= 0
                    or len(self._account_research) >= self._research_limit
                    or len(research_host) >= self._research_limit
                ):
                    self._stats["denied_research_budget"] += 1
                    return False
                if (
                    self._last_research_account is not None
                    and now - self._last_research_account < _RESEARCH_INTERVAL_SECONDS
                ) or (
                    normalized in self._last_research_host
                    and now - self._last_research_host[normalized] < _RESEARCH_INTERVAL_SECONDS
                ):
                    self._stats["denied_research_rate"] += 1
                    return False

            if (
                len(self._account_requests) >= self._total_limit
                or len(account_host) >= self._total_limit
            ):
                self._stats["denied_total_budget"] += 1
                return False

            self._account_requests.append(now)
            account_host.append(now)
            if research:
                self._account_research.append(now)
                research_host.append(now)
                self._last_research_account = now
                self._last_research_host[normalized] = now
                self._stats["admitted_research"] += 1
            else:
                self._stats["admitted_existing"] += 1
            return True

    def stats(self) -> LimiterStats:
        """Return aggregate admission counters without exposing scope or host keys."""
        with self._lock:
            return LimiterStats(
                **{field: self._stats[field] for field in LimiterStats.__dataclass_fields__}
            )
