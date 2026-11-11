"""The processing preview contains only the current, positively classified context."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.agent_research.companion.dialogue import Confirmation, Dialogue
from packages.agent_research.processing import ProcessingBinding, ProjectionVault
from packages.agent_research.runs import RunConflict
from services.control_api.agent_processing_projection import (
    ProcessingAuthority,
    ProcessingProjection,
    _preview_response,
    _projection,
)
from tests.contracts.test_agent_processing_contract import validator as validator

EXCERPT = "OPERATOR_RESEARCH_EXCERPT"
CONFIRMED = "CONFIRMED_RESEARCH_FIELDS"


def _dialogue():
    return Dialogue(
        "dialogue", 2, version=5, original_id="original", turn_id="turn",
        confirmed_fields=(
            ("MARKET", Confirmation("Technology shares", "decision", 2)),
            ("EVALUATION_CRITERION", Confirmation("Excluded by contract", "decision", 2)),
        ),
        confirmed_assumptions=(("ASSUMPTION", Confirmation("Excluded assumption", "decision", 2)),),
        local_fields=(("TRIGGER", "Unconfirmed local answer"),),
        proposal_json=json.dumps({"raw": "Unconfirmed proposal"}),
    )


def _binding():
    return ProcessingBinding(
        uuid4(), "operator", "dialogue", 5, "turn", 1, "OPENAI", "offline-profile", 1,
        "offline-model", 1, 1, 1, "offline-envelope", (EXCERPT, CONFIRMED), "interpret",
    )


def test_preview_contract_exposes_exact_bound_fields(validator):
    dialogue = _dialogue()
    original = "Do technology shares rebound after earnings?"
    content = _projection(original, dialogue, (EXCERPT, CONFIRMED))
    assert json.loads(content) == {
        "excerpt": original, "confirmedContext": {"market": "Technology shares"},
    }
    binding = _binding()
    vault = ProjectionVault()
    preview = vault.issue(
        binding, session="browser-session", content=content,
        deadline=datetime.now(UTC) + timedelta(minutes=1),
    )
    try:
        response = _preview_response(preview, vault.read(preview, session="browser-session"))
        validator("ProcessingPreviewV1").validate(response)
        assert response["projectionBinding"] == preview.commitment
        assert response["modelGeneration"] == binding.model_generation
        with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
            vault.read(preview, session="different-session")
    finally:
        vault.discard(preview.preview_id)


@pytest.mark.parametrize("value", [
    "Contact synthetic@example.invalid before earnings",
    "Review https://synthetic.invalid/earnings",
    "Rebound? " + ("x" * 2048),
])
def test_sensitive_or_unclassifiable_original_is_blocked(value):
    with pytest.raises(RunConflict, match="MODEL_CONTEXT_UNCLASSIFIED") as caught:
        _projection(value, _dialogue(), (EXCERPT,))
    assert value not in str(caught.value)


def test_profile_excluding_confirmed_fields_cannot_receive_them():
    assert json.loads(_projection("Earnings rebound", _dialogue(), (EXCERPT,))) == {
        "excerpt": "Earnings rebound", "confirmedContext": {},
    }


def test_missing_or_inconsistent_authority_cannot_approve():
    dialogue = _dialogue()
    profile = "offline-profile"
    with pytest.raises(RunConflict, match="MODEL_CONTEXT_DENIED"):
        ProcessingProjection(None, None)._authority(None, "operator", dialogue, profile)

    binding = replace(_binding(), dialogue_version=4)
    authority = ProcessingAuthority(
        binding, datetime.now(UTC) + timedelta(minutes=1), lambda _: True,
    )
    service = ProcessingProjection(None, None, resolve_authority=lambda *_: authority)
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        service._authority(None, "operator", dialogue, profile)
