"""Binding observation reuse preserves immutable scope and original expiry."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.brokers.proofs import BindingProof
from packages.domain.models import BrokerCapabilities, Capability
from packages.persistence.ledger import PostgresLedger
from services.execution.broker_binding import AlpacaExecutionBinding
from tests.contracts.test_alpaca_paper import ACCOUNT_ID, ASSET_ID, NOW


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value


def world(monkeypatch, **changes):
    cap = BrokerCapabilities(
        "alpaca-paper",
        "SPY",
        17,
        paper=Capability.CONFIRMED,
        regular_session=Capability.CONFIRMED,
        market_order=Capability.CONFIRMED,
        limit_order=Capability.CONFIRMED,
        fractional=Capability.CONFIRMED,
        quantity_step=Decimal("0.000000001"),
        price_tick=Decimal("0.0001"),
    )
    context = dict(
        version=4,
        broker_id="alpaca-paper",
        capability_version=17,
        valid_until=NOW + timedelta(seconds=20),
        session_open=True,
        session_valid_until=NOW.replace(hour=20),
        paper_confirmed=True,
        regular_session_confirmed=True,
        continuous_session_confirmed=False,
        market_order_confirmed=True,
        limit_order_confirmed=True,
        fractional_confirmed=True,
        quantity_step=cap.quantity_step,
        price_tick=cap.price_tick,
    )
    previous = dict(
        source_capability_version=19,
        provider_account_id=ACCOUNT_ID,
        provider_asset_id=ASSET_ID,
        valid_until=NOW + timedelta(seconds=20),
        session_start=NOW.replace(hour=13, minute=30),
        session_end=NOW.replace(hour=20),
    )
    source = BindingProof(
        ACCOUNT_ID,
        ASSET_ID,
        "SPY",
        previous["session_start"],
        previous["session_end"],
        NOW,
        NOW,
        19,
    )
    trade = replace(source, capability_version=17)
    view = SimpleNamespace(valid_until=NOW + timedelta(seconds=120))
    db = changes.pop("database", "lab_paper_runtime")
    context.update(changes.pop("context", {}))
    previous.update(changes.pop("previous", {}))
    cap = replace(cap, **changes.pop("capability", {}))
    assert not changes
    calls, audits = [], []
    active = False

    class Connection:
        def execute(self, statement, parameters=None):
            sql = str(statement)
            calls.append((sql, parameters))
            if "clock_timestamp()" in sql:
                return Result(NOW)
            if "SELECT currency FROM public.accounts" in sql:
                return Result("USD")
            if "SELECT count(*) FROM public.accounts" in sql:
                return Result(1)
            if "SELECT provider_account_id" in sql:
                return Result(ACCOUNT_ID)
            if "SELECT provider_asset_id" in sql:
                return Result(ASSET_ID)
            if "SELECT * FROM public.risk_instrument_context" in sql:
                return Result(context)
            if "SELECT * FROM public.broker_binding_proofs" in sql:
                return Result(previous)
            if "SELECT current_database()" in sql:
                return Result(db)
            if "public.safety_latch" in sql:
                return Result(5)
            if sql.lstrip().startswith("INSERT INTO"):
                return Result(None)
            raise AssertionError("unexpected binding SQL")

    tx = SimpleNamespace(connection=Connection(), lock_account=lambda _: None, insert=audits.append)
    ledger = object.__new__(PostgresLedger)

    @contextmanager
    def transaction():
        nonlocal active
        assert not active
        active = True
        try:
            yield tx
        finally:
            active = False

    ledger.transaction = transaction
    broker = object.__new__(AlpacaPaperBrokerAdapter)
    market = object.__new__(AlpacaMarketDataProvider)
    binding = AlpacaExecutionBinding(ledger, "paper", broker, market)

    def proofs(_):
        assert not active  # Even a reusable context is freshly observed outside SQL.
        return view, source, cap, trade

    monkeypatch.setattr(binding, "_proofs", proofs)
    monkeypatch.setattr("services.execution.broker_binding.is_execution", lambda _: True)
    return binding, cap, context, previous, calls, audits


def test_fresh_same_semantics_reuse_has_no_sql_or_audit_mutation_or_expiry_extension(monkeypatch):
    binding, cap, context, previous, calls, audits = world(monkeypatch)
    original = deepcopy((context, previous))
    assert binding.publish("SPY", "a" * 32) == cap
    assert (context, previous) == original
    assert not audits
    assert not any(sql.lstrip().startswith("INSERT") for sql, _ in calls)
    assert previous["valid_until"] == NOW + timedelta(seconds=20)


@pytest.mark.parametrize(
    "changes",
    [
        {"context": {"valid_until": NOW}},
        {"previous": {"valid_until": NOW}},
        {"context": {"fractional_confirmed": False}},
        {"context": {"quantity_step": Decimal("1")}},
        {"context": {"price_tick": Decimal("0.01")}},
        {"context": {"session_open": False}},
        {"previous": {"session_start": NOW.replace(hour=14)}},
        {"previous": {"source_capability_version": 18}},
        {"previous": {"provider_asset_id": "33333333-3333-4333-8333-333333333333"}},
        {"database": "lab_runtime"},
    ],
)
def test_expiry_semantic_changes_and_legacy_database_require_new_version(monkeypatch, changes):
    binding, cap, context, previous, calls, audits = world(monkeypatch, **changes)
    assert binding.publish("SPY", "a" * 32) == cap
    writes = [params for sql, params in calls if "INSERT INTO public.broker_binding_proofs" in sql]
    assert len(writes) == 1 and writes[0]["version"] == 5
    assert len(audits) == 2
    assert previous["valid_until"] <= NOW + timedelta(seconds=20)
