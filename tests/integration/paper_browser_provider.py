"""Stateful offline provider, never a ledger/readiness/API-response fixture.

All state belongs to the synthetic broker. Only explicit controls produce fills
or cancel terminal evidence. Actual application collectors perform every local
financial write and gate check. No credentials, requests or raw IDs are logged.
"""

from __future__ import annotations

import copy
import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import uuid4

import httpx

from services.worker.market_state import PaperMarketConfig

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
ASSETS = {
    "SPY": "22222222-2222-4222-8222-222222222222",
    "QQQ": "33333333-3333-4333-8333-333333333333",
}
_PAPER = "paper-api.alpaca.markets"
_DATA = "data.alpaca.markets"
_TERMINAL = {"filled", "canceled", "expired", "rejected"}
_MONEY_UNIT = Decimal(".000000000000000001")


def _text(value):
    return format(value, "f")


def _stamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("SYNTHETIC_INVALID_TIME")
    return result.astimezone(UTC)


class PaperProvider:
    def __init__(self, clock, before_io=None, *, rising=False):
        if type(rising) is not bool:
            raise ValueError("SYNTHETIC_INVALID_DATASET")
        self._rising = rising
        self.clock, self.before_io = clock, before_io
        self._lock = threading.RLock()
        self._config = PaperMarketConfig.load()
        self._created = self._now() - timedelta(minutes=1)
        if self._created.date() != self._now().date():
            raise ValueError("SYNTHETIC_FIRST_DAY_WINDOW")
        self._cash = Decimal("100000")
        self._holdings = {}
        self._orders = {}
        self._activities = []
        self._delete_requested = set()
        self._delete_fault = None
        self._post_fault = None
        self._counts = dict(requests=0, gets=0, posts=0, deletes=0, fills=0)

    def _now(self):
        value = self.clock.now()
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("SYNTHETIC_UTC_REQUIRED")
        return value.astimezone(UTC)

    def _bounds(self, now):
        try:
            return self._config.session_bounds(now)
        except ValueError:
            return None

    def _positions(self):
        result = []
        for symbol, (quantity, basis) in sorted(self._holdings.items()):
            if quantity == 0:
                continue
            committed = sum(
                (
                    Decimal(o["qty"]) - Decimal(o["filled_qty"])
                    for o in self._orders.values()
                    if o["symbol"] == symbol
                    and o["side"] == "sell"
                    and o["status"] not in _TERMINAL
                ),
                Decimal(0),
            )
            result.append(
                dict(
                    asset_id=ASSETS[symbol],
                    asset_class="us_equity",
                    symbol=symbol,
                    side="long",
                    qty=_text(quantity),
                    qty_available=_text(max(Decimal(0), quantity - committed)),
                    # Provider display average is bounded independently of its
                    # authoritative raw cost basis, which is never rebuilt.
                    avg_entry_price=_text(
                        (basis / quantity).quantize(_MONEY_UNIT, rounding=ROUND_HALF_EVEN)
                    ),
                    cost_basis=_text(basis),
                    market_value=_text(quantity * Decimal("15")),
                    current_price="15",
                )
            )
        return result

    def _equity(self):
        return self._cash + sum(
            (quantity * Decimal("15") for quantity, _ in self._holdings.values()),
            Decimal(0),
        )

    def evidence(self):
        """Safe aggregate evidence only: no private UUIDs, keys or raw bodies."""
        with self._lock, localcontext() as arithmetic:
            arithmetic.prec = 80
            return self._counts | dict(
                cash=_text(self._cash),
                equity=_text(self._equity()),
                positions={
                    symbol: _text(values[0])
                    for symbol, values in self._holdings.items()
                    if values[0] > 0
                },
                orders=len(self._orders),
                activities=len(self._activities),
                openOrders=sum(o["status"] not in _TERMINAL for o in self._orders.values()),
                canceledOrders=sum(o["status"] == "canceled" for o in self._orders.values()),
                filledOrders=sum(o["status"] == "filled" for o in self._orders.values()),
            )

    def control(self, action):
        with self._lock, localcontext() as arithmetic:
            arithmetic.prec = 80
            if action in {"fill-next", "partial-fill-next"}:
                order = next(
                    (o for o in self._orders.values() if o["status"] not in _TERMINAL), None
                )
                if order is None:
                    raise ValueError("SYNTHETIC_NO_OPEN_ORDER")
                remaining = Decimal(order["qty"]) - Decimal(order["filled_qty"])
                quantity = remaining if action == "fill-next" else remaining / 2
                if quantity != quantity.quantize(Decimal(".000000001")):
                    raise ValueError("SYNTHETIC_PARTIAL_PRECISION")
                self._fill(order, quantity)
            elif action == "cancel-terminal":
                order = next(
                    (
                        o
                        for o in self._orders.values()
                        if o["id"] in self._delete_requested and o["status"] not in _TERMINAL
                    ),
                    None,
                )
                if order is None:
                    raise ValueError("SYNTHETIC_NO_CANCEL_REQUEST")
                order.update(
                    status="canceled",
                    updated_at=_stamp(self._now()),
                    canceled_at=_stamp(self._now()),
                )
            elif action in {"rate-limit-next-delete", "timeout-next-delete"}:
                self._delete_fault = action
            elif action == "timeout-next-submit":
                self._post_fault = action
            else:
                raise ValueError("SYNTHETIC_UNKNOWN_CONTROL")
            return self.evidence()

    def _fill(self, order, quantity):
        symbol, side, price = order["symbol"], order["side"], Decimal(order["limit_price"])
        held, basis = self._holdings.get(symbol, (Decimal(0), Decimal(0)))
        if side == "sell" and quantity > held:
            raise ValueError("SYNTHETIC_INSUFFICIENT_POSITION")
        cost = quantity * price
        if side == "buy":
            if cost > self._cash:
                raise ValueError("SYNTHETIC_INSUFFICIENT_CASH")
            self._cash -= cost
            self._holdings[symbol] = held + quantity, basis + cost
        else:
            self._cash += cost
            allocated = (
                basis
                if quantity == held
                else (basis * quantity / held).quantize(
                    _MONEY_UNIT,
                    rounding=ROUND_HALF_EVEN,
                )
            )
            self._holdings[symbol] = held - quantity, basis - allocated
        filled = Decimal(order["filled_qty"]) + quantity
        remaining = Decimal(order["qty"]) - filled
        stamp = _stamp(self._now())
        order.update(
            filled_qty=_text(filled),
            filled_avg_price=_text(price),
            status="filled" if remaining == 0 else "partially_filled",
            updated_at=stamp,
        )
        if remaining == 0:
            order["filled_at"] = stamp
        self._activities.append(
            dict(
                id=f"synthetic::fill-{len(self._activities) + 1:06d}",
                activity_type="FILL",
                order_id=order["id"],
                symbol=symbol,
                side=side,
                qty=_text(quantity),
                price=_text(price),
                cum_qty=_text(filled),
                leaves_qty=_text(remaining),
                transaction_time=stamp,
                created_at=stamp,
                type="fill" if remaining == 0 else "partial_fill",
            )
        )
        self._counts["fills"] += 1

    def handle(self, request: httpx.Request) -> httpx.Response:
        # No socket is ever opened by this handler. Fail before callbacks for
        # arbitrary hosts, schemes, duplicate parameters or unexpected methods.
        if (
            request.url.scheme != "https"
            or request.url.port not in (None, 443)
            or request.url.host not in {_PAPER, _DATA}
            or request.method not in {"GET", "POST", "DELETE"}
            or len(request.url.params.multi_items()) != len(dict(request.url.params))
        ):
            raise AssertionError("SYNTHETIC_UNEXPECTED_REQUEST")
        if self.before_io is not None:
            self.before_io(request)
        with self._lock, localcontext() as arithmetic:
            arithmetic.prec = 80
            self._counts["requests"] += 1
            self._counts[{"GET": "gets", "POST": "posts", "DELETE": "deletes"}[request.method]] += 1
            value = self._dispatch(request)
            return (
                value
                if isinstance(value, httpx.Response)
                else httpx.Response(200, json=copy.deepcopy(value))
            )

    def _dispatch(self, request):
        path, method, params = request.url.path, request.method, request.url.params
        now = self._now()
        stamp = _stamp(now)
        data_route = path in {"/v2/stocks/bars", "/v2/stocks/quotes/latest"}
        if request.url.host != (_DATA if data_route else _PAPER):
            raise AssertionError("SYNTHETIC_ROUTE_HOST")
        if path == "/v2/orders" and method == "POST":
            return self._submit(request, stamp)
        if path.startswith("/v2/orders/"):
            order = next(
                (o for o in self._orders.values() if o["id"] == path.rsplit("/", 1)[1]), None
            )
            if order is None:
                return httpx.Response(404, json={"code": 40410000})
            if method == "DELETE":
                fault, self._delete_fault = self._delete_fault, None
                if fault == "rate-limit-next-delete":
                    return httpx.Response(
                        429, headers={"Retry-After": "5"}, json={"code": 42910000}
                    )
                if order["status"] in _TERMINAL:
                    return httpx.Response(422, json={"code": 42210000})
                self._delete_requested.add(order["id"])
                order.update(status="pending_cancel", updated_at=stamp)
                if fault == "timeout-next-delete":
                    raise httpx.ReadTimeout("SYNTHETIC_TIMEOUT", request=request)
                return httpx.Response(204)
            if method == "GET":
                return order
        if method != "GET":
            raise AssertionError("SYNTHETIC_UNEXPECTED_MUTATION")
        if path == "/v2/account":
            return dict(
                id=ACCOUNT_ID,
                currency="USD",
                status="ACTIVE",
                cash=_text(self._cash),
                equity=_text(self._equity()),
                portfolio_value=_text(self._equity()),
                account_blocked=False,
                trading_blocked=False,
                trade_suspended_by_user=False,
                created_at=_stamp(self._created),
            )
        if path == "/v2/clock":
            bounds = self._bounds(now)
            return dict(timestamp=stamp, is_open=bool(bounds and bounds[0] <= now < bounds[1]))
        if path == "/v2/positions":
            return self._positions()
        if path.startswith("/v2/positions/"):
            return next(
                (p for p in self._positions() if p["symbol"] == path.rsplit("/", 1)[1]),
                httpx.Response(404, json={"code": 40410000}),
            )
        if path == "/v2/orders:by_client_order_id":
            return self._orders.get(
                params["client_order_id"], httpx.Response(404, json={"code": 40410000})
            )
        if path == "/v2/orders":
            return self._history(
                list(self._orders.values()), params, "submitted_at", "limit", "after_order_id"
            )
        if path == "/v2/account/activities":
            return self._history(self._activities, params, "created_at", "page_size", "page_token")
        if path == "/v2/account/portfolio/history":
            # Opening observation is available only for a real UTC boundary
            # after synthetic account creation, never fabricated before birth.
            start, end = _time(params["start"]), _time(params["end"])
            if (
                params.get("timeframe") != "1Min"
                or end - start != timedelta(minutes=1)
                or end > now
                or end.time().replace(tzinfo=None) != datetime.min.time()
                or self._created >= end
            ):
                return dict(timeframe="1Min", timestamp=[], equity=[])
            before = sum(
                (
                    Decimal(a["qty"])
                    * (Decimal("15") - Decimal(a["price"]))
                    * (1 if a["side"] == "buy" else -1)
                    for a in self._activities
                    if _time(a["created_at"]) < end
                ),
                Decimal(0),
            )
            opening = Decimal("100000") + before
            # Provider history is a JSON numeric cent observation. Serialize
            # Decimal directly; never round it through a binary float.
            opening = opening.quantize(Decimal(".01"))
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=(
                    '{"timeframe":"1Min","timestamp":['
                    + str(int(start.timestamp()))
                    + '],"equity":['
                    + _text(opening)
                    + "]}"
                ).encode(),
            )
        if path == "/v2/calendar":
            day = datetime.fromisoformat(params.get("start", now.date().isoformat())).date()
            end = datetime.fromisoformat(params.get("end", day.isoformat())).date()
            if end < day or (end - day).days > 366:
                raise AssertionError("SYNTHETIC_CALENDAR_RANGE")
            rows = []
            while day <= end:
                moment = datetime.combine(day, datetime.min.time(), self._config.zone).astimezone(
                    UTC
                )
                bounds = self._bounds(moment)
                if bounds:
                    rows.append(
                        dict(
                            date=day.isoformat(),
                            open=bounds[0].astimezone(self._config.zone).strftime("%H:%M"),
                            close=bounds[1].astimezone(self._config.zone).strftime("%H:%M"),
                        )
                    )
                day += timedelta(days=1)
            return rows
        if path == "/v2/assets" or path.startswith("/v2/assets/"):
            symbols = list(ASSETS) if path == "/v2/assets" else [path.rsplit("/", 1)[1]]
            rows = [
                dict(
                    id=ASSETS[s],
                    symbol=s,
                    **{"class": "us_equity"},
                    status="active",
                    tradable=True,
                    fractionable=True,
                )
                for s in symbols
                if s in ASSETS
            ]
            return rows if path == "/v2/assets" else rows[0] if rows else httpx.Response(404)
        if path == "/v2/stocks/quotes/latest":
            if params.get("feed", "iex") != "iex":
                raise AssertionError("SYNTHETIC_FEED")
            return dict(
                quotes={s: dict(bp="14.99", ap="15.00", t=stamp) for s in self._symbols(params)}
            )
        if path == "/v2/stocks/bars":
            if params.get("timeframe") != "15Min" or params.get("feed") != "iex":
                raise AssertionError("SYNTHETIC_BAR_REQUEST")
            start, end = _time(params["start"]), _time(params["end"])
            bounds = self._bounds(now)
            bars = []
            if bounds:
                point = bounds[0]
                while point <= min(
                    end, bounds[1] - timedelta(minutes=15), now - timedelta(minutes=15)
                ):
                    if point >= start:
                        bars.append(
                            dict(
                                t=_stamp(point),
                                o="15",
                                h="16",
                                l="13",
                                c="14" if self._rising and not bars else "15",
                                v=100,
                            )
                        )
                    point += timedelta(minutes=15)
            return dict(
                bars={s: bars[: int(params.get("limit", "1000"))] for s in self._symbols(params)},
                next_page_token=None,
            )
        raise AssertionError("SYNTHETIC_UNEXPECTED_ROUTE")

    @staticmethod
    def _symbols(params):
        symbols = params["symbols"].split(",")
        if not symbols or any(s not in ASSETS for s in symbols):
            raise AssertionError("SYNTHETIC_SYMBOL")
        return symbols

    @staticmethod
    def _history(rows, params, time_key, size_key, cursor_key):
        direction = params.get("direction", "desc")
        if direction not in {"asc", "desc"}:
            raise AssertionError("SYNTHETIC_HISTORY_DIRECTION")
        rows = sorted(
            rows, key=lambda row: (_time(row[time_key]), row["id"]), reverse=direction == "desc"
        )
        cursor = params.get(cursor_key)
        if cursor:
            at = next((i for i, row in enumerate(rows) if row["id"] == cursor), None)
            if at is None:
                raise AssertionError("SYNTHETIC_HISTORY_CURSOR")
            rows = rows[at + 1 :]
        if params.get("after"):
            rows = [r for r in rows if _time(r[time_key]) >= _time(params["after"])]
        if params.get("until"):
            rows = [r for r in rows if _time(r[time_key]) <= _time(params["until"])]
        if params.get("date"):
            rows = [r for r in rows if _time(r[time_key]).date().isoformat() == params["date"]]
        status = params.get("status", "all")
        if status == "open":
            rows = [r for r in rows if r["status"] not in _TERMINAL]
        elif status == "closed":
            rows = [r for r in rows if r["status"] in _TERMINAL]
        elif status != "all":
            raise AssertionError("SYNTHETIC_HISTORY_STATUS")
        size = int(params.get(size_key, "100"))
        if not 1 <= size <= (500 if size_key == "limit" else 100):
            raise AssertionError("SYNTHETIC_HISTORY_SIZE")
        return rows[:size]

    def _submit(self, request, stamp):
        body = json.loads(request.content)
        if not isinstance(body, dict):
            return httpx.Response(422, json={"code": 42210000})
        allowed = {
            "symbol",
            "qty",
            "side",
            "type",
            "time_in_force",
            "limit_price",
            "client_order_id",
            "order_class",
            "extended_hours",
        }
        if (
            set(body) - allowed
            or body.get("symbol") not in ASSETS
            or body.get("side") not in {"buy", "sell"}
            or body.get("type") != "limit"
            or body.get("time_in_force") != "day"
            or body.get("order_class") != "simple"
            or body.get("extended_hours", False) is not False
            or not isinstance(body.get("client_order_id"), str)
        ):
            return httpx.Response(422, json={"code": 42210000})
        client = body["client_order_id"]
        if client in self._orders:
            return httpx.Response(422, json={"code": 42210000})
        try:
            quantity, price = Decimal(body["qty"]), Decimal(body["limit_price"])
            if (
                not isinstance(body["qty"], str)
                or not isinstance(body["limit_price"], str)
                or not quantity.is_finite()
                or not price.is_finite()
                or quantity <= 0
                or price <= 0
                or quantity != quantity.quantize(Decimal(".000000001"))
                or price != price.quantize(Decimal(".01") if price >= 1 else Decimal(".0001"))
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return httpx.Response(422, json={"code": 42210000})
        bounds = self._bounds(self._now())
        if not bounds or not bounds[0] <= self._now() < bounds[1]:
            return httpx.Response(422, json={"code": 42210000})
        if body["side"] == "buy" and quantity * price > self._cash:
            return httpx.Response(422, json={"code": 42210000})
        if body["side"] == "sell":
            available = next(
                (
                    Decimal(p["qty_available"])
                    for p in self._positions()
                    if p["symbol"] == body["symbol"]
                ),
                Decimal(0),
            )
            if quantity > available:
                return httpx.Response(422, json={"code": 42210000})
        order = body | dict(
            id=str(uuid4()),
            status="new",
            filled_qty="0",
            filled_avg_price=None,
            created_at=stamp,
            submitted_at=stamp,
            updated_at=stamp,
            asset_id=ASSETS[body["symbol"]],
            asset_class="us_equity",
        )
        self._orders[client] = order
        if self._post_fault:
            self._post_fault = None
            raise httpx.ReadTimeout("SYNTHETIC_TIMEOUT", request=request)
        return order
