"""One-use operator consent and transactional production smoke ledger authority."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text

from packages.agent_research.budget import (
    BudgetDenied,
    ModelOutcome,
    TokenPrices,
    Tokens,
    reserve_model,
)
from packages.agent_research.model_attempts import Attempt, ModelAttemptStore
from packages.agent_research.provider_authority import (
    ProviderAuthorityStore,
    ProviderBinding,
    ProviderPurpose,
    authority_hash,
)
from packages.agent_research.registry import AgentVersion, ModelBinding
from packages.agent_research.runs import Claim, RunConflict, RunKind, RunStore

_STEP = "provider-smoke-v1"


def _deny():
    return RunConflict("PROCESSING_GRANT_INVALID")


@dataclass(frozen=True, slots=True)
class SmokePlan:
    policy_sha256: str
    wire_sha256: str
    prices: TokenPrices
    tokens: Tokens
    maximum_cost: Decimal
    operator_limit: Decimal
    timeout_seconds: int
    expires_at: datetime | None = None
    input_bound_kind: str | None = None

    def check_reservation(self):
        reserved = reserve_model(self.prices, self.tokens, self.tokens.input + self.tokens.output)
        if (
            not self.prices.bounds_unconfirmed_cost or reserved.bound.cost != self.maximum_cost
            or self.operator_limit < self.maximum_cost
            or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 15
        ):
            raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class SmokeGrant:
    grant_id: UUID
    actor: str
    binding_digest: str
    epoch: int
    policy_sha256: str
    wire_sha256: str
    commitment: str
    maximum_cost: Decimal
    operator_limit: Decimal
    tariff_revision: int
    currency: str
    input_bound_kind: str | None
    expires_at: datetime
    run_id: UUID | None = None
    consumed: bool = False

    def public(self):
        return {
            "grantId": str(self.grant_id), "commitment": self.commitment,
            "maximumCost": str(self.maximum_cost), "operatorLimit": str(self.operator_limit),
            "expiresAt": self.expires_at.isoformat(), "tariffRevision": self.tariff_revision,
            "currency": self.currency, "inputBoundKind": self.input_bound_kind,
            "blockedReason": (
                "BUDGET_EXHAUSTED" if self.maximum_cost > self.operator_limit else None
            ),
        }


class SmokeAuthority:
    def __init__(
        self, engine, provider_store: ProviderAuthorityStore, *, binding: ProviderBinding,
        epoch: int, plan: Callable[..., SmokePlan],
    ):
        self._engine = engine
        self._provider_store = provider_store
        self.binding = binding
        self.epoch = epoch
        self._plan = plan
        self._attempts = ModelAttemptStore(engine)

    @property
    def ledger_binding(self):
        return {
            "evidence_revision": self.binding.evidence_revision,
            "authority_hash": authority_hash(self.binding, self.epoch, ProviderPurpose.PROBE),
        }

    def _current(self, scope):
        self._provider_store.require(self.binding, self.epoch, ProviderPurpose.PROBE, scope=scope)
        return self._plan(scope)

    def preview(self, actor: str, grant_id: UUID) -> SmokeGrant:
        with self._engine.begin() as scope:
            plan = self._current(scope)
            expiry = min(
                self.binding.evidence_expires_at, datetime.now(UTC) + timedelta(minutes=5),
                plan.expires_at or self.binding.evidence_expires_at,
            )
            if expiry <= datetime.now(UTC):
                raise _deny()
            digest = sha256(json.dumps([
                _STEP, str(grant_id), actor, self.binding.digest, self.epoch, plan.policy_sha256,
                plan.wire_sha256, str(plan.maximum_cost), str(plan.operator_limit),
                expiry.isoformat(),
            ], separators=(",", ":")).encode()).hexdigest()
            grant = SmokeGrant(
                grant_id, actor, self.binding.digest, self.epoch, plan.policy_sha256,
                plan.wire_sha256, digest, plan.maximum_cost, plan.operator_limit,
                plan.prices.version, plan.prices.currency, plan.input_bound_kind, expiry,
            )
            scope.execute(text("""
                INSERT INTO agent_research.provider_smoke_grants
                  (grant_id,actor,binding_digest,epoch,policy_sha256,wire_sha256,commitment,
                   maximum_cost,operator_limit,tariff_revision,currency,input_bound_kind,expires_at)
                VALUES (:grant_id,:actor,:binding_digest,:epoch,:policy_sha256,:wire_sha256,
                   :commitment,:maximum_cost,:operator_limit,:tariff_revision,:currency,
                   :input_bound_kind,:expires_at)
                ON CONFLICT (grant_id) DO NOTHING
            """), asdict(grant))
            actual = self._load(scope, grant_id)
            if actual is None or actual.actor != actor:
                raise _deny()
            self._matches_plan(actual, plan)
            return actual

    @staticmethod
    def _load(scope, grant_id, *, lock=False):
        lock_clause = " FOR UPDATE" if lock else ""
        row = scope.execute(text("""
            SELECT * FROM agent_research.provider_smoke_grants WHERE grant_id=:id
        """ + lock_clause), {"id": grant_id}).mappings().one_or_none()
        return SmokeGrant(**row) if row is not None else None

    def lookup(self, grant_id: UUID) -> SmokeGrant | None:
        with self._engine.connect() as scope:
            return self._load(scope, grant_id)

    def _matches_plan(self, grant, plan):
        if (
            grant.expires_at <= datetime.now(UTC)
            or (plan.expires_at is not None and grant.expires_at > plan.expires_at)
            or (grant.binding_digest, grant.epoch) != (self.binding.digest, self.epoch)
            or (grant.policy_sha256, grant.wire_sha256, grant.maximum_cost, grant.operator_limit,
                grant.tariff_revision, grant.currency, grant.input_bound_kind)
            != (plan.policy_sha256, plan.wire_sha256, plan.maximum_cost, plan.operator_limit,
                plan.prices.version, plan.prices.currency, plan.input_bound_kind)
        ):
            raise _deny()

    def approve(
        self, grant: SmokeGrant, actor: str, commitment: str, *, key: str,
        trace_id: str, agent: AgentVersion,
    ) -> UUID:
        with self._engine.begin() as scope:
            plan = self._current(scope)
            plan.check_reservation()
            actual = self._load(scope, grant.grant_id, lock=True)
            if actual is None or replace(actual, run_id=grant.run_id, consumed=grant.consumed) != (
                grant
            ) or (actor, commitment) != (actual.actor, actual.commitment):
                raise _deny()
            self._matches_plan(actual, plan)
            if actual.run_id is not None:
                return actual.run_id
            run = RunStore.enqueue_in_transaction(
                scope, actor=actor, key=key, kind=RunKind.PROVIDER_PROBE,
                input_ref=grant.grant_id.hex, trace_id=trace_id, agent=agent,
            )
            scope.execute(text("""
                UPDATE agent_research.provider_smoke_grants SET run_id=:run WHERE grant_id=:id
            """), {"run": run, "id": grant.grant_id})
            return run

    def reserve(self, grant: SmokeGrant, claim: Claim, model: ModelBinding) -> Attempt:
        with self._engine.begin() as scope:
            plan = self._current(scope)
            plan.check_reservation()
            self._matches_plan(grant, plan)
            if grant.run_id != claim.run_id or grant.consumed or self._load(
                scope, grant.grant_id, lock=True,
            ) != grant:
                raise _deny()
        return self._attempts.reserve(
            claim, attempt_id=grant.grant_id, step_id=_STEP, request_hash=grant.commitment,
            model=model, prices=plan.prices, tokens=plan.tokens,
            context_window=plan.tokens.input + plan.tokens.output,
            timeout_seconds=plan.timeout_seconds, **self.ledger_binding,
        )

    def authorize(self, grant: SmokeGrant, claim: Claim, request_hash: str) -> Attempt:
        with self._engine.begin() as scope:
            plan = self._current(scope)
            plan.check_reservation()
            self._matches_plan(grant, plan)
            actual = self._load(scope, grant.grant_id, lock=True)
            if (
                actual != grant or grant.run_id != claim.run_id or grant.consumed
                or request_hash != grant.commitment
            ):
                raise _deny()
            row = scope.execute(text("""
                SELECT input_bound,output_bound,cost_bound FROM agent_research.model_attempts
                WHERE attempt_id=:id AND run_id=:run
            """), {"id": grant.grant_id, "run": claim.run_id}).one_or_none()
            if row is None or tuple(row) != (
                plan.tokens.input, plan.tokens.output, plan.maximum_cost,
            ):
                raise _deny()
            result = self._attempts.authorize_in_transaction(
                scope, claim, grant.grant_id, request_hash, **self.ledger_binding,
            )
            scope.execute(text("""
                UPDATE agent_research.provider_smoke_grants SET consumed=true WHERE grant_id=:id
            """), {"id": grant.grant_id})
            return result

    def settle(
        self, grant: SmokeGrant, claim: Claim, outcome: ModelOutcome, *,
        reported_tokens: Tokens | None = None, confirmed_cost: Decimal | None = None,
    ) -> Attempt:
        with self._engine.begin() as scope:
            if outcome is ModelOutcome.COMPLETED:
                try:
                    self._matches_plan(grant, self._current(scope))
                except (RunConflict, BudgetDenied, OSError, ValueError):
                    outcome = ModelOutcome.RESULT_UNAVAILABLE
            actual = self._load(scope, grant.grant_id, lock=True)
            if actual is None or actual != replace(grant, consumed=actual.consumed):
                raise _deny()
            if not actual.consumed:
                outcome, reported_tokens, confirmed_cost = ModelOutcome.UNSENT, None, None
            return self._attempts.settle_in_transaction(
                scope, claim, grant.grant_id, outcome, reported_tokens=reported_tokens,
                confirmed_cost=confirmed_cost,
                result_ref=grant.grant_id.hex if outcome is ModelOutcome.COMPLETED else None,
                unsent_evidence_ref=grant.grant_id.hex if outcome is ModelOutcome.UNSENT else None,
                **self.ledger_binding,
            )

    def verify(self, grant: SmokeGrant):
        with self._engine.begin() as scope:
            plan = self._current(scope)
            self._matches_plan(grant, plan)
            actual = self._load(scope, grant.grant_id, lock=True)
            if actual is None or not actual.consumed or actual != replace(grant, consumed=True):
                raise _deny()
            return self._provider_store.mark_verified(
                self.binding, self.epoch, grant.grant_id, scope=scope, bounded_smoke=True,
            )
