"""Offline gateway dispatch for a fixed, one-shot synthetic credential probe."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from services.setup.llm_credentials import (
    CredentialDenied,
    CredentialState,
    CredentialStatus,
    ProbeRef,
    SyntheticCredentialStore,
)

from .dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied
from .provider_http import Capability, OfflineProviderHttp, conservative_input_bound
from .provider_wire import ResultState, Usage

PROBE_PROJECTION = b"Synthetic connection test. Return true for ok."
PROBE_INPUT_TOKENS = 1024
PROBE_OUTPUT_TOKENS = 32
PROBE_TIMEOUT_SECONDS = 5
_ENVELOPE = "provider-probe-v1"
_INSTRUCTIONS = "Perform the synthetic connectivity check."
_SCHEMA = '{"type":"object","properties":{"ok":{"type":"boolean","enum":[true]}},' \
    '"required":["ok"],"additionalProperties":false}'


def probe_hash(probe: ProbeRef, model_id: str) -> str:
    binding = probe.binding
    encoded = json.dumps([
        _ENVELOPE, probe.probe_id.hex, probe.expires_at.isoformat(), binding.provider,
        binding.profile_id, binding.profile_generation, model_id, binding.model_generation,
        binding.credential_generation, PROBE_PROJECTION.decode(), _INSTRUCTIONS, _SCHEMA,
        PROBE_OUTPUT_TOKENS, PROBE_TIMEOUT_SECONDS,
    ], separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProbeDispatchResult:
    outcome: DispatchOutcome
    usage: Usage | None = None
    authorized: bool = False


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    probe: ProbeRef
    model_id: str
    run_id: UUID
    owner: UUID
    fence: int
    run_version: int
    deadline: datetime

    def commitment(self) -> str:
        digest = probe_hash(self.probe, self.model_id)
        return sha256(f"{digest}:{self.deadline.isoformat()}".encode()).hexdigest()


class OfflineProbeGateway:
    def __init__(
        self, journal: DispatchJournal, transport: OfflineProviderHttp,
        credentials: SyntheticCredentialStore, *, authorize: Callable[[ProbeRequest, str], None],
    ):
        self._journal = journal
        self._transport = transport
        self._credentials = credentials
        self._authorize = authorize

    def dispatch(self, request: ProbeRequest) -> ProbeDispatchResult:
        probe = request.probe
        request_hash = probe_hash(probe, request.model_id)
        try:
            self._journal.claim(probe.probe_id, request_hash, probe.expires_at)
        except JournalDenied:
            return ProbeDispatchResult(DispatchOutcome.OUTCOME_UNKNOWN)
        authorized = False
        try:
            self._authorize(request, request.commitment())
            authorized = True
            key = self._credentials.take_probe(probe)
        except (ValueError, RuntimeError):
            return self._finish(probe, DispatchOutcome.UNSENT, authorized=authorized)
        binding = probe.binding

        def stopped() -> bool:
            try:
                status = self._credentials.status(binding.provider, binding.credential_generation)
                return (
                    status.state is not CredentialState.STORED
                    or datetime.now(UTC) >= request.deadline
                )
            except CredentialDenied:
                return True

        remaining = (request.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return self._finish(probe, DispatchOutcome.UNSENT, authorized=True)
        profile = Capability(
            binding.provider, request.model_id, binding.profile_id, binding.profile_generation,
            binding.model_generation, binding.credential_generation, _ENVELOPE,
            _INSTRUCTIONS, _SCHEMA, PROBE_OUTPUT_TOKENS,
            min(remaining, PROBE_TIMEOUT_SECONDS),
        )
        if conservative_input_bound(profile, PROBE_PROJECTION) > PROBE_INPUT_TOKENS:
            return self._finish(probe, DispatchOutcome.UNSENT, authorized=True)
        result = self._transport.send(
            profile, PROBE_PROJECTION, synthetic_credential=key, cancelled=stopped,
        )
        if result.state is ResultState.CAPABILITY_MISMATCH:
            return self._finish(probe, DispatchOutcome.UNSENT, authorized=True)
        usage = result.usage
        if usage is None or stopped() or result.state in {
            ResultState.OUTCOME_UNKNOWN, ResultState.DISPATCH_CANCELLED,
        }:
            return self._finish(probe, DispatchOutcome.OUTCOME_UNKNOWN, authorized=True)
        if (
            result.state is ResultState.ACCEPTED and usage.input_tokens <= PROBE_INPUT_TOKENS
            and usage.output_tokens <= PROBE_OUTPUT_TOKENS
        ):
            return self._finish(probe, DispatchOutcome.COMPLETED, usage, authorized=True)
        return self._finish(
            probe, DispatchOutcome.RESULT_UNAVAILABLE, usage, authorized=True,
        )

    def record_verified(self, probe: ProbeRef, model_id: str) -> CredentialStatus:
        if self._journal.status(probe.probe_id, probe_hash(probe, model_id)) is not (
            DispatchOutcome.COMPLETED
        ):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        return self._credentials.record_probe(probe, passed=True)

    def _finish(
        self, probe: ProbeRef, outcome: DispatchOutcome, usage: Usage | None = None,
        *, authorized: bool,
    ) -> ProbeDispatchResult:
        try:
            self._journal.finish(probe.probe_id, outcome)
        except JournalDenied:
            return ProbeDispatchResult(DispatchOutcome.OUTCOME_UNKNOWN, authorized=authorized)
        return ProbeDispatchResult(outcome, usage, authorized)
