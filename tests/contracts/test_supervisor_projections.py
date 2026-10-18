"""Public DTOs expose only normalized, contract-declared evidence."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain import models as m
from services.control_api.projections import project_resource

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
TRACE = "a" * 32


@pytest.mark.parametrize("planner_version", [
    "runtime-planner-v1", "paper-smoke-limit.v1", "ptv13-fixed-one-v1", "private-canary"
])
def test_proposal_keeps_canonical_reasons_without_exposing_planner_provenance(planner_version):
    proposal = m.TradeProposal(
        "proposal-1", "decision-1", "state-1", "SPY", m.Target.LONG, Decimal("1"),
        NOW, NOW + timedelta(seconds=30), TRACE,
        ("BASELINE_SIGNAL", planner_version, "PAPER_SMOKE_LIMIT", "OK"),
    )
    public = project_resource("proposals", proposal)
    assert public["rationaleCodes"] == ["OK"]
    assert planner_version not in repr(public)
    assert proposal.rationale_codes[1] == planner_version


def test_proposal_does_not_invent_a_successful_primary_reason():
    proposal = m.TradeProposal(
        "proposal-1", "decision-1", "state-1", "SPY", m.Target.LONG, Decimal("1"),
        NOW, NOW + timedelta(seconds=30), TRACE, ("private-canary", "OK"),
    )
    with pytest.raises(LookupError, match="reason code unavailable"):
        project_resource("proposals", proposal)


@pytest.mark.parametrize("action,reason,outcome,expected_outcome", [
    ("safety.portfolio_refresh", "BROKER_RECONCILED", "CURRENT", "OBSERVED"),
    ("safety.account_refresh", "BROKER_RECONCILED", "CURRENT", "OBSERVED"),
    ("safety.resume", "BROKER_RECONCILED", "RUNNING", "ACCEPTED"),
    ("execution.submit_result", "ACKNOWLEDGED", "ACKNOWLEDGED", "OBSERVED"),
    ("execution.submit_result", "ACKNOWLEDGED", "SUBMIT_RESULT", "OBSERVED"),
    ("execution.submit_result", "SUBMIT_UNCERTAIN", "SUBMIT_RESULT", "OBSERVED"),
    ("execution.report", "ACKNOWLEDGED", "ACKNOWLEDGED", "OBSERVED"),
])
def test_confirmed_recovery_and_ack_audits_have_canonical_public_reasons(
    action, reason, outcome, expected_outcome,
):
    public = project_resource("audit", {
        "id": "audit-recovery", "occurred_at": NOW, "actor_id": "execution-recovery",
        "action": action, "reason_codes": (reason,), "outcome": outcome,
        "trace_id": TRACE, "private_payload": "private-canary",
    })
    expected_reason = "BROKER_STATUS_UNKNOWN" if reason == "SUBMIT_UNCERTAIN" else "OK"
    assert public["reasonCode"] == expected_reason
    assert public["outcome"] == expected_outcome
    assert "private-canary" not in repr(public)


@pytest.mark.parametrize("action", ["runtime.initialize", "risk.policy.initialize"])
def test_disabled_fake_bootstrap_audit_is_observed_without_invented_trading_authority(action):
    public = project_resource("audit", {
        "id": "initial-audit", "occurred_at": NOW, "actor_id": "local-fake-provisioner",
        "action": action, "reason_codes": ("SIMULATION_BOOTSTRAP",), "outcome": None,
        "trace_id": TRACE,
    })
    assert public["reasonCode"] == "MODE_NOT_PAPER"
    assert public["outcome"] == "OBSERVED"


def test_unknown_private_audit_metadata_is_still_not_published():
    with pytest.raises(LookupError, match="audit action unavailable"):
        project_resource("audit", {
            "id": "unknown-audit", "occurred_at": NOW, "actor_id": "execution",
            "action": "private-canary", "reason_codes": ("OK",), "outcome": "OBSERVED",
            "trace_id": TRACE,
        })


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


@pytest.mark.parametrize(
    "action", ["binding.account", "binding.asset", "binding.proof", "binding.context"]
)
def test_private_provider_binding_audit_has_safe_public_projection(action):
    record = {
        "id": "binding-audit",
        "occurred_at": NOW,
        "actor_id": "execution-broker-binding",
        "action": action,
        "reason_codes": ("PROVIDER_PROOF_CONFIRMED",),
        "outcome": "CONFIRMED",
        "trace_id": TRACE,
        "provider_account_id": "private-provider-canary",
        "provider_asset_id": "private-asset-canary",
    }
    public = project_resource("audit", record)
    assert public["actor"] == "execution"
    assert public["outcome"] == "OBSERVED" and public["reasonCode"] == "OK"
    assert "private-" not in str(public)
    denied = project_resource(
        "audit",
        {
            **record,
            "action": "execution.submit_result",
            "reason_codes": ("BINDING_PROOF_STALE",),
            "outcome": "PRE_IO_REJECTED",
        },
    )
    assert denied["outcome"] == "REFUSED"
    assert denied["reasonCode"] == "RISK_CAPABILITY_UNCONFIRMED"


@pytest.mark.parametrize(
    "phase",
    [
        "baseline",
        "receipt",
        "position",
        "portfolio",
        "account",
        "activity",
        "order",
        "order_basis",
        "report",
        "reservation",
    ],
)
def test_paper_accounting_audit_projects_without_private_activity_or_provider_ids(phase):
    public = project_resource(
        "audit",
        {
            "id": "audit-paper",
            "occurred_at": NOW,
            "actor_id": "execution-reconciliation",
            "action": f"execution.paper_{phase}",
            "reason_codes": ("PAPER_ACTIVITY_BOOKED",),
            "outcome": "PAPER_ACTIVITY_BOOKED",
            "trace_id": TRACE,
            "activity_id": "private-canary::activity",
            "provider_account_id": "private-canary",
            "fee_authority_profile": "private-canary",
        },
    )
    assert public["actor"] == "execution" and public["reasonCode"] == "OK"
    assert public["outcome"] == "OBSERVED" and "private-canary" not in str(public)


@pytest.mark.parametrize(
    "reason",
    [
        "PAPER_FEE_AUTHORITY_UNKNOWN",
        "BROKER_ACTIVITY_IDENTITY_DRIFT",
        "FILL_LIMIT_PRICE_MISMATCH",
        "CRYPTO_FEE_QUANTITY_MISMATCH",
    ],
)
def test_paper_denial_projects_as_reconciliation_required(reason):
    public = project_resource(
        "audit",
        {
            "id": "audit-denial",
            "occurred_at": NOW,
            "actor_id": "execution-recovery",
            "action": "safety.resume_denied",
            "reason_codes": (reason,),
            "outcome": "DENIED",
            "trace_id": TRACE,
        },
    )
    assert public["outcome"] == "REFUSED"
    assert public["reasonCode"] == "RISK_RECONCILIATION_REQUIRED"


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
