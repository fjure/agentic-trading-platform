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
from services.worker.paper_limit import PaperPlanningInputs, evaluate_paper_limit


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
                  ledger: LedgerRepository | None = None,
                  paper_inputs: PaperPlanningInputs | None = None,
                  require_paper: bool = False) -> TradeProposal | None:
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
    paper = None
    if paper_inputs is not None or require_paper:
        paper = evaluate_paper_limit(
            decision=decision, market_state=market_state, inputs=paper_inputs, now=now,
        )
        if paper.quantity is None:
            return finish(None)
        quantity, valid_until = paper.quantity, min(valid_until, paper.valid_until)
    if valid_until - now < timedelta(seconds=config.minimum_decision_ttl_seconds):
        return finish(None)
    planner_fingerprint = hashlib.sha256(json.dumps({
        "version": config.version,
        "fixed_entry_quantity": str(config.fixed_entry_quantity),
        "proposal_ttl_seconds": config.proposal_ttl_seconds,
        "minimum_decision_ttl_seconds": config.minimum_decision_ttl_seconds,
        "minimum_confidence": str(config.minimum_confidence),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rationale = (decision.reason_code, config.version)
    if paper is not None:
        planner_fingerprint = hashlib.sha256(
            f"{planner_fingerprint}:{paper.input_hash}".encode()
        ).hexdigest()
        rationale += (
            "PAPER_LIMIT_DAY_REGULAR_USD",
            f"PAPER_LIMIT_POLICY:{paper_inputs.limit_policy_version}",
            f"PAPER_INPUT_SHA256:{paper.input_hash}",
            f"PAPER_QUOTE:{paper_inputs.quote_evidence_id}",
            *(f"PAPER_{name.upper()}:{getattr(paper_inputs, name)}" for name in (
                "context_version", "portfolio_version", "account_generation",
                "credential_generation", "policy_version", "profile_version", "budget_version",
                "capability_version",
            )),
        )
    proposal = TradeProposal(
        id=ids.new_id("proposal", f"{decision.id}:{decision.input_hash}:{planner_fingerprint}"),
        decision_id=decision.id, market_state_id=market_state.id,
        instrument_id=market_state.instrument_id, target=target, quantity=quantity,
        created_at=now, valid_until=valid_until, trace_id=decision.trace_id,
        rationale_codes=rationale,
        limit_price=paper.limit_price if paper is not None else None,
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
