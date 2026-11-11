"""One-step supervisor for the isolated research queue."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from packages.agent_research.budget import BudgetDenied
from packages.agent_research.model_attempts import Attempt, ModelAttemptStore
from packages.agent_research.registry import AgentPolicyDenied
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import Claim, ClaimedInput, RunConflict, RunKind, RunStore


@dataclass(frozen=True, slots=True)
class StepResult:
    target: RunState
    checkpoint_ref: str | None = None


Step = Callable[[Claim, ClaimedInput, tuple[Attempt, ...]], StepResult]
_CONFLICT_STATES = {
    "BUDGET_EXHAUSTED": RunState.BUDGET_EXHAUSTED,
    "AGENT_POLICY_DENIED": RunState.BLOCKED,
    "PROVIDER_OUTCOME_UNKNOWN": RunState.BLOCKED,
    "PROCESSING_GRANT_REQUIRED": RunState.WAITING_FOR_PROCESSING_DECISION,
    "PROCESSING_GRANT_INVALID": RunState.WAITING_FOR_INPUT,
    "PROCESSING_GRANT_REVOKED": RunState.WAITING_FOR_INPUT,
}


class ResearchSupervisor:
    def __init__(
        self, runs: RunStore, steps: Mapping[RunKind, Step], *, attempts: ModelAttemptStore,
        tick_schedules: Callable[[], None] | None = None,
    ):
        self.runs = runs
        self.attempts = attempts
        self.steps = MappingProxyType(dict(steps))
        self.tick_schedules = tick_schedules

    def run_once(self, owner: UUID) -> bool:
        if self.tick_schedules is not None:
            self.tick_schedules()
        discovery = RunKind.SCHEDULED in self.steps
        claim = self.runs.claim(owner, discovery=True) if discovery else self.runs.claim(owner)
        if claim is None:
            return False
        try:
            recovered = self.attempts.recover(claim)
            unknown = any(attempt.status == "OUTCOME_UNKNOWN" for attempt in recovered)
            request = self.runs.load_input(claim) if discovery or not unknown else None
            if unknown and (request is None or request.kind is not RunKind.SCHEDULED):
                result = StepResult(RunState.BLOCKED)
            else:
                step = self.steps.get(request.kind)
                result = (
                    StepResult(RunState.BLOCKED)
                    if step is None else step(claim, request, recovered)
                )
        except AgentPolicyDenied:
            result = StepResult(RunState.BLOCKED)
        except BudgetDenied as error:
            if error.code == "LEASE_LOST":
                return True
            target = (
                RunState.BUDGET_EXHAUSTED if error.code == "BUDGET_EXHAUSTED" else RunState.BLOCKED
            )
            result = StepResult(target)
        except RunConflict as error:
            if error.code == "LEASE_LOST":
                return True
            target = _CONFLICT_STATES.get(error.code)
            if target is None:
                raise
            result = StepResult(target)
        try:
            self.runs.transition(claim, result.target, checkpoint_ref=result.checkpoint_ref)
        except RunConflict as error:
            if error.code != "LEASE_LOST":
                raise
        return True
