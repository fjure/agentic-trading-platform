from bisect import bisect_left
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext
from fractions import Fraction
from uuid import UUID

import pytest

from packages.backtesting.experiment import PhaseWindow
from packages.backtesting.experiment_metrics import Metric, analyze_phase
from packages.backtesting.metrics import calculate_metrics
from packages.backtesting.simulator import Decision, EquityPoint, Fill, SimulationResult, simulate
from packages.backtesting.strategies import Target
from packages.research.calendar import NEW_YORK, CalendarCoverage, ResearchSession
from tests.unit.test_backtest_models import T, config
from tests.unit.test_backtest_simulator import bar, dataset

D = Decimal
STEP = timedelta(minutes=15)
OUTCOME = UUID(int=88)


def calendar_for_days(count, *, first=date(2024, 3, 1), early_close=True):
    sessions = []
    day = first
    while len(sessions) < count:
        if day.weekday() < 5:
            close = time(13) if early_close and len(sessions) == 3 else time(16)
            start = datetime.combine(day, time(9, 30), NEW_YORK).astimezone(UTC)
            end = datetime.combine(day, close, NEW_YORK).astimezone(UTC)
            sessions.append(ResearchSession(day, start, end, "cal"))
        day += timedelta(days=1)
    return CalendarCoverage(
        sessions[0].session_date, sessions[-1].session_date, tuple(sessions), "cal", True
    )


def full_window(calendar):
    return PhaseWindow(calendar.sessions[0].opens_at, calendar.sessions[-1].closes_at)


def bar_starts(calendar, window):
    return tuple(
        opening
        for session in calendar.sessions
        for offset in range((session.closes_at - session.opens_at) // STEP)
        if window.start <= (opening := session.opens_at + offset * STEP)
        and opening + STEP <= window.end
    )


def ledger(starts, *, closes=None, transactions=None, start_cash="1000", strategy="ema-cross-v1"):
    """Independent fixture with explicit transaction indices and Decimal cash."""
    transactions = transactions or {}
    closes = [D(value) for value in (closes or ["100"] * len(starts))]
    initial = cash = D(start_cash)
    quantity = D(0)
    equity, decisions, fills = [], [], []
    for index, (start, close) in enumerate(zip(starts, closes, strict=True)):
        at = start + STEP
        cutoff = at + timedelta(seconds=300)
        trade = transactions.get(index)
        if trade is not None:
            side, size, price, fee, slippage = trade
            size, price, fee, slippage = map(D, (size, price, fee, slippage))
            notional = (size * price).quantize(D(".00000001"), rounding=ROUND_HALF_EVEN)
            quantity += size if side == "BUY" else -size
            cash += (-notional if side == "BUY" else notional) - fee
            fills.append(Fill(start, side, size, price, notional, fee, slippage, OUTCOME, cutoff))
        value = (cash + quantity * close).quantize(D(".00000001"), rounding=ROUND_HALF_EVEN)
        equity.append(EquityPoint(at, cash, quantity, close, value, OUTCOME, cutoff))
        signal = at + timedelta(seconds=60)
        later = bisect_left(starts, signal)
        target_open = starts[later] if later < len(starts) else None
        next_trade = transactions.get(later)
        target = Target.LONG if next_trade and next_trade[0] == "BUY" else Target.FLAT
        decisions.append(Decision(signal, target, "a" * 64, target_open))
    return SimulationResult(strategy, initial, tuple(decisions), tuple(fills), tuple(equity))


def short_fixture(*, count=5, sell=True):
    calendar = calendar_for_days(1)
    opening = calendar.sessions[0].opens_at
    window = PhaseWindow(opening, opening + count * STEP)
    transactions = {2: ("BUY", "5", "100", "0", "0")}
    if sell:
        transactions[4] = ("SELL", "5", "110", "0", "0")
    simulation = ledger(
        bar_starts(calendar, window),
        closes=["100", "100", "100", "90", "110"][:count],
        transactions=transactions,
    )
    return calendar, window, simulation


@pytest.mark.parametrize(
    ("sell", "expected"),
    [
        (True, (2, 1, 0, "0.400000000000", "0.194736842105", "1.050000000000")),
        (False, (1, 0, 1, "0.600000000000", "0.299498746867", "0.500000000000")),
    ],
)
def test_five_bar_independent_exposure_and_activity(sell, expected):
    calendar, window, simulation = short_fixture(sell=sell)
    phase = analyze_phase(simulation, calendar, window)
    wire = phase.to_wire()
    assert wire["netReturn"]["value"] == "0.050000000000"
    assert wire["maxDrawdown"]["value"] == "0.050000000000"
    assert wire["observationCoverage"]["value"] == "1.000000000000"
    assert wire["exposure"]["maxGrossExposureRatio"]["value"] == (
        "0.500000000000" if sell else "0.523809523810"
    )
    assert wire["activity"]["entryCount"]["value"] == 1
    assert wire["activity"]["entryCount"]["observationCount"] == 5
    assert wire["activity"]["buyFillCount"]["value"] == 1
    assert wire["activity"]["sellFillCount"]["value"] == int(sell)
    assert wire["activity"]["buyFillCount"]["observationCount"] == expected[0]
    assert wire["exposure"]["investedBarCount"]["value"] == (2 if sell else 3)
    assert (
        wire["activity"]["fillCount"]["value"],
        wire["activity"]["exitCount"]["value"],
        wire["activity"]["endOpenPositionCount"]["value"],
        wire["exposure"]["investedBarRatio"]["value"],
        wire["exposure"]["meanGrossExposureRatio"]["value"],
        wire["turnover"]["value"],
    ) == expected
    if sell:
        mean = (Fraction(1, 2) + Fraction(9, 19)) / 5
        assert mean == Fraction(37, 190)
        assert wire["activityWarnings"]["codes"] == ["FEW_POSITION_ENTRIES"]


def test_cash_and_unaffordable_long_are_zero_activity_and_reset():
    calendar, window, _ = short_fixture()
    cash = ledger(bar_starts(calendar, window), start_cash=".001", strategy="flat-v1")
    unaffordable = replace(
        cash, decisions=tuple(replace(decision, target=Target.LONG) for decision in cash.decisions)
    )
    wire = analyze_phase(unaffordable, calendar, window).to_wire()
    assert wire["netReturn"]["value"] == wire["maxDrawdown"]["value"] == "0.000000000000"
    assert wire["fees"]["value"] == "0.00000000"
    assert wire["fees"]["observationCount"] == 0
    assert wire["activity"]["entryCount"]["value"] == wire["activity"]["fillCount"]["value"] == 0
    assert wire["exposure"]["investedBarRatio"]["value"] == "0.000000000000"
    assert wire["activityWarnings"]["codes"] == ["NO_FILLS", "NO_EXPOSURE"]
    assert wire["activityWarnings"]["status"] == "MISSING_EVIDENCE"


def test_half_even_cash_boundary_uses_two_independent_money_roundings():
    calendar, window, _ = short_fixture(count=3, sell=False)
    result = ledger(
        bar_starts(calendar, window),
        closes=["100", "100", "100.005"],
        transactions={2: ("BUY", "1.000001", "100.01", "0", "0")},
        start_cash="100.01010002",
    )
    end = result.equity[-1]
    assert end.cash == D(".00000001")
    assert end.equity == D("100.00510002")
    phase = analyze_phase(result, calendar, window)
    with localcontext() as ctx:
        ctx.prec = 38
        assert phase.metrics["maxGrossExposureRatio"].value == D("100.00510000") / end.equity
    invalid = replace(result, equity=(*result.equity[:-1], replace(end, equity=D("100.00510001"))))
    assert analyze_phase(invalid, calendar, window).metrics["netReturn"].reason_code == (
        "LEDGER_INCONSISTENT"
    )


@pytest.mark.parametrize(
    ("change", "status", "reason"),
    [
        ("missing_point", "INCOMPLETE", "INCOMPLETE_BAR_SERIES"),
        ("duplicate", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
        ("extra", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
        ("order", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
        ("missing_fills", "INCOMPLETE", "MISSING_FILL_EVIDENCE"),
        ("missing_decision", "INCOMPLETE", "MISSING_DECISION_EVIDENCE"),
        ("quantity", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
        ("partial_sale", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
        ("nonfinite", "INVALID_INPUT", "INVALID_NUMERIC_INPUT"),
        ("wrong_open", "INVALID_INPUT", "LEDGER_INCONSISTENT"),
    ],
)
def test_rejects_partial_or_contradictory_evidence(change, status, reason):
    calendar, window, result = short_fixture()
    equity, fills = result.equity, result.fills
    changes = {
        "missing_point": {"equity": equity[:-1]},
        "duplicate": {"equity": (equity[0], *equity[:-1])},
        "extra": {"equity": (*equity[:-1], replace(equity[-1], at=window.end + STEP))},
        "order": {"equity": (equity[1], equity[0], *equity[2:])},
        "missing_fills": {"fills": None},
        "missing_decision": {"decisions": result.decisions[:-1]},
        "quantity": {"equity": (*equity[:-1], replace(equity[-1], quantity=D(1), cash=D(940)))},
        "partial_sale": {"fills": (fills[0], replace(fills[1], quantity=D(4), notional=D(440)))},
        "nonfinite": {"equity": (*equity[:-1], replace(equity[-1], equity=D("NaN")))},
        "wrong_open": {
            "fills": (replace(fills[0], at=fills[0].at + timedelta(seconds=1)), fills[1])
        },
    }
    phase = analyze_phase(replace(result, **changes[change]), calendar, window)
    metric = phase.metrics["investedBarRatio"]
    assert (metric.value, metric.status, metric.reason_code) == (None, status, reason)
    assert phase.selection_values is None
    assert phase.to_wire()["activityWarnings"]["codes"] == []
    if change == "missing_point":
        assert phase.metrics["observationCoverage"].value == D(".8")
        assert phase.metrics["annualizedVolatility"].reason_code == "INCOMPLETE_DAILY_SERIES"
    if change == "duplicate":
        assert phase.expected_bar_count == phase.observed_bar_count == 5
        assert phase.metrics["observationCoverage"].value is None


def test_zero_bars_and_unknown_calendar_are_incomplete():
    calendar = calendar_for_days(2, first=date(2024, 3, 8))
    window = PhaseWindow(
        calendar.sessions[0].closes_at + timedelta(days=1),
        calendar.sessions[0].closes_at + timedelta(days=2),
    )
    phase = analyze_phase(ledger(()), calendar, window)
    assert phase.metrics["observationCoverage"].reason_code == "NO_EQUITY_OBSERVATIONS"
    assert phase.metrics["observationCoverage"].value is None
    incomplete = replace(calendar, authoritative=False)
    phase = analyze_phase(
        ledger(bar_starts(incomplete, full_window(incomplete))), incomplete, full_window(incomplete)
    )
    assert phase.metrics["netReturn"].reason_code == "INCOMPLETE_BAR_SERIES"


def test_zero_equity_keeps_legacy_facts_and_disables_division_by_equity():
    calendar, window, _ = short_fixture(count=3, sell=False)
    result = ledger(
        bar_starts(calendar, window),
        start_cash="1",
        closes=["1", "1", ".000000001"],
        transactions={2: ("BUY", "1", "1", "0", "0")},
    )
    phase = analyze_phase(result, calendar, window)
    assert phase.metrics["netReturn"].value == -1
    assert phase.metrics["maxDrawdown"].value == 1
    assert phase.metrics["meanGrossExposureRatio"].reason_code == "NON_POSITIVE_EQUITY"
    assert phase.metrics["sharpe"].reason_code == "NON_POSITIVE_EQUITY"
    assert phase.selection_values is None


def test_costs_and_negative_performance_are_separate():
    calendar, window, _ = short_fixture()
    simulation = ledger(
        bar_starts(calendar, window),
        closes=["100", "100", "100", "90", "90"],
        transactions={2: ("BUY", "1", "100", "1", "2"), 4: ("SELL", "1", "90", "1", "1")},
    )
    wire = analyze_phase(simulation, calendar, window).to_wire()
    assert wire["netReturn"]["value"] == "-0.012000000000"
    assert wire["maxDrawdown"]["value"] == "0.012000000000"
    assert wire["fees"]["value"] == "2.00000000"
    assert wire["slippage"]["value"] == "3.00000000"
    assert wire["totalCosts"]["value"] == "5.00000000"
    assert wire["turnover"]["value"] == "0.190000000000"
    assert wire["costBurden"]["value"] == "0.005000000000"


def daily_ledger(calendar, changes, *, strategy="ema-cross-v1", start_cash="100000000"):
    starts = bar_starts(calendar, full_window(calendar))
    close_by_day = {}
    previous = Fraction(start_cash)
    for session, change in zip(calendar.sessions, changes, strict=True):
        previous *= 1 + Fraction(change)
        with localcontext() as ctx:
            ctx.prec = 70
            close_by_day[session.session_date] = (
                D(previous.numerator) / previous.denominator
            ).quantize(
                D(".000000000000000001"),
                rounding=ROUND_HALF_EVEN,
            )
    return ledger(
        starts,
        start_cash=start_cash,
        strategy=strategy,
        closes=[close_by_day[start.astimezone(NEW_YORK).date()] for start in starts],
        transactions={2: ("BUY", "1", start_cash, "0", "0")},
    )


def test_daily_twenty_close_returns_match_rational_oracle_with_early_close_and_dst():
    calendar = calendar_for_days(20)
    changes = [".01", "-.01", ".02", "-.02"] * 5
    simulation = daily_ledger(calendar, changes)
    result = analyze_phase(simulation, calendar, full_window(calendar))
    assert result.complete_validation_sessions == 20
    assert result.expected_bar_count == 19 * 26 + 14
    assert len({session.opens_at.hour for session in calendar.sessions}) == 2
    variance = sum(Fraction(change) ** 2 for change in changes) / 19
    assert variance == Fraction(1, 3800)
    with localcontext() as ctx:
        ctx.prec = 65
        volatility = (D(variance.numerator) / variance.denominator * 252).sqrt()
        assert result.to_wire()["annualizedVolatility"]["value"] == f"{volatility:.12f}"
    assert result.to_wire()["sharpe"]["value"] == "0.000000000000"
    assert result.metrics["sharpe"].observation_count == 20


def test_nineteen_and_zero_variance_and_partial_boundary_rules():
    for count in (19, 20):
        calendar = calendar_for_days(count)
        window = full_window(calendar)
        result = analyze_phase(ledger(bar_starts(calendar, window)), calendar, window)
        reason = "INSUFFICIENT_OBSERVATIONS" if count == 19 else "ZERO_VARIANCE"
        assert result.metrics["sharpe"].reason_code == reason
        assert result.metrics["sharpe"].observation_count == count
        if count == 20:
            assert result.metrics["annualizedVolatility"].value == 0
    calendar = calendar_for_days(22)
    boundary = full_window(calendar)
    partial = PhaseWindow(boundary.start + STEP, boundary.end - STEP)
    result = analyze_phase(ledger(bar_starts(calendar, partial)), calendar, partial)
    assert result.complete_validation_sessions == 20
    assert result.selection_values.net_return == 0
    assert result.metrics["sharpe"].reason_code == "INCOMPLETE_DAILY_SERIES"


def test_exact_selection_and_half_even_display_ignore_ambient_context():
    calendar, window, simulation = short_fixture(count=3, sell=False)
    result = ledger(
        bar_starts(calendar, window),
        closes=["100", "100", "100"],
        transactions={2: ("BUY", "5", "100", "0", "0")},
    )
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 7, ROUND_DOWN
        phase = analyze_phase(result, calendar, window)
        assert Metric(D(".1234567890125")).to_wire()["value"] == "0.123456789012"
        assert Metric(D(".1234567890135")).to_wire()["value"] == "0.123456789014"
    assert phase.selection_values.to_wire()["investedBarRatio"] == "0." + "3" * 38
    phase = analyze_phase(
        ledger(
            bar_starts(calendar, window),
            transactions={1: ("BUY", "5", "100", "0", "0")},
        ),
        calendar,
        window,
    )
    # At consecutive 15Min bars, the prior close is too late to fill at Bar 2.
    assert phase.metrics["netReturn"].reason_code == "LEDGER_INCONSISTENT"
    assert analyze_phase(simulation, calendar, window).selection_values.entry_count == 1


def test_live_simulator_result_preserves_existing_metric_definitions():
    cfg = config(fee_usd=D("1"), slippage_bps=D("100"))
    result = simulate(
        cfg, dataset(tuple(bar(i, close="9" if i >= 2 else "10") for i in range(4)), cfg)
    )
    calendar = calendar_for_days(1, first=T.astimezone(NEW_YORK).date())
    quant = analyze_phase(result, calendar, PhaseWindow(T, T + 4 * STEP)).to_wire()
    legacy = calculate_metrics(result)
    assert quant["netReturn"]["value"] == f"{legacy.net_return:.12f}" == "-0.117821780000"
    assert quant["maxDrawdown"]["value"] == f"{legacy.max_drawdown:.12f}"
    assert quant["turnover"]["value"] == f"{legacy.turnover:.12f}"
    assert quant["totalCosts"]["value"] == f"{legacy.total_cost_usd:.8f}" == "1.98019800"
