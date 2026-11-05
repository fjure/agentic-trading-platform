"""Calendar-bound, phase-local analytics for the existing Long/Flat ledger."""

from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, DecimalException, localcontext
from types import MappingProxyType

from packages.domain.models import utc_time
from packages.research.calendar import CalendarCoverage, expected_windows
from packages.research.models import BarInterval

from .experiment import QUANT_METRICS_VERSION, SCHEMA_VERSION, PhaseWindow
from .experiment_protocol import SelectionValues
from .metrics import RETURN_SCALE
from .simulator import (
    RETROSPECTIVE_VALUATION_METHOD,
    USD_SCALE,
    Decision,
    EquityPoint,
    Fill,
    SimulationResult,
    money,
)
from .strategies import Target

CONTEXT = Context(prec=38, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)
BAR = timedelta(minutes=15)
SIGNAL_DELAY = timedelta(seconds=60)
LIMITATIONS = (
    "PRICE_ONLY",
    "DIVIDENDS_EXCLUDED",
    "IEX_PARTIAL_MARKET",
    "NO_STATISTICAL_GUARANTEE",
    "NO_TRADING_AUTHORIZATION",
    "EXTERNAL_KNOWLEDGE_UNKNOWN",
)
BAR_LIMITATIONS = (*LIMITATIONS, "BAR_WEIGHTED_NOT_TIME_WEIGHTED")
PHASE = "PHASE"
BAR_CLOSE = "BAR_CLOSE_15MIN"
DAILY = "DAILY_252"

_COSTS = ("fees", "slippage", "totalCosts")
_EXPOSURE = (
    "investedBarCount",
    "equityBarCount",
    "investedBarRatio",
    "meanGrossExposureRatio",
    "maxGrossExposureRatio",
)
_ACTIVITY = (
    "decisionCount",
    "fillCount",
    "buyFillCount",
    "sellFillCount",
    "entryCount",
    "exitCount",
    "endOpenPositionCount",
)
_FREQUENCIES = {
    "annualizedVolatility": DAILY,
    "sharpe": DAILY,
    "oosDegradation": DAILY,
    **dict.fromkeys(_EXPOSURE, BAR_CLOSE),
}


@dataclass(frozen=True)
class Metric:
    value: Decimal | int | None
    observation_count: int = 0
    frequency: str = PHASE
    status: str = "VALID"
    reason_code: str | None = None

    def to_wire(self, *, scale=RETURN_SCALE, limitations=None):
        value = self.value
        if isinstance(value, Decimal):
            with localcontext(CONTEXT):
                value = value.quantize(scale)
                value = format(value.copy_abs() if value == 0 else value, "f")
        return {
            "schemaVersion": SCHEMA_VERSION,
            "value": value,
            "status": self.status,
            "reasonCode": self.reason_code,
            "observationCount": self.observation_count,
            "frequency": self.frequency,
            "methodVersion": QUANT_METRICS_VERSION,
            "limitations": list(
                limitations or (BAR_LIMITATIONS if self.frequency == BAR_CLOSE else LIMITATIONS)
            ),
        }


def unavailable(status, reason, count=0, frequency=PHASE):
    return Metric(None, count, frequency, status, reason)


@dataclass(frozen=True)
class PhaseAnalytics:
    strategy_id: str
    valuation_method: str
    phase: PhaseWindow
    calendar: CalendarCoverage
    start_cash: Decimal
    expected_bar_count: int
    observed_bar_count: int
    complete_validation_sessions: int
    metrics: Mapping[str, Metric]
    selection_values: SelectionValues | None
    daily_mean: Decimal | None

    def to_wire(self):
        metrics = dict(self.metrics)
        mode_limitations = (
            ("NOT_STRICT_PIT", "EX_POST_REVISIONS", "SURVIVORSHIP_BIAS")
            if self.valuation_method == RETROSPECTIVE_VALUATION_METHOD
            else ()
        )

        def wire(name, metric):
            limitations = BAR_LIMITATIONS if metric.frequency == BAR_CLOSE else LIMITATIONS
            return metric.to_wire(
                scale=USD_SCALE if name in _COSTS else RETURN_SCALE,
                limitations=(*limitations, *mode_limitations),
            )

        activity = {name: wire(name, metrics.pop(name)) for name in _ACTIVITY}
        exposure = {name: wire(name, metrics.pop(name)) for name in _EXPOSURE}
        return {
            "schemaVersion": SCHEMA_VERSION,
            **{name: wire(name, metric) for name, metric in metrics.items()},
            "expectedBarCount": self.expected_bar_count,
            "observedBarCount": self.observed_bar_count,
            "completeValidationSessions": self.complete_validation_sessions,
            "activity": {"schemaVersion": SCHEMA_VERSION, **activity},
            "exposure": {"schemaVersion": SCHEMA_VERSION, **exposure},
            "activityWarnings": activity_warnings(self),
        }


class _BadEvidence(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def _inconsistent():
    raise _BadEvidence("INVALID_INPUT", "LEDGER_INCONSISTENT")


def _ordered_subset(observed, expected):
    for at in observed:
        utc_time(at)
    if observed != tuple(sorted(set(observed))) or not set(observed) <= set(expected):
        _inconsistent()


def _finite(*values):
    if any(type(value) is not Decimal or not value.is_finite() for value in values):
        raise _BadEvidence("INVALID_INPUT", "INVALID_NUMERIC_INPUT")


def _validate_ledger(result, starts, closes, phase, *, unknown_dates):
    for values, expected_type in (
        (result.equity, EquityPoint),
        (result.decisions, Decision),
        (result.fills, Fill),
    ):
        if values is not None and (
            type(values) is not tuple or any(type(value) is not expected_type for value in values)
        ):
            _inconsistent()
    _finite(result.start_cash)
    if result.start_cash <= 0:
        raise _BadEvidence("INVALID_INPUT", "INVALID_NUMERIC_INPUT")
    if unknown_dates:
        raise _BadEvidence("INCOMPLETE", "INCOMPLETE_BAR_SERIES")

    observed = tuple(point.at for point in result.equity or ())
    signals = tuple(decision.signal_at for decision in result.decisions or ())
    fill_times = tuple(fill.at for fill in result.fills or ())
    expected_signals = tuple(at + SIGNAL_DELAY for at in closes)
    _ordered_subset(observed, closes)
    _ordered_subset(signals, expected_signals)
    _ordered_subset(fill_times, starts)
    if not starts:
        raise _BadEvidence("INCOMPLETE", "NO_EQUITY_OBSERVATIONS")
    if observed != closes:
        raise _BadEvidence("INCOMPLETE", "INCOMPLETE_BAR_SERIES")
    if result.fills is None:
        raise _BadEvidence("INCOMPLETE", "MISSING_FILL_EVIDENCE")
    if result.decisions is None or signals != expected_signals:
        raise _BadEvidence("INCOMPLETE", "MISSING_DECISION_EVIDENCE")

    targets = {}
    for decision in result.decisions:
        if decision.target not in (Target.FLAT, Target.LONG):
            _inconsistent()
        target_at = decision.fill_target
        target_index = bisect_left(starts, decision.signal_at)
        if target_at is not None:
            utc_time(target_at)
            if target_at < decision.signal_at:
                _inconsistent()
        if target_index < len(starts):
            if target_at != starts[target_index]:
                _inconsistent()
            targets[target_at] = decision.target
        elif target_at is not None and target_at < phase.end:
            _inconsistent()

    fills = {fill.at: fill for fill in result.fills}
    quantity = ZERO
    cash = money(result.start_cash)
    entries = exits = buys = sells = 0
    previously_invested = False
    for start, point in zip(starts, result.equity, strict=True):
        _finite(point.cash, point.quantity, point.close, point.equity)
        if (
            point.cash < 0
            or point.quantity < 0
            or point.close <= 0
            or point.equity != money(point.cash + point.quantity * point.close)
        ):
            _inconsistent()
        fill = fills.get(start)
        if fill is not None:
            _finite(fill.quantity, fill.price, fill.notional, fill.fee, fill.slippage_cost)
            if (
                fill.quantity <= 0
                or fill.price <= 0
                or min(fill.notional, fill.fee, fill.slippage_cost) < 0
                or any(
                    value != money(value) for value in (fill.price, fill.fee, fill.slippage_cost)
                )
                or fill.notional != money(fill.quantity * fill.price)
                or fill.outcome_revision_id != point.outcome_revision_id
                or fill.outcome_cutoff != point.outcome_cutoff
            ):
                _inconsistent()
            target = targets.get(start)
            if fill.side == "BUY" and quantity == 0 and target == Target.LONG:
                buys += 1
                quantity = fill.quantity
                cash = money(cash - fill.notional - fill.fee)
            elif (
                fill.side == "SELL"
                and quantity == fill.quantity
                and target == Target.FLAT
                and fill.notional >= fill.fee
            ):
                sells += 1
                quantity = ZERO
                cash = money(cash + fill.notional - fill.fee)
            else:
                _inconsistent()
        invested = point.quantity > 0
        entries += invested and not previously_invested
        exits += not invested and previously_invested
        previously_invested = invested
        if point.quantity != quantity or point.cash != cash:
            _inconsistent()
    end_open = int(quantity > 0)
    if (entries, exits, entries - exits) != (buys, sells, end_open):
        _inconsistent()
    return buys, sells, entries, exits, end_open


def _daily(result, phase, sessions, complete_sessions, positive):
    n = len(complete_sessions)
    if not positive:
        reason = "NON_POSITIVE_EQUITY"
    elif (
        len(sessions) != n
        or not sessions
        or phase.start != sessions[0].opens_at
        or phase.end != sessions[-1].closes_at
    ):
        reason = "INCOMPLETE_DAILY_SERIES"
    elif n < 20:
        reason = "INSUFFICIENT_OBSERVATIONS"
    else:
        by_close = {point.at: point.equity for point in result.equity}
        equity = (result.start_cash, *(by_close[session.closes_at] for session in sessions))
        returns = tuple(
            current / previous - 1
            for previous, current in zip(equity[:-1], equity[1:], strict=True)
        )
        mean = sum(returns, ZERO) / n
        deviation = (sum(((value - mean) ** 2 for value in returns), ZERO) / (n - 1)).sqrt()
        annual_factor = Decimal(252).sqrt()
        volatility = Metric(deviation * annual_factor, n, DAILY)
        sharpe = (
            Metric((mean / deviation) * annual_factor, n, DAILY)
            if deviation
            else unavailable("N/A", "ZERO_VARIANCE", n, DAILY)
        )
        return volatility, sharpe, mean
    used = n if reason == "INSUFFICIENT_OBSERVATIONS" else 0
    invalid = unavailable("N/A", reason, used, DAILY)
    return invalid, invalid, None


def _core(result, counts, complete_sessions):
    points, fills = result.equity, result.fills
    n, nf = len(points), len(fills)
    buys, sells, entries, exits, end_open = counts
    peak, drawdown = result.start_cash, ZERO
    for point in points:
        peak = max(peak, point.equity)
        drawdown = max(drawdown, 1 - point.equity / peak)
    fees = money(sum((fill.fee for fill in fills), ZERO))
    slippage = money(sum((fill.slippage_cost for fill in fills), ZERO))
    total = money(fees + slippage)
    turnover = sum((abs(fill.notional) for fill in fills), ZERO) / result.start_cash
    net_return = points[-1].equity / result.start_cash - 1
    invested = sum(point.quantity > 0 for point in points)
    invested_ratio = Decimal(invested) / n
    positive = all(point.equity > 0 for point in points)
    if positive:
        gross = tuple(money(point.quantity * point.close) / point.equity for point in points)
        if any(not 0 <= value <= 1 for value in gross):
            _inconsistent()
        mean_gross, max_gross = (
            Metric(sum(gross, ZERO) / n, n, BAR_CLOSE),
            Metric(max(gross), n, BAR_CLOSE),
        )
        exact = SelectionValues(
            net_return, drawdown, turnover, invested_ratio, entries, complete_sessions
        )
    else:
        mean_gross = max_gross = unavailable("N/A", "NON_POSITIVE_EQUITY", n, BAR_CLOSE)
        exact = None
    return (
        {
            "netReturn": Metric(net_return, n),
            "maxDrawdown": Metric(drawdown, n),
            "turnover": Metric(turnover, nf),
            "costBurden": Metric(total / result.start_cash, nf),
            "fees": Metric(fees, nf),
            "slippage": Metric(slippage, nf),
            "totalCosts": Metric(total, nf),
            "decisionCount": Metric(len(result.decisions), len(result.decisions)),
            "fillCount": Metric(nf, nf),
            "buyFillCount": Metric(buys, nf),
            "sellFillCount": Metric(sells, nf),
            "entryCount": Metric(entries, n),
            "exitCount": Metric(exits, n),
            "endOpenPositionCount": Metric(end_open, n),
            "equityBarCount": Metric(n, n, BAR_CLOSE),
            "investedBarCount": Metric(invested, n, BAR_CLOSE),
            "investedBarRatio": Metric(invested_ratio, n, BAR_CLOSE),
            "meanGrossExposureRatio": mean_gross,
            "maxGrossExposureRatio": max_gross,
        },
        exact,
        positive,
    )


def _failed_metrics(status, reason):
    names = (
        "netReturn",
        "maxDrawdown",
        "turnover",
        "costBurden",
        "annualizedVolatility",
        "sharpe",
        "oosDegradation",
        "netReturnDifferenceBuyHold",
        "netReturnDifferenceFlat",
        "observationCoverage",
        *_COSTS,
        *_ACTIVITY,
        *_EXPOSURE,
    )
    return {
        name: unavailable(status, reason, frequency=_FREQUENCIES.get(name, PHASE)) for name in names
    }


def analyze_phase(result: SimulationResult, calendar: CalendarCoverage, phase: PhaseWindow):
    """Calculate from one reset simulation and the calendar evidence bound to its phase."""
    if not (
        isinstance(result, SimulationResult)
        and isinstance(calendar, CalendarCoverage)
        and isinstance(phase, PhaseWindow)
    ):
        raise TypeError("typed simulation, phase and calendar required")
    schedule = expected_windows(
        calendar, BarInterval.FIFTEEN_MINUTES, phase.start, phase.end, phase.end
    )
    starts, closes = (
        (tuple(column) for column in zip(*schedule.windows, strict=True))
        if schedule.windows
        else ((), ())
    )
    points = result.equity if isinstance(result.equity, tuple) else ()
    n, observed_count = len(starts), len(points)
    sessions = tuple(
        session
        for session in calendar.sessions
        if session.opens_at < phase.end and session.closes_at > phase.start
    )
    observed = {
        point.at
        for point in points
        if isinstance(point, EquityPoint) and isinstance(point.at, datetime)
    }
    complete = tuple(
        session
        for session in sessions
        if phase.start <= session.opens_at
        and session.closes_at <= phase.end
        and all(
            session.opens_at + i * BAR in observed
            for i in range(1, 1 + (session.closes_at - session.opens_at) // BAR)
        )
    )
    exact, mean = None, None
    with localcontext(CONTEXT):
        try:
            counts = _validate_ledger(
                result, starts, closes, phase, unknown_dates=schedule.unknown_dates
            )
            metrics, exact, positive = _core(result, counts, len(complete))
            volatility, sharpe, mean = _daily(result, phase, sessions, complete, positive)
            metrics.update(
                annualizedVolatility=volatility,
                sharpe=sharpe,
                observationCoverage=Metric(Decimal(1), n),
                oosDegradation=unavailable("N/A", "NO_PAIRED_VALIDATION", frequency=DAILY),
                netReturnDifferenceBuyHold=unavailable("N/A", "INCOMPARABLE_BINDINGS"),
                netReturnDifferenceFlat=unavailable("N/A", "INCOMPARABLE_BINDINGS"),
            )
            for metric in metrics.values():
                if (
                    isinstance(metric.value, Decimal)
                    and metric.value.quantize(RETURN_SCALE).adjusted() >= 20
                ):
                    raise _BadEvidence("INVALID_INPUT", "INVALID_NUMERIC_INPUT")
        except _BadEvidence as exc:
            exact, mean = None, None
            metrics = _failed_metrics(exc.status, exc.reason)
            if exc.reason == "INCOMPLETE_BAR_SERIES":
                metrics["annualizedVolatility"] = metrics["sharpe"] = unavailable(
                    "N/A",
                    "INCOMPLETE_DAILY_SERIES",
                    frequency=DAILY,
                )
            if n and exc.status == "INCOMPLETE" and not schedule.unknown_dates:
                metrics["observationCoverage"] = Metric(Decimal(observed_count) / n, observed_count)
        except DecimalException:
            exact, mean = None, None
            metrics = _failed_metrics("INVALID_INPUT", "INVALID_NUMERIC_INPUT")
        except ValueError:
            exact, mean = None, None
            metrics = _failed_metrics("INVALID_INPUT", "LEDGER_INCONSISTENT")
    return PhaseAnalytics(
        result.strategy_id,
        result.valuation_method,
        phase,
        calendar,
        result.start_cash,
        n,
        observed_count,
        len(complete),
        MappingProxyType(metrics),
        exact,
        mean,
    )


def activity_warnings(analytics: PhaseAnalytics):
    metrics = analytics.metrics
    count = metrics["fillCount"].value
    invested = metrics["investedBarRatio"].value
    entries = metrics["entryCount"].value
    difference = metrics["netReturnDifferenceFlat"].value
    codes = []
    if count is not None and invested is not None and entries is not None:
        if count == 0:
            codes.append("NO_FILLS")
        if invested == 0:
            codes.append("NO_EXPOSURE")
        elif invested <= Decimal("0.01"):
            codes.append("LOW_BAR_EXPOSURE")
        if 0 < entries < 3:
            codes.append("FEW_POSITION_ENTRIES")
        if difference is not None and difference.copy_abs() <= Decimal("0.0001"):
            codes.append("FLAT_LIKE_NET_RETURN")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "methodVersion": "quant-activity-warnings-v1",
        "status": "AVAILABLE"
        if difference is not None and count is not None
        else "MISSING_EVIDENCE",
        "codes": codes,
    }
