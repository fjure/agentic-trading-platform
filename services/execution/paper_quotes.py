"""Publish only sanitized immutable quote evidence through the fixed SQL port."""

from datetime import datetime
from decimal import localcontext

from sqlalchemy import text

from packages.domain.models import decimal_value, utc_time
from packages.risk.paper_daily_sql import is_isolated_paper
from services.execution.gates import SubmitDenied


def publish_quote(tx, state, snapshot, *, owner_id, fencing_token):
    connection = tx.connection
    if not is_isolated_paper(connection):
        raise SubmitDenied("PAPER_QUOTE_IDENTITY_MISMATCH")
    if any(getattr(snapshot, field) is None for field in (
        "bid", "ask", "quote_provider_time", "quote_observed_at"
    )):
        raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")
    bid, ask = decimal_value(snapshot.bid), decimal_value(snapshot.ask)
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        spread = ask - bid
    if (
        bid <= 0 or ask < bid or spread != state.spread
        or snapshot.instrument.id != state.instrument_id
        or snapshot.source_capabilities.source_id != "alpaca-market-data:iex"
    ):
        raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")
    context = connection.execute(
        text("SELECT version FROM public.risk_instrument_context "
             "WHERE account_id='paper' AND instrument_id=:instrument"),
        {"instrument": state.instrument_id},
    ).scalar_one_or_none()
    if type(context) is not int or context < 1:
        raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")

    def stamp(value):
        if not isinstance(value, datetime):
            raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")
        return utc_time(value).isoformat().replace("+00:00", "Z")

    import json

    value = {
        "sourceId": snapshot.source_capabilities.source_id,
        "bid": format(bid, "f"), "ask": format(ask, "f"),
        "providerTime": stamp(snapshot.quote_provider_time),
        "observedAt": stamp(snapshot.quote_observed_at),
        "sourceCapabilityVersion": snapshot.capability_version,
    }
    result = connection.execute(
        text("SELECT public.paper_quote_append("
             ":state,:context,:owner,:fence,CAST(:quote AS jsonb))"),
        {"state": state.id, "context": context, "owner": owner_id,
         "fence": fencing_token, "quote": json.dumps(value, separators=(",", ":"))},
    ).scalar_one_or_none()
    if not isinstance(result, dict) or set(result) != {"quoteId", "quoteHash"}:
        raise SubmitDenied("MARKET_SPREAD_UNAVAILABLE")
    return result
