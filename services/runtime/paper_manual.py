"""Human commands use the same durable Risk, reservation and submit boundary."""

from datetime import datetime
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.domain import models as m
from packages.domain.paper_workflow import HumanOrderRequest, PaperOrderSide
from services.control_api.paper_commands import PaperCommandError


def _request(claim):
    body = claim["payload"]
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        if (body["side"] == "BUY" and Decimal(body["quantity"]) * Decimal(body["limitPrice"])
                > Decimal(body["riskBudget"])):
            raise PaperCommandError(409, "BUDGET_EXCEEDED")
    try:
        return HumanOrderRequest(
            id=claim["operationId"], operation_id=claim["operationId"], account_id="paper",
            account_generation=body["accountGeneration"],
            credential_generation=body["credentialGeneration"],
            policy_version=body["policyVersion"], profile_version=body["profileVersion"],
            limit_policy_version=body["limitPolicyVersion"], budget_version=body["budgetVersion"],
            risk_budget=Decimal(body["riskBudget"]), symbol=body["symbol"],
            side=PaperOrderSide(body["side"]), quantity=Decimal(body["quantity"]),
            limit_price=Decimal(body["limitPrice"]),
            created_at=datetime.fromisoformat(claim["acceptedAt"]),
            valid_until=datetime.fromisoformat(body["expiresAt"]), trace_id=claim["traceId"],
            currency=body["currency"], order_type=body["orderType"],
            time_in_force=body["timeInForce"], session=body["session"],
        )
    except (ValueError, KeyError, TypeError):
        raise PaperCommandError(400, "INVALID_REQUEST") from None


def run_manual_order(runtime, claim, command_owner):
    from services.worker.paper_human_limit import assess_human_limit
    from services.worker.paper_planning import read_planning_inputs

    operation = {"id": claim["operationId"], "owner": command_owner, "fence": claim["fence"]}

    def finish():
        runtime._manual_pending = operation
        completed = runtime.commands._call(
            "SELECT public.paper_human_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence)",
            operation,
        ) is True
        if completed:
            runtime._manual_pending = None
        return completed

    with runtime._cycle:
        # Resume the original obligation before considering current TTL or
        # creating observations. An expired approval cannot erase its result.
        status = runtime.commands._call(
            "SELECT public.paper_human_operation_read(CAST(:id AS uuid))",
            {"id": claim["operationId"]},
        )
        if status is not None and status.get("proposalId") is not None and finish():
            return
        context = runtime._read()
        if not context or context["profileVersion"] < 1 or runtime._owner_faulted:
            raise PaperCommandError(409, "LEDGER_NOT_PREPARED")
        if status is not None and status.get("proposalId") is not None:
            if status.get("orderId") is not None:
                try:
                    runtime._refresh(context)
                except Exception:
                    return  # Keep RUNNING and reserved for independent recovery.
            elif not runtime._faulted:
                runtime._components(context)
                runtime._process_current_work()
            finish()
            return
        if runtime._faulted:
            raise PaperCommandError(409, "ENTRY_PAUSED")
        request = _request(claim)
        if runtime.clock.now() >= request.valid_until:
            raise PaperCommandError(409, "APPROVAL_EXPIRED")
        runtime._refresh(context)
        with runtime.ledger.transaction() as tx:
            state_id = tx.connection.execute(text("""
                SELECT ms.id FROM public.market_states ms
                JOIN public.risk_account_context a ON a.portfolio_snapshot_id=ms.portfolio_id
                WHERE a.account_id='paper' AND ms.instrument_id=:symbol
                ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1
            """), {"symbol": request.symbol}).scalar_one_or_none()
            state = tx.get(m.MarketState, state_id) if state_id is not None else None
            inputs = read_planning_inputs(tx.connection, state_id, execution=True)
            assessment = assess_human_limit(request, state, inputs, runtime.clock.now())
        if assessment.reason_code is not None:
            raise PaperCommandError(409, assessment.reason_code)
        result = runtime.commands._call(
            "SELECT public.paper_human_proposal_create(CAST(:id AS uuid),CAST(:owner AS uuid),"
            ":fence,:state,:hash,CAST(:expiry AS timestamptz))",
            operation | {"state": state_id, "hash": assessment.input_hash,
                         "expiry": assessment.valid_until.isoformat()},
        )
        if (type(result) is not dict or result.get("operation_id") != claim["operationId"]
                or result.get("human_request_id") != claim["operationId"]
                or result.get("proposal_source") != "HUMAN_REQUEST"
                or result.get("decision_id") is not None):
            raise PaperCommandError(409, "RECONCILIATION_REQUIRED")
        # All persisted proposals, human and automatic, compete under the same
        # account lock. Only the existing Execution submit port can perform I/O.
        runtime._process_current_work()
        finish()
