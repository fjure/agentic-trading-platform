"""Fenced, idempotent journal for bounded, transaction-local research effects."""

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection

from .budget import BudgetDenied
from .registry import AgentPolicyDenied, require_agent
from .runs import Claim, RunConflict
from .work_budget import charge_work

_REF = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ToolCall:
    step_id: str
    tool_id: str
    tool_version: int
    input_hash: str


class ToolJournal:
    def __init__(self, engine: Engine):
        self.engine = engine

    def execute(
        self, claim: Claim, call: ToolCall, *,
        authorize: Callable[[ReadOnlyScopedConnection], None],
        operation: Callable[[ScopedConnection], str],
    ) -> str:
        """Run an internal vetted SQL-only operation and commit its receipt with the effect.

        The caller's registered adapter checks its typed inputs before this call.
        The authorization callback checks current rights and reference ownership
        within the transaction, including on replay. No provider I/O belongs here.
        """
        if not _REF.fullmatch(call.step_id) or not _HASH.fullmatch(call.input_hash):
            raise ValueError("invalid tool binding")
        params = {
            "id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version, "step": call.step_id, "tool": call.tool_id,
            "tool_version": call.tool_version, "input_hash": call.input_hash,
        }
        failure = None
        with self.engine.begin() as connection:
            binding = connection.execute(text("""
                SELECT agent_binding FROM agent_research.runs
                WHERE run_id=:id AND owner=:owner AND fence=:fence AND version=:version
                  AND state='RUNNING' AND lease_until>clock_timestamp()
                FOR UPDATE
            """), params).scalar_one_or_none()
            if binding is None:
                raise RunConflict("LEASE_LOST")
            try:
                with connection.begin_nested():
                    charged = connection.execute(text("""
                        UPDATE agent_research.runs SET tool_calls=tool_calls+1
                        WHERE run_id=:id AND tool_calls<tool_limit RETURNING tool_calls
                    """), params).scalar_one_or_none()
                    if charged is None:
                        raise BudgetDenied("BUDGET_EXHAUSTED")
                    charge_work(
                        connection, claim.run_id, claim.owner, claim.fence, claim.version,
                        "tool_calls",
                    )
                with connection.begin_nested():
                    return self._apply(connection, binding, params, authorize, operation)
            except (AgentPolicyDenied, BudgetDenied, RunConflict) as error:
                if error.code not in {
                    "AGENT_POLICY_DENIED", "IDEMPOTENCY_CONFLICT",
                    "BUDGET_EXHAUSTED", "BUDGET_POLICY_UNAVAILABLE", "INVALID_TOOL_RESULT",
                }:
                    raise
                connection.execute(text("""
                    INSERT INTO agent_research.tool_denials (run_id,fence,step_id,code,occurred_at)
                    VALUES (:id,:fence,:step,:code,clock_timestamp()) ON CONFLICT DO NOTHING
                """), {
                    **params, "code": error.code,
                    "step": "tool-budget" if isinstance(error, BudgetDenied) else params["step"],
                })
                failure = error
        raise failure

    @staticmethod
    def _apply(connection: Connection, binding, params, authorize, operation) -> str:
        agent = require_agent(binding["agent_id"], binding["version"], binding["policy_version"])
        if binding["implementation_fingerprint"] != agent.implementation_fingerprint:
            raise AgentPolicyDenied(AgentPolicyDenied.code)
        agent.require_tool(params["tool"], params["tool_version"])
        authorize(ReadOnlyScopedConnection(connection))
        previous = connection.execute(text("""
            SELECT input_hash,result_ref FROM agent_research.tool_receipts
            WHERE run_id=:id AND step_id=:step AND tool_id=:tool AND tool_version=:tool_version
        """), params).one_or_none()
        if previous is not None and previous.input_hash != params["input_hash"]:
            raise RunConflict("IDEMPOTENCY_CONFLICT")
        result = (
            previous.result_ref if previous is not None else operation(ScopedConnection(connection))
        )
        if not isinstance(result, str) or not _REF.fullmatch(result):
            raise RunConflict("INVALID_TOOL_RESULT")
        live = connection.execute(text("""
            SELECT lease_until>clock_timestamp() FROM agent_research.runs WHERE run_id=:id
        """), params).scalar_one()
        if not live:
            raise RunConflict("LEASE_LOST")
        if previous is None:
            connection.execute(text("""
                INSERT INTO agent_research.tool_receipts (
                    run_id,step_id,tool_id,tool_version,input_hash,result_ref,fence,occurred_at
                ) VALUES (
                    :id,:step,:tool,:tool_version,:input_hash,:result,:fence,clock_timestamp()
                )
            """), {**params, "result": result})
        return result
