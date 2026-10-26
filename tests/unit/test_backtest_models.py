from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from packages.backtesting.models import (
    DataReference,
    ExperimentConfig,
    Fold,
    Manifest,
    WarmupBinding,
    fingerprint,
)

T = datetime(2026, 10, 20, 14, tzinfo=UTC)


def config(**changes):
    value = ExperimentConfig(
        UUID(int=1),
        "SPY",
        "buy-hold-v1",
        (),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0.000001"),
        (Fold(T - timedelta(days=1), T, T, T + timedelta(hours=1)),),
    )
    return replace(value, **changes)


def manifest(**changes):
    value = Manifest(
        config(),
        1,
        T + timedelta(days=1),
        (),
        (),
        ("cal-1",),
        ("quality-1",),
        ("coverage-1",),
        (WarmupBinding(T, T, ()),),
        "code-1",
    )
    return replace(value, **changes)


def test_canonical_hash_and_immutability():
    assert config().input_hash == config(start_cash=Decimal("100.00")).input_hash
    with localcontext() as ctx:
        ctx.prec = 2
        assert fingerprint(Decimal("123.123456")) == fingerprint("123.123456")
    assert manifest().manifest_hash == manifest().manifest_hash
    assert manifest().manifest_hash != manifest(code_version="code-2").manifest_hash
    with pytest.raises(FrozenInstanceError):
        config().symbol = "QQQ"


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "AAPL"},
        {"feed": "sip"},
        {"currency": "EUR"},
        {"strategy_id": "exec-code"},
        {"strategy_version": "2"},
        {"parameters": (("code", 2),)},
        {"start_cash": Decimal("0")},
        {"start_cash": Decimal("NaN")},
        {"start_cash": 100},
        {"fee_usd": Decimal("-1")},
        {"slippage_bps": Decimal("101")},
        {"quantity_step": Decimal("0.1")},
        {"folds": []},
    ],
)
def test_reject_invalid_experiments(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_fold_order_overlap_and_future():
    with pytest.raises(ValueError):
        Fold(T, T + timedelta(hours=1), T, T + timedelta(hours=2))
    with pytest.raises(ValueError):
        config(folds=(config().folds[0], config().folds[0]))
    with pytest.raises(ValueError):
        manifest(snapshot_at=T)
    with pytest.raises(ValueError):
        manifest(coverage_revisions=())
    with pytest.raises(ValueError):
        DataReference(UUID(int=1), "wrong", T)
    with pytest.raises(ValueError):
        manifest(
            decision_data=(
                DataReference(UUID(int=2), "a" * 64, T),
                DataReference(UUID(int=1), "b" * 64, T),
            )
        )


def test_sma_warmup_bound_to_training():
    cfg = config(strategy_id="sma-cross-v1", parameters=(("fast", 2), ("slow", 3)))
    with pytest.raises(ValueError):
        manifest(config=cfg)
    binding = WarmupBinding(
        T - timedelta(minutes=30), T, (T - timedelta(minutes=30), T - timedelta(minutes=15))
    )
    assert manifest(config=cfg, warmups=(binding,)).manifest_hash
