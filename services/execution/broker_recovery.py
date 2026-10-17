"""Rebind every durable Alpaca attempt before a new Execution process may submit."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.domain import models as m
from packages.persistence.ledger import PostgresLedger
from services.execution.broker_binding import AlpacaExecutionBinding
from services.execution.gates import SubmitDenied


def restore_alpaca_attempts(
    *,
    ledger: PostgresLedger,
    account_id: str,
    broker: AlpacaPaperBrokerAdapter,
    binding: AlpacaExecutionBinding,
    owner_id: str,
    fencing_token: int,
) -> int:
    """Read all SQL attempts and bind them locally as attempted, without provider I/O.

    The account may be PAUSE_NEW after restart. Current Execution role and owner
    fencing are still mandatory. Any invalid historic ownership blocks restore.
    """
    if (
        type(ledger) is not PostgresLedger
        or type(broker) is not AlpacaPaperBrokerAdapter
        or type(binding) is not AlpacaExecutionBinding
        or binding.ledger is not ledger
        or binding.account_id != account_id
        or binding.broker is not broker
    ):
        raise SubmitDenied("BROKER_BOUNDARY")
    from services.control_api.control import ControlDenied
    from services.execution.recovery_health import check_runtime

    with ledger.transaction() as tx:
        c = tx.connection
        if c.execute(text("SELECT current_user")).scalar_one() != "atl_execution":
            raise SubmitDenied("EXECUTION_ROLE_REQUIRED")
        tx.lock_account(account_id)
        try:
            check_runtime(c, account_id, owner_id=owner_id, fencing_token=fencing_token)
        except ControlDenied as exc:
            raise SubmitDenied(exc.reason_code) from None
        if (
            c.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() != 1
            or c.execute(
                text("SELECT count(*) FROM public.accounts WHERE id=:account"),
                {"account": account_id},
            ).scalar_one()
            != 1
        ):
            raise SubmitDenied("ACCOUNT_AMBIGUOUS")
        if c.execute(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM public.broker_orders b
                JOIN public.order_intents i ON i.id=b.intent_id
                WHERE b.adapter='alpaca-paper' AND i.account_id IS DISTINCT FROM :account
            )
        """),
            {"account": account_id},
        ).scalar_one():
            raise SubmitDenied("ACCOUNT_MISMATCH")
        if c.execute(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM public.execution_reports r
                JOIN public.broker_orders b ON b.id=r.order_id
                WHERE b.adapter='alpaca-paper' AND r.adapter<>b.adapter
            )
        """)
        ).scalar_one():
            raise SubmitDenied("RESTORE_REPORT_MISMATCH")
        rows = (
            c.execute(
                text("""
            SELECT b.id AS order_id,b.intent_id,b.client_order_id,
                   b.requested_quantity,b.trace_id,
                   i.account_id,i.instrument_id,i.capability_version,
                   i.client_order_id AS intent_client_order_id,
                   i.quantity AS intent_quantity,i.trace_id AS intent_trace_id
            FROM public.broker_orders b
            JOIN public.order_intents i ON i.id=b.intent_id
            WHERE i.account_id=:account AND b.adapter='alpaca-paper'
            ORDER BY b.id FOR UPDATE OF b
        """),
                {"account": account_id},
            )
            .mappings()
            .all()
        )
        intents: list[m.OrderIntent] = []
        seen: set[str] = set()
        for row in rows:
            if (
                row["client_order_id"] in seen
                or row["client_order_id"] != row["intent_client_order_id"]
                or Decimal(row["requested_quantity"]) != Decimal(row["intent_quantity"])
                or row["trace_id"] != row["intent_trace_id"]
            ):
                raise SubmitDenied("RESTORE_ORDER_MISMATCH")
            seen.add(row["client_order_id"])
            proof = c.execute(
                text("""
                SELECT EXISTS (
                    SELECT 1 FROM public.broker_binding_proofs p
                    JOIN public.broker_account_bindings a
                      ON a.account_id=p.account_id
                    JOIN public.broker_asset_bindings s
                      ON s.account_id=p.account_id AND s.instrument_id=p.instrument_id
                    WHERE p.account_id=:account AND p.instrument_id=:instrument
                      AND p.trading_capability_version=:version
                      AND p.provider_account_id=a.provider_account_id
                      AND p.provider_asset_id=s.provider_asset_id
                )
            """),
                {
                    "account": account_id,
                    "instrument": row["instrument_id"],
                    "version": row["capability_version"],
                },
            ).scalar_one()
            if not proof:
                raise SubmitDenied("RESTORE_PROOF_MISSING")
            intent = tx.get(m.OrderIntent, row["intent_id"])
            order = tx.get(m.BrokerOrder, row["order_id"])
            if (
                intent is None
                or order is None
                or order.intent_id != intent.id
                or order.client_order_id != intent.client_order_id
                or order.requested_quantity != intent.quantity
                or order.trace_id != intent.trace_id
            ):
                raise SubmitDenied("RESTORE_ORDER_MISMATCH")
            intents.append(intent)
        # Bind only after the entire SQL set has passed validation.
        for intent in intents:
            broker.bind_intent(intent, attempted=True)
    binding.mark_restored()
    return len(intents)
