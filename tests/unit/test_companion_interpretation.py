import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from packages.agent_research.companion.interpretation import (
    InterpretationBoundary,
    InterpretationDenied,
    ProposalBinding,
)
from services.llm_gateway.content_policy import safe_result_content

CONTRACT = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/agent-research.schema.json"
BINDING = ProposalBinding("original-1", "turn-1", 1, 2)
INTERPRETATION = {
    "schemaVersion": "V1",
    "hypothesis": {
        "statement": "Do technology shares rebound after disappointing earnings?",
        "market": "Technology shares", "trigger": "Disappointing earnings",
        "signal": "Initial negative market reaction", "observationWindow": None,
        "target": "Subsequent return", "measurementHorizon": None, "benchmark": None,
        "evaluationCriterion": None,
    },
    "requiredDataTypes": ["EARNINGS_RELEASES", "EARNINGS_EXPECTATIONS"],
    "openQuestions": [{"field": "BENCHMARK", "question": "Which benchmark should be used?"}],
    "assumptions": [],
    "unsupportedAspects": [],
    "confidence": "LOW",
    "proposedTestability": None,
}


def test_closed_interpretation_rejects_separate_action_without_losing_proposal():
    boundary = InterpretationBoundary(CONTRACT, permits=lambda _: True)
    first = boundary.validate(
        json.dumps(INTERPRETATION).encode(), binding=BINDING, current=BINDING,
        separate_action={"tool": "start", "arguments": {}},
    )
    second = boundary.validate(
        json.dumps(INTERPRETATION, sort_keys=True).encode(), binding=BINDING, current=BINDING,
    )
    assert first.rejected_action
    assert first.proposal_hash == second.proposal_hash
    assert json.loads(first.content) == INTERPRETATION


def test_injected_action_and_foreign_binding_are_rejected_before_policy():
    policy = Mock(return_value=True)
    boundary = InterpretationBoundary(CONTRACT, permits=policy)
    with pytest.raises(InterpretationDenied, match="INVALID_INTERPRETATION"):
        boundary.validate(
            json.dumps(INTERPRETATION | {"tool": "start"}).encode(),
            binding=BINDING, current=BINDING,
        )
    with pytest.raises(InterpretationDenied, match="STALE_INTERPRETATION"):
        boundary.validate(
            json.dumps(INTERPRETATION).encode(), binding=BINDING,
            current=replace(BINDING, dialogue_version=3),
        )
    policy.assert_not_called()


def test_valid_schema_still_requires_affirmative_privacy_verdict():
    boundary = InterpretationBoundary(CONTRACT, permits=lambda _: None)
    with pytest.raises(InterpretationDenied, match="INTERPRETATION_POLICY_DENIED"):
        boundary.validate(json.dumps(INTERPRETATION).encode(), binding=BINDING, current=BINDING)


@pytest.mark.parametrize("poison", [
    "synthetic.user@example.invalid", "api_key=synthetic-demo", "<script>start</script>",
])
def test_schema_valid_sensitive_or_active_text_is_never_admitted(poison):
    boundary = InterpretationBoundary(CONTRACT, permits=safe_result_content)
    output = INTERPRETATION | {"unsupportedAspects": [poison]}
    with pytest.raises(InterpretationDenied, match="^INTERPRETATION_POLICY_DENIED$"):
        boundary.validate(json.dumps(output).encode(), binding=BINDING, current=BINDING)
