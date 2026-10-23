"""Fresh activation and immutable proposal binding for isolated Paper only."""

from sqlalchemy import text

from services.execution.paper_lifecycle import validate_activation


def read_activation(connection, account_id, proposal_id, now, *, symbol=None,
                    owner_id=None, fencing_token=None):
    raw = connection.execute(
        text("SELECT public.paper_activation_guard(:account,:proposal)"),
        {"account": account_id, "proposal": proposal_id},
    ).scalar_one_or_none()
    return validate_activation(raw, now=now, symbol=symbol, owner_id=owner_id,
                               fencing_token=fencing_token,
                               require_proposal=proposal_id is not None)


def activation_reasons(connection, tx, account_id, proposal, now):
    from services.worker.paper_planning import read_registered_plan

    activation = read_activation(connection, account_id, proposal.id, now,
                                 symbol=proposal.instrument_id)
    if activation is None:
        return ("PAPER_ACTIVATION_INVALID",)
    proof = read_registered_plan(connection, proposal)
    if proof is None or any(getattr(activation, key) != getattr(proof, key) for key in (
        "credential_generation", "account_generation", "policy_version", "profile_version",
        "budget_version", "limit_policy_version", "risk_budget",
    )) or proposal.valid_until > activation.expires_at:
        return ("PAPER_ACTIVATION_MISMATCH",)
    return ()
