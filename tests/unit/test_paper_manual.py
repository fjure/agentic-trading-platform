"""Command completion never invents a broker outcome or discards an old attempt."""

from datetime import UTC, datetime, timedelta
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from services.control_api.paper_commands import PaperCommandError
from services.runtime.paper_manual import run_manual_order

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)
OPERATION = "11111111-2222-4333-8444-555555555555"


def runtime(status, *, finished=False):
    value = SimpleNamespace(
        _cycle=Lock(), _manual_pending=None, _owner_faulted=False, _faulted=False,
        clock=SimpleNamespace(now=lambda: NOW),
        _read=Mock(return_value={"profileVersion": 1}),
        _refresh=Mock(), _components=Mock(), _process_current_work=Mock(),
    )
    value.commands = SimpleNamespace(_call=Mock(side_effect=[status, finished, finished]))
    return value


def test_acknowledged_original_command_finishes_without_loading_credentials_or_refresh():
    value = runtime({"proposalId": "durable", "orderId": "known", "orderStatus": "ACKNOWLEDGED"},
                    finished=True)
    run_manual_order(value, {"operationId": OPERATION, "fence": 2}, "owner")
    value._read.assert_not_called()
    value._refresh.assert_not_called()
    value._components.assert_not_called()
    value._process_current_work.assert_not_called()
    assert value._manual_pending is None


def test_unknown_original_attempt_is_reconciled_even_after_original_ttl_expires():
    value = runtime({"proposalId": "durable", "orderId": "known", "orderStatus": "UNKNOWN"})
    claim = {"operationId": OPERATION, "fence": 2,
             "payload": {"expiresAt": (NOW-timedelta(seconds=1)).isoformat()}}
    run_manual_order(value, claim, "owner")
    value._refresh.assert_called_once_with({"profileVersion": 1})
    value._process_current_work.assert_not_called()
    assert value._manual_pending == {"id": OPERATION, "owner": "owner", "fence": 2}


def test_unknown_provider_failure_keeps_operation_pending_without_another_submit():
    value = runtime({"proposalId": "durable", "orderId": "known", "orderStatus": "UNKNOWN"})
    value._refresh.side_effect = RuntimeError("synthetic-outage")
    run_manual_order(value, {"operationId": OPERATION, "fence": 2}, "owner")
    value._process_current_work.assert_not_called()
    assert value._manual_pending is not None


def test_entry_fault_rejects_new_human_order_before_any_provider_or_proposal_creation():
    value = runtime(None)
    value.commands._call.side_effect = None
    value.commands._call.return_value = None
    value._faulted = True
    with pytest.raises(PaperCommandError) as failure:
        run_manual_order(value, {"operationId": OPERATION, "fence": 2}, "owner")
    assert failure.value.code == "ENTRY_PAUSED"
    value._refresh.assert_not_called()
    value._process_current_work.assert_not_called()
