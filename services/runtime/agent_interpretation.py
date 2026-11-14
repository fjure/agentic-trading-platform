"""A supervisor step for the explicitly configured offline interpretation path."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from packages.agent_research.budget import ModelOutcome, TokenPrices, Tokens
from packages.agent_research.companion.dialogue import DialogueError
from packages.agent_research.companion.interpretation import InterpretationDenied
from packages.agent_research.companion.service import DialogueCommand
from packages.agent_research.companion.store import CompanionConflict
from packages.agent_research.processing import ProcessingBinding
from packages.agent_research.registry import AgentVersion
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunConflict, RunKind
from services.control_api.agent_processing_projection import ProcessingAuthority
from services.llm_gateway.app import GatewayDispatchClient
from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied
from services.llm_gateway.offline_gateway import DispatchRequest, OfflineGateway
from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
from services.llm_gateway.provider_wire import ResultState
from services.setup.llm_credentials import (
    CredentialDenied,
    CredentialState,
    SyntheticCredentialStore,
)

from .agent_research import StepResult
from .research_admission import PendingDispatch, ResearchDispatchAdmission
from .research_companion import publish_model_proposal


@dataclass(frozen=True, slots=True)
class OfflineDialogueProfile:
    agent: AgentVersion
    capability: Capability
    policy_generation: int
    current: Callable
    permits: Callable[[bytes], bool]
    prices: TokenPrices
    tokens: Tokens
    context_window: int
    journal: DispatchJournal
    transport: OfflineProviderHttp
    credentials: SyntheticCredentialStore


@dataclass(frozen=True, slots=True)
class ProviderDialogueProfile:
    agent: AgentVersion
    capability: Capability
    policy_generation: int
    current: Callable
    permits: Callable[[bytes], bool]
    prices: TokenPrices
    tokens: Tokens
    context_window: int
    gateway: GatewayDispatchClient
    cancel: Callable
    evidence_revision: int
    tariff_revision: int
    data_classes: tuple[str, ...]


class OfflineDialogueStep:
    def __init__(self, engine, runs, attempts, ideas, companion, projection, profile):
        self.engine, self.runs, self.attempts = engine, runs, attempts
        self.ideas, self.companion, self.projection = ideas, companion, projection
        self.profile = profile
        self.pending = None  # Only the isolated single-step supervisor writes this slot.
        self.admission = ResearchDispatchAdmission(
            projection.processing,
            lambda _: self.pending,
            projection.check_dispatch,
        )
        if isinstance(profile, ProviderDialogueProfile):
            self.gateway = profile.gateway
            self.cancel = profile.cancel
        else:
            self.gateway = OfflineGateway(
                profile.journal,
                profile.transport,
                self.admission.consume,
                credentials=profile.credentials,
                capabilities=(profile.capability,),
            )
            self.cancel = self.gateway.cancel

    def enqueue(self, actor, dialogue_id, turn_id, trace):
        return self.runs.enqueue(
            actor=actor,
            key=turn_id,
            kind=RunKind.DIALOGUE,
            input_ref=dialogue_id,
            trace_id=trace,
            agent=self.profile.agent,
        )

    def authority(self, scope, actor, dialogue, profile_id):
        capability = self.profile.capability
        model = self.profile.agent.model_binding
        if (
            model is None
            or profile_id != capability.profile_id
            or (
                model.provider,
                model.profile_id,
                model.profile_generation,
                model.model_id,
                model.model_generation,
            )
            != (
                capability.provider,
                capability.profile_id,
                capability.profile_generation,
                capability.model,
                capability.model_generation,
            )
        ):
            raise RunConflict("MODEL_CONTEXT_DENIED")
        row = scope.execute(
            text("""
            SELECT run_id,deadline_at FROM agent_research.runs
            WHERE actor=:actor AND input_ref=:dialogue AND request_key_hash=:turn
              AND kind='DIALOGUE'
              AND state IN ('QUEUED','RUNNING','WAITING_FOR_PROCESSING_DECISION')
              AND deadline_at>clock_timestamp()
        """),
            {
                "actor": actor,
                "dialogue": dialogue.dialogue_id,
                "turn": sha256(dialogue.turn_id.encode()).hexdigest(),
            },
        ).one_or_none()
        if row is None:
            raise RunConflict("MODEL_CONTEXT_DENIED")
        binding = ProcessingBinding(
            row.run_id,
            actor,
            dialogue.dialogue_id,
            dialogue.version,
            dialogue.turn_id,
            1,
            capability.provider.value,
            profile_id,
            capability.profile_generation,
            capability.model,
            capability.model_generation,
            capability.credential_generation,
            self.profile.policy_generation,
            capability.envelope_version,
            self.profile.data_classes if isinstance(self.profile, ProviderDialogueProfile)
            else ("OPERATOR_RESEARCH_EXCERPT", "CONFIRMED_RESEARCH_FIELDS"),
            "hypothesis-interpretation",
            **(
                {
                    "evidence_revision": self.profile.evidence_revision,
                    "tariff_revision": self.profile.tariff_revision,
                }
                if isinstance(self.profile, ProviderDialogueProfile) else {}
            ),
        )
        if self.profile.current(scope, binding) is not True:
            raise RunConflict("MODEL_CONTEXT_DENIED")
        if isinstance(self.profile, ProviderDialogueProfile):
            from services.llm_gateway.provider_http import provider_request_bytes

            return ProcessingAuthority(
                binding, row.deadline_at, self.profile.permits,
                wire=lambda projection: provider_request_bytes(capability, projection),
            )
        try:
            credential = self.profile.credentials.status(
                capability.provider,
                capability.credential_generation,
            )
        except CredentialDenied:
            raise RunConflict("MODEL_CONTEXT_DENIED") from None
        if credential.state is not CredentialState.ACTIVE:
            raise RunConflict("MODEL_CONTEXT_DENIED")
        return ProcessingAuthority(binding, row.deadline_at, self.profile.permits)

    def run(self, claim, request, _recovered):
        with self.engine.connect() as connection:
            row = connection.execute(
                text("""
                SELECT receipt_id,preview_id,binding,commitment,expires_at,state,result_ref
                FROM agent_research.processing_grants WHERE run_id=:run
                ORDER BY created_at DESC,receipt_id DESC LIMIT 1
            """),
                {"run": claim.run_id},
            ).one_or_none()
        if row is None:
            return StepResult(RunState.WAITING_FOR_PROCESSING_DECISION)
        if row.state == "ACCEPTED":
            return StepResult(RunState.COMPLETED, row.result_ref)
        if row.state != "AUTHORIZED":
            return StepResult(RunState.WAITING_FOR_INPUT)
        bound = {key: value for key, value in row.binding.items() if key != "purpose"}
        binding = ProcessingBinding(
            **(
                bound
                | {
                    "run_id": claim.run_id,
                    "data_classes": tuple(row.binding["data_classes"]),
                }
            )
        )
        store = self.projection.processing
        try:
            content = store.vault._dispatch_content(row.preview_id, binding, row.commitment)
        except RunConflict:
            return StepResult(RunState.WAITING_FOR_INPUT)
        capability = self.profile.capability
        timeout = min(20, int(capability.timeout_seconds))
        deadline = min(
            row.expires_at,
            request.dispatch_deadline,
            datetime.now(UTC) + timedelta(seconds=timeout),
        )
        if deadline <= datetime.now(UTC):
            return StepResult(RunState.WAITING_FOR_INPUT)
        attempt_id = uuid5(NAMESPACE_URL, f"agent-interpretation:{claim.run_id}:{claim.fence}")
        self.attempts.reserve(
            claim,
            attempt_id=attempt_id,
            step_id=binding.step_id,
            request_hash=row.commitment,
            model=self.profile.agent.model_binding,
            prices=self.profile.prices,
            tokens=self.profile.tokens,
            context_window=self.profile.context_window,
            timeout_seconds=timeout,
            **(
                {
                    "evidence_revision": self.profile.evidence_revision,
                    "authority_hash": row.commitment,
                }
                if isinstance(self.profile, ProviderDialogueProfile) else {}
            ),
        )
        dispatch = DispatchRequest(
            attempt_id,
            row.commitment,
            sha256(content).hexdigest(),
            capability.provider,
            capability.model,
            binding.profile_id,
            binding.profile_generation,
            binding.model_generation,
            binding.credential_generation,
            binding.envelope_version,
            deadline,
        )
        del content
        self.pending = PendingDispatch(
            dispatch, claim, row.receipt_id, binding, capability,
            **(
                {"billable_input_bound": self.profile.tokens.input}
                if isinstance(self.profile, ProviderDialogueProfile) else {}
            ),
        )
        try:
            result = self.gateway.dispatch(dispatch)
        finally:
            self.pending = None
        if result.state is not ResultState.ACCEPTED:
            self._settle_failure(claim, dispatch, result)
            return StepResult(RunState.WAITING_FOR_INPUT)
        idea_id, revision = self.ideas.read_owned_dialogue(binding.actor, binding.dialogue_id)
        original_id = revision.document["original_id"]
        proposal_id = str(uuid5(NAMESPACE_URL, f"agent-proposal:{attempt_id}"))
        command = DialogueCommand(
            binding.actor,
            idea_id,
            binding.dialogue_id,
            revision.idea_version,
            binding.dialogue_version,
            proposal_id,
        )
        try:
            publish_model_proposal(
                self.companion,
                store,
                command,
                claim=claim,
                receipt_id=row.receipt_id,
                attempt_id=attempt_id,
                processing_binding=binding,
                proposal_id=proposal_id,
                original_id=original_id,
                provider_result=result,
                check_policy=self.projection.check_dispatch,
            )
        except CompanionConflict as error:
            if error.code != "INTERPRETATION_SETTLEMENT_BLOCKED":
                self._settle_failure(claim, dispatch, result)
            return StepResult(RunState.WAITING_FOR_INPUT)
        except (DialogueError, InterpretationDenied):
            self._settle_failure(claim, dispatch, result)
            return StepResult(RunState.WAITING_FOR_INPUT)
        return StepResult(RunState.COMPLETED, proposal_id)

    def _settle_failure(self, claim, dispatch, result):
        authority = (
            {
                "evidence_revision": self.profile.evidence_revision,
                "authority_hash": dispatch.request_hash,
            }
            if isinstance(self.profile, ProviderDialogueProfile) else {}
        )
        try:
            unsent = (
                isinstance(self.profile, OfflineDialogueProfile)
                and self.profile.journal.status(
                    dispatch.attempt_id,
                    dispatch.request_hash,
                )
                is DispatchOutcome.UNSENT
            )
        except JournalDenied:
            unsent = False
        if not unsent:
            with self.engine.connect() as connection:
                status = connection.execute(
                    text("""
                    SELECT status FROM agent_research.model_attempts
                    WHERE attempt_id=:attempt AND run_id=:run
                """),
                    {"attempt": dispatch.attempt_id, "run": claim.run_id},
                ).scalar_one_or_none()
            unsent = status == "RESERVED"
        if unsent:
            self.attempts.settle(
                claim,
                dispatch.attempt_id,
                ModelOutcome.UNSENT,
                unsent_evidence_ref=str(dispatch.attempt_id),
                **authority,
            )
            return
        usage = result.usage
        self.attempts.settle(
            claim,
            dispatch.attempt_id,
            ModelOutcome.OUTCOME_UNKNOWN if usage is None else ModelOutcome.RESULT_UNAVAILABLE,
            reported_tokens=None
            if usage is None
            else Tokens(usage.input_tokens, usage.output_tokens),
            **authority,
        )
