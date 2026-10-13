"""PTV-07: immutable replay, abstention, and quantity isolation."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.decisions.baseline import (
    BaselineConfig,
    DeterministicBaselineDecisionEngine,
    _canonical,
)
from packages.decisions.mock import MockDecisionEngine
from packages.domain.models import (
    AuditEvent,
    Capability,
    Choice,
    DecisionCapabilities,
    EvidenceBundle,
    Market,
    MarketBar,
    MarketState,
    PortfolioSnapshot,
    Position,
    Quality,
    Reconciliation,
    Target,
)
from services.worker.planner import PlannerConfig, plan_proposal, record_decision

NOW = datetime(2026, 10, 12, 14, 0, 10, tzinfo=UTC)
TRACE = "a" * 32


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class SeededIds:
    def new_id(self, namespace: str, seed: str | None = None) -> str:
        return hashlib.sha256(f"{namespace}:{seed}".encode()).hexdigest()


def state(*, previous: str = "100", last: str = "101", volume: str = "2000") -> MarketState:
    end = NOW - timedelta(seconds=10)
    bar = MarketBar(
        "bar-1", "SPY", end - timedelta(minutes=15), end,
        Decimal("100"), Decimal("102"), Decimal("99"), Decimal(last),
        Decimal("1000"), end, end, end, Quality.VALID,
    )
    evidence = EvidenceBundle("evidence-1", "SPY", ("fixture",), end,
                              (), Quality.VALID)
    portfolio = PortfolioSnapshot(
        "portfolio-1", "USD", Decimal("10000"), Decimal("10000"),
        Decimal("0"), NOW, 1, Quality.VALID, Reconciliation.CURRENT,
    )
    position = Position("position-1", "SPY", "USD", Decimal("0"), None,
                        Decimal("0"), 1)
    return MarketState(
        "state-1", "SPY", Market.US_EQUITY, bar, evidence, portfolio,
        position, {"last_close": Decimal(last), "previous_close": Decimal(previous),
                   "window_volume": Decimal(volume)}, Decimal("0.02"), end,
        end, end, NOW, Quality.VALID, TRACE,
    )


def engine(config: BaselineConfig | None = None) -> DeterministicBaselineDecisionEngine:
    return DeterministicBaselineDecisionEngine(
        clock=FixedClock(), ids=SeededIds(), config=config or BaselineConfig())


def test_repeat_replay_and_full_metadata_hash() -> None:
    source = state()
    decision = engine().decide(source, "config.v1")
    repeated = engine().decide(source, "config.v1")
    assert decision == repeated
    assert decision.choice is Choice.LONG
    assert decision.typed_answers is not None
    assert decision.typed_answers.signal_conflict.kind == "NOUL"
    assert decision.model_id == "neutral-baseline.v1"
    assert decision.estimated_cost is None
    assert decision.api_units is None
    assert decision.input_hash != engine().decide(
        replace(source, bar=replace(source.bar, version=2)), "config.v1").input_hash
    assert decision.input_hash != engine().decide(
        replace(source, portfolio=replace(source.portfolio, ledger_version=2)),
        "config.v1").input_hash
    assert decision.input_hash != engine().decide(source, "config.v2").input_hash
    assert decision.input_hash != engine(BaselineConfig(version="neutral-baseline.v2")) \
        .decide(source, "config.v1").input_hash
    assert json.dumps(_canonical(decision), sort_keys=True) == json.dumps(
        _canonical(repeated), sort_keys=True)


@pytest.mark.parametrize("changed", [
    lambda s: replace(s, features={"last_close": Decimal("101")}),
    lambda s: replace(s, features={**s.features, "window_volume": Decimal("0")}),
    lambda s: replace(s, evidence=replace(s.evidence, conflicts=("conflict",))),
    lambda s: replace(s, quality=Quality.STALE),
    lambda s: replace(s, portfolio=replace(s.portfolio, reconciliation=Reconciliation.PENDING)),
    lambda s: replace(s, available_at=NOW - timedelta(hours=1)),
])
def test_missing_stale_conflicting_input_abstains(changed) -> None:
    result = engine().decide(changed(state()), "config.v1")
    assert result.choice is Choice.ABSTAIN
    assert result.reason_code == "ABSTAIN_MISSING_EVIDENCE"
    assert result.model_id is result.typed_answers is result.confidence is None


def test_unsupported_capability_and_mock_failures() -> None:
    unsupported = DecisionCapabilities("mock", (), (), Capability.UNKNOWN)
    assert MockDecisionEngine(clock=FixedClock(), ids=SeededIds(),
                              capability=unsupported).decide(state(), "v1").reason_code \
        == "ABSTAIN_UNAVAILABLE"
    for failure in ("timeout", "invalid_response", "missing_response"):
        result = MockDecisionEngine(clock=FixedClock(), ids=SeededIds(),
                                    failure=failure).decide(state(), "v1")
        assert result.choice is Choice.ABSTAIN
        assert result.model_id is result.typed_answers is result.confidence is None


def test_low_confidence_and_closed_choice() -> None:
    result = engine().decide(state(previous="101", last="101"), "v1")
    assert result.choice is Choice.ABSTAIN
    assert result.reason_code == "ABSTAIN_LOW_CONFIDENCE"
    with pytest.raises(ValueError):
        Choice("SHORT")


def test_planner_quantity_is_explicit_and_closing_uses_position() -> None:
    source = state()
    decision = engine().decide(source, "v1")
    planner = PlannerConfig("fixed.v1", Decimal("2"))
    proposal = plan_proposal(decision=decision, market_state=source,
                             config=planner, clock=FixedClock(), ids=SeededIds())
    assert proposal is not None
    assert proposal.target is Target.LONG
    assert proposal.quantity == Decimal("2")
    assert proposal.valid_until <= decision.valid_until
    changed = replace(decision, confidence=Decimal("0.99"))
    same_size = plan_proposal(decision=changed, market_state=source,
                              config=planner, clock=FixedClock(), ids=SeededIds())
    assert same_size is not None and same_size.quantity == proposal.quantity
    assert plan_proposal(decision=replace(decision, confidence=Decimal("0.1")),
                         market_state=source, config=planner,
                         clock=FixedClock(), ids=SeededIds()) is None
    larger = plan_proposal(decision=decision, market_state=source,
                           config=PlannerConfig("fixed.v1", Decimal("3")),
                           clock=FixedClock(), ids=SeededIds())
    assert larger is not None and larger.id != proposal.id
    assert plan_proposal(decision=replace(decision, choice=Choice.ABSTAIN),
                         market_state=source, config=planner,
                         clock=FixedClock(), ids=SeededIds()) is None
    closing_state = replace(
        source, position=replace(source.position, quantity=Decimal("3")),
        features={**source.features, "previous_close": Decimal("102")},
    )
    closing = engine().decide(closing_state, "v1")
    closing_proposal = plan_proposal(decision=closing, market_state=closing_state,
                                     config=planner, clock=FixedClock(), ids=SeededIds())
    assert closing_proposal is not None
    assert closing_proposal.target is Target.FLAT
    assert closing_proposal.quantity == Decimal("3")


def test_planner_rejects_expiry_and_invalid_quantity() -> None:
    source = state()
    decision = engine().decide(source, "v1")
    with pytest.raises(ValueError):
        PlannerConfig("fixed.v1", Decimal("0"))
    assert plan_proposal(decision=replace(decision, valid_until=NOW),
                         market_state=source,
                         config=PlannerConfig("fixed.v1", Decimal("1")),
                         clock=FixedClock(), ids=SeededIds()) is None


def replay_corpus() -> tuple[MarketState, ...]:
    cases = []
    for index in range(100):
        source = state(previous=("100", "101", "102", "103", "104")[index % 5])
        source = replace(source, id=f"state-{index}",
                         bar=replace(source.bar, id=f"bar-{index}"))
        if index % 10 == 0:
            source = replace(source, evidence=replace(
                source.evidence, conflicts=("synthetic-conflict",)))
        if index % 10 == 1:
            source = replace(source, features={"last_close": Decimal("101")})
        if index % 10 == 2:
            source = replace(source, quality=Quality.STALE)
        cases.append(source)
    return tuple(cases)


def test_versioned_100_case_replay_corpus() -> None:
    cases = replay_corpus()
    corpus_hash = hashlib.sha256(json.dumps(
        [_canonical(case) for case in cases], sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    assert len(cases) == 100
    assert len(corpus_hash) == 64
    for source in cases:
        results = [engine().decide(source, "corpus.v1") for _ in range(3)]
        assert results[0] == results[1] == results[2]
        assert results[0].choice in (Choice.LONG, Choice.FLAT, Choice.ABSTAIN)
        if (source.evidence.conflicts or "previous_close" not in source.features
                or source.quality is Quality.STALE):
            assert results[0].choice is Choice.ABSTAIN
            assert results[0].typed_answers is None


def test_explicit_ledger_unit_writes_decision_proposal_and_audits_atomically() -> None:
    class Transaction:
        def __init__(self, ledger) -> None:
            self.ledger = ledger
            self.entities = []

        def insert(self, entity, *, adapter=None) -> None:
            self.entities.append(entity)

        def get(self, model, entity_id):
            for unit in (*self.ledger.units, self):
                for entity in unit.entities:
                    if isinstance(entity, model) and entity.id == entity_id:
                        return entity
            return None

    class Ledger:
        def __init__(self) -> None:
            self.units = []

        @contextmanager
        def transaction(self):
            unit = Transaction(self)
            yield unit
            self.units.append(unit)

    ledger = Ledger()
    source = state()
    decision = engine().decide(source, "v1")
    proposal = plan_proposal(
        decision=decision, market_state=source,
        config=PlannerConfig("fixed.v1", Decimal("2")),
        clock=FixedClock(), ids=SeededIds(), ledger=ledger,
    )
    assert proposal is not None
    assert len(ledger.units) == 1
    entities = ledger.units[0].entities
    assert entities[1] == decision
    assert entities[3] == proposal
    assert isinstance(entities[0], AuditEvent)
    assert isinstance(entities[2], AuditEvent)
    assert entities[0].entity_id == decision.id
    assert entities[2].entity_id == proposal.id
    abstain_source = replace(source, features={"last_close": Decimal("101")})
    abstain = engine().decide(abstain_source, "v1")
    assert plan_proposal(
        decision=abstain, market_state=abstain_source,
        config=PlannerConfig("fixed.v1", Decimal("2")),
        clock=FixedClock(), ids=SeededIds(), ledger=ledger,
    ) is None
    assert len(ledger.units) == 2
    assert len(ledger.units[1].entities) == 2
    assert isinstance(ledger.units[1].entities[0], AuditEvent)
    assert ledger.units[1].entities[1] == abstain
    record_decision(decision=abstain, ledger=ledger, clock=FixedClock(), ids=SeededIds())
    assert ledger.units[2].entities == []
    with pytest.raises(ValueError, match="identity collision"):
        record_decision(decision=replace(abstain, reason_code="ABSTAIN_UNAVAILABLE"),
                        ledger=ledger, clock=FixedClock(), ids=SeededIds())
    assert len(ledger.units) == 3
