"""Conservative representability of isolated-Paper risk facts and legacy isolation."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from packages.domain import models as m
from packages.risk.engine import RiskLimits
from packages.risk.paper_daily import DailyRiskObservation, DailyRiskReference, EquityInterval
from packages.risk.paper_rounding import exposure_upper, pnl_lower
from packages.risk.reservations import _exact_numeric, _fresh_facts
from tests.unit.test_decision import NOW, state


def marked_value():
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        return Decimal("0.123456789")*Decimal("101.123456789012345678")


def test_valid_fractional_mark_dust_gets_upper_exposure_and_lower_return():
    value = marked_value()
    assert value == Decimal("12.484377267751714678763907942")
    assert exposure_upper(value) == Decimal("12.484377267751714679")
    assert pnl_lower(value) == Decimal("12.484377267751714678")
    with pytest.raises(ValueError):
        _exact_numeric(value)


@pytest.mark.parametrize("value", ["0", "1", "0.000000000000000001",
                                  "99999999999999999999.999999999999999999"])
def test_exact_representable_exposure_and_return_do_not_change(value):
    value = Decimal(value)
    assert exposure_upper(value) == pnl_lower(value) == value


@pytest.mark.parametrize("value", ["0.000000000000000000001", "12.484377267751714678763907942",
                                  "99999999999999999998.999999999999999999999"])
def test_upper_and_lower_are_conservative_independent_of_low_ambient_precision(value):
    exact = Decimal(value)
    with localcontext() as arithmetic:
        arithmetic.prec = 6
        upper, lower, negative = (
            exposure_upper(exact), pnl_lower(exact), pnl_lower(exact.copy_negate()),
        )
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        assert lower <= exact <= upper
        assert upper-exact < Decimal("0.000000000000000001")
        assert exact-lower < Decimal("0.000000000000000001")
        assert negative <= -exact
        assert -exact-negative < Decimal("0.000000000000000001")


@pytest.mark.parametrize("helper", [exposure_upper, pnl_lower])
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"),
                                  Decimal("1e1000"), Decimal("-1e1000"),
                                  Decimal("100000000000000000000"), 1, "1"])
def test_nonfinite_overflow_and_untyped_values_fail_closed(helper, value):
    with pytest.raises(ValueError):
        helper(value)


def test_negative_exposure_is_invalid_and_rounding_overflow_never_clamps():
    with pytest.raises(ValueError):
        exposure_upper(Decimal("-0.000000000000000000001"))
    with pytest.raises(ValueError):
        exposure_upper(Decimal("99999999999999999999.999999999999999999999"))
    with pytest.raises(ValueError):
        pnl_lower(Decimal("-99999999999999999999.999999999999999999999"))


def facts_for_mark(monkeypatch, *, paper):
    import packages.risk.paper_lifecycle as paper_lifecycle
    import packages.risk.paper_limits as paper_limits
    import packages.risk.reservations as reservations

    source = state()
    position = replace(source.position, quantity=Decimal("0.123456789"),
                       average_price=Decimal("100"), realized_pnl=Decimal("0.123456789012345678"))
    source = replace(source, position=position,
                     bar=replace(source.bar, close=Decimal("101.123456789012345678")))
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    reference = DailyRiskReference("UTC_DAY", midnight, midnight+timedelta(days=1),
                                   EquityInterval(Decimal(10000), Decimal(10000)),
                                   Decimal(10000), "a"*64)
    observation = DailyRiskObservation(reference.evidence_hash, NOW,
                                      EquityInterval(Decimal(10000), Decimal(10000)),
                                      EquityInterval(Decimal(0), Decimal(0)), True, "b"*64)
    monkeypatch.setattr(reservations, "is_isolated_paper", lambda _: paper)
    monkeypatch.setattr(reservations, "read_daily", lambda *_: (reference, observation))
    # This unit isolates numeric fact construction after source-proof admission.
    monkeypatch.setattr(paper_limits, "paper_proposal_reasons", lambda *_: ())
    monkeypatch.setattr(paper_lifecycle, "activation_reasons", lambda *_: ())

    class Result:
        def __init__(self, value):
            self.value = value

        def mappings(self):
            return self

        def all(self):
            return self.value

        def scalar_one(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class Connection:
        def execute(self, statement, parameters=None):
            sql = str(statement)
            if "FROM public.risk_reservations r" in sql:
                return Result([])
            if "SELECT EXISTS" in sql:
                return Result(False)
            if "SELECT ms.id" in sql:
                return Result(source.id)
            if "paper_cost_basis_read" in sql:
                return Result(Decimal("12.48"))
            raise AssertionError("unexpected numeric fact fixture query")

    def get(model, identity):
        if model is m.Instrument:
            return SimpleNamespace(currency="USD")
        assert model is m.MarketState and identity == source.id
        return source

    context = dict(portfolio_version=1, day_start_at=midnight, day_start_equity=Decimal(10000),
                   day_start_realized_pnl=Decimal(0), mode="PAPER", safety_state="RUNNING",
                   reconciliation_current=True)
    capability = dict(broker_id="alpaca-paper", capability_version=7, paper_confirmed=True,
                      regular_session_confirmed=True, fractional_confirmed=True,
                      market_order_confirmed=True, limit_order_confirmed=True,
                      quantity_step=Decimal("0.000000001"), price_tick=Decimal("0.0001"),
                      session_open=True, session_valid_until=NOW+timedelta(seconds=120))
    return _fresh_facts(Connection(), SimpleNamespace(get=get), "paper",
                        SimpleNamespace(instrument_id="SPY"), source, source.portfolio,
                        [position], context, capability, SimpleNamespace(version=1),
                        RiskLimits(), NOW)


def test_actual_paper_fact_construction_preserves_cash_basis_and_exact_realized(monkeypatch):
    with localcontext() as arithmetic:
        arithmetic.prec = 6
        reasons, facts = facts_for_mark(monkeypatch, paper=True)
    assert reasons == ()
    assert facts.instrument_exposure == facts.gross_notional == exposure_upper(marked_value())
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        assert facts.unrealized_pnl == pnl_lower(marked_value()-Decimal("12.48"))
    assert facts.realized_pnl_today == Decimal("0.123456789012345678")
    assert facts.cash == facts.equity == Decimal(10000)


def test_normal_fact_path_keeps_exact_rejection_instead_of_applying_paper_rounding(monkeypatch):
    with pytest.raises(ValueError):
        facts_for_mark(monkeypatch, paper=False)
