"""Read the existing Fake and run-owned Demo ledgers without changing them."""

from __future__ import annotations

import re

from sqlalchemy import text

from packages.domain.models import decimal_string, utc_string
from services.control_api.auth import AuthDenied
from services.runtime.config import DATABASES

_MAX_POSITIONS = 10000


def unavailable_local_portfolio(reason: str = "LEDGER_NOT_PREPARED") -> dict:
    return {
        "snapshot_id": None,
        "revision": None,
        "generation": None,
        "as_of": None,
        "reconciliation_at": None,
        "quality": "INCONSISTENT" if reason == "SNAPSHOT_INCONSISTENT" else "UNAVAILABLE",
        "reconciliation": "NOT_APPLICABLE",
        "currency": None,
        "cash": None,
        "equity": None,
        "exposure": None,
        "positions": None,
        "blockers": [reason],
    }


def read_local_ledger(ledger, account_id: str, database: str) -> dict:
    """Project a checked account, valuation and open positions from one MVCC view."""
    with ledger.engine.connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")
            identity = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
            if tuple(identity) != (database, "atl_control_api"):
                return unavailable_local_portfolio("SNAPSHOT_INCONSISTENT")
            accounts = connection.execute(text("SELECT id FROM public.accounts")).scalars().all()
            if not accounts:
                return unavailable_local_portfolio()
            if accounts != [account_id]:
                return unavailable_local_portfolio("SNAPSHOT_INCONSISTENT")
            basis = connection.execute(
                text("""
                    SELECT p.id, p.currency, p.as_of, p.cash, p.equity, p.exposure, p.quality,
                           p.ledger_version, a.ledger_version AS account_revision,
                           c.portfolio_version AS context_revision, a.currency AS account_currency,
                           p.as_of > transaction_timestamp() AS future_valuation,
                           c.valid_until > transaction_timestamp()
                               AND EXISTS(SELECT 1 FROM public.safety_health_evidence h
                                          WHERE h.account_id = a.id
                                            AND h.portfolio_snapshot_id = p.id
                                            AND h.checked_at = p.as_of
                                            AND h.expires_at > transaction_timestamp())
                               AS valuation_current,
                           EXISTS(SELECT 1 FROM public.risk_instrument_context i
                                  WHERE i.account_id = a.id AND i.broker_id <> 'fake')
                           OR EXISTS(SELECT 1 FROM public.broker_orders o
                                     JOIN public.order_intents i ON i.id = o.intent_id
                                     WHERE i.account_id = a.id AND o.adapter <> 'fake')
                               AS has_other_broker
                    FROM public.risk_account_context c
                    JOIN public.accounts a ON a.id = c.account_id
                    JOIN public.portfolio_snapshots p ON p.id = c.portfolio_snapshot_id
                    WHERE c.account_id = :account
                """),
                {"account": account_id},
            ).mappings().one_or_none()
            if basis is None:
                return unavailable_local_portfolio()
            quality = _quality(basis)
            if quality in ("INCONSISTENT", "UNAVAILABLE"):
                reason = (
                    "SNAPSHOT_INCONSISTENT" if quality == "INCONSISTENT" else "LEDGER_NOT_PREPARED"
                )
                return unavailable_local_portfolio(reason)
            positions = connection.execute(
                text("""
                    SELECT i.broker_symbol AS instrument, p.currency, p.ledger_version,
                           p.quantity, p.average_price
                    FROM public.positions p
                    JOIN public.instruments i ON i.id = p.instrument_id
                    WHERE p.account_id = :account AND p.quantity > 0
                    ORDER BY i.broker_symbol, p.id
                    LIMIT :bounded
                """),
                {"account": account_id, "bounded": _MAX_POSITIONS + 1},
            ).mappings().all()
            return _portfolio(basis, quality, positions)


def _quality(basis: dict) -> str:
    if (
        basis["has_other_broker"]
        or basis["future_valuation"]
        or basis["ledger_version"] != basis["context_revision"]
        or basis["ledger_version"] != basis["account_revision"]
        or basis["currency"] != basis["account_currency"]
        or not re.fullmatch(r"[A-Z]{3}", basis["currency"])
    ):
        return "INCONSISTENT"
    return {
        "VALID": "CURRENT" if basis["valuation_current"] else "STALE",
        "STALE": "STALE",
        "CONFLICT": "INCONSISTENT",
    }.get(basis["quality"], "UNAVAILABLE")


def _portfolio(basis: dict, quality: str, positions: list) -> dict:
    blockers = ["STALE_EVIDENCE"] if quality == "STALE" else []
    if len(positions) > _MAX_POSITIONS:
        blockers.append("SOURCE_REVISION_UNAVAILABLE")
        projected = None
    else:
        projected = [_position(row) for row in positions]
    return {
        "snapshot_id": basis["id"],
        "revision": basis["ledger_version"],
        "generation": None,
        "as_of": utc_string(basis["as_of"]),
        "reconciliation_at": None,
        "quality": quality,
        "reconciliation": "NOT_APPLICABLE",
        "currency": basis["currency"],
        "cash": decimal_string(basis["cash"]),
        "equity": decimal_string(basis["equity"]),
        "exposure": decimal_string(basis["exposure"]),
        "positions": projected,
        "blockers": blockers,
    }


def _position(row: dict) -> dict:
    return {
        "instrument": row["instrument"],
        "currency": row["currency"],
        "revision": row["ledger_version"],
        "quantity": decimal_string(row["quantity"]),
        "average_price": (
            decimal_string(row["average_price"]) if row["average_price"] is not None else None
        ),
        "cost_basis": None,
        "realized_pnl": None,
    }


def read_fake_portfolio(reader, trace_id: str) -> dict:
    # The actor's API trace never comes from a query parameter and is not a source ID.
    if reader is None:
        return unavailable_local_portfolio("CAPABILITY_UNAVAILABLE")
    return read_local_ledger(reader.ledger, reader.account_id, DATABASES["lab"])


def read_demo_portfolio(demo, run_id: str, actor_id: str, trace_id: str) -> dict:
    if demo is None:
        return unavailable_local_portfolio("CAPABILITY_UNAVAILABLE")
    try:
        return demo.portfolio(run_id, actor_id, trace_id)
    except AuthDenied as error:
        if error.status in (400, 404):
            raise LookupError("demo run unavailable") from None
        raise
