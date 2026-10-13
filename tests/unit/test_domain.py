"""PTV-02 boundary evidence; runtime engine behavior is tested in later tasks."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain import ports
from packages.domain.models import (
    AgentRun,
    BrokerCapabilities,
    Capability,
    Choice,
    ChoiceAnswer,
    DecisionCapabilities,
    DecisionResult,
    EventEnvelope,
    EvidenceBundle,
    ExposureSnapshot,
    Instrument,
    InstrumentUniverse,
    Market,
    MarketBar,
    MarketState,
    Mode,
    NoulAnswer,
    OrderIntent,
    PortfolioSnapshot,
    Position,
    Quality,
    Reconciliation,
    ReservationState,
    RiskDecision,
    RiskReservation,
    SafetyState,
    ScoreAnswer,
    Target,
    TradeProposal,
    TypedAnswers,
)

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
TRACE = "a" * 32


def bar(instrument: str = "spy") -> MarketBar:
    return MarketBar(
        id="bar-1",
        instrument_id=instrument,
        start_at=NOW - timedelta(minutes=15),
        end_at=NOW,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("30"),
        event_time=NOW,
        received_at=NOW,
        available_at=NOW,
        quality=Quality.VALID,
    )


def test_t_mkt_001_universe_rejects_unknown_instrument() -> None:
    universe = InstrumentUniverse(
        (Instrument("spy", Market.US_EQUITY, "SPY", "EQUITY", "USD", 1, "NYSE"),)
    )
    assert universe.require_bar(bar()).id == "spy"
    with pytest.raises(ValueError, match="unknown instrument"):
        universe.require_bar(bar("unknown"))
    for asset_class in ("OPTION", "FUTURE", "LEVERAGED"):
        with pytest.raises(ValueError, match="unsupported asset class"):
            Instrument("other", Market.US_EQUITY, "OTHER", asset_class, "USD", 1, "NYSE")
    with pytest.raises(ValueError, match="asset class and market disagree"):
        Instrument("btc", Market.CRYPTO, "BTC/USD", "EQUITY", "USD", 1, "24/7")
    with pytest.raises(ValueError, match="closed 15-minute"):
        MarketBar(
            id="bar-2",
            instrument_id="spy",
            start_at=NOW - timedelta(minutes=15),
            end_at=NOW,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("0"),
            event_time=NOW,
            received_at=NOW,
            available_at=NOW,
            quality=Quality.VALID,
            closed=False,
        )


def test_t_mkt_008_and_t_rsk_003_unknown_capabilities_fail_closed() -> None:
    from packages.domain.models import SourceCapabilities

    source = SourceCapabilities("feed", "spy", ("PT15M",), 120)
    assert source.supports_interval("PT15M")
    assert source.news is Capability.UNKNOWN
    assert source.papers is Capability.UNKNOWN
    assert not source.supports_interval("PT1M")

    broker = BrokerCapabilities("fake", "spy", 1)
    assert not broker.permits(order_type="MARKET", fractional=False, regular_session=True)
    confirmed = BrokerCapabilities(
        "fake",
        "spy",
        1,
        paper=Capability.CONFIRMED,
        regular_session=Capability.CONFIRMED,
        market_order=Capability.CONFIRMED,
        quantity_step=Decimal("1"),
    )
    assert confirmed.permits(order_type="MARKET", fractional=False, regular_session=True)
    assert not confirmed.permits(order_type="LIMIT", fractional=False, regular_session=True)
    assert not confirmed.permits(order_type="MARKET", fractional=False, regular_session=False)
    assert not confirmed.permits(order_type="MARKET", fractional="false", regular_session=True)
    assert not confirmed.permits(order_type="MARKET", fractional=False, regular_session=None)
    continuous = BrokerCapabilities(
        "fake",
        "btc",
        1,
        paper=Capability.CONFIRMED,
        continuous_session=Capability.CONFIRMED,
        market_order=Capability.CONFIRMED,
        quantity_step=Decimal("0.0001"),
    )
    assert continuous.permits(order_type="MARKET", fractional=False, regular_session=False)
    assert not continuous.permits(order_type="MARKET", fractional=False, regular_session=True)


def test_market_state_requires_available_evidence_and_freezes_features() -> None:
    evidence = EvidenceBundle("ev-1", "spy", ("bar-1",), NOW, (), Quality.VALID)
    portfolio = PortfolioSnapshot(
        "pf-1",
        "USD",
        Decimal("1000"),
        Decimal("900"),
        Decimal("100"),
        NOW,
        1,
        Quality.VALID,
        Reconciliation.CURRENT,
    )
    position = Position("pos-1", "spy", "USD", Decimal("1"), Decimal("100"), Decimal("0"), 1)
    features = {"return": Decimal("0.01")}
    state = MarketState(
        "ms-1",
        "spy",
        Market.US_EQUITY,
        bar(),
        evidence,
        portfolio,
        position,
        features,
        Decimal("0.02"),
        NOW,
        NOW,
        NOW,
        NOW,
        Quality.VALID,
        TRACE,
    )
    features["return"] = Decimal("99")
    assert state.features["return"] == Decimal("0.01")
    with pytest.raises(TypeError):
        state.features["return"] = Decimal("2")
    late_evidence = EvidenceBundle(
        "ev-2", "spy", ("bar-1",), NOW + timedelta(seconds=1), (), Quality.VALID
    )
    with pytest.raises(ValueError, match="evidence not yet available"):
        MarketState(
            "ms-2",
            "spy",
            Market.US_EQUITY,
            bar(),
            late_evidence,
            portfolio,
            position,
            features,
            Decimal("0.02"),
            NOW,
            NOW,
            NOW,
            NOW,
            Quality.VALID,
            TRACE,
        )


def test_t_dec_008_capability_and_safe_abstention_value() -> None:
    capability = DecisionCapabilities("mock", ("market_state.v1",), ("choice",))
    assert not capability.supports(frozenset({"market_state.v1"}), frozenset({"choice"}))
    confirmed = DecisionCapabilities(
        "mock", ("market_state.v1",), ("choice",), Capability.CONFIRMED
    )
    assert confirmed.supports(frozenset({"market_state.v1"}), frozenset({"choice"}))
    assert not confirmed.supports(frozenset({"news.v1"}), frozenset({"choice"}))
    decision = DecisionResult(
        "dec-1",
        "market-1",
        Choice.ABSTAIN,
        None,
        "prompt-v1",
        "question-v1",
        "config-v1",
        "b" * 64,
        0,
        NOW,
        TRACE,
        "ABSTAIN_UNAVAILABLE",
    )
    assert decision.confidence is None
    with pytest.raises(ValueError, match="missing answers require ABSTAIN"):
        DecisionResult(
            "dec-2",
            "market-1",
            Choice.LONG,
            None,
            "prompt-v1",
            "question-v1",
            "config-v1",
            "b" * 64,
            0,
            NOW,
            TRACE,
            "OK",
        )


def _typed_answers() -> TypedAnswers:
    return TypedAnswers(
        regime=ChoiceAnswer(
            "q-regime-v1",
            "TREND",
            Decimal("0.8"),
            {"TREND": Decimal("0.8"), "RANGE": Decimal("0.1"), "UNCERTAIN": Decimal("0.1")},
        ),
        evidence_quality=ScoreAnswer(
            "q-quality-v1",
            Decimal("8"),
            {"0": "weak", "9": "strong"},
            {"0": Decimal("0.2"), "9": Decimal("0.8")},
            Decimal("0.8"),
        ),
        signal_conflict=NoulAnswer("q-conflict-v1", Decimal("0.1")),
    )


def _typed_decision(**overrides: object) -> DecisionResult:
    data: dict[str, object] = {
        "id": "dec-typed",
        "market_state_id": "market-1",
        "choice": Choice.LONG,
        "model_id": "mock-v1",
        "prompt_version": "prompt-v1",
        "question_version": "questions-v1",
        "config_version": "config-v1",
        "input_hash": "b" * 64,
        "latency_ms": 1,
        "valid_until": NOW,
        "trace_id": TRACE,
        "reason_code": "OK",
        "confidence": Decimal("0.7"),
        "distribution": {"LONG": Decimal("0.7"), "FLAT": Decimal("0.2"), "ABSTAIN": Decimal("0.1")},
        "typed_answers": _typed_answers(),
        "regime": "TREND",
        "evidence_quality": Decimal("0.8"),
        "signal_conflict": Decimal("0.1"),
    }
    data.update(overrides)
    return DecisionResult(**data)


def test_typed_answers_closed_and_distributions_exact() -> None:
    result = _typed_decision()
    assert result.typed_answers.regime.choice == "TREND"
    with pytest.raises(TypeError):
        result.typed_answers.regime.distribution["TREND"] = Decimal("0")
    with pytest.raises(ValueError, match="closed typed answers"):
        _typed_decision(typed_answers={"regime": {"choice": "TREND"}})
    with pytest.raises(ValueError, match="declared choices"):
        _typed_decision(distribution={"LONG": Decimal("1")})
    with pytest.raises(ValueError, match="sum exactly to one"):
        _typed_decision(
            distribution={
                "LONG": Decimal("0.6"),
                "FLAT": Decimal("0.2"),
                "ABSTAIN": Decimal("0.1"),
            }
        )
    with pytest.raises(ValueError, match="top regime differs"):
        _typed_decision(regime="RANGE")
    with pytest.raises(ValueError, match="unsupported choice"):
        ChoiceAnswer(
            "q-v1",
            "SHORT",
            Decimal("1"),
            {"LONG": Decimal("0"), "FLAT": Decimal("0"), "ABSTAIN": Decimal("1")},
        )
    with pytest.raises(TypeError):
        ChoiceAnswer(
            "q-v1",
            "LONG",
            Decimal("1"),
            {"LONG": Decimal("1"), "FLAT": Decimal("0"), "ABSTAIN": Decimal("0")},
            order_quantity=Decimal("1"),
        )
    with pytest.raises(ValueError, match="score outside"):
        ScoreAnswer(
            "q-v1",
            Decimal("10"),
            {"0": "low", "9": "high"},
            {"0": Decimal("0"), "9": Decimal("1")},
            Decimal("1"),
        )
    with pytest.raises(ValueError, match="score legend"):
        ScoreAnswer("q-v1", Decimal("8"), {"0": "low"}, {"0": Decimal("1")}, Decimal("1"))
    with pytest.raises(ValueError, match="sum exactly to one"):
        ScoreAnswer(
            "q-v1",
            Decimal("8"),
            {"0": "low", "9": "high"},
            {"0": Decimal("0.1"), "9": Decimal("0.8")},
            Decimal("1"),
        )
    with pytest.raises(ValueError, match="fraction outside"):
        NoulAnswer("q-v1", Decimal("1.1"))
    original = _typed_answers()
    with pytest.raises(ValueError, match="shadow direction"):
        TypedAnswers(
            original.regime,
            original.evidence_quality,
            original.signal_conflict,
            ChoiceAnswer(
                "q-shadow-v1",
                "TREND",
                Decimal("1"),
                {"TREND": Decimal("1"), "RANGE": Decimal("0"), "UNCERTAIN": Decimal("0")},
            ),
        )


def test_risk_approval_requires_actual_bool() -> None:
    common = dict(
        id="risk-2",
        proposal_id="proposal-1",
        reason_code="OK",
        reason_codes=("OK",),
        policy_version=1,
        exposure_snapshot=ExposureSnapshot("USD", Decimal("1000"), Decimal("0"), Decimal("0")),
        decided_at=NOW,
        valid_until=NOW + timedelta(seconds=30),
        trace_id=TRACE,
    )
    for approved in ("false", 1, Decimal("1")):
        with pytest.raises(ValueError, match="must be boolean"):
            RiskDecision(approved=approved, **common)


def test_targets_modes_and_immutable_proposal() -> None:
    assert {mode.value for mode in Mode} == {"DISABLED", "REPLAY", "SHADOW", "PAPER"}
    assert {mode.value for mode in SafetyState} == {"RUNNING", "PAUSE_NEW", "HALTED"}
    with pytest.raises(ValueError):
        Mode("LIVE")
    with pytest.raises(ValueError):
        Target("SHORT")
    proposal = TradeProposal(
        "proposal-1",
        "decision-1",
        "market-1",
        "spy",
        Target.LONG,
        Decimal("1"),
        NOW,
        NOW + timedelta(seconds=30),
        TRACE,
    )
    with pytest.raises(FrozenInstanceError):
        proposal.quantity = Decimal("2")
    with pytest.raises(ValueError, match="target must be LONG or FLAT"):
        TradeProposal(
            "proposal-2",
            "decision-1",
            "market-1",
            "spy",
            "SHORT",
            Decimal("1"),
            NOW,
            NOW + timedelta(seconds=30),
            TRACE,
        )


def test_t_run_001_reconstruction_links_and_typed_event() -> None:
    proposal = TradeProposal(
        "proposal-1",
        "decision-1",
        "market-1",
        "spy",
        Target.LONG,
        Decimal("1"),
        NOW,
        NOW + timedelta(seconds=30),
        TRACE,
    )
    reservation = RiskReservation(
        "reservation-1",
        proposal.id,
        "risk-1",
        "USD",
        Decimal("100"),
        ReservationState.HELD_UNKNOWN,
        1,
        NOW,
        TRACE,
    )
    risk = RiskDecision(
        "risk-1",
        proposal.id,
        True,
        "OK",
        ("OK",),
        1,
        ExposureSnapshot("USD", Decimal("1000"), Decimal("100"), Decimal("100")),
        NOW,
        NOW + timedelta(seconds=30),
        TRACE,
        reservation.id,
    )
    intent = OrderIntent(
        "intent-1",
        proposal.id,
        reservation.risk_decision_id,
        "client-1",
        "spy",
        "BUY",
        "MARKET",
        Decimal("1"),
        1,
        NOW,
        TRACE,
    )
    run = AgentRun(
        "run-1", "b" * 64, "config-v1", "checkpoint-1", "PAUSED", NOW, proposal.id, TRACE
    )
    event = EventEnvelope("event-1", "trade.proposed.v1", NOW, proposal.id, 1, TRACE, proposal)
    assert (
        risk.proposal_id,
        reservation.proposal_id,
        intent.proposal_id,
        event.aggregate_id,
        run.output_id,
    ) == (
        proposal.id,
        proposal.id,
        proposal.id,
        proposal.id,
        proposal.id,
    )
    with pytest.raises(ValueError, match="event type/payload mismatch"):
        EventEnvelope("event-2", "risk.reserved.v1", NOW, proposal.id, 1, TRACE, proposal)


def test_exactly_seven_external_ports() -> None:
    names = {
        name
        for name, value in vars(ports).items()
        if name != "Protocol"
        if isinstance(value, type) and getattr(value, "_is_protocol", False)
    }
    assert names == {
        "MarketDataProvider",
        "BrokerAdapter",
        "DecisionEngine",
        "EvidenceProvider",
        "Clock",
        "IdGenerator",
        "EventPublisher",
    }
