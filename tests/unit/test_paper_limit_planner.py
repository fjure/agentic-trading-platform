"""Bounded Paper LIMIT planning with synthetic public proof values only."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from packages.domain.models import AuditEvent, BrokerCapabilities, Capability, Target, TradeProposal
from services.worker.paper_limit import (
    PaperLimitPolicy,
    PaperPlanningInputs,
    evaluate_paper_limit,
)
from services.worker.planner import PlannerConfig, plan_proposal
from tests.unit.test_decision import NOW, FixedClock, SeededIds, engine, replay_corpus, state

CONFIG = PlannerConfig("paper-planner.v1", Decimal("999"))


def inputs(source=None, **changes):
    source = source or state()
    capability = BrokerCapabilities(
        "alpaca-paper", "SPY", 7, paper=Capability.CONFIRMED,
        regular_session=Capability.CONFIRMED, fractional=Capability.CONFIRMED,
        limit_order=Capability.CONFIRMED, quantity_step=Decimal("0.000000001"),
        price_tick=Decimal("0.0001"),
    )
    values = dict(
        policy=PaperLimitPolicy(), market_state_id=source.id,
        market_evidence_id=source.evidence.id, trace_id=source.trace_id,
        quote_source_id="alpaca-iex", quote_evidence_id="quote-1", quote_evidence_hash="b"*64,
        context_version=3, quote_context_version=3,
        portfolio_version=source.portfolio.ledger_version,
        account_generation=1, credential_generation=1, policy_version=1, profile_version=1,
        limit_policy_version=1, budget_version=1, capability_version=7, capability=capability,
        fractionable=Capability.CONFIRMED, fractional_limit_day=Capability.CONFIRMED,
        bid=Decimal("100.001"), ask=Decimal("100.009"), quote_provider_time=NOW,
        quote_observed_at=NOW, capability_provider_time=NOW, capability_observed_at=NOW,
        capability_valid_until=NOW+timedelta(seconds=120),
        approval_valid_until=NOW+timedelta(minutes=5),
        session_start=NOW.replace(hour=13, minute=30, second=0),
        session_end=NOW.replace(hour=20, minute=0, second=0), session_open=True,
        risk_budget=Decimal("100"), remaining_cash=source.portfolio.cash,
        equity=source.portfolio.equity, held_exposure=source.portfolio.exposure,
        reserved_exposure=Decimal(0), instrument_exposure=Decimal(0),
        instrument_reserved_exposure=Decimal(0), held_quantity=source.position.quantity,
        open_exit_quantity=Decimal(0),
    )
    values.update(changes)
    return PaperPlanningInputs(**values)


def plan(proof=None, *, source=None, decision=None, config=CONFIG, **kwargs):
    source = source or state()
    return plan_proposal(
        decision=decision or engine().decide(source, "v1"), market_state=source,
        config=config, clock=FixedClock(), ids=SeededIds(),
        paper_inputs=proof if proof is not None else inputs(source), require_paper=True, **kwargs,
    )


def test_fractional_buy_has_bounded_cost_cent_grid_ttl_and_immutable_provenance():
    proof = inputs()
    proposal = plan(proof)
    assert proposal is not None and proposal.target is Target.LONG
    assert proposal.limit_price == Decimal("100.010000")
    assert 0 < proposal.quantity < 1 and proposal.quantity.as_tuple().exponent == -9
    assert proposal.quantity*proposal.limit_price*Decimal("1.01") <= proof.risk_budget
    assert proposal.valid_until == NOW+timedelta(seconds=30)
    assert "PAPER_LIMIT_DAY_REGULAR_USD" in proposal.rationale_codes
    assert f"PAPER_INPUT_SHA256:{proof.digest()}" in proposal.rationale_codes
    assert "PAPER_LIMIT_POLICY:1" in proposal.rationale_codes
    assert "PAPER_CONTEXT_VERSION:3" in proposal.rationale_codes
    assert plan(proof) == proposal
    with pytest.raises(FrozenInstanceError):
        proof.risk_budget = Decimal("10000")


@pytest.mark.parametrize("field", ["account_generation", "credential_generation", "policy_version",
                                  "profile_version", "budget_version", "context_version"])
def test_authoritative_versions_change_identity_and_metadata(field):
    original = inputs()
    changes = {field: getattr(original, field)+1}
    if field == "context_version":
        changes["quote_context_version"] = changes[field]
    changed = replace(original, **changes)
    assert changed.digest() != original.digest()
    assert plan(changed).id != plan(original).id


def test_confidence_and_legacy_fixed_quantity_never_size_paper_orders():
    decision = engine().decide(state(), "v1")
    expected = plan(decision=decision)
    changed = plan(decision=replace(decision, confidence=Decimal("0.99")),
                   config=replace(CONFIG, fixed_entry_quantity=Decimal("1")))
    assert expected.quantity == changed.quantity
    assert expected.limit_price == changed.limit_price


@pytest.mark.parametrize("changes", [
    {"fractionable": Capability.UNKNOWN}, {"fractional_limit_day": Capability.UNKNOWN},
    {"fractional_limit_day": Capability.UNAVAILABLE},
])
def test_unconfirmed_fractional_envelope_uses_only_whole_shares(changes):
    assert plan(inputs(**changes)) is None  # 100USD cannot fund a whole100USD share plus buffer.
    proposal = plan(inputs(risk_budget=Decimal("500"), **changes))
    assert proposal.quantity == Decimal(4)


@pytest.mark.parametrize("field", ["paper", "limit_order", "regular_session"])
def test_unknown_required_capability_never_falls_back_to_market(field):
    proof = inputs()
    assert plan(replace(proof, capability=replace(proof.capability, **{
        field: Capability.UNKNOWN,
    }))) is None


@pytest.mark.parametrize("changes", [
    {"quote_provider_time": NOW-timedelta(seconds=30)},
    {"quote_provider_time": NOW+timedelta(microseconds=1)},
    {"quote_observed_at": NOW+timedelta(microseconds=1)},
    {"capability_provider_time": NOW-timedelta(seconds=120)},
    {"capability_observed_at": NOW+timedelta(microseconds=1)},
    {"capability_valid_until": NOW}, {"approval_valid_until": NOW},
    {"session_open": False}, {"session_end": NOW},
    {"session_start": NOW.replace(hour=0, minute=0, second=0)},
    {"quote_context_version": 4}, {"portfolio_version": 2}, {"limit_policy_version": 2},
    {"capability_version": 8}, {"market_evidence_id": "other-evidence"},
    {"market_state_id": "other-state"}, {"trace_id": "c"*32},
    {"bid": Decimal("101")}, {"ask": Decimal(0)}, {"remaining_cash": Decimal(0)},
    {"open_exit_quantity": Decimal(0), "risk_budget": Decimal("0.000000001")},
])
def test_missing_stale_or_contradictory_public_proof_abstains(changes):
    assert plan(inputs(**changes)) is None


def test_expiry_is_bounded_by_quote_capability_approval_session_and_decision():
    for changes in ({"quote_provider_time": NOW-timedelta(seconds=20)},
                    {"capability_valid_until": NOW+timedelta(seconds=10)},
                    {"approval_valid_until": NOW+timedelta(seconds=10)}):
        assert plan(inputs(**changes)).valid_until == NOW+timedelta(seconds=10)
    assert plan(inputs(approval_valid_until=NOW+timedelta(seconds=4))) is None


def test_tick_rounding_rejects_distance_and_preserves_both_provider_and_us_grids():
    proof = inputs(policy=PaperLimitPolicy(max_price_distance_fraction=Decimal("0.000001")))
    assert plan(proof) is None
    proof = inputs()
    proposal = plan(replace(proof, capability=replace(
        proof.capability, price_tick=Decimal("0.015"),
    )))
    assert proposal.limit_price == Decimal("100.020000")
    assert proposal.limit_price % Decimal("0.015") == 0
    assert proposal.limit_price % Decimal("0.01") == 0


def test_subdollar_prices_use_us_subcent_tick_without_price_rounding_down():
    proposal = plan(inputs(bid=Decimal("0.50001"), ask=Decimal("0.50005")))
    assert proposal.limit_price == Decimal("0.500100")


def test_joint_quantity_grid_never_violates_provider_step_or_nine_decimals():
    proof = inputs()
    proposal = plan(replace(proof, capability=replace(
        proof.capability, quantity_step=Decimal("0.0000000003"),
    )))
    assert proposal.quantity % Decimal("0.0000000003") == 0
    assert proposal.quantity.as_tuple().exponent == -9


def test_budget_exposures_and_reserved_buffer_reduce_buy_capacity():
    source = state()
    source = replace(source, portfolio=replace(source.portfolio, exposure=Decimal("30")))
    proof = inputs(source, risk_budget=Decimal("100"), reserved_exposure=Decimal("20"))
    proposal = plan(proof, source=source)
    assert Decimal(30)+Decimal(20)*Decimal("1.01")+proposal.quantity*proposal.limit_price*Decimal(
        "1.01") <= Decimal(100)
    cash_limited = plan(inputs(remaining_cash=Decimal("10")))
    assert cash_limited.quantity*cash_limited.limit_price*Decimal("1.01") <= Decimal(10)
    position_limited = plan(inputs(risk_budget=Decimal("10000"),
                                  instrument_reserved_exposure=Decimal("900"),
                                  reserved_exposure=Decimal("900")))
    assert (Decimal(900)*Decimal("1.01")
            + position_limited.quantity*position_limited.limit_price*Decimal("1.01")) <= Decimal(
                1000)


def test_conservative_fresh_marks_can_exceed_snapshot_but_never_understate_it():
    source = state()
    source = replace(source, portfolio=replace(source.portfolio, exposure=Decimal("30")))
    proof = inputs(source, risk_budget=Decimal("100"), held_exposure=Decimal("40"))
    proposal = plan(proof, source=source)
    assert proposal is not None
    assert Decimal(40)+proposal.quantity*proposal.limit_price*Decimal("1.01") <= Decimal(100)
    assert plan(replace(proof, held_exposure=Decimal("29")), source=source) is None


@pytest.mark.parametrize("change", [
    {"market_state_id": "different-state"}, {"trace_id": "d"*32}, {"valid_until": NOW},
])
def test_decision_reference_or_expiry_cannot_be_rebound_by_paper_proof(change):
    decision = engine().decide(state(), "v1")
    assert plan(decision=replace(decision, **change)) is None


def test_quote_proof_and_policy_fingerprint_change_proposal_identity():
    proof = inputs()
    original = plan(proof)
    for changed in (replace(proof, quote_evidence_hash="c"*64),
                    replace(proof, policy=replace(proof.policy, quote_age_seconds=29)),
                    replace(proof, quote_evidence_id="quote-2")):
        assert plan(changed).id != original.id
        assert plan(changed).rationale_codes != original.rationale_codes


def test_sell_uses_floor_bid_and_only_unreserved_long_holdings():
    source = state(previous="102")
    source = replace(source, position=replace(source.position, quantity=Decimal("1.2345678919")))
    proposal = plan(inputs(source, open_exit_quantity=Decimal("0.2")), source=source)
    assert proposal.target is Target.FLAT
    assert proposal.limit_price == Decimal("100.000000")
    assert proposal.quantity == Decimal("1.034567891")
    assert proposal.quantity+Decimal("0.2") <= source.position.quantity
    assert plan(inputs(source, open_exit_quantity=Decimal("2")), source=source) is None


def test_abstain_flat_without_holdings_and_absent_required_proof_never_plan():
    assert plan(source=state(previous="101")) is None
    assert plan(source=state(previous="102")) is None
    assert plan_proposal(decision=engine().decide(state(), "v1"), market_state=state(),
                         config=CONFIG, clock=FixedClock(), ids=SeededIds(),
                         require_paper=True) is None
    assert evaluate_paper_limit(decision=engine().decide(state(), "v1"), market_state=state(),
                                inputs=None, now=NOW).reason_code == "PAPER_PROOF_MISSING"


@pytest.mark.parametrize("change", [{"proposal_ttl_seconds": 31}, {"quote_age_seconds": 121},
                                   {"quantity_decimals": 10}, {"cash_buffer_fraction": "0.009"},
                                   {"max_position_fraction": "0.11"}, {"order_type": "MARKET"},
                                   {"version": True}])
def test_policy_cannot_weaken_fixed_safety_bounds(change):
    with pytest.raises(ValueError):
        PaperLimitPolicy(**change)


@settings(max_examples=75)
@given(st.integers(1, 1000000), st.integers(0, 1000000))
def test_buy_rounddown_cost_properties_at_low_ambient_precision(budget_cents, cash_cents):
    with localcontext() as context:
        context.prec = 6
        proof = inputs(risk_budget=Decimal(budget_cents)/100,
                       remaining_cash=Decimal(min(cash_cents, 1000000))/100)
        proposal = plan(proof)
    if proposal is not None:
        with localcontext() as context:
            context.prec = 100
            cost = proposal.quantity*proposal.limit_price*Decimal("1.01")
            assert cost <= min(proof.risk_budget, proof.remaining_cash, proof.equity*Decimal("0.1"))
            assert proposal.quantity % Decimal("0.000000001") == 0
            assert proposal.limit_price >= proof.ask


def test_legacy_hundred_case_replay_is_unchanged_when_paper_is_not_requested():
    for source in replay_corpus():
        decision = engine().decide(source, "corpus.v1")
        args = dict(decision=decision, market_state=source, config=CONFIG,
                    clock=FixedClock(), ids=SeededIds())
        assert plan_proposal(**args) == plan_proposal(
            **args, paper_inputs=None, require_paper=False,
        )


def test_paper_limit_and_provenance_exist_before_audited_persistence():
    written = []

    class Transaction:
        def get(self, model, entity_id):
            return None

        def insert(self, entity):
            if isinstance(entity, TradeProposal):
                assert entity.limit_price is not None
                assert "PAPER_LIMIT_DAY_REGULAR_USD" in entity.rationale_codes
                assert any(isinstance(item, AuditEvent) and item.entity_id == entity.id
                           for item in written)
            written.append(entity)

    class Ledger:
        @contextmanager
        def transaction(self):
            yield Transaction()

    proposal = plan(ledger=Ledger())
    assert written[-1] == proposal and proposal.limit_price is not None
