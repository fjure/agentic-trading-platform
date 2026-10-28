"""Decimal ledger for the versioned IEX next-eligible-open proxy model."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from packages.research.models import QualityStatus

from .dataset import FoldDataset
from .models import BacktestBlocked, ExperimentConfig, fingerprint
from .retrospective import RetrospectiveFoldDataset, RetrospectiveValidationContext
from .strategies import Target, decide

USD_SCALE = Decimal("0.00000001")
RETROSPECTIVE_VALUATION_METHOD = "raw-iex-close-retrospective-market-v1"


class SimulationBlocked(BacktestBlocked):
    pass


def money(value: Decimal) -> Decimal:
    return value.quantize(USD_SCALE, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class Decision:
    signal_at: datetime
    target: Target
    input_hash: str
    fill_target: datetime | None


@dataclass(frozen=True)
class Fill:
    at: datetime
    side: str
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal
    outcome_revision_id: UUID
    outcome_cutoff: datetime


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    cash: Decimal
    quantity: Decimal
    close: Decimal
    equity: Decimal
    outcome_revision_id: UUID
    outcome_cutoff: datetime


@dataclass(frozen=True)
class SimulationResult:
    strategy_id: str
    start_cash: Decimal
    decisions: tuple[Decision, ...]
    fills: tuple[Fill, ...]
    equity: tuple[EquityPoint, ...]
    valuation_method: str = "raw-iex-close-ex-post-300s-v1"

    @property
    def result_hash(self) -> str:
        return fingerprint(self)


def simulate(
    config: ExperimentConfig,
    dataset: FoldDataset | RetrospectiveFoldDataset,
    *,
    strategy_id: str | None = None,
    checkpoint: Callable[[], None] = lambda: None,
    validation_context: RetrospectiveValidationContext | None = None,
) -> SimulationResult:
    """Outcome bars are only consumed here; decide receives historical closes only."""
    if validation_context is not None:
        if type(validation_context) is not RetrospectiveValidationContext:
            raise ValueError("closed retrospective validation context required")
        validation_context.__post_init__()
        validation_context.manifest.__post_init__()
    return _simulate_bound(
        config,
        dataset,
        strategy_id=strategy_id,
        checkpoint=checkpoint,
        validation_context=validation_context,
    )


def _simulate_bound(
    config,
    dataset,
    *,
    strategy_id=None,
    checkpoint=lambda: None,
    validation_context=None,
) -> SimulationResult:
    """Private kernel entry; evaluate has revalidated the complete manifest once."""
    _validate_dataset(config, dataset, validation_context)
    strategy = strategy_id or config.strategy_id
    parameters = config.parameters if strategy == config.strategy_id else ()
    with localcontext() as ctx:
        ctx.prec = 38
        ctx.rounding = ROUND_HALF_EVEN
        return _simulate(config, dataset, strategy, parameters, checkpoint, validation_context)


def _validate_dataset(config, dataset, validation_context):
    if validation_context is None:
        if type(dataset) is not FoldDataset:
            raise ValueError(
                "Strict PIT requires its own fold dataset without retrospective context"
            )
    elif type(validation_context) is not RetrospectiveValidationContext:
        raise ValueError("closed retrospective validation context required")
    else:
        validation_context.validate(config, dataset)


def _outcome_cutoff(bar, validation_context):
    if validation_context is None:
        cutoff = bar.identity.bar_end + timedelta(seconds=300)
        if bar.available_at > cutoff or bar.ingested_at > cutoff:
            raise SimulationBlocked("BLOCKED_OUTCOME_DATA_INVALID", str(bar.revision_id))
        return cutoff
    # A simulated market boundary, never a historical local observation claim.
    return bar.identity.bar_end


def _simulate(
    config, dataset, strategy, parameters, checkpoint, validation_context
) -> SimulationResult:
    outcomes = {bar.identity.bar_start: bar for bar in dataset.outcome_bars}
    expected = dataset.expected_bar_starts
    if not expected or len(outcomes) != len(dataset.outcome_bars):
        raise SimulationBlocked("BLOCKED_DATA_MISSING")
    if tuple(sorted(set(expected))) != expected:
        raise ValueError("expected bars must be unique and chronological")
    steps = {step.signal_bar.identity.bar_start: step for step in dataset.steps}
    if set(steps) != set(expected) or len(steps) != len(dataset.steps):
        raise SimulationBlocked("BLOCKED_DECISION_DATA_MISSING")
    cash, quantity = money(config.start_cash), Decimal("0")
    pending: dict[datetime, Target] = {}
    fills, equity, decisions = [], [], []
    for start in expected:
        checkpoint()
        if not dataset.fold.test_start <= start < dataset.fold.test_end:
            raise SimulationBlocked("BLOCKED_BAR_OUTSIDE_FOLD", start.isoformat())
        bar = outcomes.get(start)
        if bar is None:
            raise SimulationBlocked("BLOCKED_OUTCOME_DATA_MISSING", start.isoformat())
        cutoff = _outcome_cutoff(bar, validation_context)
        if bar.quality != QualityStatus.VALID or bar.identity.instrument_id != config.instrument_id:
            raise SimulationBlocked("BLOCKED_OUTCOME_DATA_INVALID", str(bar.revision_id))
        target = pending.pop(start, None)
        if target == Target.LONG and quantity == 0:
            price = money(bar.open * (1 + config.slippage_bps / 10000))
            if price <= 0:
                raise SimulationBlocked("BLOCKED_INVALID_FILL_PRICE")
            available = cash - config.fee_usd
            amount = (
                max(available, Decimal("0")) / price / config.quantity_step
            ).to_integral_value(rounding=ROUND_FLOOR) * config.quantity_step
            # The fee and notional use the same fixed money scale as ledger debits.
            fee = money(config.fee_usd)
            notional = money(price * amount)
            if amount > 0 and notional + fee <= cash:
                cash = money(cash - notional - fee)
                quantity = amount
                fills.append(
                    Fill(
                        start,
                        "BUY",
                        amount,
                        price,
                        notional,
                        fee,
                        money(bar.open * config.slippage_bps / 10000 * amount),
                        bar.revision_id,
                        cutoff,
                    )
                )
        elif target == Target.FLAT and quantity > 0:
            price = money(bar.open * (1 - config.slippage_bps / 10000))
            notional, fee = money(price * quantity), money(config.fee_usd)
            if notional < fee:
                raise SimulationBlocked("BLOCKED_SELL_FEE_EXCEEDS_PROCEEDS", str(bar.revision_id))
            fills.append(
                Fill(
                    start,
                    "SELL",
                    quantity,
                    price,
                    notional,
                    fee,
                    money(bar.open * config.slippage_bps / 10000 * quantity),
                    bar.revision_id,
                    cutoff,
                )
            )
            cash, quantity = money(cash + notional - fee), Decimal("0")
        if cash < 0 or quantity < 0:
            raise AssertionError("simulation ledger invariant")
        equity.append(
            EquityPoint(
                bar.identity.bar_end,
                cash,
                quantity,
                bar.close,
                money(cash + quantity * bar.close),
                bar.revision_id,
                cutoff,
            )
        )
        step = steps[start]
        signal_at = step.signal_bar.identity.bar_end + timedelta(seconds=60)
        inputs = step.input_bars
        if (
            not inputs
            or inputs[-1] != step.signal_bar
            or any(
                b.identity.bar_end > step.signal_bar.identity.bar_end
                or (
                    validation_context is None
                    and (b.available_at > signal_at or b.ingested_at > signal_at)
                )
                or b.quality != QualityStatus.VALID
                or b.identity.instrument_id != config.instrument_id
                for b in inputs
            )
            or any(
                a.identity.bar_start >= b.identity.bar_start
                for a, b in zip(inputs, inputs[1:], strict=False)
            )
        ):
            raise SimulationBlocked("BLOCKED_POINT_IN_TIME_INPUT", str(step.signal_bar.revision_id))
        try:
            target = decide(strategy, parameters, tuple(b.close for b in inputs))
        except ValueError as exc:
            raise SimulationBlocked("BLOCKED_WARMUP_MISSING") from exc
        fill_target = step.fill_target
        if fill_target is not None and fill_target < signal_at:
            raise SimulationBlocked("BLOCKED_FILL_BEFORE_SIGNAL", fill_target.isoformat())
        target_index = bisect_left(expected, signal_at)
        expected_target = expected[target_index] if target_index < len(expected) else None
        if fill_target != expected_target:
            # Targets beyond the test end may be reported by the schedule; they expire.
            if not (
                expected_target is None
                and fill_target is not None
                and fill_target >= dataset.fold.test_end
            ):
                raise SimulationBlocked("BLOCKED_FILL_SCHEDULE_MISMATCH")
        if fill_target is not None and fill_target < signal_at:
            raise SimulationBlocked("BLOCKED_FILL_BEFORE_SIGNAL", fill_target.isoformat())
        if fill_target is not None and fill_target < dataset.fold.test_end:
            if fill_target not in expected:
                raise SimulationBlocked("BLOCKED_FILL_DATA_MISSING", fill_target.isoformat())
            # At a session boundary several signals can share the same next open.
            # The most recent chronological target supersedes the previous target.
            pending[fill_target] = target
        decisions.append(
            Decision(
                signal_at,
                target,
                fingerprint(tuple((b.revision_id, b.fingerprint) for b in inputs)),
                fill_target,
            )
        )
    result = SimulationResult(
        strategy, money(config.start_cash), tuple(decisions), tuple(fills), tuple(equity)
    )
    if validation_context is not None:
        return SimulationResult(
            result.strategy_id,
            result.start_cash,
            result.decisions,
            result.fills,
            result.equity,
            RETROSPECTIVE_VALUATION_METHOD,
        )
    return result
