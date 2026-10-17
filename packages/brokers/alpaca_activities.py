"""Bounded, exhaustive read-only Paper reconciliation evidence.

Provider identities stay inside Execution. Daily FEE/CFEE rows are deliberately
not attributed to an order and missing fees never become zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

from packages.domain.models import BrokerOrder, decimal_value, utc_time

if TYPE_CHECKING:
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter

EQUITY_ZERO_FEE_PROFILE = "alpaca-paper-us-retail-equity-zero-v1"


@dataclass(frozen=True)
class AlpacaPositionEvidence:
    asset_id: str
    instrument_id: str
    quantity: Decimal
    avg_entry_price: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class AlpacaActivityEvidence:
    kind: str
    id: str
    occurred_at: datetime | None
    order_id: str | None = None
    instrument_id: str | None = None
    side: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    cumulative_quantity: Decimal | None = None
    leaves_quantity: Decimal | None = None
    net_amount: Decimal | None = None
    activity_date: date | None = None


@dataclass(frozen=True)
class AlpacaReconciliationEvidence:
    account_id: str
    currency: str
    cash: Decimal
    equity: Decimal
    positions: tuple[AlpacaPositionEvidence, ...]
    orders: tuple[BrokerOrder, ...]
    activities: tuple[AlpacaActivityEvidence, ...]
    observed_at: datetime
    provider_time: datetime
    after: datetime
    until: datetime
    fee_authority_profile: str | None = None


def _fail(reason: str = "INVALID_RECONCILIATION_EVIDENCE") -> None:
    from packages.brokers.alpaca_paper import AlpacaPaperError

    raise AlpacaPaperError(reason)


def _number(value: object, *, positive: bool = False, signed: bool = False) -> Decimal:
    try:
        result = decimal_value(value)
    except (ValueError, TypeError):
        _fail()
    if not signed and (result < 0 or (positive and result == 0)):
        _fail()
    return result


def _symbol(value: object) -> str:
    if not isinstance(value, str):
        _fail("UNSUPPORTED_RECONCILIATION_ASSET")
    if value == "BTCUSD":
        return "BTC/USD"
    if value not in {"SPY", "QQQ", "BTC/USD"}:
        _fail("UNSUPPORTED_RECONCILIATION_ASSET")
    return str(value)


def _activity(raw: object, after: datetime, until: datetime) -> AlpacaActivityEvidence:
    from packages.brokers.alpaca_paper import _time, _uuid

    if not isinstance(raw, dict):
        _fail()
    identity, kind = raw.get("id"), raw.get("activity_type")
    if (
        not isinstance(identity, str)
        or not 1 <= len(identity) <= 256
        or "::" not in identity
        or not isinstance(kind, str)
        or not kind.isascii()
        or not kind.isupper()
        or not kind.isalpha()
    ):
        _fail()
    if kind == "FILL":
        occurred = _time(raw.get("transaction_time"))
        if not after <= occurred <= until or raw.get("side") not in {"buy", "sell"}:
            _fail()
        if raw.get("type") not in {"fill", "partial_fill"}:
            _fail()
        quantity = _number(raw.get("qty"), positive=True)
        cumulative = _number(raw.get("cum_qty"), positive=True)
        leaves = _number(raw.get("leaves_qty"))
        if cumulative < quantity or (raw["type"] == "fill" and leaves != 0):
            _fail()
        return AlpacaActivityEvidence(
            kind,
            identity,
            occurred,
            _uuid(raw.get("order_id")),
            _symbol(raw.get("symbol")),
            str(raw["side"]).upper(),
            quantity,
            _number(raw.get("price"), positive=True),
            cumulative,
            leaves,
        )
    # Preserve all cash/corporate/fee activity types, including unsupported types.
    # Daily rows cannot prove a precise per-order fee or timestamp.
    try:
        day = date.fromisoformat(raw.get("date"))
    except (ValueError, TypeError):
        _fail()
    if not after.date() <= day <= until.date():
        _fail()
    symbol = raw.get("symbol")
    return AlpacaActivityEvidence(
        kind,
        identity,
        None,
        _uuid(raw["order_id"]) if raw.get("order_id") is not None else None,
        _symbol(symbol) if symbol is not None else None,
        quantity=_number(raw["qty"], signed=True) if raw.get("qty") is not None else None,
        price=_number(raw["price"]) if raw.get("price") is not None else None,
        net_amount=_number(raw.get("net_amount"), signed=True),
        activity_date=day,
    )


def collect_evidence(
    broker: AlpacaPaperBrokerAdapter, *, after: datetime
) -> AlpacaReconciliationEvidence:
    from packages.brokers.alpaca_paper import _MAX_AGE, _time, _uuid
    from packages.brokers.fake import IdempotencyConflict

    try:
        after = utc_time(after)
    except ValueError:
        _fail("INVALID_RECONCILIATION_WINDOW")
    clock = broker._get("/v2/clock")
    if not isinstance(clock, dict):
        _fail()
    until = _time(clock.get("timestamp"))
    now = broker._now()
    if not after <= until <= now + timedelta(seconds=5) or now - until > _MAX_AGE:
        _fail("STALE_RECONCILIATION_EVIDENCE")

    def account_values() -> tuple[str, str, Decimal, Decimal]:
        account = broker._account(require_trading=False)
        if account.get("currency") != "USD":
            _fail("ACCOUNT_UNAVAILABLE")
        cash, equity = _number(account.get("cash")), _number(account.get("equity"))
        if _number(account.get("portfolio_value")) != equity:
            _fail("ACCOUNT_EVIDENCE_MISMATCH")
        return _uuid(account.get("id")), "USD", cash, equity

    def positions_values() -> tuple[AlpacaPositionEvidence, ...]:
        rows = broker._get("/v2/positions")
        if not isinstance(rows, list):
            _fail()
        result = []
        for row in rows:
            if not isinstance(row, dict) or row.get("side") != "long":
                _fail()
            result.append(
                AlpacaPositionEvidence(
                    _uuid(row.get("asset_id")),
                    _symbol(row.get("symbol")),
                    _number(row.get("qty"), positive=True),
                    _number(row.get("avg_entry_price"), positive=True),
                    _number(row.get("market_value")),
                )
            )
        if len({p.instrument_id for p in result}) != len(result):
            _fail()
        if len({p.asset_id for p in result}) != len(result):
            _fail()
        return tuple(sorted(result, key=lambda p: p.instrument_id))

    account = account_values()
    positions = positions_values()
    with localcontext() as context:
        context.prec = 80
        if account[2] + sum((p.market_value for p in positions), Decimal(0)) != account[3]:
            _fail("ACCOUNT_EVIDENCE_MISMATCH")

    # Orders are read across the entire account history, including orders opened
    # before the activity baseline. Overlap the timestamp boundary to avoid
    # silently losing orders with identical submitted_at timestamps.
    raw_orders: dict[str, dict] = {}
    cursor = None
    for _ in range(1000):
        params = {
            "status": "all",
            "direction": "asc",
            "limit": "500",
            "nested": "false",
            "until": until.isoformat(),
        }
        if cursor is not None:
            params["after"] = cursor.isoformat()
        rows = broker._get("/v2/orders", params=params)
        if not isinstance(rows, list) or len(rows) > 500:
            _fail()
        added, last = 0, None
        for row in rows:
            if not isinstance(row, dict):
                _fail()
            identity = _uuid(row.get("id"))
            submitted = _time(row.get("submitted_at"))
            if submitted > until or (last is not None and submitted < last):
                _fail()
            if cursor is not None and submitted < cursor:
                _fail()
            last = submitted
            prior = raw_orders.get(identity)
            if prior is not None and prior != row:
                _fail("CONFLICTING_RECONCILIATION_EVIDENCE")
            if prior is None:
                added += 1
            raw_orders[identity] = row
        if len(rows) < 500:
            break
        if not added or last is None:
            _fail("INCOMPLETE_RECONCILIATION_EVIDENCE")
        next_cursor = last - timedelta(microseconds=1)
        if cursor is not None and next_cursor <= cursor:
            _fail("INCOMPLETE_RECONCILIATION_EVIDENCE")
        cursor = next_cursor
    else:
        _fail("INCOMPLETE_RECONCILIATION_EVIDENCE")

    activities: dict[str, AlpacaActivityEvidence] = {}
    token = None
    tokens: set[str] = set()
    for _ in range(1000):
        day_start = after.replace(hour=0, minute=0, second=0, microsecond=0)
        params = {
            "direction": "asc",
            "page_size": "100",
            "after": day_start.isoformat(),
            "until": until.isoformat(),
        }
        if token is not None:
            params["page_token"] = token
        rows = broker._get("/v2/account/activities", params=params)
        if not isinstance(rows, list) or len(rows) > 100:
            _fail()
        for row in rows:
            item = _activity(row, day_start, until)
            prior = activities.get(item.id)
            if prior is not None and prior != item:
                _fail("CONFLICTING_RECONCILIATION_EVIDENCE")
            activities[item.id] = item
        if len(rows) < 100:
            break
        token = rows[-1]["id"]
        if token in tokens:
            _fail("INCOMPLETE_RECONCILIATION_EVIDENCE")
        tokens.add(token)
    else:
        _fail("INCOMPLETE_RECONCILIATION_EVIDENCE")

    # Do not mutate adapter order/report state until all reads validate.
    for row in raw_orders.values():
        client_id = row.get("client_order_id")
        if not isinstance(client_id, str) or client_id not in broker._intents:
            _fail("UNBOUND_RECONCILIATION_ORDER")
        if _time(row.get("updated_at")) > until:
            _fail("ACCOUNT_EVIDENCE_CHANGED")
    if account_values() != account or positions_values() != positions:
        _fail("ACCOUNT_EVIDENCE_CHANGED")
    observed = broker._now()
    if observed - until > _MAX_AGE or until > observed + timedelta(seconds=5):
        _fail("STALE_RECONCILIATION_EVIDENCE")
    previous = (
        dict(broker._orders),
        dict(broker._broker_ids),
        {key: list(reports) for key, reports in broker._reports.items()},
    )
    try:
        orders = tuple(
            broker._map(broker._intents[row["client_order_id"]], row) for row in raw_orders.values()
        )
    except (ValueError, IdempotencyConflict):
        broker._orders, broker._broker_ids, broker._reports = previous
        raise
    return AlpacaReconciliationEvidence(
        *account,
        positions,
        orders,
        tuple(a for a in activities.values() if a.occurred_at is None or a.occurred_at >= after),
        observed,
        until,
        after,
        until,
        broker.reconciliation_fee_profile,
    )
