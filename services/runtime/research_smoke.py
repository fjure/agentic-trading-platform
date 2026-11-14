"""Dispatch a persisted smoke grant through the signed production gateway."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from packages.agent_research.budget import ModelOutcome, Tokens
from packages.agent_research.runs import RunState
from services.llm_gateway.offline_gateway import AuthorizedDispatch, DispatchRequest
from services.llm_gateway.probe import PROBE_PROJECTION
from services.llm_gateway.provider_wire import ResultState

from .agent_research import StepResult


class ProductionSmoke:
    def __init__(self, authority, gateway, capability, model):
        self.authority = authority
        self.gateway = gateway
        self.capability = capability
        self.model = model
        self.pending = None

    def consume(self, request):
        pending = self.pending
        if pending is None or pending[0] != request:
            return None
        _, grant, claim = pending
        self.authority.authorize(grant, claim, request.request_hash)
        return AuthorizedDispatch(request, self.capability, PROBE_PROJECTION)

    def step(self, claim, work, recovered):
        if recovered:
            return StepResult(RunState.BLOCKED, "PROVIDER_OUTCOME_UNKNOWN")
        grant = self.authority.lookup(UUID(work.input_ref))
        if grant is None or grant.expires_at <= datetime.now(UTC):
            return StepResult(RunState.BLOCKED, "PROVIDER_UNAVAILABLE")
        started = datetime.now(UTC)
        attempt = self.authority.reserve(grant, claim, self.model)
        if attempt.status != "RESERVED":
            return StepResult(RunState.BLOCKED, "PROVIDER_OUTCOME_UNKNOWN")
        capability = self.capability
        request = DispatchRequest(
            grant.grant_id,
            grant.commitment,
            sha256(PROBE_PROJECTION).hexdigest(),
            capability.provider,
            capability.model,
            capability.profile_id,
            capability.profile_generation,
            capability.model_generation,
            capability.credential_generation,
            capability.envelope_version,
            min(
                grant.expires_at,
                work.dispatch_deadline,
                started + timedelta(seconds=capability.timeout_seconds),
            ),
            purpose="PROBE",
        )
        self.pending = (request, grant, claim)
        try:
            response = self.gateway.dispatch(request)
        finally:
            self.pending = None
        usage = response.usage
        if usage is None:
            self.authority.settle(grant, claim, ModelOutcome.OUTCOME_UNKNOWN)
            return StepResult(RunState.BLOCKED, "PROVIDER_OUTCOME_UNKNOWN")
        tokens = Tokens(usage.input_tokens, usage.output_tokens)
        success = response.state is ResultState.ACCEPTED and response.output == {"ok": True}
        attempt = self.authority.settle(
            grant,
            claim,
            ModelOutcome.COMPLETED if success else ModelOutcome.RESULT_UNAVAILABLE,
            reported_tokens=tokens,
        )
        if attempt.status != "COMPLETED":
            return StepResult(RunState.BLOCKED, "PROVIDER_UNAVAILABLE")
        return StepResult(RunState.COMPLETED, grant.grant_id.hex)


def configured_smoke(engine, store, installation, gateway):
    """Load the staged installation and re-read its explicit policy at each fence."""
    from pathlib import Path

    from sqlalchemy import text

    from packages.agent_research.registry import ModelBinding, register_provider_agent
    from services.llm_gateway.probe import production_probe_capability
    from services.llm_gateway.smoke_budget import load_smoke_budget

    from .provider_profiles import current_tariff
    from .smoke_authority import SmokeAuthority, SmokePlan

    binding = installation.binding
    capability = production_probe_capability(
        installation.capability, installation.probe_output_tokens
    )
    with engine.begin() as scope:
        epoch = scope.execute(
            text("""
            SELECT epoch FROM agent_research.provider_authority
            WHERE provider=:provider AND profile_id=:profile AND state='STAGED'
        """),
            {"provider": binding.provider, "profile": binding.profile_id},
        ).scalar_one()

    def plan(scope):
        from services.llm_gateway.supervisor_rpc import _decode

        path = Path("/run/config/research-smoke.json")
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536 or path.is_symlink():
            raise ValueError("PROVIDER_SMOKE_POLICY_INVALID")
        budget = load_smoke_budget(_decode(raw))
        quote = budget.quote(capability, PROBE_PROJECTION, datetime.now(UTC))
        if (
            quote.ledger_prices != current_tariff(scope, binding)
            or budget.evidence.revision != binding.evidence_revision
            or budget.evidence.capability_generation != binding.capability_generation
            or budget.policy_revision != binding.policy_generation
            or quote.wire_size > binding.max_request_bytes
            or quote.max_cost > binding.max_cost
        ):
            raise ValueError("PROVIDER_SMOKE_POLICY_INVALID")
        return SmokePlan(
            quote.commitment,
            quote.wire_sha256,
            quote.ledger_prices,
            quote.tokens,
            quote.max_cost,
            budget.limit.amount,
            int(capability.timeout_seconds),
            quote.expires_at,
        )

    model = ModelBinding(
        binding.provider,
        binding.profile_id,
        binding.profile_generation,
        binding.model_id,
        binding.model_generation,
    )
    authority = SmokeAuthority(engine, store, binding=binding, epoch=epoch, plan=plan)
    return ProductionSmoke(authority, gateway, capability, model), register_provider_agent(model)
