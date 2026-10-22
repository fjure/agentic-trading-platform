"""Strict public SQL proof decoding; no credential or broker capability lives here."""

from dataclasses import fields
from datetime import datetime

from sqlalchemy import text

from packages.domain.models import BrokerCapabilities, Capability
from services.worker.paper_limit import PaperLimitPolicy, PaperPlanningInputs


def read_planning_inputs(connection, state_id, *, execution=False):
    identity = tuple(
        connection.execute(text("SELECT current_database(),current_user,session_user")).one()
    )
    role = "atl_paper_execution" if execution is True else "atl_paper_worker"
    if type(execution) is not bool or identity != ("lab_paper_runtime", role, role + "_login"):
        raise PermissionError("PAPER_PLANNER_IDENTITY_MISMATCH")
    raw = connection.execute(
        text("SELECT public.paper_planner_inputs(:state)"), {"state": state_id}
    ).scalar_one_or_none()
    return decode_planning_inputs(raw, state_id)


def decode_planning_inputs(raw, state_id):
    if raw is None:
        return None
    expected = {field.name for field in fields(PaperPlanningInputs)}
    if not isinstance(raw, dict) or set(raw) != expected:
        return None
    try:
        value = dict(raw)
        policy = value["policy"]
        if not isinstance(policy, dict) or set(policy) != {
            field.name for field in fields(PaperLimitPolicy)
        }:
            return None
        value["policy"] = PaperLimitPolicy(**policy)
        capability = value["capability"]
        if not isinstance(capability, dict) or set(capability) != {
            field.name for field in fields(BrokerCapabilities)
        }:
            return None
        capability = dict(capability)
        for key in (
            "paper",
            "regular_session",
            "continuous_session",
            "fractional",
            "market_order",
            "limit_order",
        ):
            capability[key] = Capability(capability[key])
        value["capability"] = BrokerCapabilities(**capability)
        for key in ("fractionable", "fractional_limit_day"):
            value[key] = Capability(value[key])
        for key in (
            "quote_provider_time",
            "quote_observed_at",
            "capability_provider_time",
            "capability_observed_at",
            "capability_valid_until",
            "approval_valid_until",
            "session_start",
            "session_end",
        ):
            if not isinstance(value[key], str):
                return None
            value[key] = datetime.fromisoformat(value[key])
        proof = PaperPlanningInputs(**value)
        return proof if proof.market_state_id == state_id else None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def record_planning_inputs(connection, proposal, proof, claim):
    if proof is None or proposal is None:
        raise ValueError("PAPER_PLANNING_PROOF_MISSING")
    recorded = connection.execute(
        text("SELECT public.paper_planner_record(:proposal,:hash,:job,:owner,:fence)"),
        {
            "proposal": proposal.id,
            "hash": proof.digest(),
            "job": claim.job_id,
            "owner": claim.owner,
            "fence": claim.token,
        },
    ).scalar_one_or_none()
    if recorded is not True:
        raise ValueError("PAPER_PLANNING_PROOF_REJECTED")


def read_registered_plan(connection, proposal):
    raw = connection.execute(
        text("SELECT public.paper_limit_plan_read(:proposal)"), {"proposal": proposal.id}
    ).scalar_one_or_none()
    if not isinstance(raw, dict) or set(raw) != {"inputHash", "inputs"}:
        return None
    proof = decode_planning_inputs(raw["inputs"], proposal.market_state_id)
    if proof is None or raw["inputHash"] != proof.digest():
        return None
    return proof


def record_planning_outcome(tx, decision, state, proposal, proof, now, ids):
    """Append one safe Paper planning outcome without rewriting model provenance."""
    from packages.domain.models import AuditEvent
    from services.worker.paper_limit import evaluate_paper_limit

    if proposal is not None:
        reason = "PAPER_LIMIT_PLANNED"
    else:
        result = evaluate_paper_limit(
            decision=decision,
            market_state=state,
            inputs=proof,
            now=now,
        )
        reason = "PAPER_PLAN_NOT_ADMITTED" if result.quantity is not None else result.reason_code
    entity_id = decision.id + ":paper-plan-outcome"
    event = AuditEvent(
        id=ids.new_id("audit", entity_id),
        occurred_at=now,
        actor_id="worker-planner",
        action="paper.plan_outcome",
        reason_codes=(reason,),
        entity_id=entity_id,
        entity_version=1,
        trace_id=decision.trace_id,
        policy_version=proof.policy_version if type(proof) is PaperPlanningInputs else None,
    )
    existing = tx.get(AuditEvent, event.id)
    if existing is not None:
        if any(
            getattr(existing, name) != getattr(event, name)
            for name in (
                "actor_id",
                "action",
                "reason_codes",
                "entity_id",
                "entity_version",
                "trace_id",
                "policy_version",
            )
        ):
            raise ValueError("PAPER_PLANNING_OUTCOME_CONFLICT")
        return existing
    tx.insert(event)
    return event
