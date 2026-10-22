"""APW-01 source identity and immutable Paper bounds; no runtime or broker I/O."""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from packages.domain.models import (
    Choice,
    ChoiceAnswer,
    DecisionResult,
    NoulAnswer,
    ScoreAnswer,
    TradeProposal,
    TypedAnswers,
)
from packages.domain.paper_workflow import (
    HumanOrderRequest,
    HumanProposalSource,
    ModelProposalSource,
    PaperLimitTerms,
    PaperOrderSide,
    PaperTradeProposal,
    ProposalSourceKind,
)

NOW = datetime(2026, 10, 20, 15, tzinfo=UTC)
TRACE = "a" * 32


def request(**changes) -> HumanOrderRequest:
    values = dict(
        id="human-request-1", operation_id="operation-1", account_id="paper",
        account_generation=1, credential_generation=2, policy_version=3,
        profile_version=4, limit_policy_version=5, budget_version=6,
        risk_budget=Decimal("100"), symbol="SPY", side=PaperOrderSide.BUY,
        quantity=Decimal("0.123456789"), limit_price=Decimal("500.123456"),
        created_at=NOW, valid_until=NOW + timedelta(seconds=30), trace_id=TRACE,
    )
    return HumanOrderRequest(**(values | changes))


def proposal(source: HumanOrderRequest | None = None) -> PaperTradeProposal:
    return PaperTradeProposal.from_human_request(
        source or request(), proposal_id="proposal-1", market_state_id="market-1",
        created_at=NOW + timedelta(seconds=1),
    )


def decision(**changes) -> DecisionResult:
    answers = TypedAnswers(
        regime=ChoiceAnswer(
            "regime-v1", "TREND", Decimal("1"),
            {"TREND": Decimal("1"), "RANGE": Decimal("0"), "UNCERTAIN": Decimal("0")},
        ),
        evidence_quality=ScoreAnswer(
            "quality-v1", Decimal("9"), {"0": "weak", "9": "strong"},
            {"0": Decimal("0"), "9": Decimal("1")}, Decimal("1"),
        ),
        signal_conflict=NoulAnswer("conflict-v1", Decimal("0")),
    )
    values = dict(
        id="decision-1", market_state_id="market-1", choice=Choice.LONG,
        model_id="baseline-v1", prompt_version="prompt-v1", question_version="question-v1",
        config_version="config-v1", input_hash="b" * 64, latency_ms=1,
        valid_until=NOW + timedelta(seconds=30), trace_id=TRACE, reason_code="OK",
        confidence=Decimal("1"),
        distribution={"LONG": Decimal("1"), "FLAT": Decimal("0"), "ABSTAIN": Decimal("0")},
        typed_answers=answers, regime="TREND", evidence_quality=Decimal("1"),
        signal_conflict=Decimal("0"),
    )
    return DecisionResult(**(values | changes))


def model_proposal() -> PaperTradeProposal:
    return replace(
        proposal(), proposal_source=ProposalSourceKind.MODEL_DECISION,
        decision_id="decision-1", human_request_id=None,
    )


def test_human_proposal_preserves_every_bound_without_a_model_decision() -> None:
    original = request()
    planned = proposal(original)
    assert planned.source == HumanProposalSource(original.id)
    assert planned.decision_id is None
    assert planned.proposal_source is ProposalSourceKind.HUMAN_REQUEST
    assert planned.quantity == Decimal("0.123456789")
    assert planned.limit_price == Decimal("500.123456")
    for field in fields(PaperLimitTerms):
        if field.name != "created_at":
            assert getattr(original, field.name) == getattr(planned, field.name)
    planned.validate_human_request(original)
    with pytest.raises(FrozenInstanceError):
        original.risk_budget = Decimal("1000")
    with pytest.raises(FrozenInstanceError):
        planned.limit_price = Decimal("600")
    with pytest.raises(FrozenInstanceError):
        planned.source.human_request_id = "another-request"


@pytest.mark.parametrize("changes", [
    {"decision_id": "invented-model"}, {"human_request_id": None},
    {"human_request_id": ""}, {"proposal_source": "HUMAN_REQUEST"},
    {"proposal_source": ProposalSourceKind.MODEL_DECISION},
])
def test_source_ids_are_exclusive_and_discriminator_is_typed(changes) -> None:
    with pytest.raises(ValueError):
        replace(proposal(), **changes)


@pytest.mark.parametrize("changes", [
    {"id": "another-request"}, {"account_generation": 2}, {"credential_generation": 3},
    {"operation_id": "another-operation"},
    {"policy_version": 4}, {"profile_version": 5}, {"limit_policy_version": 6},
    {"budget_version": 7}, {"risk_budget": Decimal("101")}, {"symbol": "QQQ"},
    {"side": PaperOrderSide.SELL}, {"quantity": Decimal("0.1")},
    {"limit_price": Decimal("500.12")}, {"trace_id": "c" * 32},
    {"created_at": NOW + timedelta(seconds=2)},
    {"valid_until": NOW + timedelta(seconds=29)},
])
def test_source_lookup_rejects_identity_or_immutable_bound_drift(changes) -> None:
    with pytest.raises(ValueError, match="immutable request"):
        proposal().validate_human_request(request(**changes))


def test_proposal_ttl_may_shorten_but_never_extend_original_request() -> None:
    original = request()
    replace(proposal(), valid_until=NOW + timedelta(seconds=20)).validate_human_request(original)
    for created in (NOW - timedelta(microseconds=1), original.valid_until):
        with pytest.raises(ValueError):
            PaperTradeProposal.from_human_request(
                original, proposal_id="p", market_state_id="s", created_at=created,
            )
    with pytest.raises(ValueError, match="immutable request"):
        extended = replace(proposal(), valid_until=NOW + timedelta(seconds=31))
        extended.validate_human_request(original)


@pytest.mark.parametrize("changes", [
    {"account_id": "live"}, {"symbol": "BTC/USD"}, {"side": "BUY"},
    {"currency": "EUR"}, {"order_type": "MARKET"}, {"time_in_force": "GTC"},
    {"session": "EXTENDED"}, {"quantity": Decimal("0.0000000001")},
    {"quantity": Decimal("0")}, {"quantity": Decimal("-1")}, {"quantity": 0.1},
    {"limit_price": Decimal("0.0000001")}, {"limit_price": Decimal("0")},
    {"limit_price": Decimal("NaN")}, {"limit_price": 1.1}, {"risk_budget": Decimal("0")},
    {"risk_budget": Decimal("1")}, {"trace_id": "invalid"},
    {"created_at": NOW.replace(tzinfo=None)},
    {"created_at": NOW.astimezone(timezone(timedelta(hours=2)))},
    {"valid_until": NOW}, {"id": ""}, {"operation_id": ""},
])
def test_paper_request_fails_closed_without_rounding_or_scope_expansion(changes) -> None:
    with pytest.raises(ValueError):
        request(**changes)


@pytest.mark.parametrize("name", [
    "account_generation", "credential_generation", "policy_version", "profile_version",
    "limit_policy_version", "budget_version",
])
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_generations_and_versions_are_positive_integers(name, value) -> None:
    with pytest.raises(ValueError):
        request(**{name: value})


def test_budget_comparison_uses_exact_product_even_with_low_ambient_precision() -> None:
    with localcontext() as context:
        context.prec = 2
        with pytest.raises(ValueError, match="notional exceeds budget"):
            request(quantity=Decimal("1.000000001"), limit_price=Decimal("100"))
    # A SELL's notional is not new BUY exposure; free-long checks belong to risk.
    request(side=PaperOrderSide.SELL, risk_budget=Decimal("1"))


def test_real_model_reference_remains_distinct_and_source_lookup_is_checked() -> None:
    planned = model_proposal()
    assert planned.source == ModelProposalSource("decision-1")
    planned.validate_model_decision(decision())
    for changes in (
        {"id": "other"}, {"market_state_id": "other"}, {"trace_id": "c" * 32},
        {"valid_until": NOW + timedelta(seconds=29)}, {"choice": Choice.FLAT},
    ):
        with pytest.raises(ValueError, match="differs from its decision"):
            planned.validate_model_decision(decision(**changes))
    with pytest.raises(ValueError, match="immutable request"):
        planned.validate_human_request(request())
    with pytest.raises(ValueError, match="differs from its decision"):
        proposal().validate_model_decision(decision())
    with pytest.raises(ValueError):
        replace(planned, human_request_id="human-request-1")
    with pytest.raises(ValueError):
        replace(planned, decision_id=None)


def test_legacy_trade_proposal_persistence_shape_is_unchanged() -> None:
    assert tuple(field.name for field in fields(TradeProposal)) == (
        "id", "decision_id", "market_state_id", "instrument_id", "target", "quantity",
        "created_at", "valid_until", "trace_id", "rationale_codes", "limit_price",
    )
