"""Per-fold metrics; reset folds never form a chained return."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from .simulator import SimulationResult, money

RETURN_SCALE = Decimal("0.000000000001")


@dataclass(frozen=True)
class FoldMetrics:
    net_return: Decimal
    max_drawdown: Decimal
    turnover: Decimal
    fees_usd: Decimal
    slippage_usd: Decimal
    total_cost_usd: Decimal
    end_equity: Decimal
    end_cash: Decimal
    end_quantity: Decimal
    decision_count: int
    fill_count: int
    bar_count: int


def calculate_metrics(result: SimulationResult) -> FoldMetrics:
    if not result.equity or result.start_cash <= 0:
        raise ValueError("complete nonempty simulation required")
    with localcontext() as ctx:
        ctx.prec = 38
        ctx.rounding = ROUND_HALF_EVEN
        peak = result.start_cash
        drawdown = Decimal("0")
        for point in result.equity:
            peak = max(peak, point.equity)
            drawdown = max(drawdown, 1 - point.equity / peak)
        fees = money(sum((fill.fee for fill in result.fills), Decimal("0")))
        slippage = money(sum((fill.slippage_cost for fill in result.fills), Decimal("0")))
        end = result.equity[-1]
        return FoldMetrics(
            (end.equity / result.start_cash - 1).quantize(RETURN_SCALE),
            drawdown.quantize(RETURN_SCALE),
            (
                sum((abs(f.notional) for f in result.fills), Decimal("0")) / result.start_cash
            ).quantize(RETURN_SCALE),
            fees,
            slippage,
            money(fees + slippage),
            end.equity,
            end.cash,
            end.quantity,
            len(result.decisions),
            len(result.fills),
            len(result.equity),
        )
