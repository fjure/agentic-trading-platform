"""Pure lifecycle validation cannot manufacture or renew trading authority."""

from copy import deepcopy
from datetime import timedelta

import pytest

from services.execution.paper_lifecycle import validate_activation
from tests.contracts.test_alpaca_paper import NOW


def authority():
    return {
        "activationId": "11111111-2222-4333-8444-555555555555", "epoch": 1,
        "credentialGeneration": 1, "accountGeneration": 1, "policyVersion": 1,
        "profileVersion": 1, "budgetVersion": 1, "limitPolicyVersion": 1,
        "riskBudget": "100", "strategy": "mock-neutral", "symbols": ["SPY", "QQQ"],
        "ownerId": "execution-owner", "fencingToken": 7, "issuedAt": NOW.isoformat(),
        "expiresAt": (NOW+timedelta(seconds=30)).isoformat(),
        "operatingState": "RUNNING", "mode": "PAPER", "safetyState": "RUNNING",
        "proposalLinked": True,
    }


def test_current_exact_projection_is_immutable_and_bound_to_owner_symbol():
    raw = authority()
    original = deepcopy(raw)
    result = validate_activation(raw, now=NOW, symbol="SPY", owner_id="execution-owner",
                                 fencing_token=7)
    assert result is not None and result.expires_at == NOW+timedelta(seconds=30)
    assert raw == original
    assert validate_activation(raw, now=NOW, symbol="AAPL") is None
    assert validate_activation(raw, now=NOW, owner_id="other") is None
    assert validate_activation(raw, now=NOW, fencing_token=8) is None


@pytest.mark.parametrize("change", [
    {"epoch": 0}, {"profileVersion": True}, {"credentialGeneration": 0},
    {"safetyState": "HALTED"}, {"safetyState": "PAUSE_NEW"}, {"operatingState": "STOPPED"},
    {"mode": "LIVE"}, {"riskBudget": 100}, {"riskBudget": "0"}, {"proposalLinked": False},
    {"proposalLinked": 1}, {"symbols": ["SPY", "SPY"]}, {"symbols": ["AAPL"]},
    {"expiresAt": (NOW+timedelta(seconds=31)).isoformat()},
    {"issuedAt": (NOW+timedelta(seconds=1)).isoformat()}, {"extra": True},
])
def test_invalid_or_mismatched_authority_fails_closed(change):
    assert validate_activation(authority() | change, now=NOW) is None


def test_expiry_is_exclusive_and_cannot_be_extended_by_revalidation():
    raw = authority()
    assert validate_activation(raw, now=NOW+timedelta(seconds=29)) is not None
    assert validate_activation(raw, now=NOW+timedelta(seconds=30)) is None
    assert validate_activation(raw, now=NOW+timedelta(hours=1)) is None
