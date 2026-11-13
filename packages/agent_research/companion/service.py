"""Companion application operations over append-only idea and dialogue history."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

from packages.persistence.transaction import ScopedConnection

from . import dialogue
from .dialogue import Dialogue, ExpertBinding, PrivacyCheck
from .ideas import IdeaAction, IdeaLifecycle, IdeaOrigin
from .interpretation import InterpretationBoundary, ProposalBinding
from .store import CompanionConflict, CompanionStore, DialogueChange, DialogueRevision
from .testability import (
    Approximation,
    Assessment,
    DataSuitability,
    HypothesisIdentity,
    Parameters,
    assess_testability,
    ema_crossover_mapping,
)

_SLOTS = {
    "MARKET": "market", "TRIGGER": "trigger", "SIGNAL": "signal",
    "OBSERVATION_WINDOW": "observationWindow", "TARGET": "target",
    "MEASUREMENT_HORIZON": "measurementHorizon", "BENCHMARK": "benchmark",
    "EVALUATION_CRITERION": "evaluationCriterion",
}


@dataclass(frozen=True, slots=True)
class DialogueCommand:
    actor: str
    idea_id: str
    dialogue_id: str
    idea_version: int
    dialogue_version: int
    key: str

    @property
    def expected(self) -> dict:
        return {
            "expected_idea_version": self.idea_version,
            "expected_dialogue_version": self.dialogue_version,
        }


def _command(operation: str, arguments: dict) -> bytes:
    try:
        return json.dumps(
            [operation, arguments], ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise CompanionConflict("INVALID_COMPANION_COMMAND") from None


class CompanionService:
    def __init__(
        self, store: CompanionStore, lifecycle: IdeaLifecycle,
        interpretation: InterpretationBoundary,
        *, permits_local_text: PrivacyCheck,
        resolve_expert: Callable[[str, str, int, str], ExpertBinding | None] | None = None,
    ):
        self._store = store
        self._lifecycle = lifecycle
        self._interpretation = interpretation
        self._permits_local_text = permits_local_text
        self._resolve_expert = resolve_expert

    def create(
        self, actor: str, idea_id: str, dialogue_id: str, content: Mapping,
        *, key: str, origin: IdeaOrigin = IdeaOrigin.USER,
    ) -> dict:
        if content.get("dialogueId", dialogue_id) != dialogue_id:
            raise CompanionConflict("INVALID_COMPANION_REFERENCE")
        payload = dict(content)

        def make_idea() -> dict:
            return self._lifecycle.create(
                payload | {"dialogueId": dialogue_id}, idea_id=idea_id, initiator_id=actor,
                origin=origin, created_at=datetime.now(UTC),
            ).to_document()

        return self._store.create(
            actor, idea_id, dialogue_id, Dialogue(dialogue_id, 1).record(), key=key,
            command=_command("create-idea", {"origin": origin, "content": payload}),
            make_idea=make_idea,
        )

    def submit_original(
        self, command: DialogueCommand, *, turn_id: str, original_id: str, text: str,
        expert_reference: tuple[str, int, str] | None = None,
    ) -> DialogueRevision:
        arguments = {"turn_id": turn_id, "original_id": original_id, "text": text}

        def change(_idea, state):
            expert = None
            if expert_reference is not None:
                if self._resolve_expert is not None:
                    expert = self._resolve_expert(command.actor, *expert_reference)
                if expert is None or (
                    expert.definition_id, expert.definition_version, expert.definition_hash
                ) != expert_reference:
                    raise CompanionConflict("EXPERT_REFERENCE_DENIED")
            revised = dialogue.submit_original(
                state, **command.expected, **arguments, privacy_check=self._permits_local_text,
                expert=expert,
            )
            return DialogueChange(
                revised.record(), "ORIGINAL_ACCEPTED", original=(original_id, turn_id, text),
            )

        return self._change(
            command, "submit-original", arguments | {"expert": expert_reference}, change,
        )

    def bind_expert(self, command: DialogueCommand, reference: tuple[str, int, str]):
        def change(_idea, state):
            if (state.turn_id is not None or state.expert is not None
                    or self._resolve_expert is None):
                raise CompanionConflict("EXPERT_REFERENCE_DENIED")
            expert = self._resolve_expert(command.actor, *reference)
            if expert is None or (
                expert.definition_id, expert.definition_version, expert.definition_hash
            ) != reference:
                raise CompanionConflict("EXPERT_REFERENCE_DENIED")
            revised = replace(state, version=state.version + 1, expert=expert)
            return DialogueChange(revised.record(), "EXPERT_BOUND")

        return self._change(command, "bind-expert", {"reference": reference}, change)

    def answer_local(
        self, command: DialogueCommand, *, question_id: str, field: str, value: str,
        turn_id: str, proposal_id: str,
    ) -> DialogueRevision:
        arguments = {
            "question_id": question_id, "field": field, "value": value,
            "turn_id": turn_id, "proposal_id": proposal_id,
        }

        def change(_idea, state):
            revised = dialogue.answer_local(
                state, **command.expected, **arguments, privacy_check=self._permits_local_text,
            )
            return DialogueChange(revised.record(), "LOCAL_CLARIFICATION")

        return self._change(command, "answer-local", arguments, change)

    def model_proposal(
        self, command: DialogueCommand, *, proposal_id: str, binding: ProposalBinding,
        payload: bytes, scope: ScopedConnection, separate_action: object = None,
    ) -> DialogueRevision:
        """Publish in the caller's fenced consent acceptance transaction."""
        if type(payload) is not bytes or not 1 <= len(payload) <= 32768:
            raise CompanionConflict("INVALID_INTERPRETATION")
        arguments = {
            "proposal_id": proposal_id, "original_id": binding.original_id,
            "turn_id": binding.turn_id, "payload_sha256": sha256(payload).hexdigest(),
            "rejected_action": separate_action is not None,
        }

        def change(_idea, state):
            accepted = self._interpretation.validate(
                payload, binding=binding, current=state.binding, separate_action=separate_action,
            )
            revised = dialogue.propose(
                state, **command.expected, proposal_id=proposal_id, interpretation=accepted,
            )
            event = (
                "INTERPRETATION_ACTION_DENIED" if accepted.rejected_action
                else "INTERPRETATION_ACCEPTED"
            )
            return DialogueChange(revised.record(), event)

        return self._change(command, "model-proposal", arguments, change, scope=scope)

    def confirm(
        self, command: DialogueCommand, *, proposal_id: str, proposal_hash: str, decision_id: str,
        confirmed_fields: Mapping[str, str], confirmed_assumption_ids: tuple[str, ...],
    ) -> DialogueRevision:
        arguments = {
            "proposal_id": proposal_id, "proposal_hash": proposal_hash, "decision_id": decision_id,
            "confirmed_fields": dict(confirmed_fields),
            "confirmed_assumption_ids": confirmed_assumption_ids,
        }

        def change(idea, state):
            revised = dialogue.confirm(state, **command.expected, **arguments)
            hypothesis = {
                "statement": "Vom Betreiber bestätigte Forschungshypothese",
                **{slot: revised.confirmed.get(field) for field, slot in _SLOTS.items()},
            }
            successor = self._lifecycle.revise(
                self._lifecycle.restore(idea), expected_version=command.idea_version,
                action=IdeaAction.CONFIRM_HYPOTHESIS, decision_id=decision_id,
                created_at=datetime.now(UTC), changes={
                    "hypothesis": hypothesis, "dialogueId": command.dialogue_id,
                    "interpretation": state.proposal,
                    "assumptions": [
                        {"id": identifier, "statement": item.value}
                        for identifier, item in revised.confirmed_assumptions
                    ],
                    "openQuestions": [
                        {"field": item.field, "question": item.question}
                        for item in dialogue.local_questions(revised)
                    ],
                },
            )
            return DialogueChange(
                revised.record(), "HYPOTHESIS_CONFIRMED", next_idea=successor.to_document(),
            )

        return self._change(command, "confirm", arguments, change)

    def request_changes(
        self, command: DialogueCommand, *, proposal_id: str, proposal_hash: str, decision_id: str,
        requested_fields: tuple[str, ...], clarification: str,
    ) -> DialogueRevision:
        arguments = {
            "proposal_id": proposal_id, "proposal_hash": proposal_hash, "decision_id": decision_id,
            "requested_fields": requested_fields, "clarification": clarification,
        }

        def change(_idea, state):
            revised = dialogue.request_changes(
                state, **command.expected, **arguments, privacy_check=self._permits_local_text,
            )
            return DialogueChange(revised.record(), "CHANGES_REQUESTED")

        return self._change(command, "request-changes", arguments, change)

    def assess_confirmed(
        self, actor: str, idea_id: str, dialogue_id: str, *,
        research_mode: str, in_scope: bool, parameters: Parameters,
        data: DataSuitability | None = None, approximation: Approximation | None = None,
        accepted_approximation_binding: str | None = None, required_data_missing: bool = False,
    ) -> Assessment:
        """Use explicit confirmations and the existing Quant catalog to classify a hypothesis."""
        revision = self._store.read_bound_dialogue(actor, idea_id, dialogue_id)
        state = Dialogue.from_record(revision.document)
        confirmed = state.confirmed
        hypothesis = HypothesisIdentity.from_document({
            slot: confirmed.get(field) for field, slot in _SLOTS.items()
        })
        return assess_testability(
            hypothesis, research_mode=research_mode, in_scope=in_scope,
            mapping=ema_crossover_mapping(hypothesis, parameters, research_mode), data=data,
            approximation=approximation, idea_binding=(idea_id, revision.idea_version),
            accepted_approximation_binding=accepted_approximation_binding,
            required_data_missing=required_data_missing,
        )

    def revise_idea(
        self, command: DialogueCommand, *, action: IdeaAction, decision_id: str,
        changes: Mapping | None = None,
        scope: ScopedConnection | None = None,
    ) -> DialogueRevision:
        if action in {IdeaAction.CREATE, IdeaAction.CONFIRM_HYPOTHESIS}:
            raise CompanionConflict("INVALID_IDEA_TRANSITION")
        arguments = {"action": action, "decision_id": decision_id, "changes": dict(changes or {})}

        def change(idea, state):
            successor = self._lifecycle.revise(
                self._lifecycle.restore(idea), expected_version=command.idea_version,
                created_at=datetime.now(UTC), **arguments,
            ).to_document()
            preserves_meaning = (
                successor["hypothesis"] == idea["hypothesis"] and action != IdeaAction.RECHECK
            )
            revised = replace(
                state, idea_version=state.idea_version + 1, version=state.version + 1,
                proposal_json=None, local_fields=(), requested_fields=(), clarification=None,
                last_decision_id=decision_id,
                confirmed_fields=state.confirmed_fields if preserves_meaning else (),
                confirmed_assumptions=state.confirmed_assumptions if preserves_meaning else (),
            )
            return DialogueChange(revised.record(), "IDEA_REVISED", next_idea=successor)

        return self._change(command, "revise-idea", arguments, change, scope=scope)

    def _change(
        self, command: DialogueCommand, operation: str, arguments: dict,
        change: Callable[[dict, Dialogue], DialogueChange],
        *, scope: ScopedConnection | None = None,
    ) -> DialogueRevision:
        def transition(idea: dict, current: DialogueRevision) -> DialogueChange:
            result = change(idea, Dialogue.from_record(current.document))
            expected_idea = command.idea_version + int(result.next_idea is not None)
            if (result.document["version"], result.document["idea_version"]) != (
                command.dialogue_version + 1, expected_idea,
            ):
                raise CompanionConflict("INVALID_COMPANION_DOCUMENT")
            return result

        return self._store.change_dialogue(
            command.actor, command.idea_id, command.dialogue_id, **command.expected,
            key=command.key, command=_command(operation, arguments), change=transition, scope=scope,
        )
