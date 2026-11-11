"""Synthetic-only provider controls for the offline Research integration harness."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock
from time import monotonic
from uuid import UUID

from packages.agent_research.budget import BudgetDenied
from packages.agent_research.registry import AgentPolicyDenied, ModelBinding
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunConflict
from packages.domain.models import utc_string
from services.control_api.agent_research_routes import AgentResearchError
from services.llm_gateway.probe import (
    PROBE_INPUT_TOKENS,
    PROBE_OUTPUT_TOKENS,
    PROBE_TIMEOUT_SECONDS,
)
from services.llm_gateway.provider_wire import Provider
from services.setup.llm_credentials import (
    CredentialBinding,
    CredentialDenied,
    CredentialState,
    CredentialStatus,
    ProbeRef,
    SyntheticCredentialStore,
)

from .research_probe import BudgetedSyntheticProbe

SYNTHETIC_SETUP_MARKER = "synthetic-fixture-only"
_SETUP_OPERATIONS = {
    "saveAgentProviderCredential",
    "rotateAgentProviderCredential",
    "activateAgentProviderCredential",
    "revokeAgentProviderCredential",
}


class _Replay:
    """Keep this harness's versioned receipts for the lifetime of the process."""

    def __init__(self):
        self._lock = Lock()
        self._receipts: dict[tuple[str, str], tuple[bytes, dict, float]] = {}

    def command(self, operation: str, key: str, payload: dict, perform: Callable[[], dict]) -> dict:
        if not key:
            raise AgentResearchError(422, "INVALID_REQUEST")
        binding = (operation, key)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        fingerprint = sha256(encoded).digest()
        with self._lock:
            now = monotonic()
            self._receipts = {key: item for key, item in self._receipts.items() if item[2] > now}
            previous = self._receipts.get(binding)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise AgentResearchError(409, "IDEMPOTENCY_CONFLICT")
                return dict(previous[1])
            if len(self._receipts) >= 256:
                raise AgentResearchError(429, "BUDGET_EXHAUSTED")
            result = perform()
            self._receipts[binding] = (fingerprint, result, now + 86400)
            return dict(result)


def _credential(status: CredentialStatus) -> dict:
    result = {
        "credentialVersion": status.version,
        "state": status.state.value,
        "updatedAt": utc_string(status.updated_at),
        "providerRevocationState": status.provider_revocation_state,
    }
    if status.generation is not None:
        result["credentialGeneration"] = status.generation
    if status.locally_revoked_at is not None:
        result["locallyRevokedAt"] = utc_string(status.locally_revoked_at)
    return result


def _binding(model: ModelBinding, provider: Provider, payload: dict) -> CredentialBinding:
    if (
        payload["profileId"], payload["profileGeneration"], payload["modelGeneration"]
    ) != (model.profile_id, model.profile_generation, model.model_generation):
        raise AgentResearchError(403, "REQUEST_FORBIDDEN")
    return CredentialBinding(
        provider, payload["credentialGeneration"], model.profile_id,
        model.profile_generation, model.model_generation,
    )


def _error(error: CredentialDenied | RunConflict | AgentPolicyDenied | BudgetDenied):
    if error.code in {"VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"}:
        return AgentResearchError(409, error.code)
    if error.code == "BUDGET_EXHAUSTED":
        return AgentResearchError(429, error.code)
    if error.code == "CREDENTIAL_STORAGE_UNAVAILABLE":
        return AgentResearchError(503, "PROVIDER_UNAVAILABLE")
    return AgentResearchError(403, "REQUEST_FORBIDDEN")


class SyntheticCredentialSetup:
    """Independent fixture setup handler. The store generates its own offline canaries."""

    def __init__(self, credentials: SyntheticCredentialStore, *, model: ModelBinding):
        self._credentials = credentials
        self._model = model
        self._provider = Provider(model.provider)
        self._replay = _Replay()

    def __call__(self, operation, params, query, payload, *, actor, key, trace, scope):
        name = operation.operation_id
        if name not in _SETUP_OPERATIONS or params["provider"] != self._provider:
            raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
        if actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        data = {field: value for field, value in payload.items() if field != "credential"}
        if name in {"saveAgentProviderCredential", "rotateAgentProviderCredential"} and (
            payload.get("credential") != SYNTHETIC_SETUP_MARKER
        ):
            raise AgentResearchError(422, "INVALID_REQUEST")
        try:
            value = self._replay.command(name, key, data, lambda: self._perform(name, data))
        except CredentialDenied as error:
            raise _error(error) from None
        return 200, value

    def _perform(self, name, payload):
        expected = payload["expectedCredentialVersion"]
        if name == "saveAgentProviderCredential":
            status = self._credentials.save(self._provider, expected_version=expected)
        elif name == "rotateAgentProviderCredential":
            status = self._credentials.rotate(
                self._provider, expected_version=expected,
                expected_generation=payload["expectedCredentialGeneration"],
            )
        elif name == "activateAgentProviderCredential":
            binding = _binding(self._model, self._provider, payload)
            try:
                verified = UUID(payload["verifiedProbeId"])
            except ValueError:
                raise AgentResearchError(422, "INVALID_REQUEST") from None
            status = self._credentials.activate(
                binding, expected_version=expected, verified_probe_id=verified,
            )
        else:
            status = self._credentials.revoke(
                self._provider, expected_version=expected,
                generation=payload["credentialGeneration"],
            )
        return _credential(status)


class SyntheticProviderControls:
    """Project one configured offline capability and enqueue its budgeted probe."""

    def __init__(
        self, credentials: SyntheticCredentialStore, probe: BudgetedSyntheticProbe, *,
        model: ModelBinding, pricing_version: int,
    ):
        self._credentials = credentials
        self._probe = probe
        self._model = model
        self._provider = Provider(model.provider)
        self._pricing_version = pricing_version
        self._replay = _Replay()
        self._lock = Lock()
        self._latest: ProbeRef | None = None
        self._probe_state = "NOT_TESTED"
        self._step = probe.supervisor_step(self._lookup)
        self.extra = {"listAgentProviders": self._list, "probeAgentProvider": self._enqueue}

    def _lookup(self, probe_id: UUID) -> ProbeRef | None:
        with self._lock:
            if self._latest is None or self._latest.probe_id != probe_id:
                return None
            return self._latest

    def _status_for(self, reference: str, state: str) -> None:
        with self._lock:
            if self._latest is not None and self._latest.probe_id.hex == reference:
                self._probe_state = state

    def step(self, claim, work, recovered):
        reference = work.input_ref
        self._status_for(reference, "IN_FLIGHT")
        try:
            result = self._step(claim, work, recovered)
        except BudgetDenied as error:
            if error.code == "BUDGET_EXHAUSTED":
                self._status_for(reference, "RATE_LIMITED")
            raise
        state = "VERIFIED" if result.target is RunState.COMPLETED else "OUTCOME_UNKNOWN"
        self._status_for(reference, state)
        return result

    def _list(self, params, query, payload, *, actor, key, trace, scope):
        if actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        if query.get("cursor") is not None:
            raise AgentResearchError(422, "INVALID_REQUEST")
        try:
            with self._lock:
                status = self._credentials.status(self._provider)
                probe, state = self._latest, self._probe_state
        except CredentialDenied as error:
            raise _error(error) from None
        availability = {
            CredentialState.NOT_CONFIGURED: "DISABLED",
            CredentialState.ACTIVE: "AVAILABLE",
            CredentialState.REVOKED: "REVOKED",
        }.get(status.state, "BLOCKED")
        model = self._model
        result = {
            "binding": {
                "provider": self._provider.value, "profileId": model.profile_id,
                "profileGeneration": model.profile_generation, "modelId": model.model_id,
                "modelGeneration": model.model_generation,
            },
            "credential": _credential(status), "availability": availability,
            "adapter": {Provider.OPENAI: "OPENAI_RESPONSES",
                        Provider.ANTHROPIC: "ANTHROPIC_MESSAGES"}[self._provider],
            "outputSchemaVersion": 1, "schemaSubsetVersion": 1,
            "structuredOutputSupported": True, "maxInputTokens": PROBE_INPUT_TOKENS,
            "maxOutputTokens": PROBE_OUTPUT_TOKENS, "timeoutSeconds": PROBE_TIMEOUT_SECONDS,
            "cancelSupport": "NONE", "rateLimitSupport": "NONE",
            "dataHandlingPolicyId": "synthetic-probe-only", "dataHandlingPolicyVersion": 1,
            "pricingVersion": self._pricing_version,
            "probeState": "NOT_TESTED",
        }
        if probe is not None and probe.binding.credential_generation == status.generation:
            if state in {"QUEUED", "IN_FLIGHT"} and probe.expires_at <= datetime.now(UTC):
                state = "OUTCOME_UNKNOWN" if state == "IN_FLIGHT" else "REJECTED_BEFORE_EXECUTION"
            result.update({"lastProbeId": probe.probe_id.hex, "probeState": state})
        return 200, {"items": [result]}

    def _enqueue(self, params, query, payload, *, actor, key, trace, scope):
        if params["provider"] != self._provider:
            raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
        if actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        binding = _binding(self._model, self._provider, payload)

        def perform():
            with self._lock:
                current = self._credentials.status(self._provider)
                if current.generation != binding.credential_generation:
                    raise AgentResearchError(409, "VERSION_CONFLICT")
                probe = self._credentials.begin_probe(binding, expected_version=current.version)
                self._probe.enqueue(probe, actor=actor, key=key, trace_id=trace)
                self._latest, self._probe_state = probe, "QUEUED"
            return payload | {
                "probeId": probe.probe_id.hex, "provider": self._provider.value,
                "status": "QUEUED", "acceptedAt": utc_string(datetime.now(UTC)),
            }

        try:
            value = self._replay.command("probeAgentProvider", key, payload, perform)
        except (CredentialDenied, RunConflict, AgentPolicyDenied, BudgetDenied) as error:
            raise _error(error) from None
        return 202, value
