"""Period economics and fail-closed precision/time boundaries, without provider or SQL."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.risk.paper_daily import (
    DailyRiskObservation,
    DailyRiskReference,
    EquityInterval,
    assess_daily_loss,
)

NOW = datetime(2026, 10, 21, 10, tzinfo=UTC)
MIDNIGHT = NOW.replace(hour=0)
HASH = "a" * 64


def exact(value):
    return EquityInterval(Decimal(value), Decimal(value))


def reference():
    return DailyRiskReference(
        "UTC_DAY", MIDNIGHT, MIDNIGHT + timedelta(days=1), exact("100000"), Decimal("100000"), HASH
    )


def observation(equity="100000", flow="0"):
    return DailyRiskObservation(HASH, NOW, exact(equity), exact(flow), True, "b" * 64)


def assess(ref=None, obs=None, now=NOW):
    return assess_daily_loss(
        ref or reference(), obs or observation(), now=now, max_loss_fraction=Decimal("0.01")
    )


def test_historical_position_gain_is_not_an_input_to_period_loss():
    value = assess(obs=observation("98999"))
    assert value.reasons == ("DAILY_LOSS_LIMIT",)
    assert value.loss_upper == Decimal("1001")


@pytest.mark.parametrize(
    "equity,flow,loss",
    [
        ("109000", "10000", "1000"),
        ("89000", "-10000", "1000"),
        ("100100", "0", "0"),
        ("100000", "0", "0"),
    ],
)
def test_external_flows_do_not_hide_losses_or_increase_frozen_cap(equity, flow, loss):
    value = assess(obs=observation(equity, flow))
    assert value.loss_upper == Decimal(loss)
    assert value.cap_lower == Decimal("1000")
    assert bool(value.reasons) == (Decimal(loss) >= Decimal("1000"))


def test_cent_uncertainty_uses_worst_loss_and_lowest_cap():
    ref = replace(
        reference(),
        opening_equity=EquityInterval.reported_cents(Decimal("100000")),
        capital_lower=Decimal("99999.99"),
    )
    value = assess(ref, observation("99000.01"))
    assert value.loss_upper == Decimal("1000")
    assert value.cap_lower == Decimal("999.9999")
    assert value.reasons == ("DAILY_LOSS_LIMIT",)


@pytest.mark.parametrize(
    "change",
    [
        {"funding_complete": False},
        {"reference_hash": "c" * 64},
        {"observed_at": NOW + timedelta(microseconds=1)},
        {"observed_at": NOW - timedelta(seconds=120)},
        {"observed_at": MIDNIGHT - timedelta(microseconds=1)},
    ],
)
def test_unproven_stale_or_misbound_evidence_closes_gate(change):
    assert assess(obs=replace(observation(), **change)).reasons == (
        "PAPER_DAILY_EVIDENCE_UNPROVEN",
    )


def test_first_day_is_labeled_and_expires_exactly_at_next_utc_midnight():
    ref = replace(
        reference(), kind="FIRST_DAY", starts_at=NOW, account_created_at=NOW - timedelta(hours=1)
    )
    assert not assess(ref).reasons
    assert assess(
        ref, replace(observation(), observed_at=ref.expires_at), now=ref.expires_at
    ).reasons == ("PAPER_DAILY_EVIDENCE_UNPROVEN",)
    with pytest.raises(FrozenInstanceError):
        ref.capital_lower = Decimal("200000")


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "UNKNOWN"},
        {"profile": "other"},
        {"capital_lower": Decimal("100000.01")},
        {"evidence_hash": "unproven"},
        {"starts_at": NOW},
        {"expires_at": NOW + timedelta(days=1)},
        {"kind": "FIRST_DAY", "starts_at": NOW},
        {"kind": "FIRST_DAY", "starts_at": NOW, "account_created_at": MIDNIGHT},
        {"kind": "FIRST_DAY", "starts_at": NOW, "account_created_at": NOW + timedelta(seconds=1)},
    ],
)
def test_invalid_reference_cannot_be_used(change):
    with pytest.raises(ValueError):
        replace(reference(), **change)


def test_missing_reference_and_unsafe_policy_fail_closed():
    assert assess_daily_loss(
        None, observation(), now=NOW, max_loss_fraction=Decimal("0.01")
    ).reasons
    for fraction in (Decimal(0), Decimal("0.01001"), Decimal(-1)):
        with pytest.raises(ValueError):
            assess_daily_loss(reference(), observation(), now=NOW, max_loss_fraction=fraction)


def test_shared_engine_uses_paper_period_pnl_and_preserves_legacy_formula():
    from packages.risk.engine import evaluate
    from tests.property.test_risk import LIMITS, NOW, _facts, _proposal

    ref = DailyRiskReference(
        "UTC_DAY",
        NOW.replace(hour=0),
        NOW.replace(hour=0) + timedelta(days=1),
        exact("1000"),
        Decimal("1000"),
        HASH,
    )
    obs = DailyRiskObservation(HASH, NOW, exact("989"), exact("0"), True, "b" * 64)
    facts = _facts(
        equity=Decimal("989"),
        cash=Decimal("989"),
        unrealized_pnl=Decimal("500"),
        paper_daily_required=True,
        paper_reference=ref,
        paper_observation=obs,
    )
    proposal = _proposal(quantity="0.5")
    assert evaluate(proposal, facts, LIMITS) == ("DAILY_LOSS_LIMIT",)
    assert (
        evaluate(
            proposal,
            replace(
                facts, paper_daily_required=False, paper_reference=None, paper_observation=None
            ),
            LIMITS,
        )
        == ()
    )
    today_flat = replace(
        facts,
        equity=Decimal("1000"),
        cash=Decimal("1000"),
        unrealized_pnl=Decimal("-1000"),
        paper_observation=replace(obs, current_equity=exact("1000")),
    )
    assert evaluate(proposal, today_flat, LIMITS) == ()
    assert "DAILY_LOSS_LIMIT" in evaluate(
        proposal,
        replace(
            today_flat, paper_daily_required=False, paper_reference=None, paper_observation=None
        ),
        LIMITS,
    )
    slight_loss = replace(
        facts,
        equity=Decimal("994"),
        cash=Decimal("994"),
        paper_observation=replace(obs, current_equity=exact("994")),
    )
    assert not evaluate(proposal, slight_loss, LIMITS)
    assert evaluate(
        proposal, slight_loss, replace(LIMITS, max_daily_loss_fraction=Decimal("0.005"))
    ) == ("DAILY_LOSS_LIMIT",)


def test_shared_engine_never_falls_back_when_paper_authority_is_missing():
    from packages.risk.engine import evaluate
    from tests.property.test_risk import LIMITS, _facts, _proposal

    facts = _facts(paper_daily_required=True)
    assert evaluate(_proposal(), facts, LIMITS) == ("PAPER_DAILY_REFERENCE_MISSING",)


@pytest.mark.parametrize(
    "identity,expected",
    [
        (("lab_runtime", "atl_execution", "normal_login"), False),
        (("lab_paper_runtime", "atl_paper_execution", "atl_paper_execution_login"), True),
        (("lab_paper_runtime", "atl_execution", "atl_paper_execution_login"), None),
        (("lab_paper_runtime", "atl_paper_execution", "other_login"), None),
    ],
)
def test_paper_daily_model_is_bound_to_fixed_database_role_and_login(identity, expected):
    from unittest.mock import Mock

    from packages.risk.paper_daily_sql import is_isolated_paper

    connection = Mock()
    connection.execute.return_value.one.return_value = identity
    if expected is None:
        with pytest.raises(PermissionError):
            is_isolated_paper(connection)
    else:
        assert is_isolated_paper(connection) is expected
