"""Deterministic, in-memory Paper broker fixture. No transport or credentials."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from fractions import Fraction

from packages.domain.models import (
    BrokerCapabilities,
    BrokerOrder,
    Capability,
    ExecutionReport,
    OrderIntent,
    OrderStatus,
    Reconciliation,
    decimal_value,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator


class FakeFault(StrEnum):
    TIMEOUT_AFTER_ACCEPT = "TIMEOUT_AFTER_ACCEPT"
    RATE_LIMIT = "RATE_LIMIT"
    UNKNOWN_AFTER_ACCEPT = "UNKNOWN_AFTER_ACCEPT"


class RateLimitError(RuntimeError):
    """A definitive, pre-acceptance limit; caller may back off and recheck gates."""


class CapabilityError(ValueError):
    """Unconfirmed or unsupported Paper capability."""


class IdempotencyConflict(ValueError):
    """A client order ID was reused with different immutable intent content."""


class FakeBrokerAdapter:
    """BrokerAdapter fixture with explicit capability and recovery controls.

    All state is process-local. Reconnect keeps this object's broker-side state; a
    new instance is a new broker and cannot prove nonacceptance of old orders.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        capabilities: dict[str, BrokerCapabilities],
        session_open: Callable[[str, datetime], bool],
        holdings: dict[str, Decimal] | None = None,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._capabilities = dict(capabilities)
        if any(
            key != value.instrument_id or value.broker_id != "fake"
            for key, value in self._capabilities.items()
        ):
            raise CapabilityError("capability identity does not match fake broker and instrument")
        self._session_open = session_open
        self._holdings = {key: decimal_value(value) for key, value in (holdings or {}).items()}
        if any(value < 0 for value in self._holdings.values()):
            raise ValueError("negative initial holdings")
        self._intents: dict[str, OrderIntent] = {}
        self._orders: dict[str, BrokerOrder] = {}
        self._broker_ids: dict[str, str] = {}
        self._reports: dict[str, list[ExecutionReport]] = {}
        self._filled_notional: dict[str, Fraction] = {}
        self._event_signatures: dict[str, tuple[object, ...]] = {}
        self._faults: list[FakeFault] = []
        self._ambiguous: set[str] = set()
        self._unresolved: set[str] = set()
        self._connected = True
        self.accepted_count = 0

    def capabilities(self, instrument_id: str) -> BrokerCapabilities:
        try:
            return self._capabilities[instrument_id]
        except KeyError as exc:
            raise CapabilityError("instrument capability is unconfirmed") from exc

    def inject_once(self, fault: FakeFault) -> None:
        if not isinstance(fault, FakeFault):
            raise ValueError("unknown fake fault")
        self._faults.append(fault)

    def submit(self, intent: OrderIntent) -> BrokerOrder:
        with localcontext() as context:
            context.prec = 80
            return self._submit_checked(intent)

    def _submit_checked(self, intent: OrderIntent) -> BrokerOrder:
        if not self._connected:
            raise ConnectionError("fake broker disconnected; reconcile before retry")
        previous = self._intents.get(intent.client_order_id)
        if previous is not None:
            if previous != intent:
                raise IdempotencyConflict("client order ID payload mismatch")
            return self.order_status(intent.client_order_id)  # type: ignore[return-value]
        self._validate(intent)
        fault = self._faults.pop(0) if self._faults else None
        if fault is FakeFault.RATE_LIMIT:
            raise RateLimitError("fake broker rate limited before acceptance")
        client_id = intent.client_order_id
        broker_id = self._ids.new_id("fake-broker-order", client_id)
        if broker_id in self._broker_ids:
            raise ValueError("broker ID generator collision")
        accepted_event_id = f"{broker_id}:accepted"
        if accepted_event_id in self._event_signatures:
            raise ValueError("accepted event ID collision")
        order = BrokerOrder(
            id=self._ids.new_id("fake-order", client_id),
            intent_id=intent.id,
            client_order_id=client_id,
            broker_order_id=broker_id,
            status=OrderStatus.ACKNOWLEDGED,
            requested_quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            version=1,
            updated_at=utc_time(self._clock.now()),
            trace_id=intent.trace_id,
        )
        self._intents[client_id] = intent
        self._orders[client_id] = order
        self._broker_ids[broker_id] = client_id
        self._reports[client_id] = []
        self._filled_notional[client_id] = Fraction(0)
        self.accepted_count += 1
        self._event_signatures[accepted_event_id] = ("accepted", client_id, intent)
        self._report(client_id, order.status, event_id=accepted_event_id)
        if fault in (FakeFault.TIMEOUT_AFTER_ACCEPT, FakeFault.UNKNOWN_AFTER_ACCEPT):
            self._ambiguous.add(client_id)
        if fault is FakeFault.UNKNOWN_AFTER_ACCEPT:
            self._unresolved.add(client_id)
        if fault is FakeFault.TIMEOUT_AFTER_ACCEPT:
            raise TimeoutError("fake broker timed out after possible acceptance")
        return self.order_status(client_id)  # type: ignore[return-value]

    def order_status(self, client_order_id: str) -> BrokerOrder | None:
        if not self._connected:
            raise ConnectionError("fake broker disconnected")
        order = self._orders.get(client_order_id)
        if order is None:
            return None
        if client_order_id in self._ambiguous:
            return replace(order, status=OrderStatus.UNKNOWN, broker_order_id=None)
        return order

    def reports(self, client_order_id: str) -> tuple[ExecutionReport, ...]:
        if not self._connected:
            raise ConnectionError("fake broker disconnected")
        if client_order_id in self._ambiguous:
            return ()
        return tuple(self._reports.get(client_order_id, ()))

    def by_broker_id(self, broker_order_id: str) -> BrokerOrder | None:
        """Read-only broker-ID lookup for recovery; it never submits."""
        if not self._connected:
            raise ConnectionError("fake broker disconnected")
        client_id = self._broker_ids.get(broker_order_id)
        return self._orders.get(client_id) if client_id is not None else None

    def reconcile(
        self, client_order_id: str, broker_order_id: str | None = None
    ) -> BrokerOrder | None:
        """Expose authoritative local fixture state after checking both known IDs."""
        if not self._connected:
            raise ConnectionError("fake broker disconnected")
        if broker_order_id is not None and self._broker_ids.get(broker_order_id) != client_order_id:
            raise IdempotencyConflict("broker and client order IDs disagree")
        if client_order_id not in self._unresolved:
            self._ambiguous.discard(client_order_id)
        return self.order_status(client_order_id)

    def resolve_unknown(self, client_order_id: str) -> BrokerOrder:
        """Fixture control simulating later authoritative broker evidence."""
        order = self._require_order(client_order_id)
        self._unresolved.discard(client_order_id)
        self._ambiguous.discard(client_order_id)
        return order

    def holding(self, instrument_id: str) -> Decimal:
        return self._holdings.get(instrument_id, Decimal("0"))

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def fill(
        self,
        client_order_id: str,
        *,
        cumulative_quantity: Decimal,
        price: Decimal,
        event_id: str,
    ) -> BrokerOrder:
        with localcontext() as context:
            context.prec = 80
            return self._fill_checked(
                client_order_id,
                cumulative_quantity=cumulative_quantity,
                price=price,
                event_id=event_id,
            )

    def _fill_checked(
        self,
        client_order_id: str,
        *,
        cumulative_quantity: Decimal,
        price: Decimal,
        event_id: str,
    ) -> BrokerOrder:
        order = self._require_order(client_order_id)
        quantity = decimal_value(cumulative_quantity)
        price = decimal_value(price)
        signature = ("fill", client_order_id, quantity, price)
        if self._seen_event(event_id, signature):
            return order
        if quantity < 0 or quantity > order.requested_quantity or price <= 0:
            raise ValueError("invalid fill")
        delta = quantity - order.filled_quantity
        instrument_id = self._intents[client_order_id].instrument_id
        if self._intents[client_order_id].side == "SELL" and delta > self.holding(instrument_id):
            raise ValueError("sell fill exceeds long holdings")
        if quantity <= order.filled_quantity:
            self._event_signatures[event_id] = signature
            return order  # stale/out-of-order cumulative update
        exact_notional = self._filled_notional[client_order_id] + Fraction(delta) * Fraction(price)
        average = self._exact_average(exact_notional, quantity)
        if self._intents[client_order_id].side == "BUY":
            new_holding = decimal_value(self.holding(instrument_id) + delta)
        else:
            new_holding = decimal_value(self.holding(instrument_id) - delta)
        status = (
            OrderStatus.FILLED
            if quantity == order.requested_quantity
            else OrderStatus.CANCELED
            if order.status is OrderStatus.CANCELED
            else OrderStatus.PARTIALLY_FILLED
        )
        updated = self._update(client_order_id, status, quantity)
        self._filled_notional[client_order_id] = exact_notional
        self._event_signatures[event_id] = signature
        self._holdings[instrument_id] = new_holding
        self._report(client_order_id, status, event_id=event_id, price=average)
        return updated

    def cancel(
        self, client_order_id: str, *, event_id: str, confirmed: bool = False
    ) -> BrokerOrder:
        order = self._require_order(client_order_id)
        signature = ("cancel", client_order_id, confirmed)
        if self._seen_event(event_id, signature):
            return order
        self._event_signatures[event_id] = signature
        if order.status is OrderStatus.FILLED:
            return order
        status = OrderStatus.CANCELED if confirmed else OrderStatus.CANCEL_PENDING
        if order.status is OrderStatus.CANCELED and not confirmed:
            return order
        updated = self._update(client_order_id, status, order.filled_quantity)
        self._report(client_order_id, status, event_id=event_id)
        return updated

    def _seen_event(self, event_id: str, signature: tuple[object, ...]) -> bool:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("broker event ID must be nonempty")
        previous = self._event_signatures.get(event_id)
        if previous is not None and previous != signature:
            raise IdempotencyConflict("broker event ID payload or action mismatch")
        return previous is not None

    @staticmethod
    def _exact_average(notional: Fraction, quantity: Decimal) -> Decimal | None:
        """Return an exact NUMERIC(38,18) average, otherwise leave it unknown."""
        ratio = notional / Fraction(quantity)
        denominator = ratio.denominator
        twos = fives = 0
        while denominator % 2 == 0:
            denominator //= 2
            twos += 1
        while denominator % 5 == 0:
            denominator //= 5
            fives += 1
        scale = max(twos, fives)
        if denominator != 1 or scale > 18:
            return None
        scaled = ratio.numerator * (10**scale // ratio.denominator)
        digits = str(scaled)
        if scale:
            digits = digits.zfill(scale + 1)
            digits = f"{digits[:-scale]}.{digits[-scale:]}"
        try:
            return decimal_value(digits)
        except ValueError:
            return None

    def _validate(self, intent: OrderIntent) -> None:
        capability = self.capabilities(intent.instrument_id)
        fractional = intent.quantity % 1 != 0
        if (
            capability.paper is not Capability.CONFIRMED
            or capability.version != intent.capability_version
        ):
            raise CapabilityError("Paper capability or version is unconfirmed")
        if (
            intent.order_type == "MARKET" and capability.market_order is not Capability.CONFIRMED
        ) or (intent.order_type == "LIMIT" and capability.limit_order is not Capability.CONFIRMED):
            raise CapabilityError("order type is unconfirmed")
        if capability.quantity_step is None or intent.quantity % capability.quantity_step != 0:
            raise CapabilityError("quantity step is unconfirmed or violated")
        if fractional and capability.fractional is not Capability.CONFIRMED:
            raise CapabilityError("fractional quantity is unconfirmed")
        if intent.order_type == "LIMIT" and (
            capability.price_tick is None or intent.limit_price % capability.price_tick != 0
        ):
            raise CapabilityError("price tick is unconfirmed or violated")
        if (
            capability.regular_session is not Capability.CONFIRMED
            and capability.continuous_session is not Capability.CONFIRMED
        ):
            raise CapabilityError("session capability is unconfirmed")
        if self._session_open(intent.instrument_id, utc_time(self._clock.now())) is not True:
            raise CapabilityError("session is closed or unconfirmed")
        if intent.side == "SELL":
            outstanding = Decimal("0")
            for prior in self._intents.values():
                if prior.instrument_id != intent.instrument_id or prior.side != "SELL":
                    continue
                prior_order = self._orders[prior.client_order_id]
                if prior_order.status not in (
                    OrderStatus.CANCELED,
                    OrderStatus.REJECTED,
                    OrderStatus.FILLED,
                ):
                    outstanding += prior.quantity - prior_order.filled_quantity
            if intent.quantity > self.holding(intent.instrument_id) - outstanding:
                raise CapabilityError("short sale or oversubscribed close is forbidden")

    def _require_order(self, client_order_id: str) -> BrokerOrder:
        try:
            return self._orders[client_order_id]
        except KeyError as exc:
            raise ValueError("unknown client order ID") from exc

    def _update(self, client_id: str, status: OrderStatus, filled: Decimal) -> BrokerOrder:
        old = self._orders[client_id]
        new = replace(
            old,
            status=status,
            filled_quantity=filled,
            version=old.version + 1,
            updated_at=utc_time(self._clock.now()),
        )
        self._orders[client_id] = new
        return new

    def _report(
        self,
        client_id: str,
        status: OrderStatus,
        *,
        event_id: str,
        price: Decimal | None = None,
    ) -> None:
        order = self._orders[client_id]
        now = utc_time(self._clock.now())
        self._reports[client_id].append(
            ExecutionReport(
                id=self._ids.new_id("fake-report", event_id),
                order_id=order.id,
                status=status,
                requested_quantity=order.requested_quantity,
                filled_quantity=order.filled_quantity,
                reconciliation=Reconciliation.CURRENT,
                occurred_at=now,
                received_at=now,
                trace_id=order.trace_id,
                broker_event_id=event_id,
                average_fill_price=price,
            )
        )
