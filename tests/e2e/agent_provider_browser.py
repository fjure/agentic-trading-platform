"""Synthetic-only production gateway dependencies for the integrated browser."""

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from threading import Event, Thread
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text

from packages.agent_research.budget import ModelOutcome, TokenPrices, Tokens
from packages.agent_research.model_attempts import ModelAttemptStore, production_model_budget_scopes
from packages.agent_research.provider_authority import (
    ProviderAuthorityStore,
    ProviderBinding,
    ProviderPurpose,
    authority_hash,
)
from packages.agent_research.runs import RunKind, RunState, RunStore
from services.llm_gateway import app as gateway_app
from services.llm_gateway import provider_http
from services.llm_gateway.app import GatewayDispatchClient
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.provider_wire import Provider
from services.llm_gateway.supervisor_rpc import AdmissionAuthentication, CancellationClient
from services.runtime.egress import ConnectProxy, MockRoute
from services.runtime.provider_profiles import bind_dialogue_profile, check_persisted_evidence
from services.setup.llm_credentials import CredentialBinding, CredentialDenied
from tests.unit.test_llm_tls_socket import _contexts

HOST = "api.openai.com"
CREDENTIAL = "synthetic-browser-provider-credential"


def start_synthetic_egress(stack, tls_address):
    ready = Event()
    state = {}

    async def run():
        route = MockRoute(HOST, "127.0.0.1", tls_address)
        proxy = ConnectProxy("research-gateway", mock_route=route)
        async with await asyncio.start_server(proxy.handle, "127.0.0.1", 0) as server:
            state.update(
                loop=asyncio.get_running_loop(), stop=asyncio.Event(),
                port=server.sockets[0].getsockname()[1],
            )
            ready.set()
            await state["stop"].wait()

    thread = Thread(target=lambda: asyncio.run(run()), daemon=True)
    thread.start()
    assert ready.wait(5)

    def stop():
        state["loop"].call_soon_threadsafe(state["stop"].set)
        thread.join(timeout=5)
        assert not thread.is_alive()

    stack.callback(stop)
    stack.enter_context(patch.object(
        provider_http, "_PROVIDER_PROXY", f"http://127.0.0.1:{state['port']}",
    ))


def bind_dialogue(stack, owner, research, agent, capability, directory, serve, client, key):
    model = agent.model_binding
    prices = TokenPrices(1, "USD", Decimal(1), Decimal(2), True)
    tokens = Tokens(4096, capability.max_output_tokens)
    binding = ProviderBinding(
        profile_id=capability.profile_id, profile_generation=1, provider="OPENAI",
        model_id=capability.model, response_model_id=capability.model, model_generation=1,
        capability_generation=1, credential_generation=1, credential_version=2,
        credential_digest="a" * 64, api_version="v1", rights_generation=1,
        policy_generation=1, prompt_sha256=sha256(capability.instructions.encode()).hexdigest(),
        schema_sha256=sha256(capability.schema_json.encode()).hexdigest(),
        evidence_revision=1, tariff_revision=1,
        evidence_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        data_classes=("CONFIRMED_RESEARCH_FIELDS", "OPERATOR_RESEARCH_EXCERPT"),
        envelope_version=capability.envelope_version,
        max_request_bytes=65536, max_response_bytes=262144,
        timeout_seconds=int(capability.timeout_seconds), max_input_tokens=tokens.input,
        max_output_tokens=tokens.output, max_cost=Decimal("0.10"),
    )
    scopes = set()
    for kind in (RunKind.DIALOGUE, RunKind.PROVIDER_PROBE):
        scopes.update(production_model_budget_scopes(
            agent.agent_id, agent.version, kind, model, "fixture",
        ))
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.model_budget_policies
              (scope,version,currency,max_cost,max_input_tokens,max_output_tokens,max_calls,
               enabled,max_parallelism)
            VALUES (:scope,1,'USD',0.10,65536,8192,16,true,1)
            ON CONFLICT (scope) DO UPDATE SET version=model_budget_policies.version+1,
                max_calls=EXCLUDED.max_calls, max_input_tokens=EXCLUDED.max_input_tokens,
                max_output_tokens=EXCLUDED.max_output_tokens
        """), [{"scope": scope} for scope in sorted(scopes)])
        connection.execute(text("""
            INSERT INTO agent_research.model_prices
              (model_binding,version,currency,input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:model AS jsonb),1,'USD',1,2,true)
        """), {"model": json.dumps(asdict(model))})
        connection.execute(text("""
            INSERT INTO agent_research.provider_cost_evidence VALUES
            ('OPENAI',:profile,1,:digest,:expiry)
        """), {
            "profile": binding.profile_id, "digest": binding.digest,
            "expiry": binding.evidence_expires_at,
        })
    store = ProviderAuthorityStore(research, check_evidence=check_persisted_evidence)
    staged = store.stage(binding, 0)
    # The dialogue test starts from an explicitly enabled profile. A separate
    # release-gate case performs the full authorized synthetic smoke.
    runs, attempts = RunStore(research), ModelAttemptStore(research)
    runs.enqueue(
        actor="local-operator", key="synthetic-browser-dialogue-smoke", kind=RunKind.PROVIDER_PROBE,
        input_ref="fixture", trace_id="a" * 32, agent=agent,
    )
    lease = runs.claim(uuid4())
    assert lease is not None
    proof = {
        "evidence_revision": 1,
        "authority_hash": authority_hash(binding, staged.epoch, ProviderPurpose.PROBE),
    }
    attempt_id = uuid4()
    attempts.reserve(
        lease, attempt_id=attempt_id, step_id="fixture-smoke", request_hash="b" * 64,
        model=model, prices=prices, tokens=Tokens(100, 100),
        context_window=1000, timeout_seconds=10, **proof,
    )
    with research.begin() as connection:
        attempts.authorize_in_transaction(connection, lease, attempt_id, "b" * 64, **proof)
    attempts.settle(
        lease, attempt_id, ModelOutcome.COMPLETED, result_ref="fixture-smoke",
        reported_tokens=Tokens(1, 1), confirmed_cost=Decimal("0.000003"), **proof,
    )
    runs.transition(lease, RunState.COMPLETED, checkpoint_ref="fixture-smoke")
    verified = store.mark_verified(binding, staged.epoch, attempt_id)
    active = store.set_purpose(binding, verified.epoch, ProviderPurpose.DIALOGUE, True)

    class FixtureCredential:
        def read_active(self, current):
            if current != CredentialBinding(Provider.OPENAI, 1, capability.profile_id, 1, 1):
                raise CredentialDenied("CREDENTIAL_UNAVAILABLE")
            return CREDENTIAL

    auth = AdmissionAuthentication(key)
    journal = directory / "dialogue-dispatch.sqlite"
    DispatchJournal.provision(journal)
    stack.enter_context(patch.object(gateway_app, "_SUPERVISOR_ORIGIN", "http://127.0.0.1:8013"))
    stack.enter_context(serve(gateway_app.create_provider_gateway_app(
        journal_path=journal, auth=auth, credentials=FixtureCredential(),
        capabilities=(capability,),
    ), 8015))
    profile = bind_dialogue_profile(
        store, active, capability, agent, prices,
        GatewayDispatchClient(client, auth), CancellationClient(client, auth).cancel,
    )
    return profile, auth, binding


def fixture_tls(directory):
    return _contexts(directory, HOST)
