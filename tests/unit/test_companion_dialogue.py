"""APF-Q02 dialogue, revision and explicit confirmation oracles."""

import json
from pathlib import Path

import pytest

from packages.agent_research.companion.dialogue import (
    Dialogue,
    DialogueError,
    ExpertBinding,
    answer_local,
    confirm,
    local_questions,
    propose,
    request_changes,
    submit_original,
)
from packages.agent_research.companion.interpretation import InterpretationBoundary

_CONTRACT = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/agent-research.schema.json"


def _allow(_value):
    return True


def _versions(dialogue):
    return {"expected_idea_version": dialogue.idea_version,
            "expected_dialogue_version": dialogue.version}


def _turn(dialogue=None, *, number=1, text="Erholen sich Tech-Aktien nach schlechten Zahlen?"):
    state = dialogue or Dialogue("dialogue", 1)
    return submit_original(
        state, **_versions(state), turn_id=f"turn-{number}", original_id=f"original-{number}",
        text=text, privacy_check=_allow,
    )


def _model(state, *, horizon="Fünf Handelstage", trigger="Quartalsmeldung", number=1):
    payload = {
        "schemaVersion": "V1",
        "hypothesis": {
            "statement": "Erholung nach einem Kursrückgang bei Tech-Aktien",
            "market": "Tech-Aktien", "trigger": trigger, "signal": "Kursrückgang",
            "observationWindow": None, "target": "Spätere Erholung",
            "measurementHorizon": horizon, "benchmark": None, "evaluationCriterion": None,
        },
        "requiredDataTypes": [], "openQuestions": [],
        "assumptions": [{"id": "earnings", "statement": "Die Meldung ist vor dem Test bekannt."}],
        "unsupportedAspects": [], "confidence": "MEDIUM", "proposedTestability": None,
    }
    boundary = InterpretationBoundary(_CONTRACT, permits=_allow)
    validated = boundary.validate(
        json.dumps(payload).encode(), binding=state.binding, current=state.binding,
    )
    return propose(
        state, **_versions(state), proposal_id=f"proposal-{number}", interpretation=validated,
    )


def _confirm(state, fields, assumptions=(), *, decision="decision-1", **overrides):
    proposal = state.proposal
    inputs = _versions(state) | {
        "proposal_id": proposal["proposalId"], "proposal_hash": proposal["proposalHash"],
        "confirmed_fields": fields, "confirmed_assumption_ids": assumptions,
        "decision_id": decision,
    }
    return confirm(state, **(inputs | overrides))


def test_model_and_original_are_unconfirmed_and_later_conflicts_are_targeted():
    started = _turn()
    candidate = _model(started)
    assert started.original_id == candidate.proposal["originalId"]
    assert candidate.confirmed == {}
    assert candidate.idea_version == 1
    first = _confirm(candidate, {"MEASUREMENT_HORIZON": "Fünf Handelstage"}, ("earnings",))
    assert first.idea_version == 2
    candidate = _model(_turn(first, number=2), horizon="Zehn Handelstage", number=2)
    assert candidate.confirmed == {"MEASUREMENT_HORIZON": "Fünf Handelstage"}
    assert [item["field"] for item in candidate.proposal["conflicts"]] == ["MEASUREMENT_HORIZON"]
    revised = _confirm(
        candidate, {"MEASUREMENT_HORIZON": "Zehn Handelstage"}, decision="decision-2",
    )
    assert revised.idea_version == 3
    assert revised.confirmed == {"MEASUREMENT_HORIZON": "Zehn Handelstage"}
    assert dict(revised.confirmed_fields)["MEASUREMENT_HORIZON"].decision_id == "decision-2"
    assert revised.confirmed_assumptions == first.confirmed_assumptions
    assert first.confirmed == {"MEASUREMENT_HORIZON": "Fünf Handelstage"}


def test_model_cannot_replace_an_expert_definition_or_a_protected_field():
    state = Dialogue("dialogue", 1)
    expert = ExpertBinding("quant-definition", 3, "a" * 64, (("MARKET", "SPY"),))
    started = submit_original(
        state, **_versions(state), turn_id="turn-1", original_id="original-1",
        text="Test an expert-defined market", privacy_check=_allow, expert=expert,
    )
    candidate = _model(started)
    assert [item["field"] for item in candidate.proposal["conflicts"]] == ["MARKET"]
    with pytest.raises(DialogueError, match="INVALID_HYPOTHESIS_CONFIRMATION"):
        _confirm(candidate, {"MARKET": "Tech-Aktien"})
    confirmed = _confirm(candidate, {"MEASUREMENT_HORIZON": "Fünf Handelstage"})
    assert Dialogue.from_record(json.loads(json.dumps(confirmed.record()))).expert == expert
    with pytest.raises(DialogueError, match="INVALID_EXPERT_BINDING"):
        submit_original(
            confirmed, **_versions(confirmed), turn_id="turn-2", original_id="original-2",
            text="Try an alternative definition", privacy_check=_allow,
            expert=ExpertBinding("quant-definition", 4, "b" * 64),
        )


def test_local_clarification_is_bounded_and_requires_explicit_confirmation():
    original = _turn()
    questions = local_questions(original)
    assert len(questions) == 8
    question = questions[0]
    proposal = answer_local(
        original, **_versions(original), question_id=question.question_id, field=question.field,
        value="Tech-Aktien", turn_id="local-1", proposal_id="local-proposal", privacy_check=_allow,
    )
    assert proposal.proposal["origin"] == "LOCAL_CLARIFICATION"
    assert proposal.confirmed == {} and proposal.idea_version == original.idea_version
    assert question.field not in {item.field for item in local_questions(proposal)}
    with pytest.raises(DialogueError, match="VERSION_CONFLICT"):
        answer_local(
            proposal, **_versions(proposal), question_id=questions[1].question_id,
            field=questions[1].field, value="Quartalszahlen", turn_id="local-2",
            proposal_id="local-proposal-2", privacy_check=_allow,
        )
    confirmed = _confirm(proposal, {"MARKET": "Tech-Aktien"})
    assert confirmed.confirmed == {"MARKET": "Tech-Aktien"}
    assert Dialogue.from_record(json.loads(json.dumps(confirmed.record()))) == confirmed


def test_complete_local_draft_can_be_confirmed_in_one_decision():
    state = _turn()
    fields = {}
    for number in range(8):
        question = local_questions(state)[0]
        value = f"Bestimmte lokale Antwort {number + 1}"
        state = answer_local(
            state, **_versions(state), question_id=question.question_id, field=question.field,
            value=value, turn_id=f"local-{number}", proposal_id=f"proposal-{number}",
            privacy_check=_allow,
        )
        fields[question.field] = value
    assert local_questions(state) == ()
    assert state.confirmed == {}
    confirmed = _confirm(state, fields)
    assert confirmed.confirmed == fields and confirmed.idea_version == 2
    assert {entry.decision_id for _, entry in confirmed.confirmed_fields} == {"decision-1"}


def test_a_local_answer_keeps_other_changed_fields_open():
    first = _confirm(
        _model(_turn()), {"MEASUREMENT_HORIZON": "Fünf Handelstage", "TRIGGER": "Quartalsmeldung"},
    )
    current = _model(
        _turn(first, number=2), horizon="Zehn Handelstage", trigger="Dividendenmeldung",
    )
    question = next(item for item in local_questions(current) if item.field == "TRIGGER")
    draft = answer_local(
        current, **_versions(current), question_id=question.question_id, field=question.field,
        value="Geändertes Auslöseereignis", turn_id="local-1", proposal_id="local-proposal",
        privacy_check=_allow,
    )
    assert "MEASUREMENT_HORIZON" in {question.field for question in local_questions(draft)}
    assert draft.confirmed == first.confirmed


@pytest.mark.parametrize("privacy", [lambda _: False, lambda _: None])
def test_unknown_privacy_refuses_without_echo(privacy):
    draft = Dialogue("dialogue", 1)
    marker = "sensitive fixture should not echo"
    with pytest.raises(DialogueError, match="^MODEL_CONTEXT_UNCLASSIFIED$") as failure:
        submit_original(
            draft, **_versions(draft), turn_id="turn-1", original_id="original-1",
            text=marker, privacy_check=privacy,
        )
    assert marker not in str(failure.value)
    assert draft.original_id is None


def test_proposal_cannot_be_confirmed_with_wrong_binding_or_unoffered_value():
    candidate = _model(_turn())
    for overrides in ({"proposal_hash": "0" * 64}, {"expected_dialogue_version": 1}):
        with pytest.raises(DialogueError, match="VERSION_CONFLICT"):
            _confirm(candidate, {"MARKET": "Tech-Aktien"}, **overrides)
    with pytest.raises(DialogueError, match="INVALID_HYPOTHESIS_CONFIRMATION"):
        _confirm(candidate, {"MARKET": "Unbekannter Wert"})
    with pytest.raises(DialogueError, match="INVALID_HYPOTHESIS_CONFIRMATION"):
        _confirm(candidate, {"MARKET": "Tech-Aktien"}, ("unbekannt",))


def test_change_request_only_reopens_selected_fields_and_does_not_confirm():
    candidate = _model(_turn())
    proposal = candidate.proposal
    follow_up = request_changes(
        candidate, **_versions(candidate), proposal_id=proposal["proposalId"],
        proposal_hash=proposal["proposalHash"], decision_id="change-1",
        requested_fields=("MEASUREMENT_HORIZON",), clarification="Zeitraum bitte konkretisieren.",
        privacy_check=_allow,
    )
    assert [question.field for question in local_questions(follow_up)] == ["MEASUREMENT_HORIZON"]
    assert follow_up.idea_version == candidate.idea_version
    assert follow_up.confirmed == {} and follow_up.proposal is None
