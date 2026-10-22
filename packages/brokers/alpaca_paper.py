"""Execution-owned, fixed-host Alpaca Paper order adapter.

The process-local registry is not a durable order ledger. Execution must persist
an attempted client ID before it calls submit, then restore it on restart.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from functools import wraps
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from packages.brokers.fake import CapabilityError, IdempotencyConflict
from packages.brokers.proofs import BindingProof
from packages.domain.models import (
    BrokerCapabilities,
    BrokerOrder,
    Capability,
    ExecutionReport,
    OrderIntent,
    OrderStatus,
    Reconciliation,
    decimal_string,
    decimal_value,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator

if TYPE_CHECKING:
    from packages.brokers.alpaca_activities import AlpacaReconciliationEvidence
    from packages.brokers.initial_anchors import InitialHistoryProof

_HOST = "https://paper-api.alpaca.markets"
_MAX_BODY = 1_000_000
_MAX_AGE = timedelta(seconds=120)
_CRYPTO_MAX_NOTIONAL = Decimal("200000")
_EASTERN = ZoneInfo("America/New_York")
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TERMINAL = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED}
_STATUS = {
    "new": OrderStatus.ACKNOWLEDGED,
    "accepted": OrderStatus.ACKNOWLEDGED,
    "pending_new": OrderStatus.ACKNOWLEDGED,
    "accepted_for_bidding": OrderStatus.ACKNOWLEDGED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "pending_cancel": OrderStatus.CANCEL_PENDING,
    "pending_replace": OrderStatus.UNKNOWN,
    "canceled": OrderStatus.CANCELED,
    "expired": OrderStatus.CANCELED,
    "done_for_day": OrderStatus.UNKNOWN,
    "replaced": OrderStatus.UNKNOWN,
    "rejected": OrderStatus.REJECTED,
    "stopped": OrderStatus.UNKNOWN,
    "suspended": OrderStatus.UNKNOWN,
    "calculated": OrderStatus.UNKNOWN,
}


class AlpacaPaperError(ValueError):
    """Stable reason only; never includes URL, headers, body, or credentials."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _locked(method: Any) -> Any:
    @wraps(method)
    def wrapper(self: AlpacaPaperBrokerAdapter, *args: object, **kwargs: object) -> object:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise AlpacaPaperError("INVALID_RESPONSE")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise AlpacaPaperError("INVALID_RESPONSE")


def _uuid(value: object) -> str:
    try:
        canonical = str(UUID(value)) if isinstance(value, str) else ""
    except ValueError:
        canonical = ""
    if not canonical or value != canonical:
        raise AlpacaPaperError("INVALID_RESPONSE")
    return canonical


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    try:
        result = decimal_value(value)
    except ValueError:
        raise AlpacaPaperError("INVALID_RESPONSE") from None
    if result < 0 or (positive and result == 0):
        raise AlpacaPaperError("INVALID_RESPONSE")
    return result


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise AlpacaPaperError("INVALID_RESPONSE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(UTC)
    except ValueError:
        raise AlpacaPaperError("INVALID_RESPONSE") from None


def _canonical_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    raw = decimal_string(value)
    return (raw.rstrip("0").rstrip(".") if "." in raw else raw) or "0"


class AlpacaPaperBrokerAdapter:
    """No automatic POST retry. Market capability is side-neutral; BUY needs a bound."""

    def __init__(
        self,
        clock: Clock,
        ids: IdGenerator,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        paper_enabled: bool = False,
        expected_account_id: str | None = None,
        enable_crypto: bool = False,
        transport: httpx.BaseTransport | None = None,
        restored_intents: tuple[OrderIntent, ...] = (),
        attempted_client_ids: frozenset[str] = frozenset(),
        cash_cushion_fraction: Decimal = Decimal("0.01"),
        reconciliation_fee_profile: str | None = None,
        egress_proxy: bool = False,
    ) -> None:
        if type(egress_proxy) is not bool or (egress_proxy and transport is not None):
            raise ValueError("invalid egress proxy configuration")
        if type(paper_enabled) is not bool or type(enable_crypto) is not bool:
            raise ValueError("invalid enable flag")
        from packages.brokers.alpaca_activities import EQUITY_ZERO_FEE_PROFILE

        # Explicit operator selection attests to a self-directed retail cash
        # Paper account; account responses do not establish this fee category.
        if reconciliation_fee_profile is not None and (
            not isinstance(reconciliation_fee_profile, str)
            or reconciliation_fee_profile != EQUITY_ZERO_FEE_PROFILE
        ):
            raise ValueError("unknown reconciliation fee profile")
        self._reconciliation_fee_profile = reconciliation_fee_profile
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only exact MockTransport is accepted")
        if expected_account_id is not None:
            _uuid(expected_account_id)
        if type(cash_cushion_fraction) is not Decimal:
            raise ValueError("cash cushion must be Decimal")
        cushion = decimal_value(cash_cushion_fraction)
        if not Decimal("0") < cushion <= Decimal("1"):
            raise ValueError("cash cushion outside (0, 1]")
        self._clock, self._ids = clock, ids
        self._key, self._secret = api_key, api_secret
        self._enabled, self._crypto = paper_enabled, enable_crypto
        self._account_id = expected_account_id
        with localcontext() as context:
            context.prec = 80
            self._cash_buffer = Decimal("1") + cushion
        self._client = httpx.Client(
            transport=transport,
            proxy="http://execution-egress:8080" if egress_proxy else None,
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
        )
        self._lock = threading.RLock()
        self._intents: dict[str, OrderIntent] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._broker_ids: dict[str, str] = {}
        self._reports: dict[str, list[ExecutionReport]] = {}
        self._attempted = set(attempted_client_ids)
        self._capabilities: dict[str, tuple[BrokerCapabilities, datetime, datetime, datetime]] = {}
        self._binding_proofs: dict[str, BindingProof] = {}
        for intent in restored_intents:
            self.bind_intent(intent, attempted=intent.client_order_id in self._attempted)

    def close(self) -> None:
        self._client.close()

    @property
    def reconciliation_fee_profile(self) -> str | None:
        return self._reconciliation_fee_profile

    def _now(self) -> datetime:
        try:
            return utc_time(self._clock.now())
        except ValueError:
            raise AlpacaPaperError("INVALID_CLOCK") from None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, object | None]:
        static = {
            "/v2/account",
            "/v2/clock",
            "/v2/calendar",
            "/v2/assets/SPY",
            "/v2/assets/QQQ",
            "/v2/assets",
            "/v2/positions/SPY",
            "/v2/positions/QQQ",
            "/v2/positions/BTCUSD",
            "/v2/positions",
            "/v2/account/activities",
            "/v2/orders:by_client_order_id",
            "/v2/orders",
        }
        order_id_route = re.fullmatch(r"/v2/orders/[0-9a-f-]{36}", path)
        allowed = (
            (method == "GET" and (path in static or order_id_route))
            or (method == "POST" and path == "/v2/orders")
            or (method == "DELETE" and order_id_route is not None)
        )
        if not allowed:
            raise AlpacaPaperError("UNAVAILABLE")
        if not self._enabled:
            raise AlpacaPaperError("PAPER_DISABLED")
        if self._account_id is None:
            raise AlpacaPaperError("ACCOUNT_UNPINNED")
        if (
            not isinstance(self._key, str)
            or not self._key
            or not isinstance(self._secret, str)
            or not self._secret
        ):
            raise AlpacaPaperError("AUTH_UNAVAILABLE")
        if path.startswith("/v2/orders"):
            if method == "POST":
                crypto = payload is not None and payload.get("symbol") == "BTC/USD"
                self._account(crypto=crypto)
                if payload is None or not isinstance(payload.get("symbol"), str):
                    raise AlpacaPaperError("INVALID_INTENT")
                self._fresh_proof(payload["symbol"])
            else:
                # Recovery and cancellation remain available when trading is blocked.
                self._account(require_trading=False)
        headers = {
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        try:
            with self._client.stream(
                method, _HOST + path, params=params, json=payload, headers=headers
            ) as response:
                status = response.status_code
                if response.is_redirect:
                    raise AlpacaPaperError("INVALID_RESPONSE")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise AlpacaPaperError("INVALID_RESPONSE")
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isdecimal() or len(length) > 7 or int(length) > _MAX_BODY
                ):
                    raise AlpacaPaperError("INVALID_RESPONSE")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_BODY:
                        raise AlpacaPaperError("INVALID_RESPONSE")
                if status in {204, 404} and not body:
                    return status, None
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media != "application/json":
                    raise AlpacaPaperError("INVALID_RESPONSE")
        except AlpacaPaperError:
            raise
        except httpx.HTTPError:
            raise AlpacaPaperError("UNAVAILABLE") from None
        try:
            data = json.loads(
                body,
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=_constant,
                object_pairs_hook=_pairs,
            )
        except AlpacaPaperError:
            raise
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError, InvalidOperation):
            raise AlpacaPaperError("INVALID_RESPONSE") from None
        return status, data

    def _get(
        self, path: str, *, params: dict[str, str] | None = None, missing_ok: bool = False
    ) -> object | None:
        status, data = self._request("GET", path, params=params)
        if status == 404 and missing_ok:
            return None
        if status == 429:
            raise AlpacaPaperError("RATE_LIMIT")
        if status in {401, 403}:
            raise AlpacaPaperError("PERMISSION_DENIED")
        if status != 200:
            raise AlpacaPaperError("UNAVAILABLE")
        return data

    def _account(self, *, crypto: bool = False, require_trading: bool = True) -> dict[str, object]:
        account = self._get("/v2/account")
        if not isinstance(account, dict):
            raise AlpacaPaperError("INVALID_RESPONSE")
        if _uuid(account.get("id")) != self._account_id:
            raise AlpacaPaperError("ACCOUNT_MISMATCH")
        if not require_trading:
            return account
        if (
            account.get("status") != "ACTIVE"
            or account.get("currency") != "USD"
            or any(
                account.get(field) is not False
                for field in ("account_blocked", "trading_blocked", "trade_suspended_by_user")
            )
        ):
            raise AlpacaPaperError("ACCOUNT_UNAVAILABLE")
        if crypto and account.get("crypto_status") != "ACTIVE":
            raise AlpacaPaperError("CRYPTO_UNAVAILABLE")
        _decimal(account.get("cash"))
        return account

    def _session(self, now: datetime, *, crypto: bool) -> tuple[datetime, datetime]:
        if crypto:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return start, start + timedelta(days=1)
        day = now.astimezone(_EASTERN).date()
        calendar = self._get(
            "/v2/calendar", params={"start": day.isoformat(), "end": day.isoformat()}
        )
        if (
            not isinstance(calendar, list)
            or len(calendar) != 1
            or not isinstance(calendar[0], dict)
        ):
            raise CapabilityError("session closed")
        entry = calendar[0]
        if entry.get("date") != day.isoformat():
            raise AlpacaPaperError("INVALID_RESPONSE")
        try:
            opening = time.fromisoformat(entry["open"])
            closing = time.fromisoformat(entry["close"])
        except (KeyError, TypeError, ValueError):
            raise AlpacaPaperError("INVALID_RESPONSE") from None
        if (
            opening != time(9, 30)
            or not opening < closing <= time(16)
            or opening.tzinfo is not None
            or closing.tzinfo is not None
        ):
            raise AlpacaPaperError("INVALID_RESPONSE")
        return (
            datetime.combine(day, opening, _EASTERN).astimezone(UTC),
            datetime.combine(day, closing, _EASTERN).astimezone(UTC),
        )

    def _fresh_proof(self, instrument_id: str) -> None:
        proof = self._capabilities.get(instrument_id)
        if proof is None:
            raise AlpacaPaperError("STALE_CLOCK")
        _, received, broker_time, session_end = proof
        now = self._now()
        if (
            now < received
            or now - received >= _MAX_AGE
            or not -timedelta(seconds=5) <= now - broker_time < _MAX_AGE
            or now >= session_end
        ):
            raise AlpacaPaperError("STALE_CLOCK")

    def _asset(self, instrument_id: str) -> dict[str, object]:
        if instrument_id not in {"SPY", "QQQ", "BTC/USD"} or (
            instrument_id == "BTC/USD" and not self._crypto
        ):
            raise CapabilityError("instrument capability is unconfirmed")
        if instrument_id == "BTC/USD":
            assets = self._get("/v2/assets", params={"status": "active", "asset_class": "crypto"})
            if not isinstance(assets, list):
                raise AlpacaPaperError("INVALID_RESPONSE")
            matches = [
                item
                for item in assets
                if isinstance(item, dict) and item.get("symbol") == instrument_id
            ]
            if len(matches) != 1:
                raise CapabilityError("asset unavailable")
            asset = matches[0]
            required_class = "crypto"
        else:
            asset = self._get(f"/v2/assets/{instrument_id}")
            required_class = "us_equity"
        if (
            not isinstance(asset, dict)
            or asset.get("symbol") != instrument_id
            or asset.get("class") != required_class
            or asset.get("status") != "active"
            or asset.get("tradable") is not True
        ):
            raise CapabilityError("asset unavailable")
        _uuid(asset.get("id"))
        return asset

    def _capability(self, instrument_id: str) -> BrokerCapabilities:
        crypto = instrument_id == "BTC/USD"
        account = self._account(crypto=crypto)
        asset = self._asset(instrument_id)
        now = self._now()
        clock = self._get("/v2/clock")
        if not isinstance(clock, dict) or type(clock.get("is_open")) is not bool:
            raise AlpacaPaperError("INVALID_RESPONSE")
        clock_time = _time(clock.get("timestamp"))
        if not -timedelta(seconds=5) <= now - clock_time < _MAX_AGE:
            raise AlpacaPaperError("STALE_CLOCK")
        if not crypto and clock["is_open"] is not True:
            raise CapabilityError("session closed")
        session_start, session_end = self._session(now, crypto=crypto)
        if not session_start <= now < session_end or not session_start <= clock_time < session_end:
            raise CapabilityError("session closed")
        if self._now() - now >= _MAX_AGE:
            raise AlpacaPaperError("STALE_CLOCK")
        if crypto:
            quantity_step = _decimal(asset.get("min_trade_increment"), positive=True)
            price_tick = _decimal(asset.get("price_increment"), positive=True)
            _decimal(asset.get("min_order_size"), positive=True)
        else:
            # Alpaca's fractional quantity envelope is nine decimal places;
            # this is still restricted to MARKET DAY in _validate_intent.
            quantity_step = (
                Decimal("0.000000001") if asset.get("fractionable") is True else Decimal("1")
            )
            # The low-price exception is checked separately at submit.
            price_tick = Decimal("0.0001")
        metadata = "|".join(
            (
                str(account["id"]),
                str(asset["id"]),
                instrument_id,
                str(asset.get("fractionable")),
                decimal_string(quantity_step),
                decimal_string(price_tick),
                _canonical_decimal(_decimal(asset.get("min_order_size"), positive=True))
                if crypto
                else "whole-or-market-fractional",
                "continuous" if crypto else str(clock["is_open"]),
                session_start.isoformat(),
                session_end.isoformat(),
                decimal_string(self._cash_buffer),
            )
        )
        version = (
            int.from_bytes(hashlib.sha256(metadata.encode()).digest()[:4], "big") % 2147483647 + 1
        )
        cap = BrokerCapabilities(
            "alpaca-paper",
            instrument_id,
            version,
            paper=Capability.CONFIRMED,
            regular_session=Capability.CONFIRMED if not crypto else Capability.UNAVAILABLE,
            continuous_session=Capability.CONFIRMED if crypto else Capability.UNAVAILABLE,
            fractional=Capability.CONFIRMED
            if crypto or asset.get("fractionable") is True
            else Capability.UNAVAILABLE,
            market_order=Capability.CONFIRMED,
            limit_order=Capability.CONFIRMED,
            quantity_step=quantity_step,
            price_tick=price_tick,
        )
        self._capabilities[instrument_id] = (cap, self._now(), clock_time, session_end)
        self._fresh_proof(instrument_id)
        self._binding_proofs[instrument_id] = BindingProof(
            provider_account_id=str(account["id"]),
            provider_asset_id=str(asset["id"]),
            instrument_id=instrument_id,
            session_start=session_start,
            session_end=session_end,
            observed_at=self._now(),
            provider_time=clock_time,
            capability_version=version,
        )
        return cap

    @_locked
    def capabilities(self, instrument_id: str) -> BrokerCapabilities:
        return self._capability(instrument_id)

    @_locked
    def binding_proof(self, instrument_id: str) -> tuple[BrokerCapabilities, BindingProof]:
        """Fresh read-only Trading proof, including the pinned provider identities."""
        capability = self._capability(instrument_id)
        return capability, self._binding_proofs[instrument_id]

    @_locked
    def bind_intent(self, intent: OrderIntent, *, attempted: bool = True) -> None:
        """Bind persisted immutable intent for read-only recovery; never POST here."""
        if type(intent) is not OrderIntent or not _CLIENT_ID.fullmatch(intent.client_order_id):
            raise AlpacaPaperError("INVALID_INTENT")
        prior = self._intents.get(intent.client_order_id)
        if prior is not None and prior != intent:
            raise IdempotencyConflict("client order ID payload mismatch")
        self._intents[intent.client_order_id] = intent
        if attempted:
            self._attempted.add(intent.client_order_id)

    def _validate_intent(
        self,
        intent: OrderIntent,
        cap: BrokerCapabilities,
        asset: dict[str, object],
        account: dict[str, object],
    ) -> None:
        if intent.capability_version != cap.version:
            raise CapabilityError("capability version changed")
        crypto = intent.instrument_id == "BTC/USD"
        fractional = intent.quantity != intent.quantity.to_integral_value()
        if not cap.permits(
            order_type=intent.order_type, fractional=fractional, regular_session=not crypto
        ):
            raise CapabilityError("capability unconfirmed")
        with localcontext() as context:
            context.prec = 80
            if crypto:
                step = cap.quantity_step
                minimum = _decimal(asset.get("min_order_size"), positive=True)
                if step is None or intent.quantity % step or intent.quantity < minimum:
                    raise CapabilityError("crypto quantity constraint")
                if intent.order_type == "LIMIT" and (
                    cap.price_tick is None or intent.limit_price % cap.price_tick
                ):
                    raise CapabilityError("crypto price increment")
            else:
                if fractional and (
                    intent.order_type != "MARKET" or asset.get("fractionable") is not True
                ):
                    raise CapabilityError("fractional equities require market order")
                if fractional and intent.quantity % Decimal("0.000000001"):
                    raise CapabilityError("fractional equity precision")
                if not fractional and intent.quantity % 1:
                    raise CapabilityError("whole quantity required")
                if intent.order_type == "LIMIT":
                    price = intent.limit_price
                    if (
                        price is None
                        or (price >= 1 and price % Decimal("0.01"))
                        or (price < 1 and price % Decimal("0.0001"))
                    ):
                        raise CapabilityError("equity price increment")
            if intent.side == "BUY":
                # A market order has no contractual upper price bound. Do not infer
                # cash sufficiency from a stale quote or fabricate a risk limit.
                if intent.limit_price is None:
                    raise CapabilityError("market buy has no bounded cost")
                notional = intent.quantity * intent.limit_price
                if crypto and notional > _CRYPTO_MAX_NOTIONAL:
                    raise CapabilityError("crypto notional ceiling")
                # Explicit local policy cushion; the actual fee is still unknown
                # and every fill report remains PENDING until authoritative proof.
                if notional * self._cash_buffer > _decimal(account.get("cash")):
                    raise CapabilityError("insufficient cash")
            else:
                symbol = "BTCUSD" if crypto else intent.instrument_id
                position = self._get(f"/v2/positions/{symbol}", missing_ok=True)
                if position is None:
                    raise CapabilityError("short sale prohibited")
                if not isinstance(position, dict) or position.get("symbol") not in {
                    symbol,
                    intent.instrument_id,
                }:
                    raise AlpacaPaperError("INVALID_RESPONSE")
                available = _decimal(position.get("qty_available"))
                held = _decimal(position.get("qty"))
                if intent.quantity > min(available, held):
                    raise CapabilityError("short sale prohibited")

    def _unknown(self, intent: OrderIntent) -> BrokerOrder:
        prior = self._orders.get(intent.client_order_id)
        return (
            replace(prior, status=OrderStatus.UNKNOWN)
            if prior is not None
            else BrokerOrder(
                self._ids.new_id("alpaca-paper-order", intent.client_order_id),
                intent.id,
                intent.client_order_id,
                None,
                OrderStatus.UNKNOWN,
                intent.quantity,
                Decimal("0"),
                1,
                self._now(),
                intent.trace_id,
            )
        )

    def _map(self, intent: OrderIntent, data: object) -> BrokerOrder:
        if not isinstance(data, dict):
            raise AlpacaPaperError("INVALID_RESPONSE")
        broker_id = _uuid(data.get("id"))
        if (
            data.get("client_order_id") != intent.client_order_id
            or data.get("symbol") != intent.instrument_id
            or data.get("side") != intent.side.lower()
            or data.get("type") != intent.order_type.lower()
            or data.get("time_in_force") != ("gtc" if intent.instrument_id == "BTC/USD" else "day")
            or data.get("order_class") != "simple"
        ):
            raise IdempotencyConflict("broker order and immutable intent disagree")
        quantity = _decimal(data.get("qty"), positive=True)
        filled = _decimal(data.get("filled_qty"))
        if quantity != intent.quantity or filled > quantity:
            raise IdempotencyConflict("broker order quantity disagrees")
        if (
            intent.order_type == "LIMIT"
            and _decimal(data.get("limit_price"), positive=True) != intent.limit_price
        ):
            raise IdempotencyConflict("broker order limit disagrees")
        if intent.order_type == "MARKET" and data.get("limit_price") is not None:
            raise IdempotencyConflict("broker order limit disagrees")
        created = _time(data.get("created_at"))
        updated = _time(data.get("updated_at"))
        now = self._now()
        if created > updated or updated > now + timedelta(seconds=5):
            raise AlpacaPaperError("INVALID_RESPONSE")
        raw_status = data.get("status")
        status = (
            _STATUS.get(raw_status, OrderStatus.UNKNOWN)
            if isinstance(raw_status, str)
            else OrderStatus.UNKNOWN
        )
        if status is OrderStatus.FILLED and filled != quantity:
            status = OrderStatus.UNKNOWN
        if status is OrderStatus.PARTIALLY_FILLED and not 0 < filled < quantity:
            status = OrderStatus.UNKNOWN
        if status is OrderStatus.ACKNOWLEDGED and filled > 0:
            status = OrderStatus.UNKNOWN
        prior = self._orders.get(intent.client_order_id)
        if prior is not None:
            if prior.broker_order_id is not None and prior.broker_order_id != broker_id:
                raise IdempotencyConflict("broker ID changed")
            if filled < prior.filled_quantity:
                raise IdempotencyConflict("cumulative fill regressed")
            if prior.status in _TERMINAL:
                late_cancel_fill = (
                    prior.status is OrderStatus.CANCELED
                    and status in {OrderStatus.CANCELED, OrderStatus.FILLED}
                    and filled >= prior.filled_quantity
                )
                if status != prior.status and not late_cancel_fill:
                    raise IdempotencyConflict("terminal order regressed")
        other = self._broker_ids.get(broker_id)
        if other is not None and other != intent.client_order_id:
            raise IdempotencyConflict("broker ID reused")
        average = data.get("filled_avg_price")
        average_price = None if average is None else _decimal(average, positive=True)
        if filled > 0 and average_price is None:
            raise AlpacaPaperError("INVALID_RESPONSE")
        prior_reports = self._reports.get(intent.client_order_id, ())
        changed = (
            prior is None
            or (prior.status, prior.filled_quantity) != (status, filled)
            or (prior_reports and prior_reports[-1].average_fill_price != average_price)
        )
        version = 1 if prior is None else prior.version + int(bool(changed))
        order = BrokerOrder(
            prior.id
            if prior is not None
            else self._ids.new_id("alpaca-paper-order", intent.client_order_id),
            intent.id,
            intent.client_order_id,
            broker_id,
            status,
            quantity,
            filled,
            version,
            updated,
            intent.trace_id,
        )
        self._orders[intent.client_order_id] = order
        self._broker_ids[broker_id] = intent.client_order_id
        self._record(intent, order, average_price)
        return order

    def _record(
        self, intent: OrderIntent, order: BrokerOrder, average_price: Decimal | None
    ) -> None:
        reports = self._reports.setdefault(intent.client_order_id, [])
        signature = (order.status, order.filled_quantity, order.broker_order_id)
        if (
            reports
            and (
                reports[-1].status,
                reports[-1].filled_quantity,
                self._orders[intent.client_order_id].broker_order_id,
            )
            == signature
            and reports[-1].average_fill_price == average_price
        ):
            return
        # Order GET has cumulative fill evidence but no authoritative total fee.
        evidence = json.dumps(
            {
                "broker_id": order.broker_order_id,
                "client_id": intent.client_order_id,
                "status": order.status.value,
                "quantity": _canonical_decimal(order.requested_quantity),
                "filled": _canonical_decimal(order.filled_quantity),
                "average": _canonical_decimal(average_price),
                "updated_at": order.updated_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event_id = "alpaca-order-snapshot:" + hashlib.sha256(evidence.encode()).hexdigest()
        report = ExecutionReport(
            self._ids.new_id("alpaca-paper-report", event_id),
            order.id,
            order.status,
            order.requested_quantity,
            order.filled_quantity,
            Reconciliation.PENDING,
            order.updated_at,
            self._now(),
            intent.trace_id,
            broker_event_id=event_id,
            average_fill_price=average_price,
            fee=None,
            reason_code="FEE_UNVERIFIED" if order.filled_quantity > 0 else None,
        )
        reports.append(report)

    def _lookup(self, client_order_id: str) -> BrokerOrder | None:
        intent = self._intents.get(client_order_id)
        if intent is None:
            return None
        data = self._get(
            "/v2/orders:by_client_order_id",
            params={"client_order_id": client_order_id},
            missing_ok=True,
        )
        if data is None:
            return self._unknown(intent) if client_order_id in self._attempted else None
        return self._map(intent, data)

    @_locked
    def submit(self, intent: OrderIntent) -> BrokerOrder:
        if type(intent) is not OrderIntent or not _CLIENT_ID.fullmatch(intent.client_order_id):
            raise AlpacaPaperError("INVALID_INTENT")
        prior = self._intents.get(intent.client_order_id)
        if prior is not None and prior != intent:
            raise IdempotencyConflict("client order ID payload mismatch")
        if prior is not None or intent.client_order_id in self._attempted:
            if prior is None:
                self.bind_intent(intent, attempted=True)
            return self._lookup(intent.client_order_id) or self._unknown(intent)
        cap = self._capability(intent.instrument_id)
        account = self._account(crypto=intent.instrument_id == "BTC/USD")
        asset = self._asset(intent.instrument_id)
        self._validate_intent(intent, cap, asset, account)
        self.bind_intent(intent, attempted=False)
        existing = self._lookup(intent.client_order_id)
        if existing is not None:
            return existing
        # Final account/session check immediately before the only POST attempt.
        final_cap = self._capability(intent.instrument_id)
        if cap.version != final_cap.version:
            raise CapabilityError("capability version changed")
        final_account = self._account(crypto=intent.instrument_id == "BTC/USD")
        final_asset = self._asset(intent.instrument_id)
        self._validate_intent(intent, final_cap, final_asset, final_account)
        self._fresh_proof(intent.instrument_id)
        self._attempted.add(intent.client_order_id)
        payload: dict[str, object] = {
            "symbol": intent.instrument_id,
            "qty": _canonical_decimal(intent.quantity),
            "side": intent.side.lower(),
            "type": intent.order_type.lower(),
            "time_in_force": "gtc" if intent.instrument_id == "BTC/USD" else "day",
            "order_class": "simple",
            "extended_hours": False,
            "client_order_id": intent.client_order_id,
        }
        if intent.limit_price is not None:
            payload["limit_price"] = _canonical_decimal(intent.limit_price)
        try:
            status, data = self._request("POST", "/v2/orders", payload=payload)
        except AlpacaPaperError:
            unknown = self._unknown(intent)
            self._orders[intent.client_order_id] = unknown
            return unknown
        if status != 200:
            unknown = self._unknown(intent)
            self._orders[intent.client_order_id] = unknown
            return unknown
        try:
            return self._map(intent, data)
        except (AlpacaPaperError, IdempotencyConflict):
            unknown = self._unknown(intent)
            self._orders[intent.client_order_id] = unknown
            return unknown

    @_locked
    def order_status(self, client_order_id: str) -> BrokerOrder | None:
        if not _CLIENT_ID.fullmatch(client_order_id):
            raise AlpacaPaperError("INVALID_CLIENT_ID")
        return self._lookup(client_order_id)

    @_locked
    def by_broker_id(self, broker_order_id: str) -> BrokerOrder | None:
        canonical = _uuid(broker_order_id)
        data = self._get(f"/v2/orders/{canonical}", missing_ok=True)
        client_id = self._broker_ids.get(canonical)
        if data is None:
            return self._unknown(self._intents[client_id]) if client_id is not None else None
        if not isinstance(data, dict) or not isinstance(data.get("client_order_id"), str):
            raise AlpacaPaperError("INVALID_RESPONSE")
        reported_id = data["client_order_id"]
        if client_id is not None and reported_id != client_id:
            raise IdempotencyConflict("broker and client order IDs disagree")
        intent = self._intents.get(reported_id)
        if intent is None:
            raise IdempotencyConflict("broker ID refers to an unbound client order")
        return self._map(intent, data)

    @_locked
    def reports(self, client_order_id: str) -> tuple[ExecutionReport, ...]:
        return tuple(self._reports.get(client_order_id, ()))

    @_locked
    def reconciliation_evidence(
        self,
        *,
        after: datetime,
        initial_history: InitialHistoryProof | None = None,
    ) -> AlpacaReconciliationEvidence:
        """Collect exhaustive read-only account evidence, or fail closed."""
        from packages.brokers.alpaca_activities import collect_evidence

        return collect_evidence(self, after=after, initial_history=initial_history)

    @_locked
    def cancel(self, client_order_id: str) -> BrokerOrder | None:
        order = self._lookup(client_order_id)
        if order is None or order.broker_order_id is None or order.status in _TERMINAL:
            return order
        try:
            status, _ = self._request("DELETE", f"/v2/orders/{order.broker_order_id}")
        except AlpacaPaperError:
            status = 0
        if status != 204:
            return self._unknown(self._intents[client_order_id])
        pending = replace(
            order,
            status=OrderStatus.CANCEL_PENDING,
            version=order.version + 1,
            updated_at=self._now(),
        )
        self._orders[client_order_id] = pending
        return pending

    @_locked
    def reconcile(
        self, client_order_id: str, broker_order_id: str | None = None
    ) -> BrokerOrder | None:
        by_client = self.order_status(client_order_id)
        if broker_order_id is None:
            return by_client
        by_broker = self.by_broker_id(broker_order_id)
        if by_broker is not None and by_broker.client_order_id != client_order_id:
            raise IdempotencyConflict("broker and client order IDs disagree")
        if by_client is not None and by_client.broker_order_id not in {None, broker_order_id}:
            raise IdempotencyConflict("broker and client order IDs disagree")
        return by_broker or by_client
