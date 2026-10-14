"""Execution-owned heartbeat fencing and authoritative local broker recovery."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, timedelta
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.brokers.fake import FakeBrokerAdapter
from packages.brokers.reports import event_hash, normalized_report
from packages.domain import models as m
from packages.persistence.ledger import PostgresLedger
from packages.risk.engine import RiskLimits
from services.control_api.control import ControlDenied, _now, _require, _role
from services.execution.reconciliation import _source


def claim_execution_owner(
    *, ledger: PostgresLedger, account_id: str, owner_id: str, trace_id: str
) -> int:
    """Claim a monotonically fenced Execution process and latch on takeover."""
    _require(bool(owner_id) and len(owner_id) <= 255, "OWNER_INVALID")
    _require(bool(re.fullmatch(r"[0-9a-f]{32}", trace_id)), "TRACE_INVALID")
    with ledger.transaction() as tx:
        c = tx.connection
        _role(c, "atl_execution")
        tx.lock_account(account_id)
        c.execute(
            text("SELECT public.safety_latch(:a,'PAUSE_NEW','execution-startup',:t,'PAUSED_NEW')"),
            {"a": account_id, "t": trace_id},
        )
        return c.execute(
            text("""
            INSERT INTO public.execution_owners
              (account_id,owner_id,fencing_token,claimed_at,heartbeat_at,trace_id)
            VALUES (:a,:o,1,clock_timestamp(),clock_timestamp(),:t)
            ON CONFLICT (account_id) DO UPDATE SET
              owner_id=EXCLUDED.owner_id,
              fencing_token=public.execution_owners.fencing_token+1,
              claimed_at=EXCLUDED.claimed_at,
              heartbeat_at=EXCLUDED.heartbeat_at,
              trace_id=EXCLUDED.trace_id
            RETURNING fencing_token
        """),
            {"a": account_id, "o": owner_id, "t": trace_id},
        ).scalar_one()


def heartbeat_execution_owner(
    *, ledger: PostgresLedger, account_id: str, owner_id: str, fencing_token: int
) -> None:
    """Only the current, still-live owner can extend its heartbeat."""
    with ledger.transaction() as tx:
        c = tx.connection
        _role(c, "atl_execution")
        tx.lock_account(account_id)
        updated = c.execute(
            text("""
            UPDATE public.execution_owners SET heartbeat_at=clock_timestamp()
            WHERE account_id=:a AND owner_id=:o AND fencing_token=:f
              AND heartbeat_at>=clock_timestamp()-interval '30 seconds'
        """),
            {"a": account_id, "o": owner_id, "f": fencing_token},
        ).rowcount
        _require(updated == 1, "OWNER_FENCED_OR_STALE")


def check_runtime(connection, account_id: str, *, owner_id: str, fencing_token: int) -> None:
    """Execution submit gate: DB, current owner, and fresh heartbeat are required."""
    _role(connection, "atl_execution")
    row = (
        connection.execute(
            text("""
        SELECT owner_id,fencing_token,heartbeat_at
        FROM public.execution_owners WHERE account_id=:a FOR UPDATE
    """),
            {"a": account_id},
        )
        .mappings()
        .one_or_none()
    )
    now = _now(connection)
    _require(row is not None, "EXECUTION_OWNER_MISSING")
    _require(
        row["owner_id"] == owner_id and row["fencing_token"] == fencing_token,
        "EXECUTION_OWNER_FENCED",
    )
    _require(now - timedelta(seconds=30) <= row["heartbeat_at"] <= now, "EXECUTION_HEARTBEAT_STALE")


def _exact(value: Decimal) -> Decimal:
    raw = format(value, "f")
    return m.decimal_value(raw.rstrip("0").rstrip(".") if "." in raw else raw)


def cancel_known_after_halt(
    *,
    ledger: PostgresLedger,
    account_id: str,
    broker: FakeBrokerAdapter,
    owner_id: str,
    fencing_token: int,
) -> int:
    """Best-effort Fake cancel after the persistent halt, never submit or flatten."""
    _require(type(broker) is FakeBrokerAdapter, "FAKE_BROKER_REQUIRED")
    requests = []
    with ledger.transaction() as tx:
        c = tx.connection
        _role(c, "atl_execution")
        tx.lock_account(account_id)
        check_runtime(c, account_id, owner_id=owner_id, fencing_token=fencing_token)
        _require(
            c.execute(
                text(
                    "SELECT safety_state FROM public.risk_account_context "
                    "WHERE account_id=:account FOR UPDATE"
                ),
                {"account": account_id},
            ).scalar_one()
            == "HALTED",
            "HALT_REQUIRED",
        )
        rows = c.execute(
            text("""
            SELECT b.id,b.client_order_id FROM public.broker_orders b
            JOIN public.order_intents i ON i.id=b.intent_id
            WHERE i.account_id=:account AND b.adapter='fake'
              AND b.status IN ('SUBMITTING','ACKNOWLEDGED','PARTIALLY_FILLED','UNKNOWN')
            ORDER BY b.id
        """),
            {"account": account_id},
        ).all()
        for row in rows:
            try:
                source = broker.order_status(row.client_order_id)
                if source is not None and source.status in (
                    m.OrderStatus.ACKNOWLEDGED,
                    m.OrderStatus.PARTIALLY_FILLED,
                ):
                    broker.cancel(row.client_order_id, event_id=str(uuid.uuid4()), confirmed=False)
                requests.append(row.id)
            except Exception:
                requests.append(row.id)
    from services.execution.reconciliation import reconcile_order

    for order_id in requests:
        reconcile_order(ledger=ledger, account_id=account_id, order_id=order_id, broker=broker)
    return len(requests)


def _verified_broker_orders(tx, account_id: str, broker: FakeBrokerAdapter) -> int:
    c = tx.connection
    db_rows = (
        c.execute(
            text("""
        SELECT b.id FROM public.broker_orders b
        JOIN public.order_intents i ON i.id=b.intent_id
        WHERE i.account_id=:a ORDER BY b.id FOR UPDATE OF b
    """),
            {"a": account_id},
        )
        .scalars()
        .all()
    )
    proven_local_rejections = set()
    for order_id in db_rows:
        local = tx.get(m.BrokerOrder, order_id)
        if (
            local.status is not m.OrderStatus.REJECTED
            or local.broker_order_id is not None
            or local.filled_quantity != 0
        ):
            continue
        proven = c.execute(
            text("""
            SELECT EXISTS(
              SELECT 1 FROM public.execution_reports r
              JOIN public.audit_events a ON a.entity_id=:order
              WHERE r.order_id=:order AND r.adapter='fake'
                AND r.status='REJECTED' AND r.filled_quantity=0
                AND r.reconciliation='CURRENT' AND r.broker_event_id IS NULL
                AND a.actor_id='execution-submit' AND a.action='execution.submit_result'
                AND a.outcome='PRE_IO_REJECTED' AND a.reason_codes ? r.reason_code
            )
        """),
            {"order": order_id},
        ).scalar_one()
        if proven:
            proven_local_rejections.add(order_id)
    db_rows = [order_id for order_id in db_rows if order_id not in proven_local_rejections]
    try:
        source_orders = broker.all_orders()
    except Exception as exc:
        raise ControlDenied("BROKER_UNAVAILABLE") from exc
    _require(
        type(source_orders) is tuple and len(source_orders) == len(db_rows),
        "BROKER_INVENTORY_MISMATCH",
    )
    source_by_client = {
        order.client_order_id: order for order in source_orders if type(order) is m.BrokerOrder
    }
    _require(len(source_by_client) == len(db_rows), "BROKER_INVENTORY_MISMATCH")
    for order_id in db_rows:
        order = tx.get(m.BrokerOrder, order_id)
        intent = tx.get(m.OrderIntent, order.intent_id)
        _require(
            intent is not None and order.client_order_id == intent.client_order_id,
            "BROKER_INTENT_MISMATCH",
        )
        source, reports, failure = _source(broker, order)
        _require(failure is None and source is not None, "BROKER_ORDER_UNRESOLVED")
        _require(
            source_by_client.get(order.client_order_id) == source
            and source.intent_id == intent.id
            and source.trace_id == intent.trace_id
            and source.broker_order_id == order.broker_order_id
            and source.status == order.status
            and source.filled_quantity == order.filled_quantity
            and source.requested_quantity == order.requested_quantity
            and order.status not in (m.OrderStatus.UNKNOWN, m.OrderStatus.SUBMITTING),
            "BROKER_ORDER_MISMATCH",
        )
        _require(bool(reports), "BROKER_REPORT_MISSING")
        seen = set()
        for raw in reports:
            try:
                report = normalized_report(
                    raw,
                    source_order_id=source.id,
                    local_order_id=order.id,
                    requested_quantity=intent.quantity,
                    trace_id=intent.trace_id,
                )
                digest = event_hash(report)
            except ValueError as exc:
                raise ControlDenied("BROKER_REPORT_INVALID") from exc
            _require(report.broker_event_id not in seen, "BROKER_REPORT_DUPLICATE")
            seen.add(report.broker_event_id)
            existing_id = c.execute(
                text("""
                SELECT id FROM public.execution_reports
                WHERE adapter='fake' AND broker_event_id=:e
            """),
                {"e": report.broker_event_id},
            ).scalar_one_or_none()
            _require(
                existing_id is not None
                and event_hash(tx.get(m.ExecutionReport, existing_id)) == digest,
                "BROKER_REPORT_NOT_RECONCILED",
            )
        stored_count = c.execute(
            text("""
            SELECT count(*) FROM public.execution_reports
            WHERE order_id=:o AND adapter='fake' AND broker_event_id IS NOT NULL
        """),
            {"o": order.id},
        ).scalar_one()
        _require(stored_count == len(seen), "BROKER_REPORT_INVENTORY_MISMATCH")
    return len(db_rows)


def _verified_positions(
    tx, account_id: str, broker: FakeBrokerAdapter, now, limits: RiskLimits
) -> tuple[Decimal, int]:
    c = tx.connection
    rows = (
        c.execute(
            text("""
        SELECT p.instrument_id,p.quantity,p.currency
        FROM public.positions p WHERE p.account_id=:a
        ORDER BY p.instrument_id,p.id FOR UPDATE
    """),
            {"a": account_id},
        )
        .mappings()
        .all()
    )
    _require(len({row["instrument_id"] for row in rows}) == len(rows), "POSITION_AMBIGUOUS")
    try:
        holdings = broker.all_holdings()
    except Exception as exc:
        raise ControlDenied("BROKER_UNAVAILABLE") from exc
    _require(
        type(holdings) is dict
        and all(type(key) is str and type(value) is Decimal for key, value in holdings.items()),
        "BROKER_HOLDINGS_INVALID",
    )
    owned = {row["instrument_id"]: row["quantity"] for row in rows}
    _require(
        {k: v for k, v in holdings.items() if v != 0} == {k: v for k, v in owned.items() if v != 0},
        "BROKER_POSITION_MISMATCH",
    )
    instruments = (
        c.execute(
            text("""
        SELECT * FROM public.risk_instrument_context
        WHERE account_id=:a ORDER BY instrument_id FOR UPDATE
    """),
            {"a": account_id},
        )
        .mappings()
        .all()
    )
    _require(
        bool(instruments) and set(owned).issubset({item["instrument_id"] for item in instruments}),
        "INSTRUMENT_CONTEXT_MISSING",
    )
    exposure = Decimal("0")
    with localcontext() as ctx:
        ctx.prec = 80
        for instrument in instruments:
            instrument_id = instrument["instrument_id"]
            _require(
                instrument["broker_id"] == "fake"
                and instrument["valid_until"] > now
                and instrument["session_valid_until"] > now
                and instrument["session_open"]
                and instrument["paper_confirmed"]
                and instrument["regular_session_confirmed"]
                and instrument["market_order_confirmed"],
                "CAPABILITY_OR_SESSION_STALE",
            )
            try:
                capability = broker.capabilities(instrument_id)
            except Exception as exc:
                raise ControlDenied("BROKER_CAPABILITY_UNAVAILABLE") from exc
            _require(
                type(capability) is m.BrokerCapabilities
                and capability.broker_id == "fake"
                and capability.instrument_id == instrument_id
                and capability.version == instrument["capability_version"]
                and capability.paper is m.Capability.CONFIRMED
                and capability.regular_session is m.Capability.CONFIRMED
                and capability.market_order is m.Capability.CONFIRMED
                and capability.quantity_step == instrument["quantity_step"],
                "BROKER_CAPABILITY_MISMATCH",
            )
            bar = (
                c.execute(
                    text("""
                SELECT close,available_at,quality,closed FROM public.market_bars
                WHERE instrument_id=:i ORDER BY available_at DESC,id DESC LIMIT 1
            """),
                    {"i": instrument_id},
                )
                .mappings()
                .one_or_none()
            )
            _require(
                bar is not None
                and bar["quality"] == "VALID"
                and bar["closed"]
                and bar["available_at"] <= now
                and now - bar["available_at"] <= timedelta(seconds=limits.market_max_age_seconds),
                "MARKET_DATA_STALE",
            )
            exposure = _exact(exposure + owned.get(instrument_id, Decimal("0")) * bar["close"])
    return exposure, len(rows)


def execution_refresh_and_resume(
    *,
    ledger: PostgresLedger,
    account_id: str,
    request_id: str,
    broker: FakeBrokerAdapter,
    owner_id: str,
    fencing_token: int,
    limits: RiskLimits | None = None,
) -> str:
    """Record stable recovery denials after rolling back any partial evidence."""
    try:
        return _execution_refresh_and_resume(
            ledger=ledger,
            account_id=account_id,
            request_id=request_id,
            broker=broker,
            owner_id=owner_id,
            fencing_token=fencing_token,
            limits=limits,
        )
    except ControlDenied as exc:
        try:
            with ledger.transaction() as tx:
                c = tx.connection
                _role(c, "atl_execution")
                request = (
                    c.execute(
                        text("""
                    SELECT trace_id,expected_policy_version FROM public.safety_resume_requests
                    WHERE id=:r AND account_id=:a
                """),
                        {"r": request_id, "a": account_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if request is not None:
                    tx.insert(
                        m.AuditEvent(
                            str(uuid.uuid4()),
                            _now(c),
                            "execution-recovery",
                            "safety.resume_denied",
                            (str(exc),),
                            request_id,
                            1,
                            request["trace_id"],
                            request["expected_policy_version"],
                            "DENIED",
                        )
                    )
        except Exception:
            # A missing DB cannot manufacture evidence or a successful resume.
            pass
        raise


def _execution_refresh_and_resume(
    *,
    ledger: PostgresLedger,
    account_id: str,
    request_id: str,
    broker: FakeBrokerAdapter,
    owner_id: str,
    fencing_token: int,
    limits: RiskLimits | None = None,
) -> str:
    """Prove broker/ledger/data health and atomically refresh the portfolio and latch.

    Broker reads occur while the same account gate used by submit remains locked.
    The caller cannot supply a health boolean or a portfolio amount.
    """
    _require(type(broker) is FakeBrokerAdapter, "FAKE_BROKER_REQUIRED")
    limits = limits or RiskLimits()
    with ledger.transaction() as tx:
        c = tx.connection
        _role(c, "atl_execution")
        tx.lock_account(account_id)
        check_runtime(c, account_id, owner_id=owner_id, fencing_token=fencing_token)
        now = _now(c)
        request = (
            c.execute(
                text("""
            SELECT * FROM public.safety_resume_requests WHERE id=:r AND account_id=:a
        """),
                {"r": request_id, "a": account_id},
            )
            .mappings()
            .one_or_none()
        )
        _require(
            request is not None and request["requested_at"] <= now < request["expires_at"],
            "RESUME_REQUEST_MISSING_OR_EXPIRED",
        )
        context = (
            c.execute(
                text("""
            SELECT * FROM public.risk_account_context WHERE account_id=:a FOR UPDATE
        """),
                {"a": account_id},
            )
            .mappings()
            .one_or_none()
        )
        _require(
            context is not None
            and context["safety_state"] == "PAUSE_NEW"
            and context["version"] == request["pause_context_version"]
            and (
                (request["operation"] == "RESUME" and context["mode"] == "PAPER")
                or (
                    request["operation"] == "ACTIVATE_PAPER"
                    and context["mode"] in ("DISABLED", "REPLAY", "SHADOW")
                )
            ),
            "SAFETY_OR_CONTEXT_CHANGED",
        )
        _require(
            context["valid_until"] > now
            and context["risk_config_version"] == limits.config_version
            and context["day_start_at"].astimezone(UTC)
            == now.replace(hour=0, minute=0, second=0, microsecond=0),
            "ACCOUNT_CONTEXT_STALE",
        )
        policy = (
            c.execute(
                text("""
            SELECT version,currency,effective_at FROM public.risk_policies
            ORDER BY version DESC LIMIT 1
        """)
            )
            .mappings()
            .one_or_none()
        )
        _require(
            policy is not None
            and policy["version"] == request["expected_policy_version"]
            and policy["effective_at"] <= now,
            "POLICY_CHANGED",
        )
        account = (
            c.execute(
                text("""
            SELECT currency,ledger_version FROM public.accounts WHERE id=:a FOR UPDATE
        """),
                {"a": account_id},
            )
            .mappings()
            .one()
        )
        _require(
            policy["currency"] == account["currency"]
            and c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 1,
            "ACCOUNT_MISMATCH",
        )
        for table in ("positions", "risk_reservations", "order_intents"):
            _require(
                not c.execute(
                    text(f"SELECT EXISTS(SELECT 1 FROM public.{table} WHERE account_id IS NULL)")
                ).scalar_one(),
                "OWNERSHIP_UNASSIGNED",
            )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(SELECT 1 FROM public.jobs
              WHERE status IN ('LEASED','RECONCILE','DEAD'))
        """)
            ).scalar_one(),
            "JOBS_UNRESOLVED",
        )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(SELECT 1 FROM public.broker_orders
              WHERE status IN ('UNKNOWN','SUBMITTING'))
        """)
            ).scalar_one(),
            "ORDERS_UNRESOLVED",
        )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(SELECT 1 FROM public.risk_reservations
              WHERE state='HELD_UNKNOWN')
        """)
            ).scalar_one(),
            "RESERVATION_UNRESOLVED",
        )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(SELECT 1 FROM public.safety_health_evidence WHERE request_id=:r)
        """),
                {"r": request_id},
            ).scalar_one(),
            "RESUME_REQUEST_CONSUMED",
        )
        order_count = _verified_broker_orders(tx, account_id, broker)
        exposure, position_count = _verified_positions(tx, account_id, broker, now, limits)
        # Broker calls can block. Recheck all time-bound evidence with a new DB clock
        # before writing RUNNING, while the account/context/position locks remain held.
        now = _now(c)
        check_runtime(c, account_id, owner_id=owner_id, fencing_token=fencing_token)
        _require(
            now < request["expires_at"] and context["valid_until"] > now, "HEALTH_EVIDENCE_EXPIRED"
        )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(SELECT 1 FROM public.risk_instrument_context
              WHERE account_id=:a AND
                (valid_until<=:now OR session_valid_until<=:now OR NOT session_open))
        """),
                {"a": account_id, "now": now},
            ).scalar_one(),
            "CAPABILITY_OR_SESSION_STALE",
        )
        _require(
            not c.execute(
                text("""
            SELECT EXISTS(
              SELECT 1 FROM public.risk_instrument_context i
              LEFT JOIN LATERAL (
                SELECT available_at FROM public.market_bars
                WHERE instrument_id=i.instrument_id
                ORDER BY available_at DESC,id DESC LIMIT 1
              ) b ON true
              WHERE i.account_id=:a AND
                (b.available_at IS NULL OR b.available_at>:now OR
                 b.available_at<:min_time))
        """),
                {
                    "a": account_id,
                    "now": now,
                    "min_time": now - timedelta(seconds=limits.market_max_age_seconds),
                },
            ).scalar_one(),
            "MARKET_DATA_STALE",
        )
        old = tx.get(m.PortfolioSnapshot, context["portfolio_snapshot_id"])
        _require(
            old is not None
            and old.currency == account["currency"]
            and old.ledger_version == context["portfolio_version"] == account["ledger_version"],
            "PORTFOLIO_BASIS_MISMATCH",
        )
        with localcontext() as ctx:
            ctx.prec = 80
            # The fixture can have starting equity outside its modeled cash and
            # holdings. Reconciliation has checked every cash-affecting broker
            # report; keep that pre-existing baseline while repricing holdings.
            baseline = _exact(old.equity - old.cash - old.exposure)
            equity = _exact(old.cash + exposure + baseline)
        _require(equity >= 0, "PORTFOLIO_EQUITY_INVALID")
        next_version = old.ledger_version + 1
        snapshot = m.PortfolioSnapshot(
            id=str(uuid.uuid4()),
            currency=old.currency,
            equity=equity,
            cash=old.cash,
            exposure=exposure,
            as_of=now,
            ledger_version=next_version,
            quality=m.Quality.VALID,
            reconciliation=m.Reconciliation.CURRENT,
        )
        trace = request["trace_id"]
        tx.insert(
            m.AuditEvent(
                str(uuid.uuid4()),
                now,
                "execution-recovery",
                "safety.portfolio_refresh",
                ("BROKER_RECONCILED",),
                snapshot.id,
                next_version,
                trace,
                policy["version"],
                "CURRENT",
            )
        )
        tx.insert(snapshot)
        tx.insert(
            m.AuditEvent(
                str(uuid.uuid4()),
                now,
                "execution-recovery",
                "safety.account_refresh",
                ("BROKER_RECONCILED",),
                account_id,
                next_version,
                trace,
                policy["version"],
                "CURRENT",
            )
        )
        c.execute(
            text("UPDATE public.accounts SET ledger_version=:v WHERE id=:a"),
            {"v": next_version, "a": account_id},
        )
        evidence_id = str(uuid.uuid4())
        c.execute(
            text("""
            INSERT INTO public.safety_health_evidence
              (id,request_id,account_id,checked_at,expires_at,owner_id,fencing_token,
               policy_version,context_version,portfolio_snapshot_id,
               broker_order_count,broker_position_count,actor_id,trace_id)
            VALUES (:id,:r,:a,:now,:expiry,:o,:f,:p,:v,:s,:orders,:positions,
                    'execution-recovery',:t)
        """),
            {
                "id": evidence_id,
                "r": request_id,
                "a": account_id,
                "now": now,
                "expiry": now + timedelta(seconds=10),
                "o": owner_id,
                "f": fencing_token,
                "p": policy["version"],
                "v": context["version"],
                "s": snapshot.id,
                "orders": order_count,
                "positions": position_count,
                "t": trace,
            },
        )
        version = context["version"] + 1
        tx.insert(
            m.AuditEvent(
                str(uuid.uuid4()),
                now,
                "execution-recovery",
                "safety.activate_paper"
                if request["operation"] == "ACTIVATE_PAPER"
                else "safety.resume",
                ("BROKER_RECONCILED",),
                account_id,
                version,
                trace,
                policy["version"],
                "RUNNING",
            )
        )
        c.execute(
            text("""
            UPDATE public.risk_account_context SET version=:v,
              portfolio_snapshot_id=:s,portfolio_version=:pv,
              mode='PAPER',safety_state='RUNNING',reconciliation_current=true,
              actor_id='execution-recovery',trace_id=:t
            WHERE account_id=:a AND version=:old AND safety_state='PAUSE_NEW'
        """),
            {
                "v": version,
                "s": snapshot.id,
                "pv": next_version,
                "t": trace,
                "a": account_id,
                "old": context["version"],
            },
        )
        c.execute(
            text("""
            INSERT INTO public.outbox_events
              (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
               trace_id,schema_version,payload)
            VALUES (:id,'safety.changed.v1',:now,:a,:v,:t,1,CAST(:payload AS jsonb))
        """),
            {
                "id": str(uuid.uuid4()),
                "now": now,
                "a": account_id,
                "v": version,
                "t": trace,
                "payload": json.dumps(
                    {
                        "account_id": account_id,
                        "action": "PAPER_ACTIVATED"
                        if request["operation"] == "ACTIVATE_PAPER"
                        else "RESUMED",
                        "mode": "PAPER",
                        "safety_state": "RUNNING",
                        "policy_version": policy["version"],
                        "reason_code": "OK",
                        "request_id": request_id,
                    }
                ),
            },
        )
        return snapshot.id
