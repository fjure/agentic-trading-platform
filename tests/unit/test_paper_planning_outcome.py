"""Durable safe Paper planning outcomes preserve immutable model provenance."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.domain.models import AuditEvent, DecisionResult, TradeProposal
from services.worker.paper_planning import record_planning_outcome
from services.worker.planner import plan_proposal
from tests.unit.test_decision import NOW, FixedClock, SeededIds, engine, state
from tests.unit.test_paper_limit_planner import CONFIG, inputs


class MemoryLedger:
    """Transactional recording port with immutable domain entities, no broker port."""

    def __init__(self):
        self.entities = {}

    @contextmanager
    def transaction(self):
        saved = dict(self.entities)
        try:
            yield self
        except Exception:
            self.entities = saved
            raise

    def get(self, model, entity_id):
        return self.entities.get((model, entity_id))

    def insert(self, entity):
        key = type(entity), entity.id
        if key in self.entities:
            raise ValueError("immutable entity duplicate")
        self.entities[key] = entity


def actual_plan(ledger, decision, source, proof):
    return plan_proposal(
        decision=decision,
        market_state=source,
        config=CONFIG,
        clock=FixedClock(),
        ids=SeededIds(),
        ledger=ledger,
        paper_inputs=proof,
        require_paper=True,
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("missing-proof", "PAPER_PROOF_MISSING"),
        ("untyped-proof", "PAPER_PROOF_MISSING"),
        ("abstain", "PAPER_ABSTAIN"),
        ("flat-empty", "MINIMUM_QUANTITY"),
        ("minimum-budget", "MINIMUM_QUANTITY"),
        ("stale-quote", "PAPER_PROOF_STALE"),
        ("confidence", "PAPER_PLAN_NOT_ADMITTED"),
    ],
)
def test_no_proposal_has_honest_durable_outcome_without_rewriting_decision(mode, expected):
    source = state(
        previous="101" if mode == "abstain" else "102" if mode == "flat-empty" else "100"
    )
    decision = engine().decide(source, "v1")
    proof = inputs(source)
    if mode == "missing-proof":
        proof = None
    elif mode == "untyped-proof":
        proof = {"api_key": "synthetic-secret-canary"}
    elif mode == "minimum-budget":
        proof = replace(proof, risk_budget=Decimal("0.000000001"))
    elif mode == "stale-quote":
        proof = replace(proof, quote_provider_time=NOW - timedelta(seconds=30))
    elif mode == "confidence":
        decision = replace(decision, confidence=Decimal("0.1"))
    original = replace(decision)
    ledger = MemoryLedger()
    with ledger.transaction() as tx:
        proposal = actual_plan(ledger, decision, source, proof)
        assert proposal is None
        outcome = record_planning_outcome(tx, decision, source, proposal, proof, NOW, SeededIds())
    persisted = ledger.get(DecisionResult, decision.id)
    assert persisted == original == decision
    assert persisted.choice == original.choice and persisted.reason_code == original.reason_code
    assert outcome.reason_codes == (expected,)
    assert outcome.actor_id == "worker-planner" and outcome.action == "paper.plan_outcome"
    assert outcome.entity_id == decision.id + ":paper-plan-outcome"
    assert outcome.entity_version == 1 and outcome.trace_id == decision.trace_id
    assert outcome.policy_version == (
        None if mode in {"missing-proof", "untyped-proof"} else proof.policy_version
    )
    assert ledger.get(AuditEvent, outcome.id) == outcome
    assert not any(model is TradeProposal for model, _ in ledger.entities)
    assert "synthetic-secret-canary" not in repr(outcome)
    assert "100.001" not in repr(outcome) and "100.009" not in repr(outcome)


def test_real_proposal_records_planned_once_with_deterministic_id_and_original_timestamp():
    source = state()
    decision = engine().decide(source, "v1")
    proof = inputs(source)
    ledger = MemoryLedger()
    with ledger.transaction() as tx:
        proposal = actual_plan(ledger, decision, source, proof)
        assert type(proposal) is TradeProposal
        first = record_planning_outcome(tx, decision, source, proposal, proof, NOW, SeededIds())
        replay = record_planning_outcome(
            tx, decision, source, proposal, proof, NOW + timedelta(seconds=1), SeededIds()
        )
        assert first is replay
    assert first.reason_codes == ("PAPER_LIMIT_PLANNED",)
    assert first.id == SeededIds().new_id("audit", decision.id + ":paper-plan-outcome")
    assert first.occurred_at == NOW
    assert (
        len(
            [
                value
                for value in ledger.entities.values()
                if isinstance(value, AuditEvent) and value.action == "paper.plan_outcome"
            ]
        )
        == 1
    )
    assert ledger.get(TradeProposal, proposal.id) == proposal


def test_conflicting_outcome_cannot_replace_prior_immutable_record():
    source = state()
    decision = engine().decide(source, "v1")
    proof = inputs(source)
    ledger = MemoryLedger()
    with ledger.transaction() as tx:
        first = record_planning_outcome(tx, decision, source, None, None, NOW, SeededIds())
    with pytest.raises(ValueError, match="PAPER_PLANNING_OUTCOME_CONFLICT"):
        with ledger.transaction() as tx:
            record_planning_outcome(tx, decision, source, None, proof, NOW, SeededIds())
    assert ledger.get(AuditEvent, first.id) == first


def test_outcome_and_decision_share_transaction_rollback_boundary():
    source = state()
    decision = engine().decide(source, "v1")
    ledger = MemoryLedger()
    with pytest.raises(RuntimeError, match="fixture-abort"):
        with ledger.transaction() as tx:
            assert actual_plan(ledger, decision, source, None) is None
            record_planning_outcome(tx, decision, source, None, None, NOW, SeededIds())
            assert ledger.get(DecisionResult, decision.id) is not None
            raise RuntimeError("fixture-abort")
    assert ledger.entities == {}


def test_raw_decision_reason_is_preserved_but_not_copied_into_public_planning_outcome():
    source = state()
    decision = replace(engine().decide(source, "v1"), reason_code="synthetic-private-reason-canary")
    ledger = MemoryLedger()
    with ledger.transaction() as tx:
        assert actual_plan(ledger, decision, source, None) is None
        outcome = record_planning_outcome(tx, decision, source, None, None, NOW, SeededIds())
    assert ledger.get(DecisionResult, decision.id).reason_code == decision.reason_code
    assert outcome.reason_codes == ("PAPER_PROOF_MISSING",)
    assert "synthetic-private-reason-canary" not in repr(outcome)
