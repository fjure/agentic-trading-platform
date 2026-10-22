"""Independent immutable LIMIT proof checks in addition to normal risk gates."""

import re
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext

from packages.domain.models import Capability
from services.worker.paper_limit import PaperPlanningInputs, evaluate_paper_limit


def check_paper_proposal(proposal, state, decision, proof, now, *, original_proof=None):
    if type(proof) is not PaperPlanningInputs or decision is None:
        return ("PAPER_LIMIT_PROOF_MISSING",)
    original = proof if original_proof is None else original_proof
    if type(original) is not PaperPlanningInputs:
        return ("PAPER_LIMIT_PROOF_MISSING",)
    dynamic = {"remaining_cash", "reserved_exposure", "instrument_reserved_exposure",
               "open_exit_quantity", "held_exposure", "instrument_exposure", "approval_valid_until"}
    if any(getattr(proof, field.name) != getattr(original, field.name)
           for field in fields(PaperPlanningInputs) if field.name not in dynamic):
        return ("PAPER_LIMIT_PROOF_MISMATCH",)
    expected = {
        "PAPER_LIMIT_DAY_REGULAR_USD",
        f"PAPER_LIMIT_POLICY:{proof.limit_policy_version}",
        f"PAPER_QUOTE:{proof.quote_evidence_id}",
        *(f"PAPER_{name.upper()}:{getattr(proof, name)}" for name in (
            "context_version", "portfolio_version", "account_generation", "credential_generation",
            "policy_version", "profile_version", "budget_version", "capability_version",
        )),
    }
    codes = proposal.rationale_codes
    hashes = [code for code in codes if code.startswith("PAPER_INPUT_SHA256:")]
    if (
        proposal.market_state_id != state.id or proposal.instrument_id != state.instrument_id
        or proposal.decision_id != decision.id or proposal.trace_id != state.trace_id
        or proposal.target.value != decision.choice.value
        or not expected.issubset(codes) or len(hashes) != 1
        or re.fullmatch(r"PAPER_INPUT_SHA256:[0-9a-f]{64}", hashes[0]) is None
        or hashes[0] != "PAPER_INPUT_SHA256:" + original.digest()
        or len([code for code in codes if code.startswith("PAPER_")]) != len(expected) + 1
        or proposal.limit_price is None or proposal.created_at > now
        or not now < proposal.valid_until
        or proposal.valid_until > proposal.created_at + timedelta(
            seconds=proof.policy.proposal_ttl_seconds
        )
        or proposal.valid_until > decision.valid_until
    ):
        return ("PAPER_LIMIT_PROOF_MISMATCH",)
    planned = evaluate_paper_limit(
        decision=decision, market_state=state, inputs=original, now=proposal.created_at
    )
    if (
        planned.quantity is None or proposal.quantity != planned.quantity
        or proposal.limit_price != planned.limit_price or proposal.valid_until > planned.valid_until
    ):
        return ("PAPER_LIMIT_PROOF_MISMATCH",)
    # The proposal's own reservation may already exist at submit recheck. Normal
    # risk separately rechecks every concurrent obligation under the account lock.
    # This independent policy envelope must not count that same obligation twice.
    envelope = replace(
        proof, remaining_cash=state.portfolio.cash, reserved_exposure=Decimal(0),
        instrument_reserved_exposure=Decimal(0), open_exit_quantity=Decimal(0),
    )
    result = evaluate_paper_limit(decision=decision, market_state=state, inputs=envelope, now=now)
    if result.quantity is None:
        return (result.reason_code,)
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        fractional = (
            proof.fractionable is Capability.CONFIRMED
            and proof.fractional_limit_day is Capability.CONFIRMED
            and proof.capability.permits(order_type="LIMIT", fractional=True, regular_session=True)
        )
        quantity_unit = Decimal(1).scaleb(-proof.policy.quantity_decimals if fractional else 0)
        if (
            proposal.quantity <= 0 or proposal.quantity > result.quantity
            or proposal.quantity % quantity_unit != 0
            or proposal.quantity % proof.capability.quantity_step != 0
            or proposal.limit_price != result.limit_price
            or proposal.valid_until > result.valid_until
        ):
            return ("PAPER_LIMIT_ENVELOPE_MISMATCH",)
    return ()


def paper_proposal_reasons(connection, tx, proposal, state, now):
    from packages.domain.models import DecisionResult
    from services.worker.paper_planning import read_planning_inputs, read_registered_plan

    proof = read_planning_inputs(connection, state.id, execution=True)
    original = read_registered_plan(connection, proposal)
    if original is None:
        return ("PAPER_LIMIT_PROOF_MISSING",)
    decision = tx.get(DecisionResult, proposal.decision_id) if proposal.decision_id else None
    return check_paper_proposal(proposal, state, decision, proof, now, original_proof=original)
