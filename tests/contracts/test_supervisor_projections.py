"""Public DTOs expose only normalized, contract-declared evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain import models as m
from services.control_api.projections import project_resource

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
TRACE = "a" * 32


def test_order_projection_discards_broker_and_secret_fields():
    public = project_resource(
        "orders",
        {
            "id": "order-1",
            "proposal_id": "proposal-1",
            "risk_decision_id": "risk-1",
            "instrument_id": "SPY",
            "status": "PARTIALLY_FILLED",
            "requested_quantity": Decimal("2.000"),
            "filled_quantity": Decimal("0.500"),
            "average_fill_price": Decimal("512.125"),
            "reconciliation": "PENDING",
            "updated_at": NOW,
            "trace_id": TRACE,
            "broker_order_id": "secret-broker-ref",
            "adapter": "secret-provider",
            "raw_payload": {"api_key": "secret-canary"},
        },
    )
    assert public == {
        "id": "order-1",
        "proposalId": "proposal-1",
        "riskDecisionId": "risk-1",
        "instrument": "SPY",
        "status": "PARTIALLY_FILLED",
        "requestedQuantity": "2.000",
        "filledQuantity": "0.500",
        "averageFillPrice": "512.125",
        "reconciliation": "PENDING",
        "updatedAt": "2026-10-16T12:00:00Z",
        "traceId": TRACE,
    }


def test_decision_abstention_and_unknown_cost_are_explicit():
    decision = m.DecisionResult(
        "decision-1",
        "state-1",
        m.Choice.ABSTAIN,
        None,
        "prompt-v1",
        "question-v1",
        "config-v1",
        "b" * 64,
        12,
        NOW + timedelta(seconds=30),
        TRACE,
        "ABSTAIN_UNAVAILABLE",
        input_tokens=18,
        output_tokens=4,
    )
    public = project_resource("decisions", decision)
    assert public["choice"] == "ABSTAIN"
    assert public["typedAnswers"] is None
    assert public["estimatedCost"] is None
    assert public["inputTokens"] == 18 and public["outputTokens"] == 4
    cost = project_resource(
        "costs",
        {
            "id": decision.id,
            "provider": "JEV",
            "observed_at": NOW,
            "currency": "EUR",
            "amount": None,
            "trace_id": TRACE,
            "raw_provider_response": "secret-canary",
        },
    )
    assert cost == {
        "id": "decision-1",
        "provider": "JEV",
        "observedAt": "2026-10-16T12:00:00Z",
        "currency": "EUR",
        "amount": None,
        "traceId": TRACE,
    }


def test_run_requires_authoritative_role_and_start():
    base = {"id": "run-1", "status": "RUNNING", "heartbeat_at": NOW, "trace_id": TRACE}
    with pytest.raises(LookupError):
        project_resource("runs", base)
    public = project_resource(
        "runs",
        {
            **base,
            "role": "WORKER",
            "started_at": NOW - timedelta(minutes=1),
            "input_hash": "secret-canary",
        },
    )
    assert public["startedAt"] == "2026-10-16T11:59:00Z"
    assert set(public) == {"id", "role", "status", "startedAt", "heartbeatAt", "traceId"}


def test_audit_maps_actual_outcomes_without_exposing_actor_identity():
    base = {
        "id": "audit-1",
        "occurred_at": NOW,
        "actor_id": "name@example.invalid",
        "action": "risk.decide",
        "reason_codes": ("RISK_LIMIT_EXCEEDED",),
        "trace_id": TRACE,
        "policy_version": 3,
    }
    denied = project_resource("audit", {**base, "outcome": "DENIED"})
    assert denied["outcome"] == "REFUSED"
    assert denied["actor"] == "redacted-actor"
    worker = project_resource(
        "audit", {**base, "actor_id": "execution-risk", "outcome": "APPROVED"}
    )
    assert worker["actor"] == "execution" and worker["outcome"] == "ACCEPTED"
    assert denied["reasonCode"] == "RISK_LIMIT_EXCEEDED"
    assert project_resource("audit", {**base, "outcome": "PAPER"})["outcome"] == "ACCEPTED"
    with pytest.raises(LookupError):
        project_resource("audit", {**base, "outcome": "provider free text"})


def test_market_evidence_replaces_free_form_source_labels():
    bar = m.MarketBar(
        "bar-1",
        "SPY",
        NOW - timedelta(minutes=15),
        NOW,
        Decimal("1"),
        Decimal("2"),
        Decimal("1"),
        Decimal("2"),
        Decimal("4"),
        NOW,
        NOW,
        NOW,
        m.Quality.VALID,
    )
    evidence = m.EvidenceBundle(
        "ev-1", "SPY", ("secret-canary",), NOW, ("private conflict text",), m.Quality.CONFLICT
    )
    portfolio = m.PortfolioSnapshot(
        "p-1",
        "USD",
        Decimal("100"),
        Decimal("80"),
        Decimal("20"),
        NOW,
        1,
        m.Quality.VALID,
        m.Reconciliation.CURRENT,
    )
    position = m.Position("pos-1", "SPY", "USD", Decimal("1"), Decimal("2"), Decimal("0"), 1)
    state = m.MarketState(
        "state-1",
        "SPY",
        m.Market.US_EQUITY,
        bar,
        evidence,
        portfolio,
        position,
        {"momentum": Decimal("0.125")},
        Decimal("0.01"),
        NOW,
        NOW,
        NOW,
        NOW,
        m.Quality.VALID,
        TRACE,
    )
    public = project_resource("market-states", state)
    assert public["evidence"]["sources"] == ["market-data"]
    assert public["evidence"]["conflicts"] == ["CONFLICT"]
    assert public["features"] == {"momentum": "0.125"}
