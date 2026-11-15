"""Redacted browser controls backed by the durable provider smoke authority."""

from hmac import compare_digest, digest
from uuid import UUID

from sqlalchemy import text

from packages.agent_research.budget import BudgetDenied
from packages.agent_research.runs import RunConflict
from packages.domain.models import utc_string
from services.control_api.agent_research_routes import AgentResearchError

from .provider_profiles import check_persisted_evidence


class ProductionProviderControls:
    """Project only the installed profile and authorize its fixed synthetic probe."""

    def __init__(self, engine, *, capability, binding, smoke_authority=None, dialogue=False, key):
        self._engine = engine
        self._authority = smoke_authority
        self._capability = capability
        self._configured = binding
        self._key = key
        self._dialogue = dialogue

    @property
    def routes(self):
        routes = {"listAgentProviders": self._list}
        if self._authority is not None:
            routes.update(
                {
                    "prepareAgentProviderSmoke": self._preview,
                    "probeAgentProvider": self._approve,
                }
            )
        return routes

    def _binding(self, params, payload, actor):
        if actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        binding = self._configured
        if params["provider"] != binding.provider:
            raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
        expected = {
            "profileId": binding.profile_id,
            "profileGeneration": binding.profile_generation,
            "modelGeneration": binding.model_generation,
            "credentialGeneration": binding.credential_generation,
        }
        if any(payload.get(name) != value for name, value in expected.items()):
            raise AgentResearchError(409, "VERSION_CONFLICT")
        return expected

    def _preview(self, params, query, payload, *, actor, key, trace, scope):
        self._binding(params, payload, actor)
        if {"grantId", "commitment"} & payload.keys():
            raise AgentResearchError(422, "INVALID_REQUEST")
        identity = UUID(
            bytes=digest(
                self._key,
                f"provider-smoke-preview:{actor}:{key}".encode(),
                "sha256",
            )[:16],
            version=4,
        )
        try:
            grant = self._authority.preview(actor, identity)
        except (BudgetDenied, RunConflict, OSError, ValueError) as error:
            raise _problem(error) from None
        view = {key: value for key, value in grant.public().items() if value is not None}
        view["maximumCost"] = format(grant.maximum_cost, "f")
        view["operatorLimit"] = format(grant.operator_limit, "f")
        # Format canonically for the public UtcTimestamp schema.
        view["expiresAt"] = utc_string(grant.expires_at)
        # The browser must confirm the commitment returned to its authenticated session.
        view["commitment"] = self._commitment(grant, scope)
        return 200, view

    def _commitment(self, grant, scope):
        return digest(
            self._key,
            f"provider-smoke-consent:{scope}:{grant.commitment}".encode(),
            "sha256",
        ).hex()

    def _approve(self, params, query, payload, *, actor, key, trace, scope):
        expected = self._binding(params, payload, actor)
        if not {"grantId", "commitment"} <= payload.keys():
            raise AgentResearchError(409, "PROCESSING_GRANT_REQUIRED")
        try:
            grant = self._authority.lookup(UUID(payload["grantId"]))
            if grant is None or not compare_digest(
                payload["commitment"],
                self._commitment(grant, scope),
            ):
                raise AgentResearchError(409, "PROCESSING_GRANT_INVALID")
            run = self._authority.approve(
                grant,
                actor,
                grant.commitment,
                key=key,
                trace_id=trace,
                agent=self._authority.agent,
            )
        except (BudgetDenied, RunConflict, OSError, ValueError) as error:
            raise _problem(error) from None
        with self._engine.connect() as connection:
            accepted_at = connection.execute(
                text("""
                SELECT created_at FROM agent_research.runs WHERE run_id=:run AND actor=:actor
            """),
                {"run": run, "actor": actor},
            ).scalar_one()
        return 202, expected | {
            "probeId": grant.grant_id.hex,
            "provider": self._configured.provider,
            "status": "QUEUED",
            "acceptedAt": utc_string(accepted_at),
        }

    def _list(self, params, query, payload, *, actor, key, trace, scope):
        if actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        if query.get("cursor") is not None:
            raise AgentResearchError(422, "INVALID_REQUEST")
        binding, capability = self._configured, self._capability
        with self._engine.connect() as connection:
            row = connection.execute(
                text("""
                SELECT c.version,c.generation,c.blocked,c.digest IS NOT NULL AS stored,c.updated_at,
                  a.state, a.dialogue, b.digest=:digest AS current,
                  (c.version,c.generation,c.digest)=(:version,:generation,:credential) AS exact,
                  p.grant_id,p.run_state,p.attempt_state,p.exceeded_bound,p.consumed,
                  p.expires_at <= clock_timestamp() AS expired,
                  a.smoke_attempt_id, (e.valid_until=:valid_until
                    AND e.valid_until > clock_timestamp()) AS evidence_current
                FROM agent_research.provider_credential_authority c
                LEFT JOIN agent_research.provider_authority a
                  ON a.provider=c.provider AND a.profile_id=:profile
                LEFT JOIN agent_research.provider_bindings b
                  ON (b.provider,b.profile_id,b.generation)=(a.provider,a.profile_id,a.generation)
                LEFT JOIN agent_research.provider_cost_evidence e
                  ON e.provider=c.provider AND e.profile_id=:profile AND e.binding_digest=:digest
                  AND e.revision=:evidence
                LEFT JOIN LATERAL (
                    SELECT g.grant_id,g.consumed,g.expires_at,r.state AS run_state,
                           m.status AS attempt_state,m.exceeded_bound
                    FROM agent_research.provider_smoke_grants g
                    JOIN agent_research.runs r ON r.run_id=g.run_id
                    LEFT JOIN agent_research.model_attempts m ON m.attempt_id=g.grant_id
                    WHERE g.binding_digest=:digest AND g.actor=:actor
                      AND (g.epoch=a.epoch OR a.smoke_attempt_id=g.grant_id)
                    ORDER BY (g.grant_id=a.smoke_attempt_id) DESC NULLS LAST,
                             r.created_at DESC,r.run_id DESC
                    LIMIT 1
                ) p ON true
                WHERE c.provider=:provider
            """),
                {
                    "provider": binding.provider,
                    "profile": binding.profile_id,
                    "digest": binding.digest,
                    "actor": actor,
                    "evidence": binding.evidence_revision,
                    "valid_until": binding.evidence_expires_at,
                    "version": binding.credential_version,
                    "generation": binding.credential_generation,
                    "credential": binding.credential_digest,
                },
            ).one()
            policy_current = bool(
                row.evidence_current and check_persisted_evidence(connection, binding)
            )
        credential_state = _credential_state(row, policy_current)
        credential = {
            "credentialVersion": row.version,
            "state": credential_state,
            "providerRevocationState": "NOT_CONFIRMED",
            "updatedAt": utc_string(row.updated_at),
        }
        if row.generation is not None:
            credential["credentialGeneration"] = row.generation
        result = {
            "binding": {
                "provider": binding.provider,
                "profileId": binding.profile_id,
                "profileGeneration": binding.profile_generation,
                "modelId": binding.model_id,
                "modelGeneration": binding.model_generation,
            },
            "credential": credential,
            "availability": _availability(row, credential_state, self._dialogue),
            "adapter": {"OPENAI": "OPENAI_RESPONSES", "ANTHROPIC": "ANTHROPIC_MESSAGES"}[
                binding.provider
            ],
            "outputSchemaVersion": 1,
            "schemaSubsetVersion": 1,
            "structuredOutputSupported": True,
            "maxInputTokens": min(1000000, binding.max_input_tokens),
            "maxOutputTokens": capability.max_output_tokens,
            "timeoutSeconds": int(capability.timeout_seconds),
            "cancelSupport": "BEST_EFFORT",
            "rateLimitSupport": "NONE",
            "dataHandlingPolicyId": "production-provider",
            "dataHandlingPolicyVersion": binding.policy_generation,
            "pricingVersion": binding.tariff_revision,
            "probeState": _probe_state(row, policy_current),
        }
        if row.grant_id is not None:
            result["lastProbeId"] = row.grant_id.hex
        return 200, {"items": [result]}


def _problem(error):
    code = getattr(error, "code", None)
    if code in {"PROCESSING_GRANT_INVALID", "IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT"}:
        return AgentResearchError(409, code)
    if code in {"BUDGET_EXHAUSTED", "BUDGET_POLICY_UNAVAILABLE"}:
        return AgentResearchError(429, "BUDGET_EXHAUSTED")
    return AgentResearchError(503, "PROVIDER_UNAVAILABLE")


def _credential_state(row, policy_current):
    if row.generation is None:
        return "NOT_CONFIGURED"
    if not row.stored:
        return "REVOKED"
    if not row.blocked and row.current and row.exact and policy_current:
        if row.state in {"VERIFIED", "ACTIVE"}:
            return row.state
    return "STORED"


def _availability(row, credential_state, dialogue):
    if credential_state in {"NOT_CONFIGURED", "REVOKED"}:
        return {"NOT_CONFIGURED": "DISABLED", "REVOKED": "REVOKED"}[credential_state]
    return "AVAILABLE" if credential_state == "ACTIVE" and row.dialogue and dialogue else "BLOCKED"


def _probe_state(row, policy_current):
    if row.grant_id is None:
        return "NOT_TESTED"
    if row.attempt_state == "OUTCOME_UNKNOWN":
        return "OUTCOME_UNKNOWN"
    if row.run_state in {"QUEUED", "RUNNING"}:
        if row.attempt_state == "DISPATCH_AUTHORIZED":
            return "OUTCOME_UNKNOWN" if row.expired or row.blocked else "IN_FLIGHT"
        return "REJECTED_BEFORE_EXECUTION" if row.expired else "QUEUED"
    if row.attempt_state in {None, "UNSENT", "RESERVED"}:
        return "REJECTED_BEFORE_EXECUTION"
    if (
        row.current
        and row.exact
        and policy_current
        and not row.blocked
        and row.state not in {"REVOKED", "DISABLED"}
        and row.run_state == "COMPLETED"
    ):
        if row.consumed and row.attempt_state == "COMPLETED" and not row.exceeded_bound:
            return "VERIFIED"
    return "FAILED"
