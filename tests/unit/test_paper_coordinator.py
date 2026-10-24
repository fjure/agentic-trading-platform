"""Coordinator scheduling and authority boundaries without provider or database I/O."""

from datetime import timedelta
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from packages.persistence.ledger import PostgresLedger
from services.control_api.paper_commands import PaperCommandError
from services.runtime.config import RuntimeConfig
from services.runtime.paper_coordinator import PaperOwner, PaperRuntimeCoordinator
from tests.contracts.test_alpaca_paper import NOW


def runtime():
    readers = Mock(side_effect=AssertionError("credential access before explicit preparation"))
    config = RuntimeConfig(deployment="paper", broker_provider="alpaca-paper")
    value = PaperRuntimeCoordinator(config, PostgresLedger(engine=Mock()),
                                    clock=Mock(now=lambda: NOW), reader_factory=readers)
    value.commands = Mock()
    value.commands._call.return_value = None
    return value, readers


def test_empty_paper_runtime_has_no_secret_or_provider_construction():
    value, readers = runtime()
    assert value.recovery_tick() == value.heartbeat_tick() == value.cancel_tick() == "READY"
    assert value._cancel_service is None
    readers.assert_not_called()
    assert value.process is value.broker is value.binding is value.gateway is None


def test_cancel_progresses_with_entry_cycle_blocked_and_entry_faulted():
    value, readers = runtime()
    value.process = PaperOwner("owner", 7)
    value._faulted = True
    service = Mock()
    service.tick.return_value = "CANCEL_PENDING"
    value._cancel_service = service
    completed, errors = Event(), []

    def cancel():
        try:
            assert value.cancel_tick() == "CANCEL_PENDING"
            completed.set()
        except Exception as error:
            errors.append(error)

    with value._cycle:
        thread = Thread(target=cancel)
        thread.start()
        try:
            assert completed.wait(3), "entry I/O blocked the independent cancel lane"
        finally:
            thread.join(1)
    thread.join(3)
    assert not thread.is_alive() and errors == []
    service.tick.assert_called_once_with(owner_id="owner", fencing_token=7)
    readers.assert_not_called()
    value.commands._call.assert_not_called()


def test_cancel_never_uses_a_lost_execution_owner():
    value, readers = runtime()
    value.process = PaperOwner("owner", 7)
    value._owner_faulted = True
    value._cancel_service = Mock()
    assert value.cancel_tick() == "UNAVAILABLE"
    value._cancel_service.tick.assert_not_called()
    readers.assert_not_called()


def test_prepared_but_unconfigured_profile_claims_no_provider_authority():
    value, readers = runtime()
    value.commands._call.return_value = {"profileVersion": 0}
    with patch.object(value, "_ensure_owner") as owner:
        assert value.recovery_tick() == "READY"
    owner.assert_called_once_with()
    readers.assert_not_called()
    assert value.broker is None


def test_expired_start_never_collects_or_writes_activation():
    value, readers = runtime()
    claim = {"payload": {"expiresAt": NOW.isoformat()}}
    with pytest.raises(PaperCommandError) as error:
        value.start(claim, "command-owner")
    assert error.value.code == "APPROVAL_EXPIRED"
    value.commands._call.assert_not_called()
    readers.assert_not_called()


def test_failed_reconciliation_closes_existing_activation_and_stops_further_submits():
    value, readers = runtime()
    value.process = PaperOwner("owner", 7)
    value.commands._call.side_effect = [{"profileVersion": 1, "activation": {"id": "prior"}}, True]
    with patch.object(value, "_refresh", side_effect=RuntimeError("synthetic-provider-failure")), \
         patch.object(value, "_risk_and_submit") as submit:
        assert value.recovery_tick() == "DATA_UNAVAILABLE"
    # Already admitted current work is checked before observations get new
    # versions; after a failed refresh no further work may be admitted.
    submit.assert_called_once_with()
    assert value.commands._call.call_args.args == (
        "SELECT public.paper_restart_close(:owner,:fence)", {"owner": "owner", "fence": 7},
    )
    readers.assert_not_called()


def test_recovery_while_paused_does_not_restart_strategy_or_extend_approval():
    value, _ = runtime()
    value.process = PaperOwner("owner", 7)
    value.commands._call.side_effect = [
        {"profileVersion": 1, "activation": None}, {"profileVersion": 1, "activation": None},
    ]
    value.outbox.publish_one = Mock(return_value=None)
    with patch.object(value, "_refresh") as refresh, \
         patch.object(value, "_risk_and_submit") as submit:
        assert value.recovery_tick() == "READY"
    refresh.assert_called_once()
    submit.assert_not_called()
    assert all("activation_begin" not in c.args[0] for c in value.commands._call.call_args_list)


def test_command_network_wait_cannot_block_independent_owner_heartbeat():
    value, _ = runtime()
    value.process = PaperOwner("owner", 7)
    value.commands._call.side_effect = [{"profileVersion": 1}, {
        "activationId": "11111111-2222-4333-8444-555555555555",
        "expiresAt": (NOW+timedelta(seconds=30)).isoformat(),
    }]
    entered, release = Event(), Event()
    errors = []

    def refresh(_context):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(receipt_id="receipt")

    claim = {"payload": {"expiresAt": (NOW+timedelta(seconds=30)).isoformat()},
             "operationId": "operation", "fence": 2}

    def start():
        try:
            value.start(claim, "command-owner")
        except Exception as failure:
            errors.append(failure)

    with patch.object(value, "_refresh", side_effect=refresh), \
         patch("services.execution.recovery_health.heartbeat_execution_owner") as heartbeat:
        thread = Thread(target=start)
        thread.start()
        try:
            assert entered.wait(3)
            assert value.heartbeat_tick() == "READY"
            heartbeat.assert_called_once_with(ledger=value.ledger, account_id="paper",
                                               owner_id="owner", fencing_token=7)
        finally:
            release.set()
            thread.join(3)
    assert not thread.is_alive() and errors == []


def test_lost_heartbeat_sets_sticky_local_fault_and_never_reclaims_it():
    value, readers = runtime()
    value.process = PaperOwner("owner", 7)
    with patch("services.execution.recovery_health.heartbeat_execution_owner",
               side_effect=RuntimeError("fenced")):
        with pytest.raises(RuntimeError, match="fenced"):
            value.heartbeat_tick()
    value.commands._call.return_value = {"profileVersion": 1}
    assert value.recovery_tick() == "UNAVAILABLE"
    with patch("services.execution.recovery_health.heartbeat_execution_owner") as heartbeat:
        assert value.heartbeat_tick() == "UNAVAILABLE"
        heartbeat.assert_not_called()
    readers.assert_not_called()


def test_submit_fault_keeps_reconciliation_running_without_further_entry_attempts():
    value, _ = runtime()
    value.process = PaperOwner("owner", 7)
    context = {"profileVersion": 1, "activation": {"id": "original"}}
    value.commands._call.return_value = context
    value.outbox.publish_one = Mock(return_value=None)
    with patch.object(value, "_risk_and_submit", side_effect=RuntimeError("storage")) as submit, \
         patch.object(value, "_refresh") as refresh, \
         patch("services.execution.recovery_health.heartbeat_execution_owner") as heartbeat:
        assert value.recovery_tick() == "UNAVAILABLE"
        assert value.heartbeat_tick() == "UNAVAILABLE"
        assert value.recovery_tick() == "UNAVAILABLE"
    submit.assert_called_once_with()
    assert refresh.call_count == 2
    heartbeat.assert_called_once_with(ledger=value.ledger, account_id="paper",
                                      owner_id="owner", fencing_token=7)
    assert any("paper_restart_close" in c.args[0] for c in value.commands._call.call_args_list)


def test_unconfigured_profile_rejects_start_before_any_provider_construction():
    value, readers = runtime()
    value.commands._call.return_value = {"profileVersion": 0}
    claim = {"payload": {"expiresAt": (NOW+timedelta(seconds=30)).isoformat()}}
    with patch.object(value, "_refresh") as refresh:
        with pytest.raises(PaperCommandError) as error:
            value.start(claim, "command-owner")
        assert error.value.code == "LEDGER_NOT_PREPARED"
    refresh.assert_not_called()
    readers.assert_not_called()


def test_restart_owner_fence_is_established_before_claiming_start():
    value, _ = runtime()
    steps = []
    value.commands._call.return_value = {"profileVersion": 0}
    value.verifier.tick = lambda: steps.append("claim") or "READY"
    with patch.object(value, "_ensure_owner", side_effect=lambda: steps.append("restart")):
        assert value.command_tick() == "READY"
    assert steps == ["restart", "claim"]


def test_current_worker_plan_is_processed_before_binding_version_refresh():
    value, _ = runtime()
    value.process = PaperOwner("owner", 7)
    context = {"profileVersion": 1, "activation": {"id": "original"}}
    value.commands._call.side_effect = [context, context]
    value.outbox.publish_one = Mock(return_value=None)
    steps = []
    with patch.object(value, "_risk_and_submit", side_effect=lambda: steps.append("pending")), \
         patch.object(value, "_refresh", side_effect=lambda _: steps.append("refresh")):
        assert value.recovery_tick() == "READY"
    assert steps == ["pending", "refresh", "pending"]


def test_observed_manual_outcome_can_finish_while_new_entry_market_data_is_unavailable():
    value, _ = runtime()
    value.process = PaperOwner("owner", 7)
    pending = {"id": "original-operation", "owner": "command-owner", "fence": 3}
    value._manual_pending = pending
    value.commands._call.side_effect = [{"profileVersion": 1, "activation": None}, True]
    with patch.object(value, "_refresh", side_effect=RuntimeError("market-unavailable")), \
         patch.object(value, "_risk_and_submit") as submit:
        assert value.recovery_tick() == "DATA_UNAVAILABLE"
    assert value._manual_pending is None
    submit.assert_not_called()
    assert value.commands._call.call_args.args == (
        "SELECT public.paper_human_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence)",
        pending,
    )
