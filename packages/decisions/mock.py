"""Explicit fault modes for deterministic replay and failure injection."""

from __future__ import annotations

from dataclasses import replace

from packages.decisions.baseline import (
    INPUT_FIELDS,
    RESULT_FIELDS,
    BaselineConfig,
    DeterministicBaselineDecisionEngine,
)
from packages.domain.models import (
    Capability,
    Choice,
    DecisionCapabilities,
    DecisionResult,
    MarketState,
)
from packages.domain.ports import Clock, IdGenerator


class MockDecisionEngine(DeterministicBaselineDecisionEngine):
    def __init__(self, *, clock: Clock, ids: IdGenerator,
                 config: BaselineConfig | None = None, failure: str | None = None,
                 capability: DecisionCapabilities | None = None) -> None:
        if failure not in (None, "timeout", "invalid_response", "missing_response"):
            raise ValueError("unsupported mock failure")
        super().__init__(
            clock=clock, ids=ids,
            config=config or BaselineConfig(version="mock-neutral.v1"),
            capability=capability or DecisionCapabilities(
                "mock-neutral", tuple(sorted(INPUT_FIELDS)), tuple(sorted(RESULT_FIELDS)),
                Capability.CONFIRMED),
        )
        self.failure = failure

    def decide(self, market_state: MarketState | None, config_version: str) -> DecisionResult:
        result = super().decide(market_state, config_version)
        if self.failure is None:
            return result
        reason = ("ABSTAIN_UNAVAILABLE" if self.failure == "timeout"
                  else "ABSTAIN_INVALID_RESPONSE")
        return replace(result, choice=Choice.ABSTAIN, model_id=None,
                       reason_code=reason, confidence=None, distribution=None,
                       typed_answers=None, regime=None, evidence_quality=None,
                       signal_conflict=None, shadow_choice=None)
