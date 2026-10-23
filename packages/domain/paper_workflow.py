"""Additive Paper planning contracts; construction never authorizes execution.

The legacy model-only TradeProposal dataclass and event shape stay unchanged.
Source-aware SQL persists these references and adapts human proposals to the
shared risk and execution path with provenance checks under the account lock.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum

from packages.domain.models import (
    Choice,
    DecisionResult,
    Target,
    _id,
    _positive,
    _positive_version,
    _trace,
    utc_time,
)


class ProposalSourceKind(StrEnum):
    MODEL_DECISION = "MODEL_DECISION"
    HUMAN_REQUEST = "HUMAN_REQUEST"


class PaperOrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class ModelProposalSource:
    decision_id: str

    def __post_init__(self) -> None:
        _id(self.decision_id)


@dataclass(frozen=True, slots=True)
class HumanProposalSource:
    human_request_id: str

    def __post_init__(self) -> None:
        _id(self.human_request_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperLimitTerms:
    """Immutable requested bounds, independent of risk/capability approval."""

    account_id: str
    account_generation: int
    credential_generation: int
    policy_version: int
    profile_version: int
    limit_policy_version: int
    budget_version: int
    risk_budget: Decimal
    symbol: str
    side: PaperOrderSide
    quantity: Decimal
    limit_price: Decimal
    created_at: datetime
    valid_until: datetime
    trace_id: str
    currency: str = "USD"
    order_type: str = "LIMIT"
    time_in_force: str = "DAY"
    session: str = "REGULAR"

    def __post_init__(self) -> None:
        if self.account_id != "paper" or self.symbol not in {"SPY", "QQQ"}:
            raise ValueError("unsupported Paper account or symbol")
        for name in (
            "account_generation", "credential_generation", "policy_version", "budget_version",
            "profile_version", "limit_policy_version",
        ):
            _positive_version(getattr(self, name))
        _trace(self.trace_id)
        if not isinstance(self.side, PaperOrderSide):
            raise ValueError("Paper side must be BUY or SELL")
        if (self.currency, self.order_type, self.time_in_force, self.session) != (
            "USD", "LIMIT", "DAY", "REGULAR"
        ):
            raise ValueError("Paper requires USD LIMIT DAY regular-session terms")
        for name in ("risk_budget", "quantity", "limit_price"):
            object.__setattr__(self, name, _positive(getattr(self, name)))
        if self.quantity.as_tuple().exponent < -9:
            raise ValueError("Paper quantity exceeds nine decimal places")
        if self.limit_price.as_tuple().exponent < -6:
            raise ValueError("Paper limit price exceeds six decimal places")
        # Decimal's ambient context must never round a cost into the budget.
        with localcontext() as context:
            context.prec = 80
            if (
                self.side is PaperOrderSide.BUY
                and self.quantity * self.limit_price > self.risk_budget
            ):
                raise ValueError("Paper limit notional exceeds budget")
        object.__setattr__(self, "created_at", utc_time(self.created_at))
        object.__setattr__(self, "valid_until", utc_time(self.valid_until))
        if self.valid_until <= self.created_at:
            raise ValueError("Paper terms expire before creation")


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanOrderRequest(PaperLimitTerms):
    """An authenticated human's business request, never a model decision."""

    id: str
    operation_id: str

    def __post_init__(self) -> None:
        PaperLimitTerms.__post_init__(self)
        _id(self.id)
        _id(self.operation_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperTradeProposal(PaperLimitTerms):
    id: str
    operation_id: str
    market_state_id: str
    proposal_source: ProposalSourceKind
    decision_id: str | None = None
    human_request_id: str | None = None

    @property
    def instrument_id(self) -> str:
        return self.symbol

    @property
    def target(self) -> Target:
        return Target.LONG if self.side is PaperOrderSide.BUY else Target.FLAT

    @property
    def rationale_codes(self) -> tuple[str, ...]:
        if self.proposal_source is ProposalSourceKind.HUMAN_REQUEST:
            return ("PAPER_HUMAN_LIMIT", "HUMAN_REQUEST:" + self.human_request_id)
        return ("PAPER_MODEL_LIMIT", "MODEL_DECISION:" + self.decision_id)

    def __post_init__(self) -> None:
        PaperLimitTerms.__post_init__(self)
        _id(self.id)
        _id(self.operation_id)
        _id(self.market_state_id)
        if not isinstance(self.proposal_source, ProposalSourceKind):
            raise ValueError("typed proposal source required")
        if self.proposal_source is ProposalSourceKind.HUMAN_REQUEST:
            if self.decision_id is not None:
                raise ValueError("human proposal cannot reference a model decision")
            HumanProposalSource(self.human_request_id)
        else:
            if self.human_request_id is not None:
                raise ValueError("model proposal cannot reference a human request")
            ModelProposalSource(self.decision_id)

    @property
    def source(self) -> ModelProposalSource | HumanProposalSource:
        if self.proposal_source is ProposalSourceKind.HUMAN_REQUEST:
            return HumanProposalSource(self.human_request_id)
        return ModelProposalSource(self.decision_id)

    @classmethod
    def from_human_request(
        cls, request: HumanOrderRequest, *, proposal_id: str, market_state_id: str,
        created_at: datetime,
    ) -> PaperTradeProposal:
        if not isinstance(request, HumanOrderRequest):
            raise ValueError("typed human request required")
        terms = {item.name: getattr(request, item.name) for item in fields(PaperLimitTerms)}
        terms["created_at"] = created_at
        proposal = cls(
            **terms, id=proposal_id, operation_id=request.operation_id,
            market_state_id=market_state_id,
            proposal_source=ProposalSourceKind.HUMAN_REQUEST, human_request_id=request.id,
        )
        proposal.validate_human_request(request)
        return proposal

    def validate_human_request(self, request: HumanOrderRequest) -> None:
        """Check a retrieved source; persistence must also enforce its foreign key."""
        if (
            not isinstance(request, HumanOrderRequest)
            or self.source != HumanProposalSource(request.id)
            or self.operation_id != request.operation_id
            or not request.created_at <= self.created_at < self.valid_until <= request.valid_until
            or any(
                getattr(self, item.name) != getattr(request, item.name)
                for item in fields(PaperLimitTerms)
                if item.name not in {"created_at", "valid_until"}
            )
        ):
            raise ValueError("human proposal differs from its immutable request")

    def validate_model_decision(self, decision: DecisionResult) -> None:
        if (
            not isinstance(decision, DecisionResult)
            or self.source != ModelProposalSource(decision.id)
            or self.market_state_id != decision.market_state_id
            or self.trace_id != decision.trace_id
            or self.valid_until > decision.valid_until
            or decision.typed_answers is None
            or decision.choice is not (
                Choice.LONG if self.side is PaperOrderSide.BUY else Choice.FLAT
            )
        ):
            raise ValueError("model proposal differs from its decision")
