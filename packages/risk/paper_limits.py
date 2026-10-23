"""Independent immutable LIMIT proof checks in addition to normal risk gates."""

import re
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.domain.models import Capability
from packages.domain.paper_workflow import (
    HumanOrderRequest,
    PaperOrderSide,
    PaperTradeProposal,
    ProposalSourceKind,
)
from services.worker.paper_human_limit import assess_human_limit
from services.worker.paper_limit import PaperPlanningInputs, evaluate_paper_limit

_DYNAMIC = {"remaining_cash", "reserved_exposure", "instrument_reserved_exposure",
            "open_exit_quantity", "held_exposure", "instrument_exposure", "approval_valid_until"}


def is_human_proposal(proposal):
    return (type(proposal) is PaperTradeProposal
            and proposal.proposal_source is ProposalSourceKind.HUMAN_REQUEST)


def read_human_request(connection, request_id):
    from packages.persistence.ledger import _decode

    raw = connection.execute(
        text("SELECT public.paper_human_request_read(:request)"), {"request": request_id},
    ).scalar_one_or_none()
    if type(raw) is not dict or set(raw) != {field.name for field in fields(HumanOrderRequest)}:
        return None
    try:
        return _decode(HumanOrderRequest, raw)
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def proposal_source_reasons(connection, tx, proposal, state, now):
    """The same honest source admission is used before reservation and submit."""
    from packages.domain.models import DecisionResult

    if is_human_proposal(proposal):
        request = read_human_request(connection, proposal.human_request_id)
        if request is None:
            return ("SOURCE_MISSING",)
        try:
            proposal.validate_human_request(request)
        except ValueError:
            return ("HUMAN_REQUEST_MISMATCH",)
        if (proposal.market_state_id != state.id or proposal.instrument_id != state.instrument_id
                or not proposal.created_at <= now < proposal.valid_until):
            return ("PORTFOLIO_OR_SOURCE_STALE",)
        return ()
    decision = tx.get(DecisionResult, proposal.decision_id)
    if decision is None:
        return ("SOURCE_MISSING",)
    if (decision.valid_until <= now or decision.market_state_id != state.id
            or proposal.trace_id != state.trace_id or decision.choice.value != proposal.target.value
            or decision.typed_answers is None):
        return ("PORTFOLIO_OR_SOURCE_STALE",)
    return ()


def check_human_proposal(proposal, request, state, proof, now, *, original_proof,
                         own_notional=Decimal(0), own_exit_quantity=Decimal(0)):
    if (not is_human_proposal(proposal) or type(request) is not HumanOrderRequest
            or type(proof) is not PaperPlanningInputs
            or type(original_proof) is not PaperPlanningInputs):
        return ("PAPER_LIMIT_PROOF_MISSING",)
    try:
        proposal.validate_human_request(request)
    except ValueError:
        return ("HUMAN_REQUEST_MISMATCH",)
    if (proposal.market_state_id != state.id
            or any(getattr(proof, field.name) != getattr(original_proof, field.name)
                   for field in fields(PaperPlanningInputs) if field.name not in _DYNAMIC)):
        return ("PAPER_LIMIT_PROOF_MISMATCH",)
    planned = assess_human_limit(request, state, original_proof, proposal.created_at)
    if planned.reason_code is not None or proposal.valid_until > planned.valid_until:
        return (planned.reason_code or "PAPER_LIMIT_PROOF_MISMATCH",)
    # Retain every concurrent obligation, removing this proposal's reservation
    # exactly once. The account lock protects both the SQL proof and ownership.
    with localcontext() as arithmetic:
        arithmetic.prec = 100
        if (own_notional < 0 or own_exit_quantity < 0
                or own_notional > proof.reserved_exposure
                or own_notional > proof.instrument_reserved_exposure
                or own_exit_quantity > proof.open_exit_quantity):
            return ("PAPER_LIMIT_PROOF_MISMATCH",)
        current = replace(
            proof,
            remaining_cash=min(state.portfolio.cash, proof.remaining_cash+own_notional*(
                Decimal(1)+proof.policy.cash_buffer_fraction
            )),
            reserved_exposure=proof.reserved_exposure-own_notional,
            instrument_reserved_exposure=proof.instrument_reserved_exposure-own_notional,
            open_exit_quantity=proof.open_exit_quantity-own_exit_quantity,
        )
    result = assess_human_limit(request, state, current, now)
    if result.reason_code is not None or proposal.valid_until > result.valid_until:
        return (result.reason_code or "PAPER_LIMIT_PROOF_MISMATCH",)
    return ()


def check_paper_proposal(proposal, state, decision, proof, now, *, original_proof=None):
    if type(proof) is not PaperPlanningInputs or decision is None:
        return ("PAPER_LIMIT_PROOF_MISSING",)
    original = proof if original_proof is None else original_proof
    if type(original) is not PaperPlanningInputs:
        return ("PAPER_LIMIT_PROOF_MISSING",)
    if any(getattr(proof, field.name) != getattr(original, field.name)
           for field in fields(PaperPlanningInputs) if field.name not in _DYNAMIC):
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
    if is_human_proposal(proposal):
        request = read_human_request(connection, proposal.human_request_id)
        own = connection.execute(text("""
            SELECT reserved_notional FROM public.risk_reservations
            WHERE proposal_id=:proposal AND account_id=:account AND state='ACTIVE'
        """), {"proposal": proposal.id, "account": proposal.account_id}).scalar_one_or_none()
        own_notional = (own if own is not None and proposal.side is PaperOrderSide.BUY
                        else Decimal(0))
        own_exit = (proposal.quantity if own is not None and proposal.side is PaperOrderSide.SELL
                    else Decimal(0))
        return check_human_proposal(proposal, request, state, proof, now, original_proof=original,
                                    own_notional=own_notional, own_exit_quantity=own_exit)
    decision = tx.get(DecisionResult, proposal.decision_id) if proposal.decision_id else None
    return check_paper_proposal(proposal, state, decision, proof, now, original_proof=original)
