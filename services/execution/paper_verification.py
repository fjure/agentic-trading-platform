"""Bounded GET-only connection verification, never a trading authorization.

Account identity stays in a separate Execution-only result. The caller must
recheck the command generation/owner fence under its account lock after I/O.
No database writes, promotion, submit ports or runtime activation exist here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from uuid import UUID

import httpx

from packages.domain.models import decimal_value, utc_time
from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PaperGenerationReader,
    PrivatePaperCredentials,
)
from services.setup.store import generation_filename

_PAPER = "https://paper-api.alpaca.markets"
_DATA = "https://data.alpaca.markets"
_MAX_BODY = 65536
_MAX_DURATION = timedelta(seconds=30)


class _Denied(ValueError):
    pass


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
        return value
    except ValueError:
        raise _Denied("ACCOUNT_IDENTITY_MISMATCH") from None


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise _Denied("PROVIDER_UNAVAILABLE")
        value[key] = item
    return value


def _constant(_value):
    raise _Denied("PROVIDER_UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class SanitizedVerificationSnapshot:
    credential_generation: int
    observed_at: datetime
    account_label: str | None = None
    currency: str = "USD"
    cash: Decimal | None = None
    verified_at: datetime | None = None
    market_data_feed: str = "UNVERIFIED"
    provider_verification: str = "UNVERIFIED"
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrivateIdentityProof:
    """Private identity evidence, never serialize into public DTOs or audit text."""

    credential_generation: int
    provider_account_id: str = field(repr=False)
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationResult:
    public: SanitizedVerificationSnapshot
    private: PrivateIdentityProof | None = field(default=None, repr=False)


class PaperAccountVerifier:
    def __init__(
        self, *, credentials: PaperGenerationReader, clock,
        expected_account_id: str, pinned_account_id: str | None = None,
        transport: httpx.MockTransport | None = None,
    ):
        # No arbitrary hosts, proxy settings, live mode or non-test transport.
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._expected = _uuid(expected_account_id)
        self._pinned = _uuid(pinned_account_id) if pinned_account_id is not None else None
        self._credentials, self._clock, self._transport = credentials, clock, transport

    @staticmethod
    def _get(client, url, credentials, *, deadline, params=None, market=False):
        # Call sites are fixed below; this guard makes future route drift fail closed.
        if url not in {_PAPER + "/v2/account", _DATA + "/v2/stocks/quotes/latest"}:
            raise _Denied("PROVIDER_UNAVAILABLE")
        if monotonic() >= deadline:
            raise _Denied("PROVIDER_UNAVAILABLE")
        with client.stream("GET", url, params=params, headers={
            "APCA-API-KEY-ID": credentials.api_key,
            "APCA-API-SECRET-KEY": credentials.api_secret,
            "Accept": "application/json", "Accept-Encoding": "identity",
        }) as response:
            if response.status_code == 429:
                raise _Denied("PROVIDER_RATE_LIMITED")
            if response.status_code in {401, 403}:
                raise _Denied("MARKET_DATA_UNAVAILABLE" if market else "CREDENTIALS_INVALID")
            if response.status_code != 200 or response.is_redirect:
                raise _Denied("PROVIDER_UNAVAILABLE")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise _Denied("PROVIDER_UNAVAILABLE")
            if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
                "application/json"
            ):
                raise _Denied("PROVIDER_UNAVAILABLE")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isdecimal() or len(length) > 6 or int(length) > _MAX_BODY
            ):
                raise _Denied("PROVIDER_UNAVAILABLE")
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if monotonic() >= deadline or len(body) + len(chunk) > _MAX_BODY:
                    raise _Denied("PROVIDER_UNAVAILABLE")
                body.extend(chunk)
            return json.loads(body, parse_float=Decimal, parse_int=Decimal,
                              parse_constant=_constant, object_pairs_hook=_pairs)

    @staticmethod
    def _quotes(value, now):
        if (not isinstance(value, dict) or not isinstance(value.get("quotes"), dict)
                or set(value["quotes"]) != {"SPY", "QQQ"}):
            raise _Denied("MARKET_DATA_UNAVAILABLE")
        for quote in value["quotes"].values():
            if not isinstance(quote, dict):
                raise _Denied("MARKET_DATA_UNAVAILABLE")
            try:
                bid, ask = decimal_value(quote.get("bp")), decimal_value(quote.get("ap"))
                raw_time = quote.get("t")
                if not isinstance(raw_time, str) or len(raw_time) > 40:
                    raise ValueError()
                moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    raise ValueError()
                moment = moment.astimezone(UTC)
                if not Decimal("0") < bid <= ask or moment > now + timedelta(seconds=5):
                    raise ValueError()
            except (ValueError, TypeError):
                raise _Denied("MARKET_DATA_UNAVAILABLE") from None
        # Historical latest quotes prove feed access outside market hours. They
        # never establish freshness, spread authority or permission to trade.

    def verify(self, generation: int) -> VerificationResult:
        generation_filename(generation)
        started = utc_time(self._clock.now())
        deadline = monotonic() + _MAX_DURATION.total_seconds()
        try:
            credentials = self._credentials.load(generation)
            if (type(credentials) is not PrivatePaperCredentials
                    or credentials.generation != generation):
                raise _Denied("CREDENTIALS_MISSING")
            if credentials.expected_account_id != self._expected or (
                self._pinned is not None and self._pinned != self._expected
            ):
                raise _Denied("ACCOUNT_IDENTITY_MISMATCH")
            with httpx.Client(
                transport=self._transport,
                proxy="http://paper-execution-egress:8080" if self._transport is None else None,
                timeout=httpx.Timeout(5.0), trust_env=False, follow_redirects=False,
            ) as client:
                account = self._get(client, _PAPER + "/v2/account", credentials,
                                    deadline=deadline)
                if not isinstance(account, dict):
                    raise _Denied("PROVIDER_UNAVAILABLE")
                identity = _uuid(account.get("id"))
                if identity != self._expected or (
                    self._pinned is not None and identity != self._pinned
                ):
                    raise _Denied("ACCOUNT_IDENTITY_MISMATCH")
                if account.get("currency") != "USD":
                    raise _Denied("CURRENCY_UNSUPPORTED")
                if account.get("status") != "ACTIVE" or any(
                    account.get(key) is not False
                    for key in ("account_blocked", "trading_blocked", "trade_suspended_by_user")
                ):
                    raise _Denied("ACCOUNT_BLOCKED")
                if not isinstance(account.get("cash"), str):
                    raise _Denied("PROVIDER_UNAVAILABLE")
                cash = decimal_value(account["cash"])
                if cash < 0:
                    raise _Denied("ACCOUNT_BLOCKED")
                quotes = self._get(client, _DATA + "/v2/stocks/quotes/latest", credentials,
                                   deadline=deadline, params={"symbols": "SPY,QQQ", "feed": "iex"},
                                   market=True)
                observed = utc_time(self._clock.now())
                self._quotes(quotes, observed)
            if not timedelta() <= observed - started < _MAX_DURATION:
                raise _Denied("PROVIDER_UNAVAILABLE")
            label = "paper-" + hashlib.sha256(identity.encode("ascii")).hexdigest()[:8]
            return VerificationResult(
                SanitizedVerificationSnapshot(
                    generation, observed, account_label=label, cash=cash, verified_at=observed,
                    market_data_feed="IEX", provider_verification="CONFIRMED",
                ),
                PrivateIdentityProof(generation, identity, observed),
            )
        except PaperCredentialsUnavailable:
            code = "CREDENTIALS_MISSING"
        except _Denied as error:
            code = str(error)
        except Exception:
            # Provider/client/parse exceptions may contain headers or body. Never
            # retain them in returned results, logs or exception chains.
            code = "PROVIDER_UNAVAILABLE"
        return VerificationResult(SanitizedVerificationSnapshot(
            generation, started, blockers=(code,),
        ))
