"""Fresh, account-serialized checks for one already reserved Paper intent."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.domain import models as m
from packages.risk.budget import budget_reasons, read_cap
from packages.risk.engine import RiskLimits, evaluate
from packages.risk.reservations import _exact_numeric, _fresh_facts


class SubmitDenied(RuntimeError):
    """Stable, non-sensitive reason for a submit blocked before broker I/O."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SubmitDenied(reason)


def _now(connection):
    return m.utc_time(connection.execute(text("SELECT clock_timestamp()")).scalar_one())


def evaluate_submit_gate(
    *,
    tx,
    account_id: str,
    intent_id: str,
    limits: RiskLimits,
    owner_id: str,
    fencing_token: int,
    allow_submitting_order: bool = False,
    expected_broker_id: str = "fake",
):
    """Return the durable intent and fresh capability under the caller's account lock.

    The caller keeps that lock until the submit attempt and resulting ledger write
    finish. No broker method is invoked here.
    """
    connection = tx.connection
    _require(
        connection.execute(text("SELECT current_user")).scalar_one() == "atl_execution",
        "EXECUTION_ROLE_REQUIRED",
    )
    account = (
        connection.execute(
            text("SELECT id,currency FROM public.accounts WHERE id=:account"),
            {"account": account_id},
        )
        .mappings()
        .one_or_none()
    )
    _require(account is not None, "ACCOUNT_MISMATCH")
    _require(
        connection.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 1,
        "ACCOUNT_AMBIGUOUS",
    )
    tx.lock_account(account_id)
    from services.control_api.control import ControlDenied
    from services.execution.recovery_health import check_runtime

    try:
        check_runtime(connection, account_id, owner_id=owner_id, fencing_token=fencing_token)
    except ControlDenied as exc:
        raise SubmitDenied(exc.reason_code) from None
    intent = tx.get(m.OrderIntent, intent_id)
    _require(intent is not None, "INTENT_MISSING")
    account_context = (
        connection.execute(
            text("SELECT * FROM public.risk_account_context WHERE account_id=:account FOR UPDATE"),
            {"account": account_id},
        )
        .mappings()
        .one_or_none()
    )
    instrument_context = (
        connection.execute(
            text("""
            SELECT * FROM public.risk_instrument_context
            WHERE account_id=:account AND instrument_id=:instrument FOR UPDATE
        """),
            {"account": account_id, "instrument": intent.instrument_id},
        )
        .mappings()
        .one_or_none()
    )
    positions = [
        tx.get(m.Position, position_id)
        for position_id in connection.execute(
            text("""
                SELECT id FROM public.positions WHERE account_id=:account
                ORDER BY id FOR UPDATE
            """),
            {"account": account_id},
        ).scalars()
    ]
    now = _now(connection)
    try:
        check_runtime(connection, account_id, owner_id=owner_id, fencing_token=fencing_token)
    except ControlDenied as exc:
        raise SubmitDenied(exc.reason_code) from None
    owner = connection.execute(
        text("SELECT account_id FROM public.order_intents WHERE id=:id"), {"id": intent_id}
    ).scalar_one()
    _require(owner == account_id, "ACCOUNT_MISMATCH")
    proposal = tx.get(m.TradeProposal, intent.proposal_id)
    decision = tx.get(m.RiskDecision, intent.risk_decision_id)
    _require(proposal is not None and decision is not None, "RISK_LINK_MISSING")
    _require(
        decision.approved
        and decision.proposal_id == proposal.id
        and decision.reservation_id is not None
        and intent.proposal_id == proposal.id
        and intent.instrument_id == proposal.instrument_id
        and intent.trace_id == proposal.trace_id == decision.trace_id
        and intent.quantity == proposal.quantity
        and intent.limit_price == proposal.limit_price
        and intent.side == ("BUY" if proposal.target is m.Target.LONG else "SELL")
        and intent.order_type == ("LIMIT" if proposal.limit_price is not None else "MARKET"),
        "RISK_LINK_INVALID",
    )
    reservation = tx.get(m.RiskReservation, decision.reservation_id)
    _require(
        reservation is not None
        and reservation.proposal_id == proposal.id
        and reservation.risk_decision_id == decision.id
        and reservation.state is m.ReservationState.ACTIVE,
        "RESERVATION_INVALID",
    )
    reservation_owner = connection.execute(
        text("SELECT account_id FROM public.risk_reservations WHERE id=:id"),
        {"id": reservation.id},
    ).scalar_one()
    _require(reservation_owner == account_id, "ACCOUNT_MISMATCH")
    _require(now < proposal.valid_until and now < decision.valid_until, "EXPIRED")
    _require(
        decision.valid_until - decision.decided_at
        <= timedelta(seconds=limits.decision_ttl_seconds),
        "DECISION_TTL",
    )
    existing_order = (
        connection.execute(
            text("SELECT status,adapter FROM public.broker_orders WHERE intent_id=:id"),
            {"id": intent.id},
        )
        .mappings()
        .one_or_none()
    )
    unresolved_other = connection.execute(
        text("""
            SELECT EXISTS(
              SELECT 1 FROM public.broker_orders b
              JOIN public.order_intents i ON i.id=b.intent_id
              WHERE i.account_id=:account AND i.id<>:intent
                AND b.status IN ('SUBMITTING','UNKNOWN')
            )
        """),
        {"account": account_id, "intent": intent.id},
    ).scalar_one()
    _require(not unresolved_other, "UNRESOLVED_SUBMIT")
    _require(
        (
            existing_order is not None
            and existing_order["status"] == "SUBMITTING"
            and existing_order["adapter"] == expected_broker_id
        )
        if allow_submitting_order
        else existing_order is None,
        "RECONCILE_REQUIRED",
    )
    for table in ("positions", "risk_reservations", "order_intents"):
        _require(
            not connection.execute(
                text(f"SELECT EXISTS(SELECT 1 FROM public.{table} WHERE account_id IS NULL)")
            ).scalar_one(),
            "OWNERSHIP_UNASSIGNED",
        )
    policy_id = connection.execute(
        text("SELECT id FROM public.risk_policies ORDER BY version DESC LIMIT 1")
    ).scalar_one_or_none()
    policy = tx.get(m.RiskPolicy, policy_id) if policy_id else None
    _require(
        policy is not None
        and policy.version == decision.policy_version
        and policy.currency == account["currency"]
        and policy.effective_at <= now,
        "POLICY_CHANGED",
    )
    _require(account_context is not None, "ACCOUNT_CONTEXT_MISSING")
    _require(
        account_context["mode"] == m.Mode.PAPER.value
        and account_context["safety_state"] == m.SafetyState.RUNNING.value,
        "MODE_OR_SAFETY",
    )
    _require(account_context["reconciliation_current"], "RECONCILIATION")
    _require(
        account_context["valid_until"] > now
        and account_context["risk_config_version"] == limits.config_version
        and account_context["day_start_at"].astimezone(UTC)
        == now.replace(hour=0, minute=0, second=0, microsecond=0),
        "ACCOUNT_CONTEXT_STALE",
    )
    _require(instrument_context is not None, "CAPABILITY_MISSING")
    _require(
        instrument_context["valid_until"] > now
        and instrument_context["capability_version"] == intent.capability_version
        and instrument_context["broker_id"] == expected_broker_id,
        "CAPABILITY_CHANGED",
    )
    snapshot = tx.get(m.PortfolioSnapshot, account_context["portfolio_snapshot_id"])
    state = tx.get(m.MarketState, proposal.market_state_id)
    source_decision = tx.get(m.DecisionResult, proposal.decision_id)
    _require(
        snapshot is not None
        and state is not None
        and source_decision is not None
        and snapshot.quality is m.Quality.VALID
        and snapshot.reconciliation is m.Reconciliation.CURRENT
        and snapshot.currency == account["currency"]
        and snapshot.ledger_version == account_context["portfolio_version"]
        and snapshot.id == state.portfolio.id
        and snapshot.as_of <= now
        and now - snapshot.as_of <= timedelta(seconds=limits.market_max_age_seconds)
        and source_decision.valid_until > now
        and source_decision.market_state_id == state.id
        and source_decision.choice.value == proposal.target.value
        and source_decision.typed_answers is not None
        and state.instrument_id == proposal.instrument_id
        and state.trace_id == proposal.trace_id
        and state.evidence.quality is m.Quality.VALID
        and state.evidence.available_at <= now,
        "PORTFOLIO_OR_SOURCE_STALE",
    )
    with localcontext() as context:
        context.prec = 80
        required_notional = (
            _exact_numeric(
                proposal.quantity * (proposal.limit_price or state.bar.close + state.spread)
            )
            if proposal.target is m.Target.LONG
            else Decimal("0")
        )
    _require(
        reservation.currency == account["currency"]
        and reservation.reserved_notional == required_notional,
        "RESERVATION_AMOUNT_MISMATCH",
    )
    _require(
        policy.max_positions_per_instrument <= limits.max_positions_per_instrument
        and policy.max_positions_total <= limits.max_positions_total
        and policy.max_position_fraction <= limits.max_position_fraction
        and policy.max_daily_loss_fraction <= limits.max_daily_loss_fraction,
        "POLICY_LIMIT_MISMATCH",
    )
    effective_limits = RiskLimits(
        config_version=limits.config_version,
        max_positions_per_instrument=policy.max_positions_per_instrument,
        max_positions_total=policy.max_positions_total,
        max_position_fraction=policy.max_position_fraction,
        max_daily_loss_fraction=policy.max_daily_loss_fraction,
        max_spread_fraction=limits.max_spread_fraction,
        market_max_age_seconds=limits.market_max_age_seconds,
        decision_ttl_seconds=limits.decision_ttl_seconds,
    )
    reasons, facts = _fresh_facts(
        connection,
        tx,
        account_id,
        proposal,
        state,
        snapshot,
        positions,
        account_context,
        instrument_context,
        policy,
        effective_limits,
        now,
    )
    _require(not reasons and facts is not None, reasons[0] if reasons else "FACTS_MISSING")
    # _fresh_facts includes this intent's own reservation. Remove exactly that
    # already-approved amount before evaluating the candidate once more.
    if proposal.target is m.Target.LONG:
        other_long = connection.execute(
            text("""
                SELECT EXISTS(
                  SELECT 1 FROM public.risk_reservations r
                  JOIN public.trade_proposals p ON p.id=r.proposal_id
                  LEFT JOIN public.order_intents i ON i.proposal_id=p.id
                  LEFT JOIN public.broker_orders b ON b.intent_id=i.id
                  WHERE r.account_id=:account AND r.id<>:own
                    AND p.instrument_id=:instrument AND p.target='LONG'
                    AND r.state IN ('ACTIVE','HELD_UNKNOWN')
                    AND (b.status IS NULL OR b.status IN
                      ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
                       'PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN'))
                )
            """),
            {"account": account_id, "own": reservation.id, "instrument": proposal.instrument_id},
        ).scalar_one()
        held_here = any(
            position.instrument_id == proposal.instrument_id and position.quantity > 0
            for position in positions
        )
        sole_instrument = not other_long and not held_here
        with localcontext() as context:
            context.prec = 80
            facts = replace(
                facts,
                instrument_exposure=_exact_numeric(
                    facts.instrument_exposure - reservation.reserved_notional
                ),
                gross_notional=_exact_numeric(facts.gross_notional - reservation.reserved_notional),
                reserved_notional=_exact_numeric(
                    facts.reserved_notional - reservation.reserved_notional
                ),
                position_count_instrument=facts.position_count_instrument - int(sole_instrument),
                position_count_total=facts.position_count_total - int(sole_instrument),
            )
        _require(
            facts.instrument_exposure >= Decimal("0")
            and facts.gross_notional >= Decimal("0")
            and facts.reserved_notional >= Decimal("0")
            and facts.position_count_instrument >= 0
            and facts.position_count_total >= 0,
            "EXPOSURE_INCONSISTENT",
        )
    else:
        with localcontext() as context:
            context.prec = 80
            facts = replace(
                facts,
                open_exit_quantity=_exact_numeric(facts.open_exit_quantity - proposal.quantity),
            )
        _require(facts.open_exit_quantity >= Decimal("0"), "EXPOSURE_INCONSISTENT")
    reasons = evaluate(proposal, facts, effective_limits)
    reasons += budget_reasons(proposal, facts, read_cap(connection, account_id))
    _require(not reasons, reasons[0] if reasons else "GATE_DENIED")
    return intent, proposal, decision, reservation, facts.capability, now
