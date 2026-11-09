"""Run-bound step and tool admission against hierarchical daily work budgets."""

from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .budget import BudgetDenied

WorkDimension = Literal["steps", "tool_calls"]


def work_budget_scopes(
    agent_id: str, agent_version: int, kind: str, input_ref: str,
) -> tuple[str, ...]:
    """Build configuration and inspection keys for the SQL policy scopes."""
    scopes = ("global", f"agent:{agent_id}", f"agent_version:{agent_id}:{agent_version}",
              f"work:{kind}")
    if kind == "SCHEDULED":
        scopes += (f"schedule:{input_ref}",)
    return tuple(sorted(scopes))


def charge_work(
    connection: Connection, run_id: UUID, owner: UUID, fence: int, version: int,
    dimension: WorkDimension,
) -> None:
    """Charge every scope in the caller's run transaction before work begins."""
    denial = connection.execute(
        text("SELECT agent_research.charge_work(:run_id,:owner,:fence,:version,:dimension)"),
        {"run_id": run_id, "owner": owner, "fence": fence, "version": version,
         "dimension": dimension},
    ).scalar_one()
    if denial is not None:
        raise BudgetDenied(denial)
