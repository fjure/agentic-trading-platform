"""Real account replay arithmetic for a first crypto fill and a daily asset fee."""

import importlib.util
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from packages.brokers.alpaca_activities import (
    AlpacaActivityEvidence,
    AlpacaPositionEvidence,
    AlpacaReconciliationEvidence,
)
from packages.domain import models as m
from packages.persistence.ledger import _encode
from services.execution.paper_reconciliation import _activity_payload, _digest, _plan

NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
TRACE = "a" * 32
ASSET = "20000000-0000-4000-8000-000000000001"
PROVIDER = "30000000-0000-4000-8000-000000000001"


def test_activity_identity_ignores_decimal_padding_but_detects_changed_economic_values():
    activity = AlpacaActivityEvidence(
        "FILL",
        "synthetic::fill",
        NOW,
        PROVIDER,
        "SPY",
        "BUY",
        Decimal("1"),
        Decimal("100"),
        Decimal("1"),
        Decimal("0"),
    )
    padded = replace(
        activity,
        quantity=Decimal("1.00"),
        price=Decimal("100.000"),
        cumulative_quantity=Decimal("1.0"),
        leaves_quantity=Decimal("0.00"),
    )
    assert _digest(activity) == _digest(padded)
    assert _digest(activity) != _digest(replace(activity, price=Decimal("100.01")))


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def one_or_none(self):
        return self.value


class ReplayUnit:
    """Only SQL inventory is stubbed; replay, domain validation and Decimal are real."""

    def __init__(self, position, portfolio, prior=(), basis=None, terminal=()):
        self.position, self.portfolio = position, portfolio
        self.prior, self.basis, self.terminal = prior, basis, terminal
        self.connection = self

    def get(self, model, identity):
        if model is m.Position:
            assert identity == "position"
            return self.position
        assert model is m.PortfolioSnapshot
        if identity == "baseline":
            return m.PortfolioSnapshot(
                "baseline",
                "USD",
                Decimal("1000"),
                Decimal("1000"),
                Decimal("0"),
                NOW,
                1,
                m.Quality.VALID,
                m.Reconciliation.CURRENT,
            )
        assert identity == self.portfolio.id
        return self.portfolio

    def execute(self, statement, parameters=None):
        sql = " ".join(str(statement).split())
        if "FROM public.positions" in sql:
            return Result(["position"])
        if "FROM public.paper_order_accounting_bases" in sql:
            return Result(self.basis)
        if "FROM public.execution_reports" in sql:
            return Result(False if "SELECT EXISTS" in sql else self.terminal)
        if "FROM public.paper_activity_evidence" in sql:
            return Result(self.prior)
        if "FROM public.risk_account_context" in sql:
            return Result(
                {
                    "portfolio_snapshot_id": self.portfolio.id,
                    "portfolio_version": self.portfolio.ledger_version,
                }
            )
        if "SELECT ledger_version FROM public.accounts" in sql:
            return Result(self.portfolio.ledger_version)
        if "SELECT market FROM public.instruments" in sql:
            return Result(m.Market.CRYPTO.value)
        if "FROM public.broker_asset_bindings" in sql:
            return Result(ASSET)
        raise AssertionError(sql)


def crypto_case():
    position = m.Position("position", "BTC/USD", "USD", Decimal("0"), None, Decimal("0"), 1)
    portfolio = m.PortfolioSnapshot(
        "current",
        "USD",
        Decimal("1000"),
        Decimal("1000"),
        Decimal("0"),
        NOW,
        1,
        m.Quality.VALID,
        m.Reconciliation.CURRENT,
    )
    intent = m.OrderIntent(
        "intent",
        "proposal",
        "decision",
        "client",
        "BTC/USD",
        "BUY",
        "MARKET",
        Decimal("1"),
        1,
        NOW,
        TRACE,
    )
    order = m.BrokerOrder(
        "order",
        "intent",
        "client",
        PROVIDER,
        m.OrderStatus.ACKNOWLEDGED,
        Decimal("1"),
        Decimal("0"),
        1,
        NOW,
        TRACE,
    )
    source = replace(order, status=m.OrderStatus.FILLED, filled_quantity=Decimal("1"))
    fill = AlpacaActivityEvidence(
        "FILL",
        "venue::fill",
        NOW,
        PROVIDER,
        "BTC/USD",
        "BUY",
        Decimal("1"),
        Decimal("100"),
        Decimal("1"),
        Decimal("0"),
    )
    fee = AlpacaActivityEvidence(
        "CFEE",
        "venue::fee",
        None,
        instrument_id="BTC/USD",
        quantity=Decimal("-0.001"),
        net_amount=Decimal("0"),
        activity_date=NOW.date(),
    )
    evidence = AlpacaReconciliationEvidence(
        "account",
        "USD",
        Decimal("900"),
        Decimal("999.9"),
        (
            AlpacaPositionEvidence(
                ASSET, "BTC/USD", Decimal("0.999"), Decimal("100"), Decimal("99.9")
            ),
        ),
        (source,),
        (fee, fill),
        NOW,
        NOW,
        NOW,
        NOW,
    )
    baseline = {
        "positions": [_encode(position)],
        "portfolio_snapshot_id": "baseline",
        "fee_authority_profile": None,
    }
    reservation = SimpleNamespace(id="reservation", reserved_notional=Decimal("100"))
    return position, portfolio, intent, order, evidence, baseline, reservation


def test_first_crypto_buy_books_confirmed_daily_asset_fee_without_invented_midnight():
    position, portfolio, intent, order, evidence, baseline, reservation = crypto_case()
    plan = _plan(
        ReplayUnit(position, portfolio),
        "paper",
        baseline,
        [(order, intent, None, reservation)],
        evidence,
    )
    assert not plan["ready"] and plan["reason"] == "PAPER_FEE_AUTHORITY_UNKNOWN"
    assert plan["positions"]["BTC/USD"].quantity == Decimal("0.999")
    assert plan["positions"]["BTC/USD"].average_price == Decimal("100")
    assert [(movement[0].kind, movement[2], movement[4]) for movement in plan["movements"]] == [
        ("FILL", Decimal("-100"), Decimal("1")),
        ("CFEE", Decimal("0"), Decimal("-0.001")),
    ]


def test_restart_replays_same_crypto_fee_identity_without_another_delta():
    position, portfolio, intent, order, evidence, baseline, reservation = crypto_case()
    prior = [
        {
            "activity_id": a.id,
            "evidence_hash": _digest(a),
            "evidence": _activity_payload(a),
            "order_id": "order" if a.kind == "FILL" else None,
            "cash_delta": Decimal("-100") if a.kind == "FILL" else Decimal("0"),
            "instrument_id": "BTC/USD",
            "quantity_delta": Decimal("1") if a.kind == "FILL" else Decimal("-0.001"),
        }
        for a in evidence.activities
    ]
    position = replace(
        position, quantity=Decimal("0.999"), average_price=Decimal("100"), ledger_version=2
    )
    portfolio = replace(
        portfolio,
        cash=Decimal("900"),
        exposure=Decimal("99.9"),
        equity=Decimal("999.9"),
        ledger_version=2,
    )
    basis = {
        "account_id": "paper",
        "reservation_id": "reservation",
        "requested_quantity": Decimal("1"),
        "original_notional": Decimal("100"),
    }
    order = replace(order, status=m.OrderStatus.UNKNOWN, filled_quantity=Decimal("1"))
    plan = _plan(
        ReplayUnit(position, portfolio, prior, basis),
        "paper",
        baseline,
        [(order, intent, None, reservation)],
        evidence,
    )
    assert plan["positions"]["BTC/USD"].quantity == Decimal("0.999")
    assert not plan["ready"]
    assert all(activity.id in plan["prior"] for activity, *_rest in plan["movements"])


def test_unknown_status_does_not_erase_immutable_terminal_order_proof():
    position, portfolio, intent, order, evidence, baseline, reservation = crypto_case()
    order = replace(order, status=m.OrderStatus.UNKNOWN)
    source = replace(
        evidence.orders[0], status=m.OrderStatus.PARTIALLY_FILLED, filled_quantity=Decimal("0.4")
    )
    evidence = replace(evidence, orders=(source,))
    terminal = ({"status": "CANCELED", "filled_quantity": Decimal("0.4")},)
    unit = ReplayUnit(position, portfolio, terminal=terminal)
    with pytest.raises(ValueError, match="TERMINAL_STATUS_CORRECTION"):
        _plan(unit, "paper", baseline, [(order, intent, None, reservation)], evidence)


def test_paper_migration_ddl_has_no_accidental_sqlalchemy_bind_parameters(monkeypatch):
    path = Path(__file__).resolve().parents[2] / (
        "services/control_api/migrations/versions/0009_paper_reconciliation.py"
    )
    spec = importlib.util.spec_from_file_location("paper_migration_ddl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert statements
    for statement in statements:
        assert not text(statement)._bindparams, statement


def test_local_rejected_status_alone_cannot_prove_no_provider_order_was_sent():
    position, portfolio, intent, order, evidence, baseline, reservation = crypto_case()
    order = replace(order, status=m.OrderStatus.REJECTED, broker_order_id=None)
    evidence = replace(evidence, orders=())
    with pytest.raises(ValueError, match="BROKER_ORDER_MISSING_OR_AMBIGUOUS"):
        _plan(
            ReplayUnit(position, portfolio),
            "paper",
            baseline,
            [(order, intent, None, reservation)],
            evidence,
        )
