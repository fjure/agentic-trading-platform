"""Publish a companion proposal under the original research dispatch fence."""

import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from packages.agent_research.budget import Tokens
from packages.agent_research.companion.dialogue import Dialogue
from packages.agent_research.companion.interpretation import ProposalBinding
from packages.agent_research.companion.service import CompanionService, DialogueCommand
from packages.agent_research.companion.store import (
    CompanionConflict,
    CompanionStore,
    DialogueRevision,
)
from packages.agent_research.processing import (
    Preview,
    ProcessingBinding,
    ProcessingStore,
    ProjectionVault,
)
from packages.agent_research.runs import Claim
from packages.persistence.transaction import ReadOnlyScopedConnection
from services.llm_gateway.content_policy import valid_projection
from services.llm_gateway.provider_wire import ProviderResult, ResultState

_PROJECTION_FIELDS = {
    "market": "MARKET", "trigger": "TRIGGER", "signal": "SIGNAL",
    "observationWindow": "OBSERVATION_WINDOW", "expectedOutcome": "TARGET",
    "measurementHorizon": "MEASUREMENT_HORIZON", "benchmark": "BENCHMARK",
}


def create_processing_preview(
    store: CompanionStore, vault: ProjectionVault, binding: ProcessingBinding, *,
    idea_id: str, idea_version: int, original_id: str, session: str,
    deadline: datetime, projection: bytes, permits: Callable[[bytes], bool],
) -> Preview:
    if not valid_projection(projection, binding.data_classes) or binding.turn_version != 1:
        raise CompanionConflict("PROJECTION_NOT_ALLOWED")
    revision = store.read_bound_dialogue(binding.actor, idea_id, binding.dialogue_id)
    state = Dialogue.from_record(revision.document)
    if (
        state.idea_version != idea_version or state.version != binding.dialogue_version
        or state.turn_id != binding.turn_id or state.original_id != original_id
    ):
        raise CompanionConflict("STALE_DIALOGUE_VERSION")
    original = store.read_original(
        binding.actor, idea_id, binding.dialogue_id, original_id, turn_id=binding.turn_id,
    )
    projected = json.loads(projection)
    if projected["excerpt"] not in original or any(
        state.confirmed.get(_PROJECTION_FIELDS[name]) != value
        for name, value in projected["confirmedContext"].items()
    ):
        raise CompanionConflict("PROJECTION_NOT_ALLOWED")
    try:
        allowed = permits(projection) is True
    except Exception:
        allowed = False
    if not allowed:
        raise CompanionConflict("PROJECTION_NOT_ALLOWED")
    return vault.issue(binding, session=session, content=projection, deadline=deadline)


def check_current_processing_turn(
    store: CompanionStore, scope: ReadOnlyScopedConnection, binding: ProcessingBinding, *,
    idea_id: str, idea_version: int, original_id: str,
) -> None:
    if binding.turn_version != 1:
        raise CompanionConflict("STALE_DIALOGUE_VERSION")
    store.check_current_turn(
        scope, binding.actor, idea_id, binding.dialogue_id, idea_version=idea_version,
        dialogue_version=binding.dialogue_version, turn_id=binding.turn_id, original_id=original_id,
    )


def publish_model_proposal(
    companion: CompanionService, processing: ProcessingStore,
    command: DialogueCommand, *,
    claim: Claim, receipt_id: UUID, attempt_id: UUID, processing_binding: ProcessingBinding,
    proposal_id: str, original_id: str, provider_result: ProviderResult,
    check_policy: Callable[[ReadOnlyScopedConnection, ProcessingBinding], None],
    separate_action: object = None,
) -> DialogueRevision:
    if (
        provider_result.state is not ResultState.ACCEPTED
        or type(provider_result.output) is not dict
        or provider_result.usage is None
        or command.actor != processing_binding.actor
        or command.dialogue_id != processing_binding.dialogue_id
        or command.dialogue_version != processing_binding.dialogue_version
        or claim.run_id != processing_binding.run_id
    ):
        raise CompanionConflict("INTERPRETATION_NOT_PUBLISHABLE")
    binding = ProposalBinding(
        original_id, processing_binding.turn_id, command.idea_version, command.dialogue_version,
    )
    usage = Tokens(provider_result.usage.input_tokens, provider_result.usage.output_tokens)
    try:
        content = json.dumps(provider_result.output, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError):
        raise CompanionConflict("INVALID_INTERPRETATION") from None
    revision = None

    def persist(scope):
        nonlocal revision
        revision = companion.model_proposal(
            command, proposal_id=proposal_id, binding=binding, payload=content, scope=scope,
            separate_action=separate_action,
        )

    processing.accept_result(
        claim, receipt_id, attempt_id, proposal_id, binding=processing_binding,
        check_policy=check_policy, persist_result=persist, reported_tokens=usage,
    )
    if revision is None:
        raise CompanionConflict("INTERPRETATION_NOT_PUBLISHABLE")
    return revision
