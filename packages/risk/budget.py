"""Additional exact USD exposure cap; callers retain every existing risk gate."""

from __future__ import annotations

import re
from decimal import Decimal, localcontext

from sqlalchemy import text

from packages.domain import models as m

STRATEGIES = {"mock-neutral": "mock-neutral.v1", "neutral-baseline": "neutral-baseline.v1"}
_BUDGET = re.compile(
    r"(?=.*[1-9])(?:(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,18})?|1000000(?:\.0{1,18})?)\Z"
)
BUFFER = Decimal("0.01")


def validate_budget(value: str) -> Decimal:
    if not isinstance(value, str) or _BUDGET.fullmatch(value) is None:
        raise ValueError("INVALID_REQUEST")
    return m.decimal_value(value)


def read_cap(connection, account_id: str) -> Decimal | None:
    # A missing saved profile preserves legacy behavior. Database/schema errors
    # propagate: unavailable evidence is never treated as an absent cap.
    value = connection.execute(
        text("SELECT budget FROM public.trading_profiles WHERE account_id=:account"),
        {"account": account_id},
    ).scalar_one_or_none()
    return None if value is None else m.decimal_value(value)


def read_strategy(connection, account_id: str) -> tuple[str, str]:
    row = connection.execute(
        text("SELECT strategy,strategy_version FROM public.trading_profiles WHERE account_id=:a"),
        {"a": account_id},
    ).one_or_none()
    if row is None:
        return "mock-neutral", STRATEGIES["mock-neutral"]
    if row.strategy not in STRATEGIES or STRATEGIES[row.strategy] != row.strategy_version:
        raise ValueError("INVALID_STRATEGY_VERSION")
    return row.strategy, row.strategy_version


def budget_reasons(proposal: m.TradeProposal, facts, cap: Decimal | None) -> tuple[str, ...]:
    if proposal.target is not m.Target.LONG or cap is None:
        return ()
    cap = m.decimal_value(cap)
    if cap <= 0 or cap > Decimal("1000000"):
        raise ValueError("INVALID_BUDGET")
    with localcontext() as context:
        context.prec = 100
        candidate = proposal.quantity * (proposal.limit_price or facts.market_price + facts.spread)
        # gross_notional already contains held marks and active reservations.
        # A conservative extra 1% buffer applies only to not-yet-filled buys.
        total = facts.gross_notional + facts.reserved_notional * BUFFER + candidate * (1 + BUFFER)
        return ("CASH_LIMIT",) if total > cap else ()
