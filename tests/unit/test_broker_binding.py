"""Private Alpaca binding compares provider evidence before any order route."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text

from packages.brokers.alpaca_market_data import (
    AlpacaMarketDataProvider,
    MarketDataError,
    MarketSnapshot,
)
from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.brokers.fake import FakeBrokerAdapter
from packages.brokers.proofs import BindingProof
from packages.domain.models import BrokerCapabilities, Capability
from services.execution.broker_binding import AlpacaExecutionBinding, _fresh, adapter_id
from services.execution.gates import SubmitDenied

_ACCOUNT = "00000000-0000-4000-8000-000000000001"
_ASSET = "00000000-0000-4000-8000-000000000002"
_NOW = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)


class _Provider:
    def __init__(self, answer):
        self.answer = answer

    def binding_proof(self, _instrument):
        return self.answer


def _binding(source: BindingProof, trade: BindingProof, cap: BrokerCapabilities):
    market = SimpleNamespace(
        instrument=SimpleNamespace(id="SPY"),
        capability_version=source.capability_version,
        source_capabilities=SimpleNamespace(history=Capability.CONFIRMED),
    )
    binding = object.__new__(AlpacaExecutionBinding)
    binding._market_provider = _Provider((market, source))
    binding._broker = _Provider((cap, trade))
    return binding


def _proof(version: int) -> BindingProof:
    return BindingProof(
        _ACCOUNT, _ASSET, "SPY", _NOW - timedelta(hours=1),
        _NOW + timedelta(hours=1), _NOW, _NOW, version,
    )


def _cap(version: int) -> BrokerCapabilities:
    return BrokerCapabilities(
        "alpaca-paper", "SPY", version,
        paper=Capability.CONFIRMED,
        regular_session=Capability.CONFIRMED,
        fractional=Capability.CONFIRMED,
        market_order=Capability.CONFIRMED,
        limit_order=Capability.CONFIRMED,
        quantity_step=Decimal("0.000000001"),
        price_tick=Decimal("0.0001"),
    )


def test_proofs_keep_separate_market_and_canonical_trading_versions():
    source, trade = _proof(17), _proof(29)
    _, actual_source, actual_trade, _ = _binding(source, trade, _cap(29))._proofs("SPY")
    assert actual_source.capability_version == 17
    assert actual_trade.version == 29
    assert "provider_account_id" not in {field.name for field in fields(MarketSnapshot)}


@pytest.mark.parametrize("change", [
    {"provider_account_id": "00000000-0000-4000-8000-000000000003"},
    {"provider_asset_id": "00000000-0000-4000-8000-000000000003"},
    {"session_end": _NOW + timedelta(hours=2)},
    {"instrument_id": "QQQ"},
    {"capability_version": 30},
])
def test_cross_adapter_mismatch_fails_closed(change):
    source, trade = _proof(17), replace(_proof(29), **change)
    with pytest.raises(SubmitDenied):
        _binding(source, trade, _cap(29))._proofs("SPY")


def test_unconfirmed_order_capability_fails_closed():
    with pytest.raises(SubmitDenied):
        _binding(
            _proof(17), _proof(29),
            replace(_cap(29), market_order=Capability.UNKNOWN),
        )._proofs("SPY")


def test_freshness_rejects_stale_provider_and_receive_times():
    assert _fresh(_proof(17), _NOW)
    assert not _fresh(replace(_proof(17), observed_at=_NOW - timedelta(seconds=121)), _NOW)
    assert not _fresh(replace(_proof(17), provider_time=_NOW - timedelta(seconds=121)), _NOW)


def test_market_binding_reads_metadata_only_and_closed_equity_fails():
    now = datetime(2026, 10, 14, 14, 1, tzinfo=UTC)
    paths = []
    open_state = True

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        payload = {
            "/v2/account": {
                "id": _ACCOUNT, "status": "ACTIVE", "currency": "USD",
                "account_blocked": False, "trading_blocked": False,
                "trade_suspended_by_user": False,
            },
            "/v2/assets/SPY": {
                "id": _ASSET, "symbol": "SPY", "class": "us_equity",
                "status": "active", "tradable": True, "fractionable": True,
            },
            "/v2/clock": {"timestamp": now.isoformat(), "is_open": open_state},
            "/v2/calendar": [{"date": "2026-10-14", "open": "09:30", "close": "16:00"}],
        }
        return httpx.Response(200, json=payload[request.url.path])

    provider = AlpacaMarketDataProvider(
        SimpleNamespace(now=lambda: now), object(),
        api_key="synthetic-key", api_secret="synthetic-secret", enabled=True,
        transport=httpx.MockTransport(handle),
    )
    view, proof = provider.binding_proof("SPY")
    assert view.capability_version == proof.capability_version
    assert view.source_capabilities.history is Capability.CONFIRMED
    assert proof.provider_account_id == _ACCOUNT
    assert proof.provider_asset_id == _ASSET
    assert paths == ["/v2/account", "/v2/assets/SPY", "/v2/clock", "/v2/calendar"]
    open_state = False
    with pytest.raises(MarketDataError, match="SESSION_CLOSED"):
        provider.binding_proof("SPY")


def test_exact_adapter_identity_only():
    assert adapter_id(object.__new__(FakeBrokerAdapter)) == "fake"
    assert adapter_id(object.__new__(AlpacaPaperBrokerAdapter)) == "alpaca-paper"
    with pytest.raises(SubmitDenied):
        adapter_id(object())


def test_binding_migration_ddl_has_no_sqlalchemy_bind_tokens(monkeypatch):
    migration = import_module("services.control_api.migrations.versions.0008_broker_binding")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    assert statements
    assert all(not text(statement)._bindparams for statement in statements)
