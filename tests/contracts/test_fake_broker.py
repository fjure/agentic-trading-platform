"""PTV-08 BrokerAdapter contract and deterministic recovery fixtures."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.brokers.fake import (
    CapabilityError,
    FakeBrokerAdapter,
    FakeFault,
    IdempotencyConflict,
    RateLimitError,
)
from packages.domain.models import BrokerCapabilities, Capability, OrderIntent, OrderStatus
from packages.domain.ports import BrokerAdapter

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
TRACE = "a" * 32


class FrozenClock:
    def now(self) -> datetime:
        return NOW


class SeededIds:
    def new_id(self, namespace: str, seed: str | None = None) -> str:
        return f"{namespace}:{seed}"


def capabilities(**changes: object) -> BrokerCapabilities:
    return replace(
        BrokerCapabilities(
            broker_id="fake",
            instrument_id="spy",
            version=1,
            paper=Capability.CONFIRMED,
            regular_session=Capability.CONFIRMED,
            continuous_session=Capability.UNAVAILABLE,
            fractional=Capability.UNAVAILABLE,
            market_order=Capability.CONFIRMED,
            limit_order=Capability.CONFIRMED,
            quantity_step=Decimal("1"),
            price_tick=Decimal("0.01"),
        ),
        **changes,
    )


def broker(
    *,
    cap: BrokerCapabilities | None = None,
    open_now: bool = True,
    holdings: dict[str, Decimal] | None = None,
) -> FakeBrokerAdapter:
    return FakeBrokerAdapter(
        clock=FrozenClock(),
        ids=SeededIds(),
        capabilities={"spy": cap or capabilities()},
        session_open=lambda instrument, now: open_now and instrument == "spy" and now == NOW,
        holdings=holdings,
    )


def intent(client_id: str = "client-1", *, side: str = "BUY", quantity: str = "2") -> OrderIntent:
    return OrderIntent(
        id=f"intent:{client_id}",
        proposal_id="proposal-1",
        risk_decision_id="risk-1",
        client_order_id=client_id,
        instrument_id="spy",
        side=side,
        order_type="MARKET",
        quantity=Decimal(quantity),
        capability_version=1,
        created_at=NOW,
        trace_id=TRACE,
    )


def test_broker_port_and_immutable_idempotency() -> None:
    fake = broker()
    port: BrokerAdapter = fake
    first = port.submit(intent())
    assert first.status is OrderStatus.ACKNOWLEDGED
    assert first.broker_order_id == "fake-broker-order:client-1"
    assert port.submit(intent()) == first
    assert fake.accepted_count == 1
    with pytest.raises(IdempotencyConflict, match="payload mismatch"):
        port.submit(replace(intent(), quantity=Decimal("3")))
    assert port.order_status("missing") is None
    assert port.reports("missing") == ()
    assert len(port.reports("client-1")) == 1


@pytest.mark.parametrize(
    ("cap", "changed_intent"),
    [
        (capabilities(paper=Capability.UNKNOWN), intent()),
        (capabilities(market_order=Capability.UNKNOWN), intent()),
        (capabilities(quantity_step=None), intent()),
        (
            capabilities(fractional=Capability.UNKNOWN, quantity_step=Decimal("0.1")),
            intent(quantity="1.5"),
        ),
        (capabilities(), replace(intent(), capability_version=2)),
        (capabilities(), intent(quantity="1.5")),
        (
            capabilities(limit_order=Capability.UNKNOWN),
            replace(intent(), order_type="LIMIT", limit_price=Decimal("10.00")),
        ),
        (
            capabilities(price_tick=Decimal("0.05")),
            replace(intent(), order_type="LIMIT", limit_price=Decimal("10.01")),
        ),
        (capabilities(regular_session=Capability.UNKNOWN), intent()),
    ],
)
def test_t_rsk_003_unconfirmed_capabilities_fail_closed(
    cap: BrokerCapabilities, changed_intent: OrderIntent
) -> None:
    fake = broker(cap=cap)
    with pytest.raises(CapabilityError):
        fake.submit(changed_intent)
    assert fake.accepted_count == 0


def test_closed_session_unknown_instrument_and_short_sale_rejected() -> None:
    closed = broker(open_now=False)
    with pytest.raises(CapabilityError, match="session"):
        closed.submit(intent())
    fake = broker()
    with pytest.raises(CapabilityError, match="short sale"):
        fake.submit(intent(side="SELL"))
    with pytest.raises(CapabilityError, match="instrument"):
        fake.capabilities("absent")


def test_capability_identity_must_match_key_and_fake_broker() -> None:
    with pytest.raises(CapabilityError, match="identity"):
        broker(cap=replace(capabilities(), instrument_id="qqq"))
    with pytest.raises(CapabilityError, match="identity"):
        broker(cap=replace(capabilities(), broker_id="other"))


def test_t_rsk_009_timeout_reconnect_and_no_blind_resubmit() -> None:
    fake = broker()
    fake.inject_once(FakeFault.TIMEOUT_AFTER_ACCEPT)
    with pytest.raises(TimeoutError):
        fake.submit(intent())
    assert fake.accepted_count == 1
    assert fake.order_status("client-1").status is OrderStatus.UNKNOWN
    assert fake.submit(intent()).status is OrderStatus.UNKNOWN
    fake.disconnect()
    with pytest.raises(ConnectionError):
        fake.order_status("client-1")
    fake.reconnect()
    recovered = fake.reconcile("client-1", "fake-broker-order:client-1")
    assert recovered.status is OrderStatus.ACKNOWLEDGED
    assert fake.by_broker_id(recovered.broker_order_id) == recovered
    assert fake.submit(intent()) == recovered
    assert fake.accepted_count == 1
    with pytest.raises(IdempotencyConflict, match="disagree"):
        fake.reconcile("client-1", "wrong")


def test_unknown_remains_unresolved_until_authoritative_evidence() -> None:
    fake = broker()
    fake.inject_once(FakeFault.UNKNOWN_AFTER_ACCEPT)
    assert fake.submit(intent()).status is OrderStatus.UNKNOWN
    assert fake.reconcile("client-1").status is OrderStatus.UNKNOWN
    assert fake.reports("client-1") == ()
    assert fake.resolve_unknown("client-1").status is OrderStatus.ACKNOWLEDGED
    assert len(fake.reports("client-1")) == 1
    assert fake.accepted_count == 1


def test_rate_limit_is_definitive_preacceptance() -> None:
    fake = broker()
    fake.inject_once(FakeFault.RATE_LIMIT)
    with pytest.raises(RateLimitError):
        fake.submit(intent())
    assert fake.accepted_count == 0
    assert fake.order_status("client-1") is None
    assert fake.submit(intent()).status is OrderStatus.ACKNOWLEDGED


def test_t_rsk_010_t_run_002_partial_duplicate_out_of_order_and_cancel_fill_race() -> None:
    fake = broker()
    fake.submit(intent())
    first = fake.fill(
        "client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="fill-1"
    )
    assert first.status is OrderStatus.PARTIALLY_FILLED
    assert fake.holding("spy") == Decimal("1")
    assert (
        fake.fill(
            "client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="fill-1"
        )
        == first
    )
    assert (
        fake.fill(
            "client-1", cumulative_quantity=Decimal("0.5"), price=Decimal("100"), event_id="stale"
        )
        == first
    )
    fake.cancel("client-1", event_id="cancel-request")
    fake.cancel("client-1", event_id="cancel-ack", confirmed=True)
    late = fake.fill(
        "client-1", cumulative_quantity=Decimal("2"), price=Decimal("102"), event_id="fill-2"
    )
    assert late.status is OrderStatus.FILLED
    assert late.filled_quantity == Decimal("2")
    assert fake.holding("spy") == Decimal("2")
    assert fake.reports("client-1")[-1].average_fill_price == Decimal("101")
    assert [r.broker_event_id for r in fake.reports("client-1")] == [
        "fake-broker-order:client-1:accepted",
        "fill-1",
        "cancel-request",
        "cancel-ack",
        "fill-2",
    ]
    assert fake.accepted_count == 1


def test_sell_closes_only_confirmed_long_and_reserves_open_close_quantity() -> None:
    fake = broker()
    fake.submit(intent())
    fake.fill(
        "client-1", cumulative_quantity=Decimal("2"), price=Decimal("100"), event_id="buy-fill"
    )
    fake.submit(intent("close-1", side="SELL", quantity="1"))
    with pytest.raises(CapabilityError, match="oversubscribed"):
        fake.submit(intent("close-2", side="SELL", quantity="2"))
    fake.fill(
        "close-1", cumulative_quantity=Decimal("1"), price=Decimal("101"), event_id="sell-fill"
    )
    assert fake.holding("spy") == Decimal("1")
    fake.submit(intent("close-2", side="SELL", quantity="1"))


def test_cancel_confirmation_before_partial_late_fill_keeps_filled_quantity() -> None:
    fake = broker()
    fake.submit(intent())
    fake.cancel("client-1", event_id="cancel-ack", confirmed=True)
    late = fake.fill(
        "client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="late-fill"
    )
    assert late.status is OrderStatus.CANCELED
    assert late.filled_quantity == Decimal("1")
    assert fake.holding("spy") == Decimal("1")
    assert len(fake.reports("client-1")) == 3
    assert (
        fake.fill(
            "client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="late-fill"
        )
        == late
    )
    assert fake.holding("spy") == Decimal("1")


def test_broker_event_id_cannot_be_reused_for_another_order() -> None:
    fake = broker()
    fake.submit(intent())
    fake.submit(intent("client-2"))
    fake.fill("client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="one")
    with pytest.raises(IdempotencyConflict, match="payload or action"):
        fake.fill(
            "client-2", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="one"
        )
    assert fake.order_status("client-2").filled_quantity == Decimal("0")


def test_same_event_id_rejects_changed_payload_action_and_accepted_id() -> None:
    fake = broker()
    fake.submit(intent())
    fake.fill("client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="one")
    for quantity, price in (("1", "101"), ("2", "100")):
        with pytest.raises(IdempotencyConflict, match="payload or action"):
            fake.fill(
                "client-1",
                cumulative_quantity=Decimal(quantity),
                price=Decimal(price),
                event_id="one",
            )
    with pytest.raises(IdempotencyConflict, match="payload or action"):
        fake.cancel("client-1", event_id="one")
    with pytest.raises(IdempotencyConflict, match="payload or action"):
        fake.fill(
            "client-1",
            cumulative_quantity=Decimal("2"),
            price=Decimal("100"),
            event_id="fake-broker-order:client-1:accepted",
        )
    fake.cancel("client-1", event_id="cancel", confirmed=False)
    with pytest.raises(IdempotencyConflict, match="payload or action"):
        fake.cancel("client-1", event_id="cancel", confirmed=True)
    with pytest.raises(ValueError, match="nonempty"):
        fake.fill("client-1", cumulative_quantity=Decimal("2"), price=Decimal("100"), event_id="")
    assert fake.order_status("client-1").filled_quantity == Decimal("1")


def test_nonterminating_weighted_average_is_unknown_without_rounding() -> None:
    fake = broker()
    fake.submit(intent(quantity="3"))
    fake.fill("client-1", cumulative_quantity=Decimal("1"), price=Decimal("100"), event_id="a")
    final = fake.fill(
        "client-1", cumulative_quantity=Decimal("3"), price=Decimal("101"), event_id="b"
    )
    assert final.filled_quantity == Decimal("3")
    assert fake.reports("client-1")[-1].average_fill_price is None


def test_38_digit_quantity_step_tick_and_fill_delta_stay_exact() -> None:
    tiny = Decimal("0.000000000000000001")
    before = Decimal("12345678901234567890.123456789012345677")
    final = Decimal("12345678901234567890.123456789012345678")
    fake = broker(
        cap=capabilities(fractional=Capability.CONFIRMED, quantity_step=tiny, price_tick=tiny)
    )
    order_intent = replace(
        intent(quantity=str(final)),
        order_type="LIMIT",
        limit_price=final,
    )
    fake.submit(order_intent)
    fake.fill("client-1", cumulative_quantity=before, price=Decimal("1"), event_id="first")
    assert fake.holding("spy") == before
    fake.fill("client-1", cumulative_quantity=final, price=Decimal("1"), event_id="last")
    assert fake.holding("spy") == final
    assert fake.order_status("client-1").filled_quantity == final
    assert fake.reports("client-1")[-1].average_fill_price == Decimal("1")


def test_holding_overflow_rejects_fill_before_state_change() -> None:
    maximum = Decimal("99999999999999999999.999999999999999999")
    tiny = Decimal("0.000000000000000001")
    fake = broker(
        cap=capabilities(fractional=Capability.CONFIRMED, quantity_step=tiny),
        holdings={"spy": maximum},
    )
    fake.submit(intent(quantity=str(tiny)))
    with pytest.raises(ValueError, match="outside NUMERIC"):
        fake.fill("client-1", cumulative_quantity=tiny, price=Decimal("1"), event_id="overflow")
    assert fake.holding("spy") == maximum
    assert fake.order_status("client-1").filled_quantity == Decimal("0")
    assert len(fake.reports("client-1")) == 1
