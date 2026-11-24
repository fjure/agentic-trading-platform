"""Project one source snapshot into the investment experience contract."""

import hashlib
import json
import re
from datetime import UTC, datetime

from packages.domain.models import decimal_string

NAMESPACES = {"FAKE": "fake", "DEMO": "demo", "ALPACA_PAPER": "alpaca-paper"}
_OPAQUE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_MONEY_FIELDS = {"cash": "cash", "equity": "equity", "exposure": "exposure"}
_HISTORY = "PERFORMANCE_HISTORY_NOT_AVAILABLE"
_VALUATION = "VALUATION_NOT_AVAILABLE"


def fingerprint(value) -> str:
    canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _timestamp(value: str | None) -> str | None:
    if value is not None:
        if "T" not in value or not value.endswith("Z"):
            raise ValueError("source timestamp must be UTC")
        datetime.fromisoformat(value)
    return value


def _reference(kind: str, identity: str, revision: int | None) -> dict:
    return {"kind": kind, "id": identity, "version": revision}


def _metric(value, unit, as_of, sources, *, method, missing, stale=False, currency="USD"):
    native_amount = None
    if value is not None and unit == "USD" and currency != "USD":
        if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
            native_amount = {
                "currency": currency, "value": decimal_string(value), "asOf": as_of,
                "availability": "STALE" if stale else "AVAILABLE",
            }
        value = None
    return {
        "value": decimal_string(value) if value is not None else None,
        "currency": "USD",
        "unit": unit,
        "methodVersion": method,
        "availability": "UNAVAILABLE" if value is None else ("STALE" if stale else "AVAILABLE"),
        "reasonCodes": [missing] if value is None else (["STALE_EVIDENCE"] if stale else []),
        "asOf": as_of if value is not None else None,
        "sourceReferences": sources,
        **({"nativeAmount": native_amount} if native_amount is not None else {}),
    }


def _position(item, snapshot_id, as_of, ledger, stale, currency):
    revision = item["revision"]
    if revision is None or revision < 1:
        raise ValueError("unbound position")
    symbol = item["instrument"]
    if not isinstance(symbol, str) or not 1 <= len(symbol) <= 32:
        raise ValueError("invalid source instrument")
    sources = [
        _reference("POSITION", "position:" + fingerprint([snapshot_id, symbol]), revision),
        ledger,
    ]
    instrument_currency = item.get("currency", currency)
    quantity = _metric(
        item["quantity"], "QUANTITY", as_of, sources, method="ledger-quantity-v1",
        missing="SOURCE_REVISION_UNAVAILABLE", stale=stale,
    )
    basis = {
        key: _metric(
            item[field], "USD", as_of, sources, method="ledger-basis-v1",
            missing="METHOD_INPUTS_UNAVAILABLE", stale=stale, currency=instrument_currency,
        )
        for key, field in (("averagePrice", "average_price"), ("costBasis", "cost_basis"))
    }
    return {
        "instrument": symbol,
        "ledgerVersion": revision,
        "quantity": quantity,
        **basis,
        # The ledger does not prove net results when fee history is incomplete.
        "realizedPnl": _metric(None, "USD", None, sources, method="net-result-v1",
                               missing="FEE_EVIDENCE_MISSING"),
        "marketValue": _metric(None, "USD", None, sources, method="valuation-v1",
                               missing=_VALUATION),
        "unrealizedPnl": _metric(None, "USD", None, sources, method="net-result-v1",
                                 missing="FEE_EVIDENCE_MISSING"),
        "weight": _metric(None, "RATIO", None, sources, method="weight-v1", missing=_VALUATION),
        "sourceReferences": sources,
    }


def project(
    source: dict, context: str, demo_id: str | None, offset: int, limit: int,
) -> tuple[dict, str, int | None]:
    namespace = NAMESPACES[context]
    binding = {"context": context, "namespace": namespace}
    if demo_id is not None:
        if _OPAQUE.fullmatch(demo_id) is None:
            raise ValueError("invalid demo reference")
        binding["demoRunId"] = demo_id

    source_id = source["snapshot_id"]
    revision = source["revision"]
    as_of = _timestamp(source["as_of"])
    quality = source["quality"]
    currency = source["currency"]
    generation = source["generation"]
    reconciliation = source["reconciliation"]
    reasons = list(source.get("blockers", ()))
    positions = source["positions"]
    if quality not in {"CURRENT", "STALE", "UNAVAILABLE", "INCONSISTENT"}:
        raise ValueError("invalid source quality")

    bound = source_id is not None and revision is not None and revision >= 1 and as_of is not None
    if not bound and quality in {"CURRENT", "STALE"}:
        quality = "INCONSISTENT"
    if quality == "INCONSISTENT":
        reasons.append("SNAPSHOT_INCONSISTENT")
    if quality == "UNAVAILABLE" and not reasons:
        reasons.append("LEDGER_NOT_PREPARED")
    if quality == "STALE":
        reasons.append("STALE_EVIDENCE")

    usable = bound and quality in {"CURRENT", "STALE"}
    stale = quality == "STALE"
    revision = revision if usable else None
    # The full projection fingerprint changes even if a source updates a
    # position or its reconciliation evidence without advancing the cash revision.
    evidence = {key: value for key, value in source.items() if key != "observed_at"}
    digest = fingerprint([binding, evidence])
    snapshot_id = "portfolio:" + digest
    ledger = _reference("LEDGER", namespace + ":" + fingerprint([binding, source_id]), revision)
    refs = [_reference("SNAPSHOT", snapshot_id, revision), ledger] if usable else []
    if demo_id is not None:
        refs.append(_reference("RUN", demo_id, None))

    missing = {
        "UNAVAILABLE": reasons[0] if reasons else "LEDGER_NOT_PREPARED",
        "INCONSISTENT": "SNAPSHOT_INCONSISTENT",
    }.get(quality, "METHOD_INPUTS_UNAVAILABLE")
    metrics = {
        key: _metric(
            source[field] if usable else None, "USD", as_of, refs,
            method="ledger-observed-v1", missing=missing, stale=stale, currency=currency,
        )
        for key, field in _MONEY_FIELDS.items()
    }
    metrics.update({
        "realizedPnl": _metric(None, "USD", None, refs, method="net-result-v1",
                               missing="FEE_EVIDENCE_MISSING"),
        "marketValue": _metric(None, "USD", None, refs, method="valuation-v1", missing=_VALUATION),
        "unrealizedPnl": _metric(None, "USD", None, refs, method="net-result-v1",
                                 missing="FEE_EVIDENCE_MISSING"),
        **{
            key: _metric(None, "RATIO", None, refs, method="history-v1", missing=_HISTORY)
            for key in ("dailyReturn", "totalReturn", "drawdown")
        },
    })
    positions = positions if usable else None
    return {
        "schemaVersion": "1",
        "contextReference": binding,
        "observedAt": _timestamp(source.get("observed_at"))
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "snapshotId": snapshot_id,
        # The envelope is version 1 even when no financial revision is available.
        "snapshotVersion": revision or 1,
        "accountGeneration": generation,
        "portfolioLedgerVersion": revision,
        "asOf": as_of if usable else None,
        "quality": quality,
        "reconciliation": reconciliation,
        "reconciliationAt": _timestamp(source.get("reconciliation_at")) if usable else None,
        "metrics": metrics,
        "positions": (
            [
                _position(item, snapshot_id, as_of, ledger, stale, currency)
                for item in positions[offset:offset + limit]
            ]
            if positions is not None
            else None
        ),
        "marketEvidence": [],
        "blockers": list(dict.fromkeys(reasons)),
        "sourceReferences": refs,
    }, digest, len(positions) if positions is not None else None
