"""Pure, exact-Decimal risk evaluation over facts read under an account lock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from packages.domain.models import (
    BrokerCapabilities,
    Market,
    Mode,
    Quality,
    SafetyState,
    Target,
    TradeProposal,
    decimal_value,
    utc_time,
)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    config_version: str = "paper-risk-v1"
    max_positions_per_instrument: int = 1
    max_positions_total: int = 2
    max_position_fraction: Decimal = Decimal("0.10")
    max_daily_loss_fraction: Decimal = Decimal("0.01")
    max_spread_fraction: Decimal = Decimal("0.01")
    market_max_age_seconds: int = 120
    decision_ttl_seconds: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.config_version, str) or not self.config_version:
            raise ValueError("risk config version required")
        for name in (
            "max_positions_per_instrument",
            "max_positions_total",
            "market_max_age_seconds",
            "decision_ttl_seconds",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"invalid risk limit: {name}")
        for name in ("max_position_fraction", "max_daily_loss_fraction", "max_spread_fraction"):
            value = decimal_value(getattr(self, name))
            if not Decimal("0") < value <= Decimal("1"):
                raise ValueError(f"invalid risk fraction: {name}")
            object.__setattr__(self, name, value)
        if self.max_positions_per_instrument > 1 or self.max_positions_total > 2:
            raise ValueError("simulation position defaults cannot be relaxed")
        if self.max_position_fraction > Decimal("0.10") or self.max_daily_loss_fraction > Decimal(
            "0.01"
        ):
            raise ValueError("simulation money defaults cannot be relaxed")
        if (
            self.max_spread_fraction > Decimal("0.01")
            or self.market_max_age_seconds > 120
            or self.decision_ttl_seconds > 30
        ):
            raise ValueError("simulation evidence and TTL defaults cannot be relaxed")

    @classmethod
    def from_yaml(cls, path: Path) -> RiskLimits:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        required = {
            "version",
            "simulation",
            "max_positions_per_instrument",
            "max_positions_total",
            "max_position_fraction",
            "max_daily_loss_fraction",
            "max_spread_fraction",
            "market_max_age_seconds",
            "decision_ttl_seconds",
        }
        if not isinstance(raw, dict) or set(raw) != required or raw["simulation"] is not True:
            raise ValueError("incomplete or non-simulation risk config")
        return cls(
            config_version=raw["version"],
            max_positions_per_instrument=raw["max_positions_per_instrument"],
            max_positions_total=raw["max_positions_total"],
            max_position_fraction=raw["max_position_fraction"],
            max_daily_loss_fraction=raw["max_daily_loss_fraction"],
            max_spread_fraction=raw["max_spread_fraction"],
            market_max_age_seconds=raw["market_max_age_seconds"],
            decision_ttl_seconds=raw["decision_ttl_seconds"],
        )


@dataclass(frozen=True, slots=True)
class RiskFacts:
    now: datetime
    mode: Mode
    safety_state: SafetyState
    policy_version: int
    portfolio_version: int
    proposal_portfolio_version: int
    capability_version: int
    capability: BrokerCapabilities
    session_open: bool
    session_valid_until: datetime
    market_quality: Quality
    market_available_at: datetime
    market_price: Decimal
    spread: Decimal
    equity: Decimal
    cash: Decimal
    day_start_equity: Decimal
    realized_pnl_today: Decimal
    unrealized_pnl: Decimal
    instrument_exposure: Decimal
    gross_notional: Decimal
    reserved_notional: Decimal
    position_count_instrument: int
    position_count_total: int
    open_exit_quantity: Decimal
    held_quantity: Decimal
    reconciliation_current: bool
    market: Market = Market.US_EQUITY

    def __post_init__(self) -> None:
        for name in ("now", "session_valid_until", "market_available_at"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))
        for name in (
            "policy_version",
            "portfolio_version",
            "proposal_portfolio_version",
            "capability_version",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"missing version: {name}")
        for name in (
            "market_price",
            "spread",
            "equity",
            "cash",
            "day_start_equity",
            "realized_pnl_today",
            "unrealized_pnl",
            "instrument_exposure",
            "gross_notional",
            "reserved_notional",
            "open_exit_quantity",
            "held_quantity",
        ):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.market_price <= 0 or self.equity <= 0 or self.day_start_equity <= 0:
            raise ValueError("positive price and equity required")
        if (
            self.spread < 0
            or self.instrument_exposure < 0
            or self.cash < 0
            or self.gross_notional < 0
            or self.reserved_notional < 0
            or self.open_exit_quantity < 0
            or self.held_quantity < 0
        ):
            raise ValueError("negative exposure or spread")
        if any(
            type(value) is not int or value < 0
            for value in (self.position_count_instrument, self.position_count_total)
        ):
            raise ValueError("negative position count")
        if type(self.session_open) is not bool or type(self.reconciliation_current) is not bool:
            raise ValueError("session and reconciliation must be explicit booleans")
        if not isinstance(self.market, Market):
            raise ValueError("market must be explicit")


def evaluate(proposal: TradeProposal, facts: RiskFacts, limits: RiskLimits) -> tuple[str, ...]:
    """Return stable blocking codes. Exact arithmetic avoids context rounding."""
    reasons: list[str] = []

    def deny(condition: bool, code: str) -> None:
        if condition:
            reasons.append(code)

    deny(
        facts.mode is not Mode.PAPER or facts.safety_state is not SafetyState.RUNNING,
        "MODE_OR_SAFETY",
    )
    deny(not facts.reconciliation_current, "RECONCILIATION")
    deny(facts.proposal_portfolio_version != facts.portfolio_version, "PORTFOLIO_VERSION")
    deny(facts.capability_version != facts.capability.version, "CAPABILITY_VERSION")
    deny(facts.now < proposal.created_at or facts.now >= proposal.valid_until, "PROPOSAL_EXPIRED")
    deny(
        proposal.valid_until - proposal.created_at > timedelta(seconds=limits.decision_ttl_seconds),
        "PROPOSAL_TTL",
    )
    deny(not facts.session_open or facts.now >= facts.session_valid_until, "SESSION_CLOSED")
    deny(
        facts.market_quality is not Quality.VALID
        or facts.market_available_at > facts.now
        or facts.now - facts.market_available_at > timedelta(seconds=limits.market_max_age_seconds),
        "MARKET_STALE",
    )
    order_type = "LIMIT" if proposal.limit_price is not None else "MARKET"
    step = facts.capability.quantity_step
    fractional = proposal.quantity != proposal.quantity.to_integral_value()
    deny(
        facts.capability.instrument_id != proposal.instrument_id
        or not facts.capability.permits(
            order_type=order_type,
            fractional=fractional,
            regular_session=facts.market is Market.US_EQUITY,
        ),
        "BROKER_CAPABILITY",
    )
    with localcontext() as context:
        context.prec = 80
        deny(step is None or proposal.quantity % step != 0, "QUANTITY_STEP")
        if proposal.limit_price is not None:
            tick = facts.capability.price_tick
            deny(tick is None or proposal.limit_price % tick != 0, "PRICE_TICK")
        deny(facts.spread > facts.market_price * limits.max_spread_fraction, "SPREAD")
        if proposal.target is Target.LONG:
            notional = proposal.quantity * (
                proposal.limit_price or facts.market_price + facts.spread
            )
            deny(
                facts.position_count_instrument >= limits.max_positions_per_instrument,
                "INSTRUMENT_POSITION_LIMIT",
            )
            deny(facts.position_count_total >= limits.max_positions_total, "TOTAL_POSITION_LIMIT")
            deny(
                facts.instrument_exposure + notional > facts.equity * limits.max_position_fraction,
                "POSITION_NOTIONAL_LIMIT",
            )
            deny(notional > facts.cash - facts.reserved_notional, "CASH_LIMIT")
            daily_loss = -(facts.realized_pnl_today + facts.unrealized_pnl)
            deny(
                daily_loss >= facts.day_start_equity * limits.max_daily_loss_fraction,
                "DAILY_LOSS_LIMIT",
            )
        elif proposal.target is Target.FLAT:
            deny(
                proposal.quantity > facts.held_quantity - facts.open_exit_quantity, "EXIT_QUANTITY"
            )
        else:
            reasons.append("TARGET_UNSUPPORTED")
    return tuple(reasons)
