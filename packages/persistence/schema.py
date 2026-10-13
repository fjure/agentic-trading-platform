"""SQLAlchemy Core schema for the durable ledger."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from packages.domain import models

metadata = MetaData(schema="public")

ENTITY_TYPES = (
    models.Instrument,
    models.MarketBar,
    models.EvidenceBundle,
    models.Position,
    models.PortfolioSnapshot,
    models.MarketState,
    models.DecisionResult,
    models.TradeProposal,
    models.AuditEvent,
    models.RiskPolicy,
    models.RiskDecision,
    models.RiskReservation,
    models.OrderIntent,
    models.BrokerOrder,
    models.ExecutionReport,
    models.AgentRun,
)
TABLE_NAMES = {
    models.Instrument: "instruments",
    models.MarketBar: "market_bars",
    models.EvidenceBundle: "evidence_bundles",
    models.Position: "positions",
    models.PortfolioSnapshot: "portfolio_snapshots",
    models.MarketState: "market_states",
    models.DecisionResult: "decisions",
    models.TradeProposal: "trade_proposals",
    models.AuditEvent: "audit_events",
    models.RiskPolicy: "risk_policies",
    models.RiskDecision: "risk_decisions",
    models.RiskReservation: "risk_reservations",
    models.OrderIntent: "order_intents",
    models.BrokerOrder: "broker_orders",
    models.ExecutionReport: "execution_reports",
    models.AgentRun: "agent_runs",
}
REFERENCES = {
    "market_bars": {"instrument_id": "instruments"},
    "evidence_bundles": {"instrument_id": "instruments"},
    "positions": {"instrument_id": "instruments"},
    "market_states": {
        "instrument_id": "instruments",
        "bar": "market_bars",
        "evidence": "evidence_bundles",
        "portfolio": "portfolio_snapshots",
        "position": "positions",
    },
    "decisions": {"market_state_id": "market_states"},
    "trade_proposals": {
        "decision_id": "decisions",
        "market_state_id": "market_states",
        "instrument_id": "instruments",
    },
    "risk_policies": {"audit_event_id": "audit_events"},
    "risk_decisions": {"proposal_id": "trade_proposals"},
    "risk_reservations": {
        "proposal_id": "trade_proposals",
        "risk_decision_id": "risk_decisions",
    },
    "order_intents": {
        "proposal_id": "trade_proposals",
        "risk_decision_id": "risk_decisions",
        "instrument_id": "instruments",
    },
    "broker_orders": {"intent_id": "order_intents"},
    "execution_reports": {"order_id": "broker_orders"},
}


def _sql_type(hint: object, *, reference: bool = False) -> object:
    if reference:
        return String(255)
    variants = set(get_args(hint)) if get_origin(hint) in (UnionType, Union) else {hint}
    if Decimal in variants:
        return Numeric(38, 18, asdecimal=True)
    if datetime in variants:
        return TIMESTAMP(timezone=True)
    if bool in variants:
        return Boolean()
    if int in variants:
        return Integer()
    if str in variants or any(isinstance(t, type) and issubclass(t, StrEnum) for t in variants):
        return String(255)
    return JSONB()


TABLES: dict[type, Table] = {}
for entity_type in ENTITY_TYPES:
    name = TABLE_NAMES[entity_type]
    hints = get_type_hints(entity_type)
    columns = []
    for field_name, hint in hints.items():
        reference = field_name in REFERENCES.get(name, {})
        column_name = (
            field_name + "_id" if reference and not field_name.endswith("_id") else field_name
        )
        target = REFERENCES.get(name, {}).get(field_name)
        args = [column_name, _sql_type(hint, reference=reference)]
        if target:
            args.append(ForeignKey(f"{target}.id", ondelete="RESTRICT"))
        columns.append(
            Column(*args, primary_key=field_name == "id", nullable=type(None) in get_args(hint))
        )
    if name in ("broker_orders", "execution_reports"):
        columns.append(Column("adapter", String(100), nullable=False))
    if name == "audit_events":
        columns.append(
            Column(
                "transaction_id", BigInteger, nullable=False, server_default=text("txid_current()")
            )
        )
    constraints = []
    if name == "order_intents":
        constraints.append(UniqueConstraint("proposal_id", name="uq_intent_proposal"))
        constraints.append(UniqueConstraint("client_order_id", name="uq_intent_client_order"))
        constraints.append(UniqueConstraint("id", "client_order_id", name="uq_intent_client_pair"))
    if name == "broker_orders":
        constraints.extend(
            (
                UniqueConstraint("intent_id", name="uq_order_intent"),
                UniqueConstraint("client_order_id", name="uq_order_client"),
                UniqueConstraint("adapter", "broker_order_id", name="uq_order_broker"),
                CheckConstraint("filled_quantity <= requested_quantity", name="ck_order_fill"),
            )
        )
    if name == "execution_reports":
        constraints.extend(
            (
                UniqueConstraint("adapter", "broker_event_id", name="uq_report_broker_event"),
                CheckConstraint("filled_quantity <= requested_quantity", name="ck_report_fill"),
            )
        )
    if name == "risk_reservations":
        constraints.append(UniqueConstraint("proposal_id", name="uq_reservation_proposal"))
        constraints.append(UniqueConstraint("risk_decision_id", name="uq_reservation_decision"))
    if name == "risk_policies":
        constraints.append(UniqueConstraint("version", name="uq_policy_version"))
    TABLES[entity_type] = Table(name, metadata, *columns, *constraints)

accounts = Table(
    "accounts",
    metadata,
    Column("id", String(255), primary_key=True),
    Column("currency", String(16), nullable=False),
    Column("ledger_version", BigInteger, nullable=False, server_default="0"),
)

# Composite keys bind references to the proposal being executed.
TABLES[models.RiskDecision].append_constraint(
    UniqueConstraint("id", "proposal_id", name="uq_risk_decision_proposal")
)
TABLES[models.RiskReservation].append_constraint(
    UniqueConstraint("id", "risk_decision_id", name="uq_reservation_decision_pair")
)
TABLES[models.DecisionResult].append_constraint(
    UniqueConstraint("id", "market_state_id", name="uq_decision_market_state")
)
TABLES[models.TradeProposal].append_constraint(
    ForeignKeyConstraint(
        ["decision_id", "market_state_id"],
        ["decisions.id", "decisions.market_state_id"],
        name="fk_proposal_decision_market_state",
        ondelete="RESTRICT",
    )
)
TABLES[models.RiskReservation].append_constraint(
    ForeignKeyConstraint(
        ["risk_decision_id", "proposal_id"],
        ["risk_decisions.id", "risk_decisions.proposal_id"],
        name="fk_reservation_decision_proposal",
        ondelete="RESTRICT",
    )
)
TABLES[models.OrderIntent].append_constraint(
    ForeignKeyConstraint(
        ["risk_decision_id", "proposal_id"],
        ["risk_decisions.id", "risk_decisions.proposal_id"],
        name="fk_intent_decision_proposal",
        ondelete="RESTRICT",
    )
)
TABLES[models.BrokerOrder].append_constraint(
    ForeignKeyConstraint(
        ["intent_id", "client_order_id"],
        ["order_intents.id", "order_intents.client_order_id"],
        name="fk_order_intent_client",
        ondelete="RESTRICT",
    )
)
TABLES[models.RiskDecision].append_constraint(
    ForeignKeyConstraint(
        ["policy_version"],
        ["risk_policies.version"],
        name="fk_risk_decision_policy_version",
        ondelete="RESTRICT",
    )
)
TABLES[models.RiskDecision].append_constraint(
    ForeignKeyConstraint(
        ["reservation_id", "id"],
        ["risk_reservations.id", "risk_reservations.risk_decision_id"],
        name="fk_decision_reservation",
        ondelete="RESTRICT",
        deferrable=True,
        use_alter=True,
        initially="DEFERRED",
    )
)

position_versions = Table(
    "position_versions",
    metadata,
    Column(
        "position_id",
        String(255),
        ForeignKey("positions.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("ledger_version", Integer, primary_key=True),
    Column(
        "instrument_id",
        String(255),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("currency", String(255), nullable=False),
    Column("quantity", Numeric(38, 18, asdecimal=True), nullable=False),
    Column("average_price", Numeric(38, 18, asdecimal=True)),
    Column("realized_pnl", Numeric(38, 18, asdecimal=True), nullable=False),
)
TABLES[models.MarketState].append_column(Column("position_ledger_version", Integer, nullable=False))
TABLES[models.MarketState].append_constraint(
    ForeignKeyConstraint(
        ["position_id", "position_ledger_version"],
        ["position_versions.position_id", "position_versions.ledger_version"],
        name="fk_market_state_position_version",
        ondelete="RESTRICT",
    )
)
