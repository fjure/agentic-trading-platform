"""Human provenance and exact LIMIT terms share public proofs, never model choices."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from packages.domain import models as m
from packages.domain.paper_workflow import HumanOrderRequest, PaperOrderSide, PaperTradeProposal
from packages.persistence.ledger import _encode
from packages.risk.paper_limits import (
    check_human_proposal,
    proposal_source_reasons,
    read_human_request,
)
from services.worker.paper_human_limit import assess_human_limit
from tests.unit.test_decision import NOW, state
from tests.unit.test_paper_limit_planner import inputs


def request(proof=None, **changes):
    proof = proof or inputs()
    terms = {key: getattr(proof, key) for key in (
        "account_generation", "credential_generation", "policy_version", "profile_version",
        "budget_version", "limit_policy_version", "risk_budget",
    )}
    terms.update(id="human-request", operation_id="operation", account_id="paper", symbol="SPY",
                 side=PaperOrderSide.BUY, quantity=Decimal("0.5"), limit_price=Decimal("100.00"),
                 created_at=NOW, valid_until=NOW+timedelta(seconds=30), trace_id="b"*32)
    return HumanOrderRequest(**(terms | changes))


def case():
    source = state()
    proof = inputs(source)
    human = request(proof)
    proposal = PaperTradeProposal.from_human_request(
        human, proposal_id="human-proposal", market_state_id=source.id, created_at=NOW,
    )
    return source, proof, human, proposal


def test_exact_passive_price_quantity_and_distinct_http_trace_are_preserved():
    source, proof, human, proposal = case()
    original = (human, proof.digest())
    result = assess_human_limit(human, source, proof, NOW)
    assert result.reason_code is None and result.input_hash == proof.digest()
    assert result.valid_until == human.valid_until
    assert human.trace_id != source.trace_id == proof.trace_id
    assert proposal.instrument_id == "SPY" and proposal.target is m.Target.LONG
    assert proposal.decision_id is None
    assert proposal.rationale_codes == ("PAPER_HUMAN_LIMIT", "HUMAN_REQUEST:human-request")
    assert (human, proof.digest()) == original
    assert check_human_proposal(proposal, human, source, proof, NOW, original_proof=proof) == ()
    with pytest.raises(FrozenInstanceError):
        result.valid_until += timedelta(seconds=1)


@pytest.mark.parametrize("price", ["100.00", "100.01", "99.91", "100.10"])
def test_inside_distance_uses_confirmed_price_without_rounding(price):
    source, proof, human, _ = case()
    human = replace(human, limit_price=Decimal(price))
    assert assess_human_limit(human, source, proof, NOW).reason_code is None
    assert human.limit_price == Decimal(price)


@pytest.mark.parametrize("price,reason", [
    ("100.001", "INVALID_REQUEST"), ("99.90", "POLICY_MISMATCH"),
    ("100.11", "POLICY_MISMATCH"),
])
def test_invalid_tick_or_distance_is_rejected_without_repricing(price, reason):
    source, proof, human, _ = case()
    human = replace(human, limit_price=Decimal(price))
    assert assess_human_limit(human, source, proof, NOW).reason_code == reason
    assert human.limit_price == Decimal(price)


@pytest.mark.parametrize("field", [
    "account_generation", "credential_generation", "policy_version", "profile_version",
    "budget_version", "limit_policy_version",
])
def test_every_authoritative_generation_and_version_must_match(field):
    source, proof, human, _ = case()
    changed = replace(human, **{field: getattr(human, field)+1})
    assert assess_human_limit(changed, source, proof, NOW).reason_code == "VERSION_CONFLICT"


@pytest.mark.parametrize("change,reason", [
    ({"quote_provider_time": NOW-timedelta(seconds=30)}, "STALE_QUOTE"),
    ({"quote_observed_at": NOW+timedelta(microseconds=1)}, "STALE_QUOTE"),
    ({"capability_valid_until": NOW}, "CAPABILITY_UNCONFIRMED"),
    ({"approval_valid_until": NOW}, "APPROVAL_EXPIRED"),
    ({"session_open": False}, "SESSION_CLOSED"),
    ({"quote_context_version": 4}, "POLICY_MISMATCH"),
    ({"quote_evidence_id": "another"}, None),
])
def test_current_public_proof_boundaries(change, reason):
    source, proof, human, _ = case()
    assert assess_human_limit(human, source, replace(proof, **change), NOW).reason_code == reason


def test_fractional_grid_requires_fresh_confirmed_combination_and_never_resizes():
    source, proof, human, _ = case()
    for field in ("fractionable", "fractional_limit_day"):
        changed = replace(proof, **{field: m.Capability.UNKNOWN})
        assert assess_human_limit(human, source, changed, NOW).reason_code == (
            "CAPABILITY_UNCONFIRMED"
        )
    changed = replace(proof, capability=replace(proof.capability, quantity_step=Decimal("0.3")))
    assert assess_human_limit(human, source, changed, NOW).reason_code == "INVALID_REQUEST"
    assert human.quantity == Decimal("0.5")


def test_buy_capacity_includes_buffer_concurrent_budget_and_broker_cash():
    source, proof, human, _ = case()
    # Raw notional fits, but a 1% buffer is an additional hard obligation.
    human = replace(human, quantity=Decimal("1"), limit_price=Decimal("100"))
    assert assess_human_limit(human, source, proof, NOW).reason_code == "BUDGET_EXCEEDED"
    human = replace(human, quantity=Decimal("0.5"))
    for changed in (
        replace(proof, remaining_cash=Decimal("50")),
        replace(proof, reserved_exposure=Decimal("50")),
        replace(proof, held_exposure=Decimal("50")),
    ):
        assert assess_human_limit(human, source, changed, NOW).reason_code == "BUDGET_EXCEEDED"


def test_sell_uses_only_free_long_quantity_and_preserves_passive_limit():
    source = state()
    source = replace(source, position=replace(source.position, quantity=Decimal("2")),
                     portfolio=replace(source.portfolio, exposure=Decimal("200"),
                                       cash=source.portfolio.equity-Decimal("200")))
    proof = inputs(source, held_exposure=Decimal("200"), instrument_exposure=Decimal("200"),
                   open_exit_quantity=Decimal("0.25"), risk_budget=Decimal("1000"))
    human = request(proof, side=PaperOrderSide.SELL, quantity=Decimal("1.75"),
                    limit_price=Decimal("100.01"))
    assert assess_human_limit(human, source, proof, NOW).reason_code is None
    assert assess_human_limit(replace(human, quantity=Decimal("1.750000001")),
                              source, proof, NOW).reason_code == "INSUFFICIENT_POSITION"


def test_ttl_is_exclusive_and_cannot_extend_the_request_or_quote():
    source, proof, human, _ = case()
    assert assess_human_limit(human, source, proof, human.valid_until).reason_code == (
        "APPROVAL_EXPIRED"
    )
    assert assess_human_limit(replace(human, valid_until=NOW+timedelta(seconds=31)),
                              source, proof, NOW).reason_code == "APPROVAL_EXPIRED"
    earlier = replace(proof, quote_provider_time=NOW-timedelta(seconds=5))
    assert assess_human_limit(human, source, earlier, NOW).valid_until == NOW+timedelta(seconds=25)


def test_decimal_assessment_is_independent_of_ambient_precision():
    source, proof, human, _ = case()
    expected = assess_human_limit(human, source, proof, NOW)
    with localcontext() as arithmetic:
        arithmetic.prec = 6
        assert assess_human_limit(human, source, proof, NOW) == expected


def test_missing_current_market_state_is_a_public_fail_closed_assessment():
    _, proof, human, _ = case()
    result = assess_human_limit(human, None, proof, NOW)
    assert result.reason_code == "RECONCILIATION_REQUIRED"
    assert result.valid_until is result.input_hash is None


def test_submit_recheck_removes_own_reservation_only_and_retains_concurrent_buffer():
    source, original, human, proposal = case()
    current = replace(original, remaining_cash=source.portfolio.cash-Decimal("50.5"),
                      reserved_exposure=Decimal("50"), instrument_reserved_exposure=Decimal("50"))
    assert check_human_proposal(proposal, human, source, current, NOW, original_proof=original,
                                own_notional=Decimal("50")) == ()
    concurrent = replace(current, reserved_exposure=Decimal("100"),
                         instrument_reserved_exposure=Decimal("100"))
    assert check_human_proposal(proposal, human, source, concurrent, NOW,
                                original_proof=original, own_notional=Decimal("50")) == (
        "BUDGET_EXCEEDED",
    )


class Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, values):
        assert str(sql) == "SELECT public.paper_human_request_read(:request)"
        assert values == {"request": "human-request"}
        return SimpleNamespace(scalar_one_or_none=lambda: self.raw)


def test_strict_human_source_reader_and_common_provenance_never_load_a_decision():
    source, _, human, proposal = case()
    raw = _encode(human)
    connection = Connection(raw)
    tx = SimpleNamespace(get=lambda *_: pytest.fail("human source loaded a model decision"))
    assert read_human_request(connection, human.id) == human
    assert proposal_source_reasons(connection, tx, proposal, source, NOW) == ()
    assert proposal_source_reasons(Connection(raw | {"trace_id": "c"*32}), tx,
                                   proposal, source, NOW) == ("HUMAN_REQUEST_MISMATCH",)
    assert read_human_request(Connection(raw | {"untrusted": "canary"}), human.id) is None
    assert read_human_request(Connection(None), human.id) is None


def test_changed_terms_or_original_proof_cannot_replace_the_immutable_source():
    source, proof, human, proposal = case()
    assert check_human_proposal(replace(proposal, limit_price=Decimal("100.01")), human,
                                source, proof, NOW, original_proof=proof) == (
        "HUMAN_REQUEST_MISMATCH",
    )
    assert check_human_proposal(proposal, human, source,
                                replace(proof, quote_evidence_id="other"), NOW,
                                original_proof=proof) == ("PAPER_LIMIT_PROOF_MISMATCH",)


def test_legacy_trade_proposal_fields_and_serialized_event_shape_are_unchanged():
    assert "proposal_source" not in {field.name for field in fields(m.TradeProposal)}
    assert "human_request_id" not in {field.name for field in fields(m.TradeProposal)}
    _, _, _, proposal = case()
    # Compatibility properties are not new serialized fields.
    assert "instrument_id" not in _encode(proposal)


def test_manual_distance_never_exceeds_point_one_percent_even_with_looser_policy():
    source, proof, human, _ = case()
    proof = replace(proof, policy=replace(proof.policy, max_price_distance_fraction=Decimal(".01")))
    human = replace(human, limit_price=Decimal("100.20"))
    assert assess_human_limit(human, source, proof, NOW).reason_code == "POLICY_MISMATCH"


def test_whole_quantity_does_not_require_fractional_capability_or_model_decision():
    source, proof, _, _ = case()
    proof = replace(proof, risk_budget=Decimal("200"), fractionable=m.Capability.UNKNOWN,
                    fractional_limit_day=m.Capability.UNKNOWN)
    human = request(proof, quantity=Decimal("1"))
    assert assess_human_limit(human, source, proof, NOW).reason_code is None


def test_actual_limit_price_selects_subdollar_grid_without_changing_confirmed_terms():
    source, proof, _, _ = case()
    proof = replace(proof, bid=Decimal(".9998"), ask=Decimal("1"))
    human = request(proof, limit_price=Decimal(".9999"))
    assert assess_human_limit(human, source, proof, NOW).reason_code is None
    assert assess_human_limit(replace(human, limit_price=Decimal(".99995")),
                              source, proof, NOW).reason_code == "INVALID_REQUEST"
