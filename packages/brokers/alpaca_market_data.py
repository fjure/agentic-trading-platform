"""Read-only, execution-owned Alpaca market evidence. No order endpoint exists here."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from packages.domain.models import (
    BrokerCapabilities,
    Capability,
    Instrument,
    Market,
    MarketBar,
    Quality,
    SourceCapabilities,
    decimal_string,
    decimal_value,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator

_PAPER = "https://paper-api.alpaca.markets"
_DATA = "https://data.alpaca.markets"
_EASTERN = ZoneInfo("America/New_York")
_BAR = timedelta(minutes=15)
_MAX_AGE = timedelta(seconds=120)
_MAX_BODY = 1_000_000
_MAX_PAGES = 4
_ROUTES = {
    _PAPER: frozenset(
        {
            "/v2/account",
            "/v2/assets",
            "/v2/assets/SPY",
            "/v2/assets/QQQ",
            "/v2/clock",
            "/v2/calendar",
        }
    ),
    _DATA: frozenset({"/v2/stocks/bars", "/v1beta3/crypto/us/bars"}),
}


class MarketDataError(ValueError):
    """Stable, redacted rejection reason for a market capture."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    instrument: Instrument
    source_capabilities: SourceCapabilities
    broker_capabilities: BrokerCapabilities
    bars: tuple[MarketBar, ...]
    as_of: datetime
    valid_until: datetime
    session_open: bool
    session_start: datetime
    session_end: datetime
    capability_version: int


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise MarketDataError("INVALID_RESPONSE")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise MarketDataError("INVALID_RESPONSE")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise MarketDataError("INVALID_RESPONSE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(UTC)
    except ValueError:
        raise MarketDataError("INVALID_RESPONSE") from None


def _date(value: object) -> date:
    if not isinstance(value, str):
        raise MarketDataError("INVALID_RESPONSE")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise MarketDataError("INVALID_RESPONSE") from None


def _time(value: object) -> time:
    if not isinstance(value, str):
        raise MarketDataError("INVALID_RESPONSE")
    try:
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError("unsupported session time")
        return parsed
    except ValueError:
        raise MarketDataError("INVALID_RESPONSE") from None


def _decimal(value: object) -> Decimal:
    try:
        return decimal_value(value)
    except ValueError:
        raise MarketDataError("INVALID_BAR") from None


def _asset_increment(asset: dict[str, object], field: str) -> Decimal | None:
    """Unknown when absent; an explicit invalid constraint invalidates the asset proof."""
    if field not in asset:
        return None
    try:
        value = decimal_value(asset[field])
    except ValueError:
        raise MarketDataError("INVALID_RESPONSE") from None
    if value <= 0:
        raise MarketDataError("INVALID_RESPONSE")
    return value


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise MarketDataError("INVALID_RESPONSE")
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise MarketDataError("INVALID_RESPONSE") from None
    if value != canonical:
        raise MarketDataError("INVALID_RESPONSE")
    return canonical


class AlpacaMarketDataProvider:
    """Fixed-host Alpaca REST reader; credentials remain inside Execution."""

    def __init__(
        self,
        clock: Clock,
        ids: IdGenerator,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        enabled: bool = False,
        enable_crypto: bool = False,
        feed: str = "iex",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if feed not in {"iex", "sip"}:
            raise ValueError("unsupported Alpaca stock feed")
        if type(enabled) is not bool or type(enable_crypto) is not bool:
            raise ValueError("invalid enable flag")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only exact MockTransport is accepted for injected transport")
        self._clock = clock
        self._ids = ids
        self._api_key = api_key
        self._api_secret = api_secret
        self._enabled = enabled
        self._enable_crypto = enable_crypto
        self._feed = feed
        # trust_env=False prevents proxy or CA overrides. The normal transport verifies TLS.
        self._client = httpx.Client(
            transport=transport, timeout=httpx.Timeout(5.0), follow_redirects=False, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def _now(self) -> datetime:
        try:
            return utc_time(self._clock.now())
        except ValueError:
            raise MarketDataError("INVALID_CLOCK") from None

    def _get(self, host: str, path: str, params: dict[str, str] | None = None) -> object:
        if path not in _ROUTES.get(host, ()):
            raise MarketDataError("UNAVAILABLE")
        if (
            not isinstance(self._api_key, str)
            or not self._api_key
            or not isinstance(self._api_secret, str)
            or not self._api_secret
        ):
            raise MarketDataError("AUTH_UNAVAILABLE")
        try:
            with self._client.stream(
                "GET",
                host + path,
                params=params,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code == 429:
                    raise MarketDataError("RATE_LIMIT")
                if response.status_code in {401, 403}:
                    raise MarketDataError("PERMISSION_DENIED")
                if response.status_code != 200:
                    raise MarketDataError("UNAVAILABLE")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise MarketDataError("INVALID_RESPONSE")
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if content_type.strip().lower() != "application/json":
                    raise MarketDataError("INVALID_RESPONSE")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdecimal() or len(length) > 7 or int(length) > _MAX_BODY
                ):
                    raise MarketDataError("INVALID_RESPONSE")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BODY:
                        raise MarketDataError("INVALID_RESPONSE")
        except MarketDataError:
            raise
        except httpx.HTTPError:
            raise MarketDataError("UNAVAILABLE") from None
        try:
            return json.loads(
                body,
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=_reject_constant,
                object_pairs_hook=_pairs,
            )
        except MarketDataError:
            raise
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError, InvalidOperation):
            raise MarketDataError("INVALID_RESPONSE") from None

    @staticmethod
    def _instrument(instrument_id: str, enable_crypto: bool) -> Instrument:
        if instrument_id in {"SPY", "QQQ"}:
            return Instrument(
                instrument_id, Market.US_EQUITY, instrument_id, "ETF", "USD", 1, "XNYS"
            )
        if instrument_id == "BTC/USD" and enable_crypto:
            return Instrument(
                instrument_id, Market.CRYPTO, instrument_id, "CRYPTO", "USD", 1, "24/7"
            )
        raise MarketDataError("UNKNOWN_INSTRUMENT")

    @staticmethod
    def _asset(assets: object, symbol: str, asset_class: str) -> dict[str, object]:
        if not isinstance(assets, list):
            raise MarketDataError("INVALID_RESPONSE")
        matches = [
            item for item in assets if isinstance(item, dict) and item.get("symbol") == symbol
        ]
        if len(matches) != 1:
            raise MarketDataError("ASSET_UNAVAILABLE")
        asset = matches[0]
        if (
            asset.get("class") != asset_class
            or asset.get("status") != "active"
            or asset.get("tradable") is not True
        ):
            raise MarketDataError("ASSET_UNAVAILABLE")
        _canonical_uuid(asset.get("id"))
        return asset

    def _account(self, crypto: bool) -> dict[str, object]:
        account = self._get(_PAPER, "/v2/account")
        if not isinstance(account, dict):
            raise MarketDataError("INVALID_RESPONSE")
        if (
            account.get("status") != "ACTIVE"
            or account.get("currency") != "USD"
            or account.get("account_blocked") is not False
            or account.get("trading_blocked") is not False
            or account.get("trade_suspended_by_user") is not False
        ):
            raise MarketDataError("ACCOUNT_UNAVAILABLE")
        if crypto and account.get("crypto_status") != "ACTIVE":
            raise MarketDataError("CRYPTO_UNAVAILABLE")
        _canonical_uuid(account.get("id"))
        return account

    def _session(self, day: date, now: datetime, crypto: bool) -> tuple[datetime, datetime]:
        if crypto:
            if day != now.date():
                raise MarketDataError("SESSION_CLOSED")
            start = datetime.combine(day, time.min, UTC)
            return start, start + timedelta(days=1)
        if day != now.astimezone(_EASTERN).date():
            raise MarketDataError("SESSION_CLOSED")
        calendar = self._get(
            _PAPER, "/v2/calendar", {"start": day.isoformat(), "end": day.isoformat()}
        )
        if (
            not isinstance(calendar, list)
            or len(calendar) != 1
            or not isinstance(calendar[0], dict)
        ):
            raise MarketDataError("SESSION_CLOSED")
        entry = calendar[0]
        if _date(entry.get("date")) != day:
            raise MarketDataError("INVALID_RESPONSE")
        opening, closing = _time(entry.get("open")), _time(entry.get("close"))
        if opening != time(9, 30) or not time(9, 30) < closing <= time(16):
            raise MarketDataError("INVALID_RESPONSE")
        return (
            datetime.combine(day, opening, _EASTERN).astimezone(UTC),
            datetime.combine(day, closing, _EASTERN).astimezone(UTC),
        )

    def _bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        capture_start: datetime,
        session_start: datetime,
        session_end: datetime,
    ) -> tuple[MarketBar, ...]:
        crypto = instrument.market is Market.CRYPTO
        path = "/v1beta3/crypto/us/bars" if crypto else "/v2/stocks/bars"
        params = {
            "symbols": instrument.broker_symbol,
            "timeframe": "15Min",
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "limit": "1000",
        }
        if not crypto:
            params["feed"] = self._feed
        raw_bars: list[object] = []
        seen_tokens: set[str] = set()
        for _ in range(_MAX_PAGES):
            page = self._get(_DATA, path, params)
            if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
                raise MarketDataError("INVALID_RESPONSE")
            bars_map = page["bars"]
            if set(bars_map) != {instrument.broker_symbol}:
                raise MarketDataError("INCOMPLETE_WINDOW")
            batch = bars_map[instrument.broker_symbol]
            if not isinstance(batch, list) or len(raw_bars) + len(batch) > 1000:
                raise MarketDataError("INVALID_RESPONSE")
            raw_bars.extend(batch)
            token = page.get("next_page_token")
            if token is None:
                break
            if not isinstance(token, str) or not token or token in seen_tokens:
                raise MarketDataError("INVALID_RESPONSE")
            seen_tokens.add(token)
            params["page_token"] = token
        else:
            raise MarketDataError("INCOMPLETE_WINDOW")
        if len(raw_bars) != 2:
            raise MarketDataError("INCOMPLETE_WINDOW")
        received_at = self._now()
        source_id = "alpaca-market-data:crypto-us" if crypto else f"alpaca-market-data:{self._feed}"
        normalized: list[MarketBar] = []
        seen_starts: set[datetime] = set()
        for raw in raw_bars:
            if not isinstance(raw, dict):
                raise MarketDataError("INVALID_BAR")
            bar_start = _timestamp(raw.get("t"))
            bar_end = bar_start + _BAR
            if bar_start in seen_starts:
                raise MarketDataError("DUPLICATE_BAR")
            seen_starts.add(bar_start)
            if (
                bar_start < start
                or bar_start > end
                or bar_start < (session_start - 2 * _BAR if crypto else session_start)
                or bar_end > session_end
                or bar_end > capture_start
                or (bar_start - session_start) % _BAR != timedelta()
            ):
                raise MarketDataError("OPEN_OR_OUTSIDE_SESSION")
            values = tuple(_decimal(raw.get(key)) for key in ("o", "h", "l", "c", "v"))
            identity = "|".join(
                (
                    source_id,
                    instrument.id,
                    bar_start.isoformat(),
                    *(decimal_string(v) for v in values),
                )
            )
            try:
                normalized.append(
                    MarketBar(
                        id=hashlib.sha256(identity.encode()).hexdigest(),
                        instrument_id=instrument.id,
                        start_at=bar_start,
                        end_at=bar_end,
                        open=values[0],
                        high=values[1],
                        low=values[2],
                        close=values[3],
                        volume=values[4],
                        event_time=bar_end,
                        received_at=received_at,
                        available_at=received_at,
                        quality=Quality.VALID,
                    )
                )
            except ValueError:
                raise MarketDataError("INVALID_BAR") from None
        if normalized[1].start_at != normalized[0].end_at:
            raise MarketDataError("BAR_GAP_OR_ORDER")
        return tuple(normalized)

    def snapshot(self, instrument_id: str, session_id: str) -> MarketSnapshot:
        if not self._enabled:
            raise MarketDataError("DISABLED")
        instrument = self._instrument(instrument_id, self._enable_crypto)
        try:
            day = date.fromisoformat(session_id)
        except (TypeError, ValueError):
            raise MarketDataError("INVALID_SESSION") from None
        if day.isoformat() != session_id:
            raise MarketDataError("INVALID_SESSION")
        capture_start = self._now()
        crypto = instrument.market is Market.CRYPTO
        account = self._account(crypto)
        first_proof_at = self._now()
        asset_class = "crypto" if crypto else "us_equity"
        assets_payload = (
            self._get(_PAPER, "/v2/assets", {"status": "active", "asset_class": "crypto"})
            if crypto
            else [self._get(_PAPER, f"/v2/assets/{instrument.broker_symbol}")]
        )
        asset = self._asset(
            assets_payload,
            instrument.broker_symbol,
            asset_class,
        )
        quantity_step = _asset_increment(asset, "min_trade_increment")
        price_tick = _asset_increment(asset, "price_increment")
        instrument = replace(instrument, quantity_step=quantity_step, price_tick=price_tick)
        broker_clock = self._get(_PAPER, "/v2/clock")
        clock_received_at = self._now()
        if not isinstance(broker_clock, dict) or type(broker_clock.get("is_open")) is not bool:
            raise MarketDataError("INVALID_RESPONSE")
        clock_time = _timestamp(broker_clock.get("timestamp"))
        clock_lag = clock_received_at - clock_time
        if not -timedelta(seconds=5) <= clock_lag <= _MAX_AGE:
            raise MarketDataError("STALE_CLOCK")
        session_start, session_end = self._session(day, capture_start, crypto)
        if not session_start <= clock_time < session_end:
            raise MarketDataError("STALE_CLOCK")
        if not session_start <= capture_start < session_end:
            raise MarketDataError("SESSION_CLOSED")
        if not crypto and broker_clock["is_open"] is not True:
            raise MarketDataError("SESSION_CLOSED")
        floor = capture_start.replace(
            minute=(capture_start.minute // 15) * 15, second=0, microsecond=0
        )
        bars = self._bars(
            instrument,
            floor - 2 * _BAR,
            floor - timedelta(microseconds=1),
            capture_start,
            session_start,
            session_end,
        )
        final_time = self._now()
        if (
            final_time < capture_start
            or final_time < first_proof_at
            or final_time < bars[-1].received_at
            or final_time - first_proof_at >= _MAX_AGE
            or final_time - clock_time >= _MAX_AGE
        ):
            raise MarketDataError("STALE_CLOCK")
        if final_time - bars[-1].end_at >= _MAX_AGE or final_time >= session_end:
            raise MarketDataError("STALE_DATA")
        metadata = "|".join(
            (
                instrument.id,
                account["id"],
                account["status"],
                account["currency"],
                str(account.get("crypto_status")),
                asset["status"],
                str(asset["tradable"]),
                str(asset.get("fractionable")),
                asset["id"],
                decimal_string(quantity_step) if quantity_step is not None else "unknown",
                decimal_string(price_tick) if price_tick is not None else "unknown",
                session_start.isoformat(),
                session_end.isoformat(),
                self._feed,
            )
        )
        version = (
            int.from_bytes(hashlib.sha256(metadata.encode()).digest()[:4], "big") % 2147483647 + 1
        )
        source = SourceCapabilities(
            "alpaca-market-data:crypto-us" if crypto else f"alpaca-market-data:{self._feed}",
            instrument.id,
            ("PT15M",),
            120,
            news=Capability.UNAVAILABLE,
            papers=Capability.UNAVAILABLE,
            history=Capability.CONFIRMED,
        )
        broker = BrokerCapabilities(
            "alpaca-paper",
            instrument.id,
            version,
            paper=Capability.CONFIRMED,
            regular_session=Capability.CONFIRMED if not crypto else Capability.UNAVAILABLE,
            continuous_session=Capability.CONFIRMED if crypto else Capability.UNAVAILABLE,
            fractional=Capability.CONFIRMED
            if asset.get("fractionable") is True
            else Capability.UNKNOWN,
            quantity_step=quantity_step,
            price_tick=price_tick,
        )
        return MarketSnapshot(
            instrument,
            source,
            broker,
            bars,
            final_time,
            min(
                final_time + _MAX_AGE,
                first_proof_at + _MAX_AGE,
                clock_time + _MAX_AGE,
                bars[-1].end_at + _MAX_AGE,
                session_end,
            ),
            True,
            session_start,
            session_end,
            version,
        )

    def capabilities(self, instrument_id: str) -> SourceCapabilities:
        now = self._now()
        day = now.date() if instrument_id == "BTC/USD" else now.astimezone(_EASTERN).date()
        return self.snapshot(instrument_id, day.isoformat()).source_capabilities

    def broker_capabilities(self, instrument_id: str) -> BrokerCapabilities:
        now = self._now()
        day = now.date() if instrument_id == "BTC/USD" else now.astimezone(_EASTERN).date()
        return self.snapshot(instrument_id, day.isoformat()).broker_capabilities

    def closed_bars(
        self, instrument_id: str, session_id: str, cursor: str | None = None
    ) -> tuple[MarketBar, ...]:
        if cursor is not None:
            raise MarketDataError("UNSUPPORTED_CURSOR")
        return self.snapshot(instrument_id, session_id).bars
