"""APF-R01/R02 policy and deterministic transition checks."""

from dataclasses import FrozenInstanceError
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.agent_research.budget import BudgetDenied
from packages.agent_research.model_attempts import Attempt, ModelAttemptStore
from packages.agent_research.registry import AgentPolicyDenied, ModelBinding, ToolId, require_agent
from packages.agent_research.run_state import InvalidRunTransition, RunState, require_transition
from packages.agent_research.runs import Claim, ClaimedInput, RunConflict, RunKind, RunStore
from services.runtime.agent_research import ResearchSupervisor, StepResult


def _attempts():
    attempts = Mock(spec=ModelAttemptStore)
    attempts.recover.return_value = ()
    return attempts


def _supervisor(runs, step):
    return ResearchSupervisor(runs, {RunKind.DIALOGUE: step}, attempts=_attempts())


def test_versioned_registry_is_closed_and_offline_by_default():
    agent = require_agent("quant-research-agent-v1", 1, 1)
    assert agent.autonomy_level == 0
    assert agent.limits.max_cost == Decimal("0.10")
    assert {tool.tool_id for tool in agent.tools} == set(ToolId)
    assert len(agent.implementation_fingerprint) == 64
    with pytest.raises(FrozenInstanceError):
        agent.autonomy_level = 1
    with pytest.raises(AgentPolicyDenied, match="AGENT_POLICY_DENIED"):
        agent.require_model(ModelBinding("OPENAI", "offline-profile", 1, "offline-model", 1))


@pytest.mark.parametrize("version,policy", [(2, 1), (1, 2), (True, 1)])
def test_missing_agent_or_policy_version_is_denied(version, policy):
    with pytest.raises(AgentPolicyDenied, match="AGENT_POLICY_DENIED"):
        require_agent("quant-research-agent-v1", version, policy)


@pytest.mark.parametrize(
    "tool,version", [("QUANT_START", 1), ("BROKER_SUBMIT", 1), ("HTTP", 1), ("QUANT_READ", 2)]
)
def test_unregistered_tool_cannot_acquire_authority(tool, version):
    agent = require_agent("quant-research-agent-v1", 1, 1)
    assert agent.require_tool("QUANT_READ", 1).tool_id == ToolId.QUANT_READ
    with pytest.raises(AgentPolicyDenied, match="AGENT_POLICY_DENIED"):
        agent.require_tool(tool, version)


def test_run_must_follow_waiting_states_and_cannot_revive():
    path = (
        RunState.CREATED, RunState.QUEUED, RunState.RUNNING,
        RunState.WAITING_FOR_PROCESSING_DECISION, RunState.QUEUED, RunState.RUNNING,
        RunState.WAITING_FOR_APPROVAL, RunState.WAITING_FOR_BATCH, RunState.QUEUED,
        RunState.RUNNING, RunState.COMPLETED,
    )
    for current, target in zip(path, path[1:], strict=False):
        require_transition(current, target)
    for current, target in (
        (RunState.RUNNING, RunState.WAITING_FOR_BATCH),
        (RunState.WAITING_FOR_PROCESSING_DECISION, RunState.RUNNING),
        (RunState.CANCELLED, RunState.QUEUED),
        (RunState.COMPLETED, RunState.RUNNING),
    ):
        with pytest.raises(InvalidRunTransition, match="INVALID_RUN_STATE"):
            require_transition(current, target)


def test_supervisor_uses_only_configured_step_and_keeps_checkpoint():
    owner = uuid4()
    claim = Claim(uuid4(), owner, 1, 2)
    request = ClaimedInput(RunKind.DIALOGUE, "turn-1", "checkpoint-1")
    runs = Mock(spec=RunStore)
    runs.claim.return_value = claim
    runs.load_input.return_value = request
    step = Mock(return_value=StepResult(RunState.WAITING_FOR_INPUT, "checkpoint-2"))
    assert _supervisor(runs, step).run_once(owner)
    step.assert_called_once_with(claim, request, ())
    runs.transition.assert_called_once_with(
        claim, RunState.WAITING_FOR_INPUT, checkpoint_ref="checkpoint-2"
    )


def test_supervisor_blocks_when_bound_agent_policy_is_denied():
    owner = uuid4()
    claim = Claim(uuid4(), owner, 1, 2)
    runs = Mock(spec=RunStore)
    runs.claim.return_value = claim
    runs.load_input.side_effect = AgentPolicyDenied(AgentPolicyDenied.code)
    step = Mock()
    assert _supervisor(runs, step).run_once(owner)
    step.assert_not_called()
    runs.transition.assert_called_once_with(claim, RunState.BLOCKED, checkpoint_ref=None)


@pytest.mark.parametrize(
    "error", [BudgetDenied("BUDGET_EXHAUSTED"), RunConflict("BUDGET_EXHAUSTED")]
)
def test_supervisor_persists_budget_exhaustion_without_retry(error):
    owner = uuid4()
    claim = Claim(uuid4(), owner, 1, 2)
    runs = Mock(spec=RunStore)
    runs.claim.return_value = claim
    runs.load_input.return_value = ClaimedInput(RunKind.DIALOGUE, "turn-1", None)
    step = Mock(side_effect=error)
    assert _supervisor(runs, step).run_once(owner)
    runs.transition.assert_called_once_with(claim, RunState.BUDGET_EXHAUSTED, checkpoint_ref=None)


def test_supervisor_discards_result_when_fence_is_lost_before_commit():
    owner = uuid4()
    claim = Claim(uuid4(), owner, 1, 2)
    runs = Mock(spec=RunStore)
    runs.claim.return_value = claim
    runs.load_input.return_value = ClaimedInput(RunKind.DIALOGUE, "turn-1", None)
    runs.transition.side_effect = RunConflict("LEASE_LOST")
    step = Mock(return_value=StepResult(RunState.COMPLETED, "checkpoint-1"))
    assert _supervisor(runs, step).run_once(owner)
    runs.transition.assert_called_once()


def test_supervisor_does_not_start_another_step_after_ambiguous_dispatch():
    owner = uuid4()
    claim = Claim(uuid4(), owner, 2, 3)
    runs = Mock(spec=RunStore)
    runs.claim.return_value = claim
    attempts = _attempts()
    attempts.recover.return_value = (Attempt(uuid4(), "OUTCOME_UNKNOWN", None),)
    step = Mock()
    assert ResearchSupervisor(runs, {RunKind.DIALOGUE: step}, attempts=attempts).run_once(owner)
    runs.load_input.assert_not_called()
    step.assert_not_called()
    runs.transition.assert_called_once_with(claim, RunState.BLOCKED, checkpoint_ref=None)
