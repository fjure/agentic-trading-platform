"""Seven typed boundaries; implementations live outside the domain package."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from packages.domain.models import (
    BrokerCapabilities,
    BrokerOrder,
    DecisionCapabilities,
    DecisionResult,
    EventEnvelope,
    EvidenceBundle,
    ExecutionReport,
    MarketBar,
    MarketState,
    OrderIntent,
    SourceCapabilities,
)


class MarketDataProvider(Protocol):
    def capabilities(self, instrument_id: str) -> SourceCapabilities: ...

    def closed_bars(
        self, instrument_id: str, session_id: str, cursor: str | None
    ) -> tuple[MarketBar, ...]: ...


class BrokerAdapter(Protocol):
    """Execution-owned port. Workers and API must never receive this object."""

    def capabilities(self, instrument_id: str) -> BrokerCapabilities: ...

    def submit(self, intent: OrderIntent) -> BrokerOrder: ...

    def order_status(self, client_order_id: str) -> BrokerOrder | None: ...

    def reports(self, client_order_id: str) -> tuple[ExecutionReport, ...]: ...


class DecisionEngine(Protocol):
    def capabilities(self) -> DecisionCapabilities: ...

    def decide(self, market_state: MarketState, config_version: str) -> DecisionResult: ...


class EvidenceProvider(Protocol):
    def evidence(self, instrument_id: str, as_of: datetime) -> EvidenceBundle: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self, namespace: str, seed: str | None = None) -> str: ...


class EventPublisher(Protocol):
    def publish(self, event: EventEnvelope) -> None: ...
