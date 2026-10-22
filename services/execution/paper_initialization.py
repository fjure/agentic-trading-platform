"""Execution-private immutable proof serialization and atomic SQL completion."""

from datetime import datetime
from decimal import Decimal

from services.control_api.paper_commands import PaperCommandError
from services.control_api.paper_dto import identity
from services.execution.paper_daily_evidence import PreparedPaperEvidence


def read_initial_history(connection, account_id):
    """Fetch only immutable import anchors over the fixed Execution SQL port."""
    from sqlalchemy import text

    from packages.brokers.initial_anchors import InitialHistoryProof

    raw = connection.execute(
        text("SELECT public.paper_initial_history(:account)"), {"account": account_id}
    ).scalar_one_or_none()
    if not isinstance(raw, dict) or set(raw) != {
        "providerAccountId", "baselineAt", "orders", "activities"
    }:
        raise ValueError("INITIAL_HISTORY_UNAVAILABLE")
    if not isinstance(raw["orders"], list) or not isinstance(raw["activities"], list):
        raise ValueError("INITIAL_HISTORY_UNAVAILABLE")
    if any(not isinstance(a, dict) or set(a) != {"id", "hash", "untraded"}
           or type(a["untraded"]) is not bool for a in raw["orders"]):
        raise ValueError("INITIAL_HISTORY_UNAVAILABLE")
    if any(not isinstance(a, dict) or set(a) != {"id", "hash", "kind"}
           for a in raw["activities"]):
        raise ValueError("INITIAL_HISTORY_UNAVAILABLE")
    return InitialHistoryProof(
        raw["providerAccountId"], datetime.fromisoformat(raw["baselineAt"]),
        tuple((a["id"], a["hash"]) for a in raw["orders"]),
        tuple((a["id"], a["hash"]) for a in raw["activities"]),
    )


def initial_payload(evidence):
    if type(evidence) is not PreparedPaperEvidence:
        raise PaperCommandError(400, "RECONCILIATION_REQUIRED")
    initial, ref, observation = (
        evidence.initial,
        evidence.daily_reference,
        evidence.daily_observation,
    )
    if (
        ref.evidence_hash != observation.reference_hash
        or observation.observed_at != initial.confirmed_at
        or observation.evidence_hash != initial.evidence_hash
        or observation.current_equity.lower != initial.equity
        or observation.current_equity.upper != initial.equity
        or not observation.funding_complete
        or observation.net_external_funding.lower != observation.net_external_funding.upper
    ):
        raise PaperCommandError(400, "RECONCILIATION_REQUIRED")

    def value(item):
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, datetime):
            return item.isoformat().replace("+00:00", "Z")
        return item

    return {
        "credentialGeneration": initial.credential_generation,
        "providerAccountId": initial.provider_account_id,
        "cash": value(initial.cash),
        "equity": value(initial.equity),
        "positions": [
            {
                "symbol": p.symbol,
                "providerAssetId": p.provider_asset_id,
                "quantity": value(p.quantity),
                "costBasis": value(p.cost_basis),
                "averageEntryPrice": value(p.average_entry_price),
                "marketValue": value(p.market_value),
            }
            for p in initial.positions
        ],
        "firstReadAt": value(initial.first_read_at),
        "confirmedAt": value(initial.confirmed_at),
        "providerTime": value(initial.provider_time),
        "snapshotHash": initial.snapshot_hash,
        "evidenceHash": initial.evidence_hash,
        "orderAnchors": [
            {"id": o.provider_order_id, "hash": o.evidence_hash, "untraded": o.untraded}
            for o in initial.order_anchors
        ],
        "activityAnchors": [
            {"id": a.activity_id, "hash": a.evidence_hash, "kind": a.kind}
            for a in initial.activity_anchors
        ],
        "daily": {
            "kind": ref.kind,
            "startsAt": value(ref.starts_at),
            "expiresAt": value(ref.expires_at),
            "openingLower": value(ref.opening_equity.lower),
            "openingUpper": value(ref.opening_equity.upper),
            "capitalLower": value(ref.capital_lower),
            "externalFunding": value(observation.net_external_funding.upper),
            "referenceHash": ref.evidence_hash,
            "observationHash": observation.evidence_hash,
            "accountCreatedAt": value(ref.account_created_at),
        },
    }


def complete_preparation(commands, claim, owner, evidence):
    import json

    identity(claim["operationId"])
    identity(owner)
    result = commands._call(
        "SELECT public.paper_prepare(CAST(:id AS uuid),CAST(:owner AS uuid),:fence,"
        "CAST(:evidence AS jsonb))",
        {
            "id": claim["operationId"],
            "owner": owner,
            "fence": claim["fence"],
            "evidence": json.dumps(initial_payload(evidence), separators=(",", ":")),
        },
    )
    if result != {"prepared": True}:
        raise PaperCommandError(503, "TEMPORARILY_UNAVAILABLE")
