"""Allowed supervisor transitions before the persistence layer applies a fence."""

from enum import StrEnum
from types import MappingProxyType


class RunState(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    WAITING_FOR_PROCESSING_DECISION = "WAITING_FOR_PROCESSING_DECISION"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_BATCH = "WAITING_FOR_BATCH"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class InvalidRunTransition(ValueError):
    code = "INVALID_RUN_STATE"


_STOP = frozenset({RunState.BLOCKED, RunState.CANCELLED, RunState.TIMED_OUT})
TRANSITIONS = MappingProxyType({
    RunState.CREATED: _STOP | {RunState.QUEUED},
    RunState.QUEUED: _STOP | {RunState.RUNNING, RunState.BUDGET_EXHAUSTED},
    RunState.RUNNING: _STOP | {
        RunState.QUEUED, RunState.WAITING_FOR_INPUT, RunState.WAITING_FOR_PROCESSING_DECISION,
        RunState.WAITING_FOR_APPROVAL, RunState.COMPLETED, RunState.FAILED,
        RunState.BUDGET_EXHAUSTED,
    },
    RunState.WAITING_FOR_INPUT: _STOP | {RunState.QUEUED},
    RunState.WAITING_FOR_PROCESSING_DECISION: _STOP | {RunState.QUEUED, RunState.WAITING_FOR_INPUT},
    RunState.WAITING_FOR_APPROVAL: _STOP | {RunState.WAITING_FOR_BATCH, RunState.QUEUED},
    RunState.WAITING_FOR_BATCH: _STOP | {RunState.QUEUED},
})


def require_transition(current: RunState, target: RunState) -> None:
    if target not in TRANSITIONS.get(current, ()):
        raise InvalidRunTransition(InvalidRunTransition.code)
