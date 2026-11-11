"""Synthetic provider commands only expose typed and redacted projections."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.agent_research.registry import ModelBinding
from packages.agent_research.run_state import RunState
from services.control_api.agent_research_routes import (
    OPERATIONS,
    AgentResearchError,
    validate_response,
)
from services.llm_gateway.provider_wire import Provider
from services.runtime.agent_provider_controls import (
    SYNTHETIC_SETUP_MARKER,
    SyntheticCredentialSetup,
    SyntheticProviderControls,
)
from services.runtime.agent_research import StepResult
from services.setup.llm_credentials import CredentialState, SyntheticCredentialStore

MODEL = ModelBinding("OPENAI", "synthetic-profile", 1, "fixture-model", 1)
PROFILE = {"profileId": MODEL.profile_id, "profileGeneration": 1, "modelGeneration": 1}
AUTHORITY = {"actor": "local-operator", "trace": "a" * 32, "scope": "b" * 64}
OPS = {op.operation_id: op for op in OPERATIONS}
PARAMS = {"provider": "OPENAI"}


def setup_call(setup, name, payload, key):
    operation = OPS[name + "AgentProviderCredential"]
    status, body = setup(operation, PARAMS, {}, payload, key=key, **AUTHORITY)
    validate_response(operation, status, body)
    return body


def provider_call(provider, name, payload=None, *, key=None):
    status, body = provider.extra[name](
        PARAMS if payload else {}, {}, payload, key=key, **AUTHORITY,
    )
    validate_response(OPS[name], status, body)
    return body


class _SyntheticProbeDouble:
    def __init__(self, credentials):
        self.credentials = credentials
        self.queued = []

    def enqueue(self, probe, *, actor, key, trace_id):
        self.queued.append(probe)
        return uuid4()

    def supervisor_step(self, lookup):
        def step(claim, work, recovered):
            probe = lookup(self.queued[0].probe_id)
            assert probe == self.queued[0]
            self.credentials.take_probe(probe)
            self.credentials.record_probe(probe, passed=True)
            return StepResult(RunState.COMPLETED)

        return step


def test_provider_controls_keep_setup_probe_activation_and_revocation_separate():
    credentials = SyntheticCredentialStore()
    probe = _SyntheticProbeDouble(credentials)
    controls = SyntheticProviderControls(credentials, probe, model=MODEL, pricing_version=1)
    setup = SyntheticCredentialSetup(credentials, model=MODEL)

    disabled = provider_call(controls, "listAgentProviders")["items"][0]
    assert disabled["credential"]["state"] == "NOT_CONFIGURED"
    assert disabled["availability"] == "DISABLED" and probe.queued == []
    incoming = {"expectedCredentialVersion": 1, "credential": SYNTHETIC_SETUP_MARKER}
    stored = setup_call(setup, "save", incoming, "synthetic-save-key")
    assert setup_call(setup, "save", incoming, "synthetic-save-key") == stored
    assert stored["credentialGeneration"] == 1 and stored["state"] == "STORED"
    assert probe.queued == [] and "credential" not in stored

    request = PROFILE | {"credentialGeneration": 1}
    accepted = provider_call(controls, "probeAgentProvider", request, key="fixture-probe-key")
    assert provider_call(
        controls, "probeAgentProvider", request, key="fixture-probe-key",
    ) == accepted
    assert len(probe.queued) == 1
    queued = provider_call(controls, "listAgentProviders")["items"][0]
    assert queued["lastProbeId"] == accepted["probeId"] and queued["probeState"] == "QUEUED"
    activate = request | {"verifiedProbeId": accepted["probeId"],
                          "expectedCredentialVersion": queued["credential"]["credentialVersion"]}
    with pytest.raises(AgentResearchError) as error:
        setup_call(setup, "activate", activate, "fixture-activate-key")
    assert (error.value.status, error.value.code) == (403, "REQUEST_FORBIDDEN")

    work = SimpleNamespace(input_ref=accepted["probeId"])
    assert controls.step(None, work, ()).target == RunState.COMPLETED
    verified = provider_call(controls, "listAgentProviders")["items"][0]
    assert verified["probeState"] == verified["credential"]["state"] == "VERIFIED"
    activate["expectedCredentialVersion"] = verified["credential"]["credentialVersion"]
    active = setup_call(setup, "activate", activate, "fixture-activate-key")
    assert active["state"] == "ACTIVE"

    revoked = setup_call(setup, "revoke", {
        "expectedCredentialVersion": active["credentialVersion"], "credentialGeneration": 1,
    }, "fixture-revoke-key")
    assert revoked["state"] == "REVOKED" and revoked["providerRevocationState"] == "NOT_CONFIRMED"
    assert "locallyRevokedAt" in revoked


def test_setup_rejects_arbitrary_values_and_version_reuse_without_mutation():
    credentials = SyntheticCredentialStore()
    setup = SyntheticCredentialSetup(credentials, model=MODEL)
    payload = {"credential": "unapproved-input", "expectedCredentialVersion": 1}
    with pytest.raises(AgentResearchError) as error:
        setup_call(setup, "save", payload, "fixture-save-key")
    assert (error.value.status, error.value.code) == (422, "INVALID_REQUEST")
    assert credentials.status(Provider.OPENAI).state is CredentialState.NOT_CONFIGURED

    payload["credential"] = SYNTHETIC_SETUP_MARKER
    saved = setup_call(setup, "save", payload, "fixture-save-key")
    with pytest.raises(AgentResearchError) as error:
        setup_call(setup, "save", payload | {"expectedCredentialVersion": 2}, "fixture-save-key")
    assert (error.value.status, error.value.code) == (409, "IDEMPOTENCY_CONFLICT")
    rotated = setup_call(setup, "rotate", payload | {
        "expectedCredentialVersion": saved["credentialVersion"], "expectedCredentialGeneration": 1,
    }, "fixture-rotate-key")
    assert rotated["credentialGeneration"] == 2

    controls = SyntheticProviderControls(
        credentials, _SyntheticProbeDouble(credentials), model=MODEL, pricing_version=1,
    )
    with pytest.raises(AgentResearchError) as error:
        provider_call(controls, "probeAgentProvider", PROFILE | {"credentialGeneration": 1},
                      key="fixture-probe-key")
    assert (error.value.status, error.value.code) == (409, "VERSION_CONFLICT")
