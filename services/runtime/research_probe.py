"""Supervisor-owned budget and fence for a synthetic provider connection test."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from packages.agent_research.budget import ModelOutcome, TokenPrices, Tokens
from packages.agent_research.model_attempts import Attempt, ModelAttemptStore
from packages.agent_research.registry import AgentVersion, ModelBinding
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import Claim, ClaimedInput, RunConflict, RunKind, RunStore
from services.llm_gateway.dispatch_journal import DispatchOutcome
from services.llm_gateway.probe import (
    PROBE_INPUT_TOKENS,
    PROBE_OUTPUT_TOKENS,
    PROBE_TIMEOUT_SECONDS,
    OfflineProbeGateway,
    ProbeRequest,
)
from services.runtime.agent_research import Step, StepResult
from services.setup.llm_credentials import CredentialDenied, CredentialStatus, ProbeRef

_STEP_ID = "provider-probe-v1"


@dataclass(frozen=True, slots=True)
class ProbeReport:
    outcome: DispatchOutcome
    verified: CredentialStatus | None = None


class BudgetedSyntheticProbe:
    """Reserve in the research ledger before asking the offline gateway to test connectivity."""

    def __init__(
        self, runs: RunStore, attempts: ModelAttemptStore, gateway: OfflineProbeGateway,
        *, agent: AgentVersion, prices: TokenPrices, permits_probe: Callable[[ProbeRef], bool],
    ):
        self._runs = runs
        self._attempts = attempts
        self._gateway = gateway
        self._agent = agent
        self._prices = prices
        self._permits_probe = permits_probe

    def _check(self, probe: ProbeRef) -> ModelBinding:
        if not isinstance(probe, ProbeRef):
            raise RunConflict("AGENT_POLICY_DENIED")
        binding = probe.binding
        model = self._agent.model_binding
        if (
            model is None or probe.expires_at <= datetime.now(UTC)
            or (model.provider, model.profile_id, model.profile_generation, model.model_generation)
            != (binding.provider, binding.profile_id, binding.profile_generation,
                binding.model_generation)
            or self._permits_probe(probe) is not True
        ):
            raise RunConflict("AGENT_POLICY_DENIED")
        return model

    def enqueue(self, probe: ProbeRef, *, actor: str, key: str, trace_id: str) -> UUID:
        self._check(probe)
        return self._runs.enqueue(
            actor=actor, key=key, kind=RunKind.PROVIDER_PROBE, input_ref=probe.probe_id.hex,
            trace_id=trace_id, agent=self._agent,
        )

    def _check_claim(self, claim: Claim, probe: ProbeRef) -> datetime:
        work = self._runs.load_input(claim)
        if (
            work.kind is not RunKind.PROVIDER_PROBE or work.input_ref != probe.probe_id.hex
            or work.dispatch_deadline is None
        ):
            raise RunConflict("AGENT_POLICY_DENIED")
        return work.dispatch_deadline

    def authorize_gateway(self, request: ProbeRequest, commitment: str) -> None:
        """Bootstrap-only authority for the isolated offline gateway callback."""
        model = self._check(request.probe)
        claim = Claim(request.run_id, request.owner, request.fence, request.run_version)
        deadline = self._check_claim(claim, request.probe)
        now = datetime.now(UTC)
        if (
            request.model_id != model.model_id or commitment != request.commitment()
            or not now < request.deadline <= min(
                deadline, request.probe.expires_at, now + timedelta(seconds=PROBE_TIMEOUT_SECONDS),
            )
        ):
            raise RunConflict("AGENT_POLICY_DENIED")
        self._attempts.authorize(claim, request.probe.probe_id, commitment)

    def supervisor_step(self, lookup: Callable[[UUID], ProbeRef | None]) -> Step:
        """Use normal supervisor recovery, fencing, and terminal transitions."""
        def step(claim: Claim, work: ClaimedInput, recovered: tuple[Attempt, ...]) -> StepResult:
            if work.kind is not RunKind.PROVIDER_PROBE or recovered:
                return StepResult(RunState.BLOCKED)
            try:
                probe_id = UUID(hex=work.input_ref)
            except ValueError:
                return StepResult(RunState.BLOCKED)
            probe = lookup(probe_id)
            if probe is None:
                return StepResult(RunState.BLOCKED)
            try:
                report = self.execute(claim, probe)
            except CredentialDenied:
                return StepResult(RunState.BLOCKED)
            except RunConflict as error:
                if error.code != "IDEMPOTENCY_CONFLICT":
                    raise
                return StepResult(RunState.BLOCKED)
            target = RunState.COMPLETED if report.verified is not None else RunState.BLOCKED
            return StepResult(target)

        return step

    def execute(self, claim: Claim, probe: ProbeRef) -> ProbeReport:
        model = self._check(probe)
        deadline = min(
            self._check_claim(claim, probe), probe.expires_at,
            datetime.now(UTC) + timedelta(seconds=PROBE_TIMEOUT_SECONDS),
        )
        request = ProbeRequest(
            probe, model.model_id, claim.run_id, claim.owner, claim.fence, claim.version, deadline,
        )
        attempt = self._attempts.reserve(
            claim, attempt_id=probe.probe_id, step_id=_STEP_ID, request_hash=request.commitment(),
            model=model, prices=self._prices,
            tokens=Tokens(PROBE_INPUT_TOKENS, PROBE_OUTPUT_TOKENS),
            context_window=PROBE_INPUT_TOKENS + PROBE_OUTPUT_TOKENS,
            timeout_seconds=PROBE_TIMEOUT_SECONDS,
        )
        if attempt.status != "RESERVED":
            raise RunConflict("PROVIDER_OUTCOME_UNKNOWN")
        result = self._gateway.dispatch(request)
        if not result.authorized:
            # The supervisor never committed a dispatch permission for this execution.
            # Never settle a possibly concurrent claim unless the gateway proved non-send.
            if result.outcome is DispatchOutcome.UNSENT:
                self._attempts.settle(
                    claim, probe.probe_id, ModelOutcome.UNSENT,
                    unsent_evidence_ref=f"probe-journal:{probe.probe_id.hex}",
                )
            return ProbeReport(result.outcome)

        outcome = result.outcome
        if outcome is DispatchOutcome.COMPLETED:
            try:
                self._check(probe)
            except RunConflict:
                outcome = DispatchOutcome.RESULT_UNAVAILABLE
            if datetime.now(UTC) >= deadline:
                outcome = DispatchOutcome.OUTCOME_UNKNOWN
        reported = result.usage
        tokens = Tokens(reported.input_tokens, reported.output_tokens) if reported else None
        self._attempts.settle(
            claim, probe.probe_id, ModelOutcome(outcome.value),
            reported_tokens=tokens,
            result_ref=probe.probe_id.hex if outcome is DispatchOutcome.COMPLETED else None,
            unsent_evidence_ref=(
                f"probe-journal:{probe.probe_id.hex}" if outcome is DispatchOutcome.UNSENT
                else None
            ),
        )
        if outcome is not DispatchOutcome.COMPLETED:
            return ProbeReport(outcome)
        self._check(probe)
        if datetime.now(UTC) >= deadline:
            return ProbeReport(DispatchOutcome.RESULT_UNAVAILABLE)
        return ProbeReport(outcome, self._gateway.record_verified(probe, model.model_id))
