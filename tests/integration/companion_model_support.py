"""Run the consent, gateway and companion publication path with disposable fixtures."""

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from packages.agent_research import registry
from packages.agent_research.budget import TokenPrices, Tokens
from packages.agent_research.companion.dialogue import Dialogue
from packages.agent_research.companion.ideas import IdeaAction
from packages.agent_research.companion.service import DialogueCommand
from packages.agent_research.companion.store import CompanionConflict
from packages.agent_research.companion.testability import Reason
from packages.agent_research.model_attempts import ModelAttemptStore, model_budget_scopes
from packages.agent_research.processing import ProcessingBinding, ProcessingStore, ProjectionVault
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunConflict, RunKind, RunStore
from services.control_api.agent_processing_projection import (
    ProcessingAuthority,
    ProcessingProjection,
)
from services.llm_gateway.app import GatewayDispatchClient, create_gateway_app
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.offline_gateway import DispatchRequest, OfflineGateway
from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
from services.llm_gateway.provider_wire import Provider, ResultState
from services.llm_gateway.supervisor_rpc import (
    AdmissionAuthentication,
    AdmissionClient,
    create_admission_app,
)
from services.runtime.research_admission import PendingDispatch, ResearchDispatchAdmission
from services.runtime.research_companion import (
    create_processing_preview,
    publish_model_proposal,
)
from services.setup.llm_credentials import CredentialBinding, SyntheticCredentialStore
from tests.unit.test_companion_interpretation import CONTRACT, INTERPRETATION


def _provider_schema() -> str:
    definitions = json.loads(CONTRACT.read_text(encoding="utf-8"))["$defs"]
    names = (
        "AgentResearchHypothesis", "AgentHypothesisSlotText", "AgentHypothesisDataType",
        "AgentHypothesisQuestion", "AgentHypothesisField", "AgentHypothesisAssumption",
        "AgentOpaqueId", "AgentTestabilityClass",
    )
    return json.dumps(definitions["ResearchHypothesisInterpretationV1"] | {
        "$defs": {name: definitions[name] for name in names},
    }, separators=(",", ":"))


def _configure_budget(owner, agent, model):
    scopes = model_budget_scopes(agent.agent_id, agent.version, RunKind.DIALOGUE, model, "dialogue")
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.model_budget_policies (scope,version,currency,max_cost,
                max_input_tokens,max_output_tokens,max_calls,enabled)
            VALUES (:scope,1,'USD',0.10,4096,1024,1,true)
        """), [{"scope": scope} for scope in scopes])
        connection.execute(text("""
            INSERT INTO agent_research.model_prices (model_binding,version,currency,
                input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:model AS jsonb),1,'USD',1,2,false)
        """), {"model": json.dumps(asdict(model))})


def _credentials(request):
    credentials = SyntheticCredentialStore()
    binding = CredentialBinding(request.provider, 1, request.profile_id, 1, 1)
    saved = credentials.save(request.provider, expected_version=1)
    probe = credentials.begin_probe(binding, expected_version=saved.version)
    credentials.take_probe(probe)
    verified = credentials.record_probe(probe, passed=True)
    credentials.activate(
        binding, expected_version=verified.version, verified_probe_id=probe.probe_id,
    )
    return credentials


def _dispatch(pending, processing, projection, check_policy):
    request = pending.request
    admission = ResearchDispatchAdmission(
        processing, lambda identifier: pending if identifier == request.attempt_id else None,
        check_policy,
    )
    auth = AdmissionAuthentication(bytes(range(32)))
    calls = []

    def provider(http_request):
        calls.append(http_request.url)
        body = json.loads(http_request.content)
        assert body["input"] == [{"role": "user", "content": projection.decode()}]
        assert body["store"] is False and "tools" not in body
        return httpx.Response(200, json={
            "model": request.model, "status": "completed",
            "usage": {"input_tokens": 93, "output_tokens": 46}, "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(INTERPRETATION)}],
            }],
        })

    with TemporaryDirectory() as directory, TestClient(
        create_admission_app(auth, admission.consume),
    ) as admission_http:
        gateway = OfflineGateway(
            DispatchJournal.provision(Path(directory) / "dispatch.sqlite"),
            OfflineProviderHttp(httpx.MockTransport(provider)),
            AdmissionClient(admission_http, auth).authorize, credentials=_credentials(request),
            capabilities=(pending.capability,),
        )
        with TestClient(create_gateway_app(gateway, auth)) as gateway_http:
            dispatch = GatewayDispatchClient(gateway_http, auth)
            result = dispatch.dispatch(request)
            assert result.state is ResultState.ACCEPTED
            assert dispatch.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
    assert len(calls) == 1
    return result


def check_model_publication(owner, worker, service, store):
    original = "Can technology shares rebound after disappointing earnings?"
    submitted = service.submit_original(
        DialogueCommand("operator", "idea", "dialogue", 2, 4, "submit-model-turn"),
        turn_id="model-turn", original_id="model-original", text=original,
    )
    assert submitted.idea_version == 2 and submitted.version == 5
    model = registry.ModelBinding("OPENAI", "offline-profile", 1, "offline-model", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    _configure_budget(owner, agent, model)
    with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}):
        _check_bound_model(worker, service, store, agent, model)


def _check_bound_model(worker, service, store, agent, model):
    runs = RunStore(worker)
    run_id = runs.enqueue(
        actor="operator", key="companion-model-run", kind=RunKind.DIALOGUE,
        input_ref="dialogue", trace_id="1" * 32, agent=agent,
    )
    claim = runs.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    runs.transition(claim, RunState.WAITING_FOR_PROCESSING_DECISION)
    binding = ProcessingBinding(
        run_id=run_id, actor="operator", dialogue_id="dialogue", dialogue_version=5,
        turn_id="model-turn", turn_version=1, provider="OPENAI", profile_id=model.profile_id,
        profile_generation=1, model_id=model.model_id, model_generation=1, credential_generation=1,
        policy_generation=1, envelope_version="offline-envelope-v1",
        data_classes=("OPERATOR_RESEARCH_EXCERPT",), step_id="semantic-step",
    )
    projection = (
        b'{"confirmedContext":{},"excerpt":"Can technology shares rebound '
        b'after disappointing earnings?"}'
    )
    vault = ProjectionVault()
    processing = ProcessingStore(worker, vault)
    with pytest.raises(CompanionConflict, match="PROJECTION_NOT_ALLOWED"):
        create_processing_preview(
            store, vault, binding, idea_id="idea", idea_version=2, original_id="model-original",
            session="synthetic-session", projection=projection, permits=lambda _: False,
            deadline=datetime.now(UTC) + timedelta(minutes=1),
        )
    authority = ProcessingAuthority(
        binding, datetime.now(UTC) + timedelta(minutes=1), lambda value: value == projection,
    )
    control = ProcessingProjection(store, processing, resolve_authority=lambda *_: authority)
    session = "synthetic-session"
    target = ("operator", "dialogue", "model-turn")
    preview_args = (target[0], session, *target[1:])
    versions = {"expectedTurnVersion": 1, "expectedDialogueVersion": 5}
    preview_payload = versions | {"profileId": model.profile_id}
    preview_key = "companion-model-preview"
    preview = control.preview(*preview_args, preview_payload, preview_key)
    assert control.preview(*preview_args, preview_payload, preview_key) == preview
    lost = ProcessingProjection(store, processing, resolve_authority=lambda *_: authority)
    with pytest.raises(RunConflict, match="PROCESSING_PREVIEW_UNAVAILABLE"):
        lost.preview(*preview_args, preview_payload, preview_key)
    assert control.status(*target)["state"] == "AWAITING_DECISION"
    consent = versions | {
        "action": "SEND", "previewId": preview["previewId"],
        "projectionBinding": preview["projectionBinding"],
    }
    consent_key = "companion-send-consent"
    wrong_session = (target[0], "different-session", *target[1:])
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        control.decide(*wrong_session, consent, consent_key)
    current = authority
    authority = replace(current, binding=replace(binding, credential_generation=2))
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        control.decide(*preview_args, consent, consent_key)
    authority = current
    receipt = control.decide(*preview_args, consent, consent_key)
    receipt_id = UUID(receipt["receipt"]["receiptId"])
    assert receipt["state"] == "SEND_AUTHORIZED"
    assert lost.decide(*preview_args, consent, consent_key) == receipt
    with pytest.raises(RunConflict, match="IDEMPOTENCY_CONFLICT"):
        control.decide(*preview_args, versions | {"action": "DECLINE"}, consent_key)
    with pytest.raises(RunConflict, match="PROCESSING_PREVIEW_UNAVAILABLE"):
        control.preview(*preview_args, preview_payload, preview_key)

    check_policy = control.check_dispatch
    claim = runs.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    attempts = ModelAttemptStore(worker)
    commitment = preview["projectionBinding"]
    expires_at = datetime.fromisoformat(preview["expiresAt"])
    attempt = attempts.reserve(
        claim, attempt_id=uuid4(), step_id=binding.step_id, request_hash=commitment,
        model=model, prices=TokenPrices(1, "USD", Decimal(1), Decimal(2)),
        tokens=Tokens(4096, 512), context_window=8192, timeout_seconds=20,
    )
    request = DispatchRequest(
        attempt.attempt_id, commitment, sha256(projection).hexdigest(),
        Provider.OPENAI,
        model.model_id, model.profile_id, 1, 1, 1, binding.envelope_version,
        min(expires_at, datetime.now(UTC) + timedelta(seconds=10)),
    )
    profile = Capability(
        request.provider, request.model, request.profile_id, 1, 1, 1, request.envelope_version,
        "Return a structured hypothesis only.", _provider_schema(), 512, 2,
    )
    pending = PendingDispatch(request, claim, receipt_id, binding, profile)
    result = _dispatch(pending, processing, projection, check_policy)
    command = DialogueCommand("operator", "idea", "dialogue", 2, 5, "publish-model")
    arguments = {
        "claim": claim, "receipt_id": receipt_id, "attempt_id": attempt.attempt_id,
        "processing_binding": binding, "proposal_id": "model-proposal", "provider_result": result,
        "original_id": "model-original", "check_policy": check_policy,
        "separate_action": {"tool": "start", "arguments": {}},
    }
    published = publish_model_proposal(service, processing, command, **arguments)
    assert published.version == 6 and published.idea_version == 2
    state = Dialogue.from_record(published.document)
    assert state.proposal["interpretation"] == INTERPRETATION
    assert state.proposal["origin"] == "MODEL_INTERPRETATION"
    assert state.confirmed == {"MARKET": "Technology shares"}
    assert store.read_idea("operator", "idea")["version"] == 2
    accepted = processing.status(receipt_id, actor="operator")
    assert accepted.state == "ACCEPTED" and accepted.result_ref == "model-proposal"
    status = lost.status(*target)
    assert status["state"] == "PROPOSAL_VALIDATED" and status["delivery"] == "SENT"
    assert "projection" not in status and status["dialogueVersion"] == 6
    revoke = versions | {
        "action": "REVOKE", "receiptId": str(receipt_id), "expectedDialogueVersion": 6,
    }
    revoked = control.decide(*preview_args, revoke, "companion-revoke-consent")
    assert revoked["state"] == "REVOKED" and revoked["delivery"] == "SENT"
    assert lost.decide(*preview_args, revoke, "companion-revoke-consent") == revoked
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_REVOKED"):
        publish_model_proposal(service, processing, command, **arguments)
    with worker.connect() as connection:
        event = connection.execute(text("""
            SELECT code FROM agent_research.companion_events
            WHERE dialogue_id='dialogue' AND version=6
        """)).scalar_one()
        assert event == "INTERPRETATION_ACTION_DENIED"
        assert connection.scalar(text("SELECT count(*) FROM agent_research.model_attempts")) == 1
    proposal = state.proposal
    decision = DialogueCommand("operator", "idea", "dialogue", 2, 6, "confirm-model")
    confirmed = service.confirm(
        decision, proposal_id=proposal["proposalId"], proposal_hash=proposal["proposalHash"],
        decision_id="confirmed-model", confirmed_fields={
            "TRIGGER": "Disappointing earnings", "SIGNAL": "Initial negative market reaction",
        }, confirmed_assumption_ids=(),
    )
    assert confirmed.version == 7 and confirmed.idea_version == 3
    idea = store.read_idea("operator", "idea")
    assert idea["hypothesis"]["trigger"] == "Disappointing earnings"
    assert idea["experimentDraftIds"] == [] and idea["testability"] is None
    assert idea["interpretation"]["interpretation"]["requiredDataTypes"] == [
        "EARNINGS_RELEASES", "EARNINGS_EXPECTATIONS",
    ]
    assessment = service.assess_confirmed(
        "operator", "idea", "dialogue", research_mode="RETROSPECTIVE",
        in_scope=True, parameters=(),
    )
    assert assessment.reasons == (Reason.CLARIFICATION_REQUIRED,)
    assert "MEASUREMENT_HORIZON" in assessment.missing_fields
    with pytest.raises(CompanionConflict, match="STALE_IDEA_VERSION"):
        service.revise_idea(
            replace(decision, key="stale-review"), action=IdeaAction.REVIEW,
            decision_id="stale",
        )
    review = DialogueCommand("operator", "idea", "dialogue", 3, 7, "review-idea")
    reviewed = service.revise_idea(review, action=IdeaAction.REVIEW, decision_id="review")
    assert reviewed.version == 8 and reviewed.idea_version == 4
    assert service.revise_idea(review, action=IdeaAction.REVIEW, decision_id="review") == reviewed
    assert store.read_idea("operator", "idea")["status"] == "REVIEWED"

    service.create("operator", "local-idea", "local-dialogue", {
        "title": "Local research", "originalText": "Do prices respond to earnings?",
    }, key="local-idea-create")
    service.submit_original(
        DialogueCommand("operator", "local-idea", "local-dialogue", 1, 1, "local-idea-turn"),
        turn_id="local-only", original_id="local-original",
        text="Does trading volume rise after earnings?",
    )
    decline = versions | {"action": "DECLINE", "expectedDialogueVersion": 2}
    local = ("operator", session, "local-dialogue", "local-only")
    refused = control.decide(*local, decline, "companion-decline-consent")
    assert refused["state"] == "DECLINED" and refused["delivery"] == "NOT_SENT"
    assert lost.status(local[0], *local[2:]) == refused
    assert lost.decide(*local, decline, "companion-decline-consent") == refused
