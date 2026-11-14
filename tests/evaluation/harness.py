"""Budgeted synthetic evaluation orchestration for a trusted supervisor adapter."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Protocol

from .catalog import read_verified_holdout

REPETITIONS = (1, 2, 3)


def file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RunBinding:
    profile: str
    model: str
    api: str
    dataset_sha256: str
    holdout_sha256: str
    prompt_sha256: str
    schema_sha256: str
    scorer_sha256: str
    settings_sha256: str
    repetitions: tuple[int, ...] = REPETITIONS

    def verify(self, files: dict[str, Path]) -> None:
        expected = {
            "dataset": self.dataset_sha256, "holdout": self.holdout_sha256,
            "prompt": self.prompt_sha256, "schema": self.schema_sha256,
            "scorer": self.scorer_sha256, "settings": self.settings_sha256,
        }
        if files.keys() != expected.keys() or any(
            not compare_digest(file_sha(files[name]), digest)
            for name, digest in expected.items()
        ):
            raise ValueError("EVALUATION_BINDING_MISMATCH")
        if not all((self.profile, self.model, self.api)) or self.repetitions != REPETITIONS:
            raise ValueError("EVALUATION_BINDING_MISMATCH")


@dataclass(frozen=True, slots=True)
class Approval:
    id: str
    binding: RunBinding
    expires_at: datetime
    max_calls: int
    max_cost: Decimal
    cost_bound: Decimal


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    case_id: str
    text: str
    dialogue_state: dict


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    status: str
    confirmed_cost: Decimal | None = None


class SupervisorPort(Protocol):
    """Private operator-provided route with durable one-shot grants and reservation."""

    def consume(self, approval: Approval, cases: tuple[str, ...]) -> bool:
        """Atomically consume the persisted operator approval for this exact case set."""

    def execute(
        self, approval: Approval, repetition: int, case: EvaluationInput,
    ) -> AttemptOutcome:
        """Recheck authority, reserve, send through the production gateway and store privately."""


def run_holdout(
    binding: RunBinding,
    files: dict[str, Path],
    approval: Approval | None,
    supervisor: SupervisorPort,
) -> dict:
    """Return redacted attempt states. Raw output remains with the supervisor adapter."""
    now = datetime.now(UTC)
    if (
        approval is None or approval.binding != binding or not approval.id
        or approval.expires_at.tzinfo is None or approval.expires_at <= now
        or type(approval.max_calls) is not int or approval.max_calls < 1
        or any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in (approval.max_cost, approval.cost_bound)
        )
    ):
        raise ValueError("EVALUATION_AUTHORIZATION_REQUIRED")
    binding.verify(files)
    cases = read_verified_holdout(
        files["dataset"], files["holdout"], binding.dataset_sha256, binding.holdout_sha256,
    )
    selected = tuple(
        EvaluationInput(case["caseId"], case["input"], case["dialogueState"])
        for case in cases if not case["offlineOnly"]
    )
    ids = tuple(case.case_id for case in selected)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("EVALUATION_BINDING_MISMATCH")
    if supervisor.consume(approval, ids) is not True:
        raise ValueError("EVALUATION_AUTHORIZATION_REQUIRED")
    remaining = approval.max_cost
    runs: dict[int, dict[str, str]] = {}
    calls = 0
    for repetition in REPETITIONS:
        runs[repetition] = {}
        for case in selected:
            if (
                calls >= approval.max_calls or remaining < approval.cost_bound
                or datetime.now(UTC) >= approval.expires_at
            ):
                return {"complete": False, "calls": calls, "runs": runs}
            remaining -= approval.cost_bound
            calls += 1
            result = supervisor.execute(approval, repetition, case)
            if result.status not in {"answered", "refused", "timeout", "provider_blocked"}:
                raise ValueError("EVALUATION_OUTCOME_INVALID")
            runs[repetition][case.case_id] = result.status
            cost = result.confirmed_cost
            if cost is not None:
                if not isinstance(cost, Decimal) or not cost.is_finite() or cost < 0:
                    raise ValueError("EVALUATION_OUTCOME_INVALID")
                if cost > approval.cost_bound:
                    return {"complete": False, "calls": calls, "runs": runs}
                remaining += approval.cost_bound - cost
    return {"complete": True, "calls": calls, "runs": runs}
