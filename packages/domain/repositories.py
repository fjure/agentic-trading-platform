"""Persistence port for the versioned trading ledger."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Protocol, TypeVar

from packages.domain import models

Entity = (
    models.Instrument
    | models.MarketBar
    | models.EvidenceBundle
    | models.Position
    | models.PortfolioSnapshot
    | models.MarketState
    | models.DecisionResult
    | models.TradeProposal
    | models.RiskPolicy
    | models.RiskDecision
    | models.RiskReservation
    | models.OrderIntent
    | models.BrokerOrder
    | models.ExecutionReport
    | models.AgentRun
    | models.AuditEvent
)
T = TypeVar("T", bound=Entity)


class LedgerTransaction(Protocol):
    """All writes in one unit commit atomically, including their audit event."""

    def insert(self, entity: Entity, *, adapter: str | None = None) -> None: ...

    def get(self, model: type[T], entity_id: str) -> T | None: ...

    def replace(self, entity: Entity, *, expected_version: int | None = None) -> bool: ...

    def lock_account(self, account_id: str) -> None: ...

    def lock_accounts(self, account_ids: Iterator[str]) -> None: ...


class LedgerRepository(Protocol):
    def transaction(self) -> AbstractContextManager[LedgerTransaction]: ...
