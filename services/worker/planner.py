"""Pure proposal planning from a valid decision and explicit quantity policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from packages.domain.models import (
    AuditEvent,
    Choice,
    DecisionResult,
    MarketState,
    Quality,
    Reconciliation,
    Target,
    TradeProposal,
    decimal_value,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator
from packages.domain.repositories import LedgerRepository, LedgerTransaction


@dataclass(frozen=True, slots=True)
class PlannerConfig:
    version: str
    fixed_entry_quantity: Decimal
    proposal_ttl_seconds: int = 30
    minimum_decision_ttl_seconds: int = 5
    minimum_confidence: Decimal = Decimal("0.7")

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("planner version required")
        quantity = decimal_value(self.fixed_entry_quantity)
        if quantity <= 0:
            raise ValueError("fixed entry quantity must be positive")
        object.__setattr__(self, "fixed_entry_quantity", quantity)
        confidence = decimal_value(self.minimum_confidence)
        if not Decimal("0") < confidence <= Decimal("1"):
            raise ValueError("minimum confidence must be in (0, 1]")
        object.__setattr__(self, "minimum_confidence", confidence)
        if (type(self.proposal_ttl_seconds) is not int or self.proposal_ttl_seconds <= 0
                or type(self.minimum_decision_ttl_seconds) is not int
                or self.minimum_decision_ttl_seconds <= 0
                or self.proposal_ttl_seconds < self.minimum_decision_ttl_seconds):
            raise ValueError("invalid proposal lifetime")


def plan_proposal(*, decision: DecisionResult, market_state: MarketState,
                  config: PlannerConfig, clock: Clock, ids: IdGenerator,
                  ledger: LedgerRepository | None = None) -> TradeProposal | None:
    now = utc_time(clock.now())

    def finish(proposal: TradeProposal | None) -> TradeProposal | None:
        if ledger is not None:
            with ledger.transaction() as tx:
                _insert_audited(tx, decision, "decision.persist", now, ids)
                if proposal is not None:
                    _insert_audited(tx, proposal, "proposal.persist", now, ids)
        return proposal

    if (decision.choice is Choice.ABSTAIN or decision.typed_answers is None
            or decision.confidence is None
            or decision.confidence < config.minimum_confidence
            or decision.distribution is None
            or decision.distribution.get(decision.choice.value, Decimal("0"))
            < config.minimum_confidence
            or decision.model_id is None
            or decision.market_state_id != market_state.id
            or decision.trace_id != market_state.trace_id
            or decision.valid_until - now < timedelta(seconds=config.minimum_decision_ttl_seconds)
            or market_state.available_at > now or market_state.as_of > now
            or market_state.quality is not Quality.VALID
            or market_state.evidence.quality is not Quality.VALID
            or market_state.portfolio.quality is not Quality.VALID
            or market_state.portfolio.reconciliation is not Reconciliation.CURRENT):
        return finish(None)
    if decision.choice is Choice.LONG:
        if market_state.position.quantity != 0:
            return finish(None)
        target, quantity = Target.LONG, config.fixed_entry_quantity
    elif decision.choice is Choice.FLAT:
        if market_state.position.quantity <= 0:
            return finish(None)
        target, quantity = Target.FLAT, market_state.position.quantity
    else:
        return finish(None)
    valid_until = min(decision.valid_until, now + timedelta(seconds=config.proposal_ttl_seconds))
    if valid_until - now < timedelta(seconds=config.minimum_decision_ttl_seconds):
        return finish(None)
    planner_fingerprint = hashlib.sha256(json.dumps({
        "version": config.version,
        "fixed_entry_quantity": str(config.fixed_entry_quantity),
        "proposal_ttl_seconds": config.proposal_ttl_seconds,
        "minimum_decision_ttl_seconds": config.minimum_decision_ttl_seconds,
        "minimum_confidence": str(config.minimum_confidence),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    proposal = TradeProposal(
        id=ids.new_id("proposal", f"{decision.id}:{decision.input_hash}:{planner_fingerprint}"),
        decision_id=decision.id, market_state_id=market_state.id,
        instrument_id=market_state.instrument_id, target=target, quantity=quantity,
        created_at=now, valid_until=valid_until, trace_id=decision.trace_id,
        rationale_codes=(decision.reason_code, config.version),
    )
    return finish(proposal)


def record_decision(*, decision: DecisionResult, ledger: LedgerRepository,
                    clock: Clock, ids: IdGenerator) -> None:
    """Persist an immutable result even when no proposal is planned."""
    with ledger.transaction() as tx:
        _insert_audited(tx, decision, "decision.persist", utc_time(clock.now()), ids)


def _insert_audited(tx: LedgerTransaction, entity: DecisionResult | TradeProposal,
                    action: str, now: datetime, ids: IdGenerator) -> None:
    existing = tx.get(type(entity), entity.id)
    if existing is not None:
        if existing != entity:
            raise ValueError("immutable ledger identity collision")
        return
    tx.insert(AuditEvent(
        id=ids.new_id("audit", entity.id), occurred_at=now,
        actor_id="worker-planner", action=action,
        reason_codes=(entity.reason_code,) if isinstance(entity, DecisionResult)
                     else entity.rationale_codes,
        entity_id=entity.id, entity_version=1, trace_id=entity.trace_id,
    ))
    tx.insert(entity)
