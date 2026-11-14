"""Bind the supervisor's dialogue dispatch to a durable provider authority."""

import json
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text

from packages.agent_research.budget import TokenPrices, Tokens, reserve_model
from packages.agent_research.discovery.evidence import EvaluationBinding, TriggerPolicy
from packages.agent_research.provider_authority import (
    ProviderAuthority,
    ProviderAuthorityStore,
    ProviderBinding,
    ProviderPurpose,
)
from packages.agent_research.registry import (
    AgentVersion,
    ModelBinding,
    register_provider_agent,
    require_agent,
)
from packages.agent_research.runs import RunConflict
from services.llm_gateway.app import GatewayDispatchClient
from services.llm_gateway.content_policy import safe_result_content, valid_projection
from services.llm_gateway.discovery import ProviderDiscoveryDispatch
from services.llm_gateway.provider_http import (
    Capability,
    production_input_bound,
    provider_request_bytes,
)
from services.llm_gateway.provider_installation import ProviderInstallation
from services.llm_gateway.supervisor_rpc import AdmissionAuthentication, CancellationClient

from .agent_interpretation import ProviderDialogueProfile
from .research_discovery import DiscoveryConfiguration


def load_dialogue_profile(engine, store, installation: ProviderInstallation, key, client):
    binding = installation.binding
    with engine.begin() as scope:
        authority = store.load_active(
            binding.provider, binding.profile_id, ProviderPurpose.DIALOGUE, scope=scope,
        )
        if authority.binding != binding:
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
        prices = current_tariff(scope, binding)
        if prices is None:
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    agent = register_provider_agent(ModelBinding(
        binding.provider, binding.profile_id, binding.profile_generation,
        binding.model_id, binding.model_generation,
    ))
    auth = AdmissionAuthentication(key)
    return bind_dialogue_profile(
        store, authority, installation.capability, agent, prices,
        GatewayDispatchClient(client, auth), CancellationClient(client, auth).cancel,
    ), auth


def load_discovery_profile(engine, store, installation, key, client):
    binding = installation.binding
    options = installation.discovery
    if type(options) is not dict or set(options) != {"instruments", "maxAgeDays", "sources"}:
        raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    if (
        type(options["instruments"]) is not list or not 1 <= len(options["instruments"]) <= 100
        or any(type(item) is not str for item in options["instruments"])
        or type(options["maxAgeDays"]) is not int or not 1 <= options["maxAgeDays"] <= 366
        or type(options["sources"]) is not list or not 1 <= len(options["sources"]) <= 100
    ):
        raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    sources = {}
    for entry in options["sources"]:
        if type(entry) is not dict or set(entry) != {"sourceId", "digest", "market", "quotes"}:
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
        digest, market, quotes = entry["digest"], entry["market"], entry["quotes"]
        source_id = entry["sourceId"]
        try:
            if type(source_id) is not str or str(UUID(source_id)) != source_id:
                raise ValueError
        except ValueError:
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE") from None
        if (
            type(digest) is not str or len(digest) != 64 or (source_id, digest) in sources
            or any(char not in "0123456789abcdef" for char in digest)
            or type(market) is not str or not 1 <= len(market) <= 80
            or type(quotes) is not list or not 1 <= len(quotes) <= 32
            or any(type(quote) is not str or not 1 <= len(quote) <= 2048 for quote in quotes)
            or not safe_result_content([market, *quotes])
        ):
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
        sources[source_id, digest] = (market, tuple(quotes))
    policy_hash = sha256(json.dumps(
        options, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    if binding.discovery_policy_sha256 != policy_hash:
        raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    with engine.begin() as scope:
        authority = store.load_active(
            binding.provider, binding.profile_id, ProviderPurpose.DISCOVERY, scope=scope,
        )
        prices = current_tariff(scope, binding)
        if authority.binding != binding or prices is None:
            raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    model = ModelBinding(
        binding.provider, binding.profile_id, binding.profile_generation,
        binding.model_id, binding.model_generation,
    )
    agent = register_provider_agent(model)
    revision = f"policy-{binding.policy_generation}"
    rights = f"rights-{binding.rights_generation}"
    config = DiscoveryConfiguration(
        agent=agent,
        trigger=TriggerPolicy(
            revision, rights, frozenset(options["instruments"]),
            timedelta(days=options["maxAgeDays"]),
        ),
        evaluation=EvaluationBinding(
            revision, rights, agent.agent_id, agent.version, binding.prompt_sha256,
            binding.provider, binding.profile_id, binding.profile_generation,
            binding.model_id, binding.model_generation, binding.schema_sha256,
        ),
        prices=prices, tokens=Tokens(binding.max_input_tokens, binding.max_output_tokens),
        context_window=binding.max_input_tokens + binding.max_output_tokens,
        policy_version=binding.policy_generation,
    )
    auth = AdmissionAuthentication(key)
    return (
        ProviderDiscoveryDispatch(GatewayDispatchClient(client, auth), installation.capability),
        bind_discovery_authority(store, authority, config),
        sources,
        auth,
    )


def bind_discovery_authority(
    store: ProviderAuthorityStore, authority: ProviderAuthority, config: DiscoveryConfiguration,
) -> DiscoveryConfiguration:
    """Bind scheduled work to its own enabled purpose and immutable profile epoch."""
    binding = authority.binding
    if not binding.data_classes or not set(binding.data_classes) <= {
        "HISTORICAL_REVISION", "PERSISTED_RESEARCH_RESULT",
    } or config.agent.model_binding != ModelBinding(
        binding.provider, binding.profile_id, binding.profile_generation,
        binding.model_id, binding.model_generation,
    ) or (
        config.prices.version != binding.tariff_revision
        or not config.prices.bounds_unconfirmed_cost
        or config.tokens != Tokens(binding.max_input_tokens, binding.max_output_tokens)
        or reserve_model(config.prices, config.tokens, config.context_window).bound.cost
        > binding.max_cost
    ):
        raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")

    def current(scope):
        try:
            store.require(binding, authority.epoch, ProviderPurpose.DISCOVERY, scope=scope)
        except RunConflict:
            return False
        return current_tariff(scope, binding) == config.prices

    return replace(
        config, provider_authority=current, evidence_revision=binding.evidence_revision,
        data_classes=frozenset(binding.data_classes), max_request_bytes=binding.max_request_bytes,
    )


def current_tariff(scope, binding: ProviderBinding) -> TokenPrices | None:
    """Read the current tariff using the same immutable identity as the ledger."""
    model = ModelBinding(
        binding.provider, binding.profile_id, binding.profile_generation,
        binding.model_id, binding.model_generation,
    )
    row = scope.execute(text("""
        SELECT version,currency,input_price,output_price,bounds_unconfirmed_cost
        FROM agent_research.model_prices
        WHERE model_binding=CAST(:model AS jsonb) FOR SHARE
    """), {"model": json.dumps(asdict(model))}).one_or_none()
    if row is None or row.version != binding.tariff_revision or not row.bounds_unconfirmed_cost:
        return None
    return TokenPrices(*row)


def check_persisted_evidence(scope, binding: ProviderBinding) -> bool:
    """Require the reviewed binding digest to match the current evidence record."""
    row = scope.execute(text("""
        SELECT revision,binding_digest,valid_until
        FROM agent_research.provider_cost_evidence
        WHERE provider=:provider AND profile_id=:profile
    """), {"provider": binding.provider, "profile": binding.profile_id}).one_or_none()
    if row is None or tuple(row) != (
        binding.evidence_revision, binding.digest, binding.evidence_expires_at,
    ):
        return False
    prices = current_tariff(scope, binding)
    if prices is None:
        return False
    tokens = Tokens(binding.max_input_tokens, binding.max_output_tokens)
    reservation = reserve_model(prices, tokens, tokens.input + tokens.output)
    return reservation.bound.cost <= binding.max_cost


def bind_dialogue_profile(
    store: ProviderAuthorityStore, authority: ProviderAuthority, capability: Capability,
    agent: AgentVersion, prices: TokenPrices, gateway: GatewayDispatchClient, cancel,
) -> ProviderDialogueProfile:
    binding = authority.binding
    model = ModelBinding(
        binding.provider, binding.profile_id, binding.profile_generation,
        binding.model_id, binding.model_generation,
    )
    if (
        agent.model_binding != model
        or require_agent(agent.agent_id, agent.version, agent.policy_version) != agent
        or not prices.bounds_unconfirmed_cost or prices.version != binding.tariff_revision
        or binding.response_model_id != binding.model_id
        or binding.api_version != ("2026-10-21" if binding.provider == "ANTHROPIC" else "v1")
        or not {
            "OPERATOR_RESEARCH_EXCERPT", "CONFIRMED_RESEARCH_FIELDS",
        }.issuperset(binding.data_classes)
        or "OPERATOR_RESEARCH_EXCERPT" not in binding.data_classes
        or (
            capability.provider.value, capability.profile_id, capability.profile_generation,
            capability.model, capability.model_generation, capability.credential_generation,
            capability.envelope_version, capability.max_output_tokens, capability.timeout_seconds,
        ) != (
            model.provider, model.profile_id, model.profile_generation, model.model_id,
            model.model_generation, binding.credential_generation, binding.envelope_version,
            binding.max_output_tokens, binding.timeout_seconds,
        )
        or sha256(capability.instructions.encode()).hexdigest() != binding.prompt_sha256
        or sha256(capability.schema_json.encode()).hexdigest() != binding.schema_sha256
        or not 1 <= capability.timeout_seconds <= 15
        or binding.max_response_bytes != 262144
    ):
        raise RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")
    tokens = Tokens(binding.max_input_tokens, binding.max_output_tokens)
    window = tokens.input + tokens.output
    if reserve_model(prices, tokens, window).bound.cost > binding.max_cost:
        raise RunConflict("BUDGET_EXHAUSTED")

    def current(scope, processing):
        if (
            processing.policy_generation != authority.epoch
            or processing.tariff_revision != binding.tariff_revision
            or processing.evidence_revision != binding.evidence_revision
        ):
            return False
        store.require(binding, authority.epoch, ProviderPurpose.DIALOGUE, scope=scope)
        return True

    def permits(projection):
        if not valid_projection(projection):
            return False
        wire = provider_request_bytes(capability, projection)
        return (
            len(wire) <= binding.max_request_bytes
            and production_input_bound(wire) <= tokens.input
        )

    return ProviderDialogueProfile(
        agent, capability, authority.epoch, current, permits, prices, tokens, window,
        gateway, cancel, binding.evidence_revision, binding.tariff_revision,
        data_classes=binding.data_classes,
    )
