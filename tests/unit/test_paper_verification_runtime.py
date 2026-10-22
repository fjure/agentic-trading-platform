"""APW-03 runtime orchestration with fake commands and synthetic identities only."""

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest

from services.execution.paper_credentials import (
    PaperCredentialsUnavailable,
    PrivatePaperCredentials,
)
from services.execution.paper_verification import (
    PrivateIdentityProof,
    SanitizedVerificationSnapshot,
    VerificationResult,
)
from services.runtime.paper_verification import PaperVerificationRuntime

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
ACCOUNT = "11111111-2222-4333-8444-555555555555"
OWNER = "11111111-2222-4333-8444-555555555556"
OPERATION = "11111111-2222-4333-8444-555555555557"
CANARY = "synthetic-runtime-key-canary-never-a-real-key"
CLAIM = {
    "operationId": OPERATION, "operationType": "VERIFY_CONNECTION",
    "credentialGeneration": 7, "pinnedAccountId": ACCOUNT, "fence": 9,
}


class Commands:
    def __init__(self, claim=CLAIM, *, accept_finish=True):
        self.claimed, self.accept_finish = deepcopy(claim), accept_finish
        self.finishes, self.owners = [], []
        self.operation_status = "RUNNING"

    def claim(self, owner):
        self.owners.append(owner)
        return self.claimed

    def finish(self, *args, **kwargs):
        self.finishes.append((args, deepcopy(kwargs)))
        if self.accept_finish:
            self.operation_status = "BLOCKED" if kwargs["error_code"] else "SUCCEEDED"
        return self.accept_finish


def runtime(*, commands=None, result=None):
    commands = commands if commands is not None else Commands()
    reader = Mock()
    reader.load.return_value = PrivatePaperCredentials(7, CANARY, CANARY + "-secret", ACCOUNT)
    reader_factory = Mock(return_value=reader)
    verifier = Mock()
    verifier.verify.return_value = result or VerificationResult(
        SanitizedVerificationSnapshot(
            7, NOW, "paper-12345678", cash=Decimal("100000"), verified_at=NOW,
            market_data_feed="IEX", provider_verification="CONFIRMED",
        ), PrivateIdentityProof(7, ACCOUNT, NOW),
    )
    verifier_factory = Mock(return_value=verifier)
    instance = PaperVerificationRuntime(
        commands=commands, reader_factory=reader_factory, verifier_factory=verifier_factory,
        clock=Mock(now=Mock(return_value=NOW)), owner=OWNER,
    )
    return instance, commands, reader_factory, reader, verifier_factory, verifier


def test_empty_tick_never_constructs_reader_verifier_or_touches_credentials():
    instance, commands, readers, reader, factories, verifier = runtime(commands=Commands(None))
    assert instance.tick() == "READY"
    assert commands.owners == [OWNER] and commands.finishes == []
    readers.assert_not_called()
    reader.load.assert_not_called()
    factories.assert_not_called()
    verifier.verify.assert_not_called()


@pytest.mark.parametrize("kind", ["PAUSE", "STOP", "KILL"])
def test_controls_complete_without_setup_or_provider_dependencies(kind):
    commands = Commands(dict(CLAIM, operationType=kind))
    instance, _, readers, reader, factories, verifier = runtime(commands=commands)
    assert instance.tick() == "READY"
    assert commands.operation_status == "SUCCEEDED"
    assert commands.finishes[0][1] == {
        "public": {}, "private_account_id": None, "error_code": None,
    }
    readers.assert_not_called()
    reader.load.assert_not_called()
    factories.assert_not_called()
    verifier.verify.assert_not_called()


def test_claimed_verification_binds_generation_pin_owner_fence_and_private_uuid():
    instance, commands, readers, reader, factories, verifier = runtime()
    assert instance.tick() == "READY"
    readers.assert_called_once_with()
    reader.load.assert_called_once_with(7)
    verifier.verify.assert_called_once_with(7)
    assert factories.call_args.kwargs == {
        "credentials": reader, "clock": instance.clock,
        "expected_account_id": ACCOUNT, "pinned_account_id": ACCOUNT,
    }
    args, value = commands.finishes[0]
    assert args == (OPERATION, OWNER, 9)
    assert value["private_account_id"] == ACCOUNT and value["error_code"] is None
    assert value["public"]["cash"] == Decimal("100000")
    assert value["public"]["credential_generation"] == 7
    assert ACCOUNT not in repr(value["public"]) and CANARY not in repr(value)
    assert commands.operation_status == "SUCCEEDED"


@pytest.mark.parametrize("code", [
    "ACCOUNT_BLOCKED", "CREDENTIALS_INVALID", "PROVIDER_RATE_LIMITED",
])
def test_blocked_verification_never_passes_private_identity_for_promotion(code):
    result = VerificationResult(
        SanitizedVerificationSnapshot(7, NOW, blockers=(code,)),
        PrivateIdentityProof(7, ACCOUNT, NOW),
    )
    instance, commands, *_ = runtime(result=result)
    instance.tick()
    value = commands.finishes[0][1]
    assert value["error_code"] == code and value["private_account_id"] is None
    assert value["public"]["blockers"] == (code,)
    assert commands.operation_status == "BLOCKED"


@pytest.mark.parametrize("error,code", [
    (PaperCredentialsUnavailable(), "CREDENTIALS_MISSING"),
    (RuntimeError(CANARY), "PROVIDER_UNAVAILABLE"),
])
def test_reader_errors_are_canonical_redacted_and_do_not_construct_verifier(error, code, caplog):
    instance, commands, _, reader, factories, _ = runtime()
    reader.load.side_effect = error
    instance.tick()
    assert commands.finishes[0][1] == {
        "public": {}, "private_account_id": None, "error_code": code,
    }
    factories.assert_not_called()
    assert CANARY not in caplog.text


def test_missing_private_proof_is_blocked_and_stale_finish_never_changes_operation_state():
    result = VerificationResult(SanitizedVerificationSnapshot(7, NOW))
    instance, commands, *_ = runtime(result=result)
    instance.tick()
    assert commands.finishes[0][1]["error_code"] == "ACCOUNT_IDENTITY_MISMATCH"
    stale = Commands(accept_finish=False)
    instance, *_ = runtime(commands=stale)
    assert instance.tick() == "READY"  # Process health is separate from operation outcome.
    assert stale.operation_status == "RUNNING" and len(stale.finishes) == 1


def test_unexpected_verifier_exception_becomes_fixed_error_without_private_data(caplog):
    instance, commands, _, _, _, verifier = runtime()
    verifier.verify.side_effect = RuntimeError(CANARY)
    instance.tick()
    assert commands.finishes[0][1] == {
        "public": {}, "private_account_id": None, "error_code": "PROVIDER_UNAVAILABLE",
    }
    assert CANARY not in caplog.text


def test_nonverification_command_cannot_open_private_reader():
    instance, commands, readers, _, factories, _ = runtime(
        commands=Commands(CLAIM | {"operationType": "START"}),
    )
    instance.tick()
    readers.assert_not_called()
    factories.assert_not_called()
    assert commands.finishes[0][1]["error_code"] == "LEDGER_NOT_PREPARED"
