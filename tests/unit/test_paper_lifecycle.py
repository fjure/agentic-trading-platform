"""Immutable plan identity must match the current short-lived activation."""

from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.risk.paper_lifecycle import activation_reasons, read_activation
from services.execution.paper_lifecycle import validate_activation
from tests.contracts.test_alpaca_paper import NOW
from tests.unit.test_paper_activation import authority


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def execute(self, statement, parameters):
        assert str(statement) == "SELECT public.paper_activation_guard(:account,:proposal)"
        self.calls.append(parameters)
        return SimpleNamespace(scalar_one_or_none=lambda: self.raw)


def context(monkeypatch):
    import services.worker.paper_planning as planning

    proof = SimpleNamespace(
        credential_generation=1, account_generation=1, policy_version=1, profile_version=1,
        budget_version=1, limit_policy_version=1, risk_budget=Decimal("100"),
    )
    proposal = SimpleNamespace(id="proposal", instrument_id="SPY",
                               valid_until=NOW+timedelta(seconds=30))
    monkeypatch.setattr(planning, "read_registered_plan", lambda *_: proof)
    return proof, proposal


def test_guard_uses_exact_proposal_binding_and_returns_immutable_value():
    connection = Connection(authority())
    result = read_activation(connection, "paper", "proposal", NOW, symbol="SPY",
                             owner_id="execution-owner", fencing_token=7)
    assert connection.calls == [{"account": "paper", "proposal": "proposal"}]
    assert result is not None
    with pytest.raises(FrozenInstanceError):
        result.expires_at += timedelta(seconds=1)


@pytest.mark.parametrize("field", [
    "credential_generation", "account_generation", "policy_version", "profile_version",
    "budget_version", "limit_policy_version", "risk_budget",
])
def test_each_original_plan_generation_and_version_is_bound(monkeypatch, field):
    proof, proposal = context(monkeypatch)
    assert activation_reasons(Connection(authority()), None, "paper", proposal, NOW) == ()
    setattr(proof, field, getattr(proof, field)+1)
    assert activation_reasons(Connection(authority()), None, "paper", proposal, NOW) == (
        "PAPER_ACTIVATION_MISMATCH",
    )


def test_proposal_cannot_outlive_activation_or_be_rebound_after_epoch_change(monkeypatch):
    _, proposal = context(monkeypatch)
    proposal.valid_until += timedelta(microseconds=1)
    assert activation_reasons(Connection(authority()), None, "paper", proposal, NOW) == (
        "PAPER_ACTIVATION_MISMATCH",
    )
    # SQL denies old proposal linkage after a new activation, even when every
    # version number happens to be identical. The pure boundary must honor it.
    assert activation_reasons(Connection(authority() | {"epoch": 2, "proposalLinked": False}),
                              None, "paper", proposal, NOW) == ("PAPER_ACTIVATION_INVALID",)


def test_invalid_types_offsets_and_currency_like_strings_do_not_create_authority():
    for change in ({"riskBudget": "1e2"}, {"riskBudget": "NaN"}, {"symbols": [True]},
                   {"strategy": []}, {"ownerId": 3}, {"fencingToken": True},
                   {"issuedAt": NOW.isoformat().replace("+00:00", "+02:00")}):
        assert validate_activation(authority() | change, now=NOW) is None


def test_missing_sql_activation_and_missing_immutable_plan_fail_closed(monkeypatch):
    import services.worker.paper_planning as planning

    _, proposal = context(monkeypatch)
    assert activation_reasons(Connection(None), None, "paper", proposal, NOW) == (
        "PAPER_ACTIVATION_INVALID",
    )
    monkeypatch.setattr(planning, "read_registered_plan", lambda *_: None)
    assert activation_reasons(Connection(authority()), None, "paper", proposal, NOW) == (
        "PAPER_ACTIVATION_MISMATCH",
    )
