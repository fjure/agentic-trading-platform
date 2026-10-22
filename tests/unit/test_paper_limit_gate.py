"""Independent Paper risk envelope checks over actual immutable planner output."""

import re
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.domain.models import Choice, Target
from packages.risk.paper_limits import check_paper_proposal
from tests.unit.test_decision import NOW, engine, state
from tests.unit.test_paper_limit_planner import inputs, plan


def case(**proof_changes):
    source = state()
    decision = engine().decide(source, "v1")
    proof = inputs(source, **proof_changes)
    proposal = plan(proof, source=source, decision=decision)
    assert proposal is not None
    return proposal, source, decision, proof


def denied(proposal, source, decision, proof, now=NOW):
    reasons = check_paper_proposal(proposal, source, decision, proof, now)
    assert reasons
    assert isinstance(reasons, tuple)
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", reason) for reason in reasons)
    assert "canary-private-provider-text" not in repr(reasons)
    return reasons


def test_unchanged_actual_plan_passes_without_mutating_immutable_inputs():
    proposal, source, decision, proof = case()
    original = (proposal, source, decision, proof.digest())
    assert check_paper_proposal(proposal, source, decision, proof, NOW) == ()
    assert (proposal, source, decision, proof.digest()) == original


@pytest.mark.parametrize("price", [None, Decimal("100.02"), Decimal("100.00"), Decimal("100.001")])
def test_missing_looser_or_off_tick_limit_cannot_replace_planned_price(price):
    proposal, source, decision, proof = case()
    denied(replace(proposal, limit_price=price), source, decision, proof)


@pytest.mark.parametrize("mode", ["oversized", "ten-decimal", "provider-grid"])
def test_quantity_must_stay_inside_recomputed_envelope_and_both_grids(mode):
    if mode == "provider-grid":
        original = inputs()
        proposal, source, decision, proof = case(
            capability=replace(original.capability, quantity_step=Decimal("0.25"))
        )
        quantity = Decimal("0.1")
    else:
        proposal, source, decision, proof = case()
        quantity = (
            proposal.quantity + Decimal("0.000000001")
            if mode == "oversized"
            else Decimal("0.1234567891")
        )
    denied(replace(proposal, quantity=quantity), source, decision, proof)


def test_any_changed_planned_quantity_is_rejected_without_execution_resize():
    proposal, source, decision, proof = case()
    smaller = replace(proposal, quantity=proposal.quantity - Decimal("0.000000001"))
    # Execution revalidates the immutable plan; it never resizes it, even downward.
    assert denied(smaller, source, decision, proof) == ("PAPER_LIMIT_PROOF_MISMATCH",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_id", "other-decision"),
        ("market_state_id", "other-state"),
        ("instrument_id", "QQQ"),
        ("trace_id", "c" * 32),
        ("target", Target.FLAT),
        ("created_at", NOW + timedelta(microseconds=1)),
        ("valid_until", NOW + timedelta(seconds=31)),
    ],
)
def test_direct_proposal_binding_or_expiry_change_fails(field, value):
    proposal, source, decision, proof = case()
    denied(replace(proposal, **{field: value}), source, decision, proof)


def test_expiry_is_strict_at_boundary_and_cannot_outlive_decision():
    proposal, source, decision, proof = case()
    denied(proposal, source, decision, proof, now=proposal.valid_until)
    shortened = replace(decision, valid_until=NOW + timedelta(seconds=1))
    denied(proposal, source, shortened, proof)


@pytest.mark.parametrize(
    "prefix",
    [
        "PAPER_LIMIT_DAY_REGULAR_USD",
        "PAPER_LIMIT_POLICY:",
        "PAPER_QUOTE:",
        "PAPER_CONTEXT_VERSION:",
        "PAPER_PORTFOLIO_VERSION:",
        "PAPER_ACCOUNT_GENERATION:",
        "PAPER_CREDENTIAL_GENERATION:",
        "PAPER_POLICY_VERSION:",
        "PAPER_PROFILE_VERSION:",
        "PAPER_BUDGET_VERSION:",
        "PAPER_CAPABILITY_VERSION:",
        "PAPER_INPUT_SHA256:",
    ],
)
def test_each_required_immutable_metadata_binding_is_mandatory(prefix):
    proposal, source, decision, proof = case()
    changed = replace(
        proposal,
        rationale_codes=tuple(
            code for code in proposal.rationale_codes if not code.startswith(prefix)
        ),
    )
    denied(changed, source, decision, proof)


@pytest.mark.parametrize(
    "mode", ["duplicate-hash", "malformed-hash", "unbound-hash", "extra-proof", "duplicate-version"]
)
def test_ambiguous_or_changed_metadata_cannot_authorize_a_plan(mode):
    proposal, source, decision, proof = case()
    codes = proposal.rationale_codes
    if mode == "duplicate-hash":
        codes += (f"PAPER_INPUT_SHA256:{proof.digest()}",)
    elif mode in {"malformed-hash", "unbound-hash"}:
        codes = tuple(
            "PAPER_INPUT_SHA256:" + ("z" * 64 if mode == "malformed-hash" else "d" * 64)
            if code.startswith("PAPER_INPUT_SHA256:")
            else code
            for code in codes
        )
    elif mode == "extra-proof":
        codes += ("PAPER_UNREVIEWED:canary-private-provider-text",)
    else:
        codes += ("PAPER_CONTEXT_VERSION:3",)
    denied(replace(proposal, rationale_codes=codes), source, decision, proof)


@pytest.mark.parametrize(
    "field",
    [
        "account_generation",
        "credential_generation",
        "policy_version",
        "profile_version",
        "budget_version",
        "context_version",
        "portfolio_version",
        "limit_policy_version",
        "capability_version",
    ],
)
def test_authoritative_generation_or_version_change_invalidates_existing_proposal(field):
    proposal, source, decision, proof = case()
    changes = {field: getattr(proof, field) + 1}
    if field == "context_version":
        changes["quote_context_version"] = changes[field]
    if field == "capability_version":
        changes["capability"] = replace(proof.capability, version=changes[field])
    denied(proposal, source, decision, replace(proof, **changes))


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote_evidence_id", "other-quote"),
        ("quote_evidence_hash", "e" * 64),
        ("market_evidence_id", "other-evidence"),
        ("market_state_id", "other-state"),
        ("trace_id", "d" * 32),
        ("ask", Decimal("100.02")),
        ("quote_provider_time", NOW - timedelta(seconds=30)),
        ("capability_valid_until", NOW),
        ("approval_valid_until", NOW),
        ("session_open", False),
    ],
)
def test_quote_reference_hash_prices_and_freshness_cannot_be_substituted(field, value):
    proposal, source, decision, proof = case()
    denied(proposal, source, decision, replace(proof, **{field: value}))


@pytest.mark.parametrize(
    "mode", ["state", "decision-reference", "decision-trace", "decision-choice"]
)
def test_referenced_state_and_decision_cannot_be_swapped(mode):
    proposal, source, decision, proof = case()
    if mode == "state":
        source = replace(source, id="other-state")
    elif mode == "decision-reference":
        decision = replace(decision, market_state_id="other-state")
    elif mode == "decision-trace":
        decision = replace(decision, trace_id="d" * 32)
    else:
        decision = replace(decision, choice=Choice.FLAT)
    denied(proposal, source, decision, proof)


@pytest.mark.parametrize("proof", [None, object(), {}])
def test_missing_or_untyped_proof_never_uses_legacy_envelope(proof):
    proposal, source, decision, _ = case()
    assert denied(proposal, source, decision, proof) == ("PAPER_LIMIT_PROOF_MISSING",)


def test_missing_decision_never_uses_legacy_envelope():
    proposal, source, _, proof = case()
    assert denied(proposal, source, None, proof) == ("PAPER_LIMIT_PROOF_MISSING",)


def test_existing_own_reservation_is_not_counted_twice_by_independent_envelope():
    proposal, source, decision, proof = case()
    own_notional = proposal.quantity * proposal.limit_price
    reserved = replace(
        proof,
        remaining_cash=proof.remaining_cash - own_notional * Decimal("1.01"),
        reserved_exposure=own_notional,
        instrument_reserved_exposure=own_notional,
    )
    assert (
        check_paper_proposal(proposal, source, decision, reserved, NOW, original_proof=proof) == ()
    )
    # This gate checks immutable planning policy only. Concurrent obligations are
    # still independently enforced by normal account-locked budget/reservation gates.


@pytest.mark.parametrize(
    "field,value",
    [
        ("quote_evidence_hash", "f" * 64),
        ("quote_evidence_id", "replacement-quote"),
        ("bid", Decimal("100.0009")),
        ("quote_observed_at", NOW - timedelta(microseconds=1)),
        ("risk_budget", Decimal("101")),
    ],
)
def test_registered_original_proof_is_not_replaceable_by_changed_immutable_authority(field, value):
    proposal, source, decision, original = case()
    current = replace(original, **{field: value})
    reasons = check_paper_proposal(
        proposal, source, decision, current, NOW, original_proof=original
    )
    assert reasons and all(re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", reason) for reason in reasons)


def test_registered_original_digest_must_match_proposal_even_if_live_envelope_is_valid():
    proposal, source, decision, proof = case()
    substituted = replace(proof, quote_evidence_hash="f" * 64)
    reasons = check_paper_proposal(
        proposal, source, decision, proof, NOW, original_proof=substituted
    )
    assert reasons


@pytest.mark.parametrize(
    "mode", ["missing", "hash-changed", "hash-malformed", "extra-key", "invalid-inputs"]
)
def test_missing_or_corrupt_registered_sql_source_never_falls_back_to_current_proof(mode):
    from types import SimpleNamespace

    from packages.risk.paper_limits import paper_proposal_reasons
    from tests.unit.test_paper_planning_ports import EXECUTION, Connection, Result, serialized

    proposal, source, decision, proof = case()
    registered = {"inputHash": proof.digest(), "inputs": serialized()}
    if mode == "missing":
        registered = None
    elif mode == "hash-changed":
        registered["inputHash"] = "f" * 64
    elif mode == "hash-malformed":
        registered["inputHash"] = True
    elif mode == "extra-key":
        registered["credentials"] = "canary-private-provider-text"
    else:
        registered["inputs"]["session_open"] = "true"

    class RegistryConnection(Connection):
        def execute(self, statement, parameters=None):
            if "paper_limit_plan_read" in str(statement):
                self.calls.append((str(statement), parameters))
                return Result(registered)
            return super().execute(statement, parameters)

    def forbidden_legacy_read(*_):
        raise AssertionError("missing registry source must not enter a legacy path")

    connection = RegistryConnection(raw=serialized(), identity=EXECUTION)
    reasons = paper_proposal_reasons(
        connection, SimpleNamespace(get=forbidden_legacy_read), proposal, source, NOW
    )
    assert reasons == ("PAPER_LIMIT_PROOF_MISSING",)
    assert connection.calls[-1][1] == {"proposal": proposal.id}
    assert len(connection.calls) == 3


def test_valid_registered_sql_source_and_current_proof_reach_actual_gate():
    from types import SimpleNamespace

    from packages.domain.models import DecisionResult
    from packages.risk.paper_limits import paper_proposal_reasons
    from tests.unit.test_paper_planning_ports import EXECUTION, Connection, Result, serialized

    proposal, source, decision, proof = case()

    class RegistryConnection(Connection):
        def execute(self, statement, parameters=None):
            if "paper_limit_plan_read" in str(statement):
                self.calls.append((str(statement), parameters))
                return Result({"inputHash": proof.digest(), "inputs": serialized()})
            return super().execute(statement, parameters)

    def get(model, identity):
        assert model is DecisionResult and identity == proposal.decision_id
        return decision

    connection = RegistryConnection(raw=serialized(), identity=EXECUTION)
    assert paper_proposal_reasons(connection, SimpleNamespace(get=get), proposal, source, NOW) == ()
    assert len(connection.calls) == 3
