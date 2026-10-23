"""Account-serialized, fail-closed PostgreSQL paper reservations.

Only the execution role may call this module. It has no BrokerAdapter or credentials.
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import select, text

from packages.domain import models as m
from packages.observability.outbox import persist
from packages.persistence.ledger import PostgresLedger
from packages.persistence.schema import TABLES, accounts
from packages.risk.budget import budget_reasons, read_cap
from packages.risk.engine import RiskFacts, RiskLimits, evaluate
from packages.risk.paper_daily import assess_daily_loss
from packages.risk.paper_daily_sql import is_isolated_paper, read_daily
from packages.risk.paper_rounding import exposure_upper, pnl_lower

_NAMESPACE = uuid.UUID("b1429754-6ca2-455c-a126-c51c8cb0d078")
_OPEN = (
    "CREATED",
    "RISK_RESERVED",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "CANCEL_PENDING",
    "UNKNOWN",
)


def _id(kind: str, proposal_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{proposal_id}"))


def _exact_numeric(value: Decimal) -> Decimal:
    """Remove only fractional trailing zeros; reject any lost nonzero digit."""
    exact_text = format(value, "f")
    if "." in exact_text:
        exact_text = exact_text.rstrip("0").rstrip(".")
    return m.decimal_value(exact_text)


def reserve(
    *,
    ledger: PostgresLedger,
    account_id: str,
    proposal_id: str,
    limits: RiskLimits,
) -> m.RiskDecision:
    """Persist one decision, optional reservation and intent, audit and outbox atomically.

    The caller supplies only identifiers and versioned configuration. Time and
    every economic/context fact come from PostgreSQL after the account lock.
    """
    with ledger.transaction() as tx:
        connection = tx.connection
        account_rows = connection.execute(select(accounts.c.id, accounts.c.currency)).all()
        if len(account_rows) != 1 or account_rows[0].id != account_id:
            raise ValueError("paper account is missing, ambiguous, or mismatched")
        tx.lock_account(account_id)
        now = m.utc_time(connection.execute(text("SELECT clock_timestamp()")).scalar_one())
        paper_daily_required = is_isolated_paper(connection)
        account_currency = account_rows[0].currency
        # Any unassigned ownership is unsafe. Include terminal rows because historic
        # ambiguity cannot be silently attributed to this account.
        for table in ("positions", "risk_reservations", "order_intents"):
            if connection.execute(
                text(f"SELECT EXISTS(SELECT 1 FROM public.{table} WHERE account_id IS NULL) ")
            ).scalar_one():
                raise ValueError("unassigned economic ledger row")
        proposal = tx.get(m.TradeProposal, proposal_id)
        if proposal is None:
            raise ValueError("proposal is not durable")
        prior_id = connection.execute(
            select(TABLES[m.RiskDecision].c.id).where(
                TABLES[m.RiskDecision].c.proposal_id == proposal_id
            )
        ).scalar_one_or_none()
        if prior_id is not None:
            return tx.get(m.RiskDecision, prior_id)
        # Position locks follow account lock and stable identity order.
        position_ids = (
            connection.execute(
                text("""
            SELECT id FROM public.positions WHERE account_id=:account ORDER BY id FOR UPDATE
        """),
                {"account": account_id},
            )
            .scalars()
            .all()
        )
        positions = [tx.get(m.Position, item) for item in position_ids]
        policy_id = connection.execute(
            text("""
            SELECT id FROM public.risk_policies ORDER BY version DESC LIMIT 1
        """)
        ).scalar_one_or_none()
        if policy_id is None:
            raise ValueError("no durable risk policy")
        policy = tx.get(m.RiskPolicy, policy_id)
        if policy is None or policy.currency != account_currency or policy.effective_at > now:
            raise ValueError("risk policy unavailable")
        account_context = (
            connection.execute(
                text("""
            SELECT * FROM public.risk_account_context WHERE account_id=:account FOR UPDATE
        """),
                {"account": account_id},
            )
            .mappings()
            .first()
        )
        instrument_context = (
            connection.execute(
                text("""
            SELECT * FROM public.risk_instrument_context
            WHERE account_id=:account AND instrument_id=:instrument FOR UPDATE
        """),
                {"account": account_id, "instrument": proposal.instrument_id},
            )
            .mappings()
            .first()
        )
        reasons: tuple[str, ...]
        facts: RiskFacts | None = None
        snapshot: m.PortfolioSnapshot | None = None
        if account_context is None or instrument_context is None:
            reasons = ("RISK_CONTEXT_MISSING",)
        elif account_context["valid_until"] <= now or instrument_context["valid_until"] <= now:
            reasons = ("RISK_CONTEXT_STALE",)
        elif account_context["risk_config_version"] != limits.config_version:
            reasons = ("RISK_CONFIG_VERSION",)
        elif not paper_daily_required and account_context["day_start_at"].astimezone(
            UTC
        ) != now.replace(hour=0, minute=0, second=0, microsecond=0):
            reasons = ("DAY_START_STALE",)
        else:
            snapshot = tx.get(m.PortfolioSnapshot, account_context["portfolio_snapshot_id"])
            state = tx.get(m.MarketState, proposal.market_state_id)
            decision = tx.get(m.DecisionResult, proposal.decision_id)
            if snapshot is None or state is None or decision is None:
                reasons = ("SOURCE_MISSING",)
            elif (
                snapshot.quality is not m.Quality.VALID
                or snapshot.reconciliation is not m.Reconciliation.CURRENT
                or snapshot.currency != account_currency
                or snapshot.id != state.portfolio.id
                or snapshot.ledger_version != account_context["portfolio_version"]
                or snapshot.as_of > now
                or now - snapshot.as_of > timedelta(seconds=limits.market_max_age_seconds)
                or decision.valid_until <= now
                or decision.market_state_id != state.id
                or proposal.trace_id != state.trace_id
                or state.instrument_id != proposal.instrument_id
                or state.evidence.quality is not m.Quality.VALID
                or decision.choice.value != proposal.target.value
                or decision.typed_answers is None
            ):
                reasons = ("PORTFOLIO_OR_SOURCE_STALE",)
            elif policy.version > 0 and (
                policy.max_positions_per_instrument > limits.max_positions_per_instrument
                or policy.max_positions_total > limits.max_positions_total
                or policy.max_position_fraction > limits.max_position_fraction
                or policy.max_daily_loss_fraction > limits.max_daily_loss_fraction
            ):
                reasons = ("POLICY_LIMIT_MISMATCH",)
            else:
                effective_limits = RiskLimits(
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
                if facts is not None:
                    reasons = evaluate(proposal, facts, effective_limits)
                    reasons += budget_reasons(proposal, facts, read_cap(connection, account_id))
        return _record(
            tx,
            account_id,
            proposal,
            policy,
            snapshot,
            account_currency,
            reasons,
            now,
            instrument_context["capability_version"] if instrument_context else None,
            facts,
        )


def _fresh_facts(
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
    limits,
    now,
):
    paper_daily_required = is_isolated_paper(connection)
    paper_reference, paper_observation = None, None
    if paper_daily_required:
        from packages.risk.paper_lifecycle import activation_reasons
        from packages.risk.paper_limits import paper_proposal_reasons

        lifecycle_blockers = activation_reasons(connection, tx, account_id, proposal, now)
        if lifecycle_blockers:
            return lifecycle_blockers, None
        policy_blockers = paper_proposal_reasons(connection, tx, proposal, state, now)
        if policy_blockers:
            return policy_blockers, None
        paper_reference, paper_observation = read_daily(
            connection,
            account_id,
            account_context["portfolio_version"],
        )
        assessment = assess_daily_loss(
            paper_reference,
            paper_observation,
            now=now,
            max_loss_fraction=limits.max_daily_loss_fraction,
        )
        blockers = tuple(code for code in assessment.reasons if code != "DAILY_LOSS_LIMIT")
        if blockers:
            return blockers, None
        if (
            account_context["day_start_at"] != paper_reference.starts_at
            or account_context["day_start_equity"] != paper_reference.capital_lower
            or snapshot.equity != paper_observation.current_equity.lower
        ):
            return ("PAPER_DAILY_EVIDENCE_UNPROVEN",), None
    rows = (
        connection.execute(
            text("""
        SELECT r.proposal_id,r.currency,r.reserved_notional,r.state,
               p.instrument_id,p.target,p.quantity,
               i.id AS intent_id,i.side,i.quantity AS intent_quantity,i.limit_price,
               b.status,b.filled_quantity
        FROM public.risk_reservations r
        JOIN public.trade_proposals p ON p.id=r.proposal_id
        LEFT JOIN public.order_intents i ON i.proposal_id=p.id
        LEFT JOIN public.broker_orders b ON b.intent_id=i.id
        WHERE r.account_id=:account AND r.state IN ('ACTIVE','HELD_UNKNOWN')
        ORDER BY r.id
    """),
            {"account": account_id},
        )
        .mappings()
        .all()
    )
    # An order without a reservation is unbounded economic exposure.
    orphan = connection.execute(
        text("""
        SELECT EXISTS(
          SELECT 1 FROM public.order_intents i
          LEFT JOIN public.risk_reservations r ON r.proposal_id=i.proposal_id
          LEFT JOIN public.broker_orders b ON b.intent_id=i.id
          WHERE i.account_id=:account AND
            (r.id IS NULL OR (b.status IN
              ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
               'PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')
               AND r.state NOT IN ('ACTIVE','HELD_UNKNOWN')))
        )
    """),
        {"account": account_id},
    ).scalar_one()
    if orphan:
        return ("ORPHAN_INTENT",), None
    instrument = tx.get(m.Instrument, proposal.instrument_id)
    if instrument is None or instrument.currency != snapshot.currency:
        return ("CURRENCY_MISMATCH",), None
    if any(row["currency"] != snapshot.currency for row in rows):
        return ("CURRENCY_MISMATCH",), None
    held: dict[str, Decimal] = {}
    unrealized = Decimal("0")
    realized = Decimal("0")
    gross_notional = Decimal("0")
    with localcontext() as context:
        context.prec = 80
        for position in positions:
            if position.currency != snapshot.currency:
                return ("CURRENCY_MISMATCH",), None
            realized += position.realized_pnl
            if position.quantity == 0:
                continue
            mark = connection.execute(
                text("""
                SELECT ms.id FROM public.market_states ms
                WHERE ms.instrument_id=:instrument AND ms.quality='VALID'
                ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1
            """),
                {"instrument": position.instrument_id},
            ).scalar_one_or_none()
            marked = tx.get(m.MarketState, mark) if mark else None
            if (
                marked is None
                or marked.available_at > now
                or now - marked.available_at > timedelta(seconds=limits.market_max_age_seconds)
                or position.average_price is None
            ):
                return ("POSITION_MARK_STALE",), None
            held[position.instrument_id] = (
                held.get(position.instrument_id, Decimal("0")) + position.quantity
            )
            if paper_daily_required:
                basis = connection.execute(
                    text("SELECT public.paper_cost_basis_read(:id,:version)"),
                    {"id": position.id, "version": position.ledger_version},
                ).scalar_one_or_none()
                if basis is None:
                    return ("PAPER_POSITION_COST_BASIS_MISSING",), None
                unrealized += position.quantity * marked.bar.close - basis
            else:
                unrealized += position.quantity * (marked.bar.close - position.average_price)
            gross_notional += position.quantity * marked.bar.close
        exposure = {
            instrument: qty * state.bar.close
            for instrument, qty in held.items()
            if instrument == proposal.instrument_id
        }
        reserved_total = Decimal("0")
        for row in rows:
            if row["intent_id"] is None or row["status"] is None:
                remaining = row["reserved_notional"]
            elif row["status"] in _OPEN:
                # Reservation is authoritative for this same intent; never add both.
                remaining = row["reserved_notional"]
            else:
                remaining = Decimal("0")
            if row["target"] == "LONG" and remaining > 0:
                reserved_total += remaining
                gross_notional += remaining
                exposure[row["instrument_id"]] = (
                    exposure.get(row["instrument_id"], Decimal("0")) + remaining
                )
        active_instruments = set(held)
        active_instruments.update(key for key, value in exposure.items() if value > 0)
        open_exit = sum(
            (
                row["quantity"] - (row["filled_quantity"] or Decimal("0"))
                for row in rows
                if row["instrument_id"] == proposal.instrument_id
                and row["target"] == "FLAT"
                and (row["status"] is None or row["status"] in _OPEN)
            ),
            Decimal("0"),
        )
        # Alpaca's SQL context is published from a complete Trading capability.
        # A false Alpaca bit therefore means UNAVAILABLE; legacy Fake contexts
        # retain UNKNOWN for their unconfirmed bits.
        unconfirmed = (
            m.Capability.UNAVAILABLE
            if instrument_context["broker_id"] == "alpaca-paper"
            else m.Capability.UNKNOWN
        )
        capability = m.BrokerCapabilities(
            broker_id=instrument_context["broker_id"],
            instrument_id=proposal.instrument_id,
            version=instrument_context["capability_version"],
            paper=m.Capability.CONFIRMED if instrument_context["paper_confirmed"] else unconfirmed,
            regular_session=m.Capability.CONFIRMED
            if instrument_context["regular_session_confirmed"]
            else unconfirmed,
            continuous_session=m.Capability.CONFIRMED
            if instrument_context.get("continuous_session_confirmed", False)
            else unconfirmed,
            fractional=m.Capability.CONFIRMED
            if instrument_context["fractional_confirmed"]
            else unconfirmed,
            market_order=m.Capability.CONFIRMED
            if instrument_context["market_order_confirmed"]
            else unconfirmed,
            limit_order=m.Capability.CONFIRMED
            if instrument_context["limit_order_confirmed"]
            else unconfirmed,
            quantity_step=instrument_context["quantity_step"],
            price_tick=instrument_context["price_tick"],
        )
        facts = RiskFacts(
            now=now,
            mode=m.Mode(account_context["mode"]),
            safety_state=m.SafetyState(account_context["safety_state"]),
            policy_version=policy.version,
            portfolio_version=account_context["portfolio_version"],
            proposal_portfolio_version=state.portfolio.ledger_version,
            capability_version=instrument_context["capability_version"],
            capability=capability,
            session_open=instrument_context["session_open"],
            session_valid_until=instrument_context["session_valid_until"],
            market_quality=state.quality,
            market_available_at=state.available_at,
            market_price=state.bar.close,
            spread=state.spread,
            equity=snapshot.equity,
            cash=snapshot.cash,
            day_start_equity=account_context["day_start_equity"],
            realized_pnl_today=_exact_numeric(realized - account_context["day_start_realized_pnl"]),
            unrealized_pnl=(pnl_lower if paper_daily_required else _exact_numeric)(unrealized),
            instrument_exposure=(exposure_upper if paper_daily_required else _exact_numeric)(
                exposure.get(proposal.instrument_id, Decimal("0"))
            ),
            gross_notional=(exposure_upper if paper_daily_required else _exact_numeric)(
                gross_notional
            ),
            reserved_notional=reserved_total,
            position_count_instrument=int(proposal.instrument_id in active_instruments),
            position_count_total=len(active_instruments),
            open_exit_quantity=open_exit,
            held_quantity=held.get(proposal.instrument_id, Decimal("0")),
            reconciliation_current=account_context["reconciliation_current"],
            market=state.market,
            paper_daily_required=paper_daily_required,
            paper_reference=paper_reference,
            paper_observation=paper_observation,
        )
        return (), facts


def _record(
    tx, account_id, proposal, policy, portfolio, currency, reasons, now, capability_version, facts
):
    approved = not reasons
    decision_id = _id("risk-decision", proposal.id)
    reservation_id = _id("reservation", proposal.id) if approved else None
    expiry = (
        min(proposal.valid_until, now + timedelta(seconds=30))
        if approved
        else now + timedelta(seconds=1)
    )
    exposure = m.ExposureSnapshot(
        currency=currency,
        equity=portfolio.equity if portfolio else Decimal("0"),
        gross_notional=facts.gross_notional if facts else Decimal("0"),
        reserved_notional=facts.reserved_notional if facts else Decimal("0"),
    )
    reason_codes = reasons or ("APPROVED",)
    decision = m.RiskDecision(
        id=decision_id,
        proposal_id=proposal.id,
        approved=approved,
        reason_code=reason_codes[0],
        reason_codes=reason_codes,
        policy_version=policy.version,
        exposure_snapshot=exposure,
        decided_at=now,
        valid_until=expiry,
        trace_id=proposal.trace_id,
        reservation_id=reservation_id,
    )
    tx.insert(
        m.AuditEvent(
            id=_id("risk-audit", proposal.id),
            occurred_at=now,
            actor_id="execution-risk",
            action="risk.decide",
            reason_codes=reason_codes,
            entity_id=decision_id,
            entity_version=1,
            trace_id=proposal.trace_id,
            policy_version=policy.version,
            outcome="APPROVED" if approved else "DENIED",
        )
    )
    tx.insert(decision)
    persist(
        tx.connection,
        m.EventEnvelope(
            event_id=_id("risk-outbox", proposal.id),
            event_type="risk.decided.v1",
            occurred_at=now,
            aggregate_id=decision.id,
            aggregate_version=1,
            trace_id=proposal.trace_id,
            payload=decision,
        ),
    )
    if approved:
        with localcontext() as context:
            context.prec = 80
            notional = (
                proposal.quantity * (proposal.limit_price or facts.market_price + facts.spread)
                if proposal.target is m.Target.LONG
                else Decimal("0")
            )
        notional = _exact_numeric(notional)
        reservation = m.RiskReservation(
            id=reservation_id,
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            currency=currency,
            reserved_notional=notional,
            state=m.ReservationState.ACTIVE,
            version=1,
            created_at=now,
            trace_id=proposal.trace_id,
        )
        tx.insert(
            m.AuditEvent(
                id=_id("reservation-audit", proposal.id),
                occurred_at=now,
                actor_id="execution-risk",
                action="risk.reserve",
                reason_codes=("APPROVED",),
                entity_id=reservation.id,
                entity_version=1,
                trace_id=proposal.trace_id,
                policy_version=policy.version,
                outcome="ACTIVE",
            )
        )
        tx.insert(reservation)
        tx.connection.execute(
            text("UPDATE public.risk_reservations SET account_id=:account WHERE id=:id"),
            {"account": account_id, "id": reservation.id},
        )
        intent = m.OrderIntent(
            id=_id("intent", proposal.id),
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            client_order_id=_id("client-order", proposal.id),
            instrument_id=proposal.instrument_id,
            side="BUY" if proposal.target is m.Target.LONG else "SELL",
            order_type="LIMIT" if proposal.limit_price is not None else "MARKET",
            quantity=proposal.quantity,
            capability_version=capability_version,
            created_at=now,
            trace_id=proposal.trace_id,
            limit_price=proposal.limit_price,
        )
        tx.insert(
            m.AuditEvent(
                id=_id("intent-audit", proposal.id),
                occurred_at=now,
                actor_id="execution-risk",
                action="intent.create",
                reason_codes=("APPROVED",),
                entity_id=intent.id,
                entity_version=1,
                trace_id=proposal.trace_id,
                policy_version=policy.version,
                outcome="CREATED",
            )
        )
        tx.insert(intent)
        tx.connection.execute(
            text("UPDATE public.order_intents SET account_id=:account WHERE id=:id"),
            {"account": account_id, "id": intent.id},
        )
        persist(
            tx.connection,
            m.EventEnvelope(
                event_id=_id("reservation-outbox", proposal.id),
                event_type="risk.reserved.v1",
                occurred_at=now,
                aggregate_id=reservation.id,
                aggregate_version=1,
                trace_id=proposal.trace_id,
                payload=reservation,
            ),
        )
    return decision
