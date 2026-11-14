import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from . import operator_store
from .harness import Approval, RunBinding
from .operator_store import OperatorStore, OperatorStoreDenied


def _approval():
    binding = RunBinding("synthetic", "fixture-model", "fixture-api", *["a" * 64] * 6)
    return Approval(
        "fixture-approval", binding, datetime.now(UTC) + timedelta(minutes=1),
        2, Decimal("0.2"), Decimal("0.1"),
    )


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def _store_lifecycle(tmp_path):
    with pytest.raises(OperatorStoreDenied, match="EVALUATION_STORE_UNAVAILABLE"):
        OperatorStore(tmp_path / "absent")
    directory = _private_directory(tmp_path / "private")
    store = OperatorStore.provision(directory)
    with pytest.raises(OperatorStoreDenied, match="EVALUATION_STORE_UNAVAILABLE"):
        OperatorStore.provision(directory)
    approval = _approval()
    assert not store.consume(approval, ("DE-01",))
    store.register(approval, ("DE-01", "DE-02"))
    assert not store.consume(approval, ("DE-02", "DE-01"))
    assert not store.consume(replace(approval, max_calls=1), ("DE-01", "DE-02"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = executor.map(
            lambda _: OperatorStore(directory).consume(approval, ("DE-01", "DE-02")), range(2),
        )
        assert sorted(results) == [False, True]
    assert not store.reserve(approval, 1, "unregistered")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = executor.map(
            lambda _: OperatorStore(directory).reserve(approval, 1, "DE-01"), range(2),
        )
        assert sorted(results) == [False, True]
    with pytest.raises(OperatorStoreDenied, match="EVALUATION_ATTEMPT_UNAVAILABLE"):
        store.settle(approval, 1, "DE-01", "answered", Decimal("0.05"), {"fixture": "answer"})
    assert store.authorize(approval, 1, "DE-01")
    assert not store.authorize(approval, 1, "DE-01")
    store.settle(approval, 1, "DE-01", "answered", Decimal("0.05"), {"fixture": "answer"})
    restored = OperatorStore(directory)
    assert restored.states(approval) == {1: {"DE-01": "answered"}}
    assert restored.raw_result(approval, 1, "DE-01") == {"fixture": "answer"}
    assert restored.reserve(approval, 1, "DE-02")
    assert restored.authorize(approval, 1, "DE-02")
    restored.settle(approval, 1, "DE-02", "timeout", Decimal(0), None)
    assert not restored.reserve(approval, 2, "DE-01")
    with sqlite3.connect(directory / "evaluation.sqlite") as connection:
        assert connection.execute(
            "SELECT cost FROM attempts WHERE case_id='DE-02'",
        ).fetchone() == (100_000_000_000,)
        connection.execute(
            "UPDATE attempts SET raw_expires='2000-01-01' WHERE raw IS NOT NULL",
        )
    assert restored.raw_result(approval, 1, "DE-01") is None
    assert restored.purge_expired() == 1
    assert restored.states(approval) == {1: {"DE-01": "answered", "DE-02": "timeout"}}
    (directory / "evaluation.sqlite").chmod(0o644)
    with pytest.raises(OperatorStoreDenied, match="EVALUATION_STORE_UNAVAILABLE"):
        OperatorStore(directory)


def test_operator_store_is_durable_private_and_non_replayable(tmp_path):
    if os.name != "posix":
        with pytest.raises(OperatorStoreDenied, match="EVALUATION_PRIVATE_POSIX_STORAGE_REQUIRED"):
            OperatorStore(tmp_path)
        return
    _store_lifecycle(tmp_path)


def test_operator_admission_stops_on_expiry_or_recorded_overrun(tmp_path):
    if os.name != "posix":
        with pytest.raises(OperatorStoreDenied, match="EVALUATION_PRIVATE_POSIX_STORAGE_REQUIRED"):
            OperatorStore.provision(tmp_path)
        return
    store = OperatorStore.provision(_private_directory(tmp_path / "run"))
    approval = replace(_approval(), max_calls=4, max_cost=Decimal("1.00"))
    store.register(approval, ("DE-1", "DE-2", "DE-3"))
    assert store.consume(approval, ("DE-1", "DE-2", "DE-3"))
    assert store.reserve(approval, 1, "DE-1")
    assert store.reserve(approval, 1, "DE-2")
    with patch.object(operator_store, "_now", return_value=approval.expires_at):
        assert not store.authorize(approval, 1, "DE-2")
    assert store.authorize(approval, 1, "DE-1")
    store.settle(approval, 1, "DE-1", "answered", Decimal("0.11"), {"fixture": "overrun"})
    assert not store.authorize(approval, 1, "DE-2")
    assert not store.reserve(approval, 1, "DE-3")
