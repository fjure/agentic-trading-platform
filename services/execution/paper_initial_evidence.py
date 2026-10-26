"""Private GET-only, twice-confirmed initial Paper evidence. Never writes a ledger.

Sources: Alpaca /us/docs/account-activities, /us/docs/orders-at-alpaca,
/us/docs/position-average-entry-price-calculation and /us/reference/getallopenpositions.
Provider cost_basis is authoritative: avg_entry_price can be rounded and its
accounting method changes at BOD. Previous-close equity is not UTC day-start equity.
All returned values/anchors are Execution-private, never public DTOs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from time import monotonic
from uuid import UUID

import httpx

from packages.brokers.initial_anchors import activity_fingerprint, order_fingerprint
from packages.domain.models import decimal_value, utc_time
from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PaperGenerationReader,
    PrivatePaperCredentials,
)
from services.execution.paper_verification import PrivateIdentityProof
from services.setup.store import generation_filename

_PAPER = "https://paper-api.alpaca.markets"
_ROUTES = {"/v2/clock", "/v2/account", "/v2/positions", "/v2/orders", "/v2/account/activities",
           "/v2/account/portfolio/history"}
_BODY_LIMIT = 1048576
_MAX_PAGES = 20
_ORDER_PAGE = 500
_ACTIVITY_PAGE = 100
_MAX_DURATION = timedelta(seconds=30)
_TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
_CODES = {
    "CREDENTIALS_MISSING", "CREDENTIALS_INVALID", "ACCOUNT_IDENTITY_MISMATCH",
    "CURRENCY_UNSUPPORTED", "ACCOUNT_BLOCKED", "PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED",
    "INITIAL_EVIDENCE_STALE", "INITIAL_EVIDENCE_CHANGED", "INITIAL_OPEN_ORDERS",
    "INITIAL_POSITION_UNSUPPORTED", "INITIAL_COST_BASIS_MISSING", "INITIAL_HISTORY_INCOMPLETE",
    "INITIAL_EVIDENCE_INVALID",
    "DAILY_REFERENCE_UNPROVEN", "EXTERNAL_FLOW_UNPROVEN",
}


class InitialEvidenceUnavailable(ValueError):
    def __init__(self, code):
        super().__init__(code if code in _CODES else "PROVIDER_UNAVAILABLE")


def _deny(code="INITIAL_EVIDENCE_INVALID"):
    raise InitialEvidenceUnavailable(code)


def _uuid(value):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        _deny("ACCOUNT_IDENTITY_MISMATCH")
    return value


def _number(value, *, positive=False):
    if not isinstance(value, str):
        _deny()
    result = decimal_value(value)
    if result < 0 or positive and result == 0:
        _deny()
    return result


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        _deny()
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        _deny()
    return result.astimezone(UTC)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _deny()
        result[key] = value
    return result


def _hash(value):
    def encode(item):
        if isinstance(item, Decimal):
            raw = format(item, "f")
            return raw.rstrip("0").rstrip(".") if "." in raw else raw
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        _deny()
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=encode,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class InitialPaperPosition:
    provider_asset_id: str
    symbol: str
    quantity: Decimal
    cost_basis: Decimal
    average_entry_price: Decimal
    market_value: Decimal

    @property
    def average_only_ledger_compatible(self):
        with localcontext() as context:
            context.prec = 80
            return self.average_entry_price > 0 and (
                self.quantity * self.average_entry_price == self.cost_basis
            )


@dataclass(frozen=True, slots=True, repr=False)
class InitialOrderAnchor:
    provider_order_id: str
    status: str
    submitted_at: datetime | None
    updated_at: datetime | None
    evidence_hash: str
    untraded: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class InitialActivityAnchor:
    activity_id: str
    kind: str
    occurred_at: datetime | None
    activity_date: date | None
    evidence_hash: str
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class InitialPaperEvidence:
    credential_generation: int
    provider_account_id: str
    cash: Decimal
    equity: Decimal
    provider_previous_close_equity: Decimal | None
    positions: tuple[InitialPaperPosition, ...]
    order_anchors: tuple[InitialOrderAnchor, ...]
    activity_anchors: tuple[InitialActivityAnchor, ...]
    first_read_at: datetime
    confirmed_at: datetime
    provider_time: datetime
    snapshot_hash: str
    evidence_hash: str
    currency: str = field(default="USD", init=False)

    @property
    def average_only_ledger_compatible(self):
        return all(position.average_only_ledger_compatible for position in self.positions)


@dataclass(frozen=True, slots=True, repr=False)
class _Snapshot:
    account: tuple
    positions: tuple[InitialPaperPosition, ...]
    orders: tuple[InitialOrderAnchor, ...]
    activities: tuple[InitialActivityAnchor, ...]
    observed_at: datetime
    provider_time: datetime
    digest: str


class InitialPaperEvidenceCollector:
    def __init__(
        self, *, credentials: PaperGenerationReader, clock, expected_account_id: str,
        pinned_account_id: str | None = None, transport: httpx.MockTransport | None = None,
    ):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._expected = _uuid(expected_account_id)
        self._pinned = _uuid(pinned_account_id) if pinned_account_id is not None else None
        self._credentials, self._clock, self._transport = credentials, clock, transport

    @staticmethod
    def _get(client, route, credentials, deadline, *, params=None):
        if route not in _ROUTES or monotonic() >= deadline:
            _deny("PROVIDER_UNAVAILABLE")
        from packages.research.limits import existing_get_allowed
        if not existing_get_allowed(_PAPER):
            _deny("PROVIDER_UNAVAILABLE")
        remaining = max(.001, min(5., deadline - monotonic()))
        with client.stream("GET", _PAPER + route, params=params, timeout=remaining, headers={
            "APCA-API-KEY-ID": credentials.api_key,
            "APCA-API-SECRET-KEY": credentials.api_secret,
            "Accept": "application/json", "Accept-Encoding": "identity",
        }) as response:
            if response.status_code == 429:
                from packages.research.limits import existing_get_rate_limited
                existing_get_rate_limited(_PAPER, response.headers.get("retry-after"))
                _deny("PROVIDER_RATE_LIMITED")
            if response.status_code in {401, 403}:
                _deny("CREDENTIALS_INVALID")
            if response.status_code != 200 or response.is_redirect:
                _deny("PROVIDER_UNAVAILABLE")
            if response.headers.get("content-encoding", "identity").lower() != "identity" or (
                response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                _deny("PROVIDER_UNAVAILABLE")
            length = response.headers.get("content-length")
            if length is not None and (
                not length.isdecimal() or len(length) > 8 or int(length) > _BODY_LIMIT
            ):
                _deny("PROVIDER_UNAVAILABLE")
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if monotonic() >= deadline or len(body) + len(chunk) > _BODY_LIMIT:
                    _deny("PROVIDER_UNAVAILABLE")
                body.extend(chunk)
            return json.loads(
                body, object_pairs_hook=_pairs, parse_float=Decimal, parse_int=Decimal,
                parse_constant=lambda _: _deny(),
            )

    def _account(self, raw):
        if not isinstance(raw, dict):
            _deny()
        identity = _uuid(raw.get("id"))
        if identity != self._expected or self._pinned is not None and identity != self._pinned:
            _deny("ACCOUNT_IDENTITY_MISMATCH")
        if raw.get("currency") != "USD":
            _deny("CURRENCY_UNSUPPORTED")
        if raw.get("status") != "ACTIVE" or any(raw.get(key) is not False for key in (
            "account_blocked", "trading_blocked", "trade_suspended_by_user",
        )):
            _deny("ACCOUNT_BLOCKED")
        cash, equity = _number(raw.get("cash")), _number(raw.get("equity"))
        if _number(raw.get("portfolio_value")) != equity:
            _deny()
        if "short_market_value" in raw and decimal_value(raw["short_market_value"]) != 0:
            _deny("INITIAL_POSITION_UNSUPPORTED")
        previous = _number(raw["last_equity"]) if raw.get("last_equity") is not None else None
        return identity, cash, equity, previous

    @staticmethod
    def _positions(raw):
        if not isinstance(raw, list) or len(raw) > 2:
            _deny("INITIAL_POSITION_UNSUPPORTED")
        result = []
        for row in raw:
            if not isinstance(row, dict) or (
                row.get("symbol") not in {"SPY", "QQQ"} or row.get("side") != "long"
                or row.get("asset_class") != "us_equity"
            ):
                _deny("INITIAL_POSITION_UNSUPPORTED")
            if row.get("cost_basis") is None or row.get("avg_entry_price") is None:
                _deny("INITIAL_COST_BASIS_MISSING")
            result.append(InitialPaperPosition(
                _uuid(row.get("asset_id")), row["symbol"], _number(row.get("qty"), positive=True),
                _number(row["cost_basis"]), _number(row["avg_entry_price"]),
                _number(row.get("market_value"), positive=True),
            ))
        if len({p.symbol for p in result}) != len(result) or (
            len({p.provider_asset_id for p in result}) != len(result)
        ):
            _deny("INITIAL_POSITION_UNSUPPORTED")
        return tuple(sorted(result, key=lambda position: position.symbol))

    @staticmethod
    def _order(row, until):
        if not isinstance(row, dict):
            _deny()
        if row.get("status") not in _TERMINAL:
            _deny("INITIAL_OPEN_ORDERS")
        identity = _uuid(row.get("id"))
        submitted = _time(row["submitted_at"]) if row.get("submitted_at") is not None else None
        updated = _time(row["updated_at"]) if row.get("updated_at") is not None else None
        created = _time(row["created_at"]) if row.get("created_at") is not None else None
        if submitted is None and created is None:
            _deny("INITIAL_HISTORY_INCOMPLETE")
        if any(moment > until for moment in (submitted, updated, created) if moment is not None):
            _deny("INITIAL_EVIDENCE_CHANGED")
        if submitted is not None and updated is not None and submitted > updated:
            _deny()
        try:
            digest = order_fingerprint(row)
        except ValueError:
            _deny()
        untraded = False
        if row["status"] in {"canceled", "expired", "rejected"} and isinstance(
            row.get("filled_qty"), str,
        ):
            try:
                average = row.get("filled_avg_price")
                untraded = decimal_value(row["filled_qty"]) == 0 and (
                    average is None or isinstance(average, str) and decimal_value(average) == 0
                )
            except ValueError:
                # Malformed or absent fill evidence never proves an untraded order.
                pass
        return InitialOrderAnchor(identity, row["status"], submitted, updated, digest, untraded)

    @staticmethod
    def _activity(row, until):
        if not isinstance(row, dict):
            _deny()
        identity, kind = row.get("id"), row.get("activity_type")
        if (not isinstance(identity, str) or not 1 <= len(identity) <= 256 or "::" not in identity
                or not isinstance(kind, str) or re.fullmatch(r"[A-Z]{1,32}", kind) is None):
            _deny()
        occurred = _time(row.get("transaction_time")) if kind == "FILL" else None
        raw_day = row.get("date")
        day = None if occurred else (
            date.fromisoformat(raw_day) if isinstance(raw_day, str) and len(raw_day) == 10
            else _time(raw_day).date()
        )
        created = _time(row["created_at"]) if row.get("created_at") is not None else None
        # API after/until filters creation time, not NTA settlement date. A later
        # settlement date is not evidence of a later-created activity.
        if occurred is not None and occurred > until or created is not None and created > until:
            _deny("INITIAL_EVIDENCE_CHANGED")
        # Financial history is anchored, never replayed here. Unknown historical
        # activity kinds are retained rather than treated as post-import effects.
        try:
            digest = activity_fingerprint(row)
        except ValueError:
            _deny()
        return InitialActivityAnchor(identity, kind, occurred, day, digest, created)

    def _orders(self, get, until):
        found, cursor = {}, None
        for _ in range(_MAX_PAGES):
            params = {"status": "all", "direction": "asc", "limit": str(_ORDER_PAGE),
                      "nested": "false"}
            if cursor is not None:
                # Official ID cursors cannot be combined with after/until.
                params["after_order_id"] = cursor
            rows = get("/v2/orders", params=params)
            if not isinstance(rows, list) or len(rows) > _ORDER_PAGE:
                _deny("INITIAL_HISTORY_INCOMPLETE")
            for raw in rows:
                anchor = self._order(raw, until)
                prior = found.get(anchor.provider_order_id)
                if prior is not None and prior != anchor:
                    _deny("INITIAL_EVIDENCE_CHANGED")
                if prior is not None:
                    _deny("INITIAL_HISTORY_INCOMPLETE")
                found[anchor.provider_order_id] = anchor
            if len(rows) < _ORDER_PAGE:
                return tuple(sorted(found.values(), key=lambda order: order.provider_order_id))
            cursor = rows[-1]["id"]
        _deny("INITIAL_HISTORY_INCOMPLETE")

    def _activities(self, get, until):
        found, tokens, token = {}, set(), None
        for _ in range(_MAX_PAGES):
            params = {"direction": "asc", "page_size": str(_ACTIVITY_PAGE),
                      "until": until.isoformat()}
            if token is not None:
                params["page_token"] = token
            rows = get("/v2/account/activities", params=params)
            if not isinstance(rows, list) or len(rows) > _ACTIVITY_PAGE:
                _deny("INITIAL_HISTORY_INCOMPLETE")
            for raw in rows:
                anchor = self._activity(raw, until)
                prior = found.get(anchor.activity_id)
                if prior is not None and prior != anchor:
                    _deny("INITIAL_EVIDENCE_CHANGED")
                if prior is not None:
                    _deny("INITIAL_HISTORY_INCOMPLETE")
                found[anchor.activity_id] = anchor
            if len(rows) < _ACTIVITY_PAGE:
                return tuple(sorted(found.values(), key=lambda activity: activity.activity_id))
            token = rows[-1]["id"]
            if token in tokens:
                _deny("INITIAL_HISTORY_INCOMPLETE")
            tokens.add(token)
        _deny("INITIAL_HISTORY_INCOMPLETE")

    def _snapshot(self, client, credentials, deadline):
        def get(route, **kwargs):
            return self._get(client, route, credentials, deadline, **kwargs)
        clock = get("/v2/clock")
        if not isinstance(clock, dict):
            _deny()
        until = _time(clock.get("timestamp"))
        now = utc_time(self._clock.now())
        if not -timedelta(seconds=5) <= now - until < _MAX_DURATION:
            _deny("INITIAL_EVIDENCE_STALE")
        account = self._account(get("/v2/account"))
        positions = self._positions(get("/v2/positions"))

        def no_open_orders():
            opened = get("/v2/orders", params={"status": "open", "limit": "1", "nested": "true"})
            if not isinstance(opened, list):
                _deny()
            if opened:
                _deny("INITIAL_OPEN_ORDERS")

        no_open_orders()
        orders, activities = self._orders(get, until), self._activities(get, until)
        if self._positions(get("/v2/positions")) != positions or (
            self._account(get("/v2/account")) != account
        ):
            _deny("INITIAL_EVIDENCE_CHANGED")
        no_open_orders()
        with localcontext() as arithmetic:
            arithmetic.prec = 80
            if account[1] + sum((p.market_value for p in positions), Decimal(0)) != account[2]:
                _deny()
        observed = utc_time(self._clock.now())
        if not -timedelta(seconds=5) <= observed - until < _MAX_DURATION:
            _deny("INITIAL_EVIDENCE_STALE")
        digest = _hash([
            account,
            [[p.provider_asset_id, p.symbol, p.quantity, p.cost_basis,
              p.average_entry_price, p.market_value] for p in positions],
            [[order.provider_order_id, order.evidence_hash] for order in orders],
            [[activity.activity_id, activity.evidence_hash] for activity in activities],
        ])
        return _Snapshot(account, positions, orders, activities, observed, until, digest)

    def collect(
        self, generation: int, identity_proof: PrivateIdentityProof,
    ) -> InitialPaperEvidence:
        """Caller must still fence, lock and check freshness immediately before commit."""
        try:
            generation_filename(generation)
            started = utc_time(self._clock.now())
            if (type(identity_proof) is not PrivateIdentityProof
                    or identity_proof.credential_generation != generation
                    or identity_proof.provider_account_id != self._expected
                    or not timedelta() <= started - utc_time(identity_proof.observed_at)
                    < timedelta(seconds=120)):
                _deny("ACCOUNT_IDENTITY_MISMATCH")
            credentials = self._credentials.load(generation)
            if (type(credentials) is not PrivatePaperCredentials
                    or credentials.generation != generation):
                _deny("CREDENTIALS_MISSING")
            if credentials.expected_account_id != self._expected or (
                self._pinned is not None and self._pinned != self._expected
            ):
                _deny("ACCOUNT_IDENTITY_MISMATCH")
            deadline = monotonic() + _MAX_DURATION.total_seconds()
            with httpx.Client(
                transport=self._transport, trust_env=False, follow_redirects=False,
                proxy="http://paper-execution-egress:8080" if self._transport is None else None,
                timeout=httpx.Timeout(5.),
            ) as client:
                first = self._snapshot(client, credentials, deadline)
                second = self._snapshot(client, credentials, deadline)
            if first.digest != second.digest or second.provider_time < first.provider_time:
                _deny("INITIAL_EVIDENCE_CHANGED")
            if not started <= first.observed_at <= second.observed_at or not (
                timedelta() <= second.observed_at - started < _MAX_DURATION
            ):
                _deny("INITIAL_EVIDENCE_STALE")
            return InitialPaperEvidence(
                generation, second.account[0], second.account[1], second.account[2],
                second.account[3], second.positions, second.orders, second.activities,
                first.observed_at, second.observed_at, second.provider_time,
                second.digest,
                _hash([generation, second.digest, first.observed_at,
                       second.observed_at, second.provider_time]),
            )
        except PaperCredentialsUnavailable:
            code = "CREDENTIALS_MISSING"
        except InitialEvidenceUnavailable as error:
            code = str(error)
        except Exception:
            code = "PROVIDER_UNAVAILABLE"
        raise InitialEvidenceUnavailable(code) from None
