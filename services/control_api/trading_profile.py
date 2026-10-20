"""Public configuration through an audited, account-serialized narrow SQL transition."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from sqlalchemy import text

from packages.risk.budget import STRATEGIES, validate_budget
from services.control_api.auth import AuthDenied

_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")
_TRACE = re.compile(r"[0-9a-f]{32}\Z")


def _public(row):
    amount = format(Decimal(row["budget"]), "f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return {"budget": amount, "currency": "USD", "strategy": row["strategy"]}


class TradingProfileBackend:
    def __init__(self, ledger, account_id: str):
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("account required")
        self.ledger = ledger
        self.account_id = account_id

    def read(self) -> dict:
        try:
            with self.ledger.transaction() as tx:
                row = tx.connection.execute(
                    text("SELECT budget,strategy FROM public.trading_profiles WHERE account_id=:a"),
                    {"a": self.account_id},
                ).mappings().one_or_none()
                # Display defaults are not silently installed as financial authority.
                return _public(row) if row else {
                    "budget": "100", "currency": "USD", "strategy": "mock-neutral",
                }
        except Exception:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE") from None

    def update(self, payload: dict, actor: str, key: str, trace: str) -> dict:
        try:
            if (not isinstance(payload, dict) or set(payload) != {"budget", "currency", "strategy"}
                    or payload["currency"] != "USD" or payload["strategy"] not in STRATEGIES
                    or not isinstance(actor, str) or not 1 <= len(actor) <= 255
                    or any(unicodedata.category(char).startswith("C") for char in actor)
                    or not isinstance(key, str) or _KEY.fullmatch(key) is None
                    or not isinstance(trace, str) or _TRACE.fullmatch(trace) is None):
                raise ValueError("INVALID_REQUEST")
            amount = validate_budget(payload["budget"])
        except (ValueError, TypeError):
            raise AuthDenied(400, "INVALID_REQUEST") from None
        try:
            with self.ledger.transaction() as tx:
                result = tx.connection.execute(text("""
                    SELECT public.control_update_trading_profile(
                        :a,:actor,:key,:trace,:budget,:strategy)
                """), {"a": self.account_id, "actor": actor, "key": key, "trace": trace,
                       "budget": amount, "strategy": payload["strategy"]}).scalar_one()
                if "error" in result:
                    code = result["error"]
                    if code not in {"IDEMPOTENCY_CONFLICT", "RISK_LIMIT_EXCEEDED"}:
                        raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
                    raise AuthDenied(409, code)
                return _public(result)
        except AuthDenied:
            raise
        except Exception:
            raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE") from None


ProfileBackend = TradingProfileBackend
