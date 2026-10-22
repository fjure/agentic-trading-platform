"""Read-only daily authority for the fixed isolated Paper role and ledger version."""

from datetime import datetime

from sqlalchemy import text

from packages.risk.paper_daily import DailyRiskObservation, DailyRiskReference, EquityInterval


def is_isolated_paper(connection):
    identity = tuple(
        connection.execute(text("SELECT current_database(),current_user,session_user")).one()
    )
    if identity[0] != "lab_paper_runtime":
        return False
    if identity != ("lab_paper_runtime", "atl_paper_execution", "atl_paper_execution_login"):
        raise PermissionError("PAPER_RISK_IDENTITY_MISMATCH")
    return True


def read_daily(connection, account_id, portfolio_version):
    value = connection.execute(
        text("SELECT public.paper_daily_read(:account,:version)"),
        {"account": account_id, "version": portfolio_version},
    ).scalar_one_or_none()
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "startsAt",
        "expiresAt",
        "openingLower",
        "openingUpper",
        "capitalLower",
        "externalFunding",
        "currentEquity",
        "referenceHash",
        "observationHash",
        "observedAt",
        "accountCreatedAt",
    }:
        return None, None
    try:

        def stamp(raw):
            return datetime.fromisoformat(raw) if isinstance(raw, str) else None

        reference = DailyRiskReference(
            value["kind"],
            stamp(value["startsAt"]),
            stamp(value["expiresAt"]),
            EquityInterval(value["openingLower"], value["openingUpper"]),
            value["capitalLower"],
            value["referenceHash"],
            stamp(value["accountCreatedAt"]),
        )
        observation = DailyRiskObservation(
            reference.evidence_hash,
            stamp(value["observedAt"]),
            EquityInterval(value["currentEquity"], value["currentEquity"]),
            EquityInterval(value["externalFunding"], value["externalFunding"]),
            True,
            value["observationHash"],
        )
        return reference, observation
    except (ValueError, TypeError):
        return None, None
