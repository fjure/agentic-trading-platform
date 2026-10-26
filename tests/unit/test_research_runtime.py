"""Research cannot consume safety capacity or silently enable private reads."""

from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.research.limits import (
    SharedAccountHostLimiter,
    existing_get_rate_limited,
    install_process_limiter,
)
from packages.research.registry import definitions
from services.runtime import config
from services.runtime.research import PaperResearchRuntime, SafetyPriority, safety_lane


def test_disabled_runtime_registers_metadata_without_constructing_provider(monkeypatch):
    connection = Mock()

    @contextmanager
    def transaction():
        yield connection

    engine = Mock(begin=transaction)
    factory = Mock(return_value=engine)
    monkeypatch.setattr(config, "database_engine", factory)
    register = Mock()
    monkeypatch.setattr("services.runtime.research.register", register)
    runtime = PaperResearchRuntime(config.RuntimeConfig(), SimpleNamespace(), Event())
    runtime.source = Mock(side_effect=AssertionError("disabled source constructed"))
    runtime.start()
    try:
        register.assert_called_once_with(connection)
        runtime.source.assert_not_called()
        assert runtime.thread is None
        values = connection.execute.call_args.args[1]
        assert values["state"] == "DISABLED" and values["license"] == "UNKNOWN"
        factory.assert_called_once_with("execution", deployment="paper", isolated_research=True)
    finally:
        runtime.close()


def test_safety_priority_stays_busy_until_all_overlapping_lanes_exit():
    limiter = SharedAccountHostLimiter("paper", 120, clock=lambda: 0)
    priority = SafetyPriority(limiter)
    with priority.work():
        with priority.work():
            assert not limiter.allow("data.alpaca.markets", research=True)
        assert not limiter.allow("data.alpaca.markets", research=True)
        assert limiter.allow("data.alpaca.markets", research=False)
    assert limiter.allow("data.alpaca.markets", research=True)


def test_safety_exception_releases_only_its_own_admission_guard():
    limiter = SharedAccountHostLimiter("paper", 120, clock=lambda: 0)

    class Coordinator:
        research_priority = SafetyPriority(limiter)

        @safety_lane
        def tick(self):
            assert not limiter.allow("paper-api.alpaca.markets", research=True)
            raise RuntimeError("synthetic")

    with pytest.raises(RuntimeError, match="synthetic"):
        Coordinator().tick()
    assert limiter.allow("paper-api.alpaca.markets", research=True)


@pytest.mark.parametrize("header", [None, "not-a-delay", "Wed, 21 Oct 2026 12:00:00 GMT", "999999"])
def test_existing_429_blocks_research_for_bounded_window_but_preserves_safety(header):
    now = [0]
    limiter = SharedAccountHostLimiter("paper", 120, clock=lambda: now[0])
    install_process_limiter(limiter)
    try:
        existing_get_rate_limited("https://data.alpaca.markets", header)
        assert not limiter.allow("data.alpaca.markets", research=True)
        assert limiter.allow("data.alpaca.markets", research=False)
        # Host backoff is specific; the account still shares its total budget.
        assert limiter.allow("paper-api.alpaca.markets", research=True)
        now[0] = 60
        assert limiter.allow("data.alpaca.markets", research=True)
    finally:
        install_process_limiter(None)


@pytest.mark.parametrize("role", ["api", "execution"])
def test_paper_research_pool_is_independent_bounded_and_preserves_role(monkeypatch, role):
    monkeypatch.setattr(
        config,
        "secret_fields",
        lambda *_: {"username": config.PAPER_ROLES[role] + "_login", "password": "synthetic-only"},
    )
    engine = config.database_engine(role, deployment="paper", isolated_research=True)
    try:
        assert engine.pool.size() == 1 and engine.pool._max_overflow == 0
        assert engine.pool.timeout() == 1
        assert engine.url.database == "lab_paper_runtime"
        assert engine.url.username == config.PAPER_ROLES[role] + "_login"
    finally:
        engine.dispose()


def test_registry_has_stable_separate_research_identities_and_bounded_symbol_history():
    assert definitions() == definitions()
    pairs = definitions()
    assert {m.symbol for _, m in pairs} == {"SPY", "QQQ"}
    assert len({i.instrument_id for i, _ in pairs}) == 2
    for instrument, mapping in pairs:
        assert instrument.instrument_id == mapping.instrument_id
        assert mapping.provider == "alpaca" and instrument.currency == "USD"
        assert instrument.base_unit == mapping.symbol
    qqq = next(mapping for _, mapping in pairs if mapping.symbol == "QQQ")
    assert qqq.valid_from.isoformat() == "2011-03-23T00:00:00+00:00"
