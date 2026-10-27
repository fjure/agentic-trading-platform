"""Offline-Vertrauensprüfung; öffentliche Schlüssel kommen ausschließlich aus dem Register.

Keine Signierfunktion, kein Netz und keine historische Freigabe durch einen Hash.
Lokale Zulassungen sind menschliche Attestierungen, keine Lizenzverifikation.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

POLICY_VERSION = "research-trust-v1"
TEMPORAL_METHOD = "offline-witness-ed25519-v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_KINDS = {"CALENDAR", "QUALITY", "CORPORATE_ACTION"}
_ASSERTIONS = {"EXISTENCE_BY", "PUBLICATION_OBSERVED_BY"}
_INPUT_FIELDS = {
    "decisionId",
    "sourceId",
    "keyId",
    "witnessId",
    "authorityType",
    "scope",
    "effectiveAt",
    "validFrom",
    "validTo",
    "revoked",
    "compromisedAt",
    "compromiseTimeKnown",
    "policyVersion",
    "reason",
    "publicKey",
    "algorithm",
    "methods",
    "actorRef",
    "documentFingerprint",
    "testOnly",
    "predecessorDecisionId",
}
_STORED_FIELDS = {"recordedAt", "version", "commitVersion"}


class EvidenceRejected(ValueError):
    """Ausschließlich redigierter Code; niemals Nutzdaten oder Kryptofehlertext."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset().total_seconds() != 0:
            raise EvidenceRejected("INVALID_TIME")
        return value.astimezone(UTC)
    try:
        if not isinstance(value, str) or not _UTC.fullmatch(value):
            raise ValueError
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise EvidenceRejected("INVALID_TIME") from None


def canonical_bytes(value: object) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError, RecursionError):
        raise EvidenceRejected("INVALID_CANONICAL_INPUT") from None


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _opaque(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise EvidenceRejected("INVALID_TRUST_DECISION")


def _decoded(value: str, size: int) -> bytes:
    try:
        result = base64.b64decode(value, validate=True)
        if len(result) != size or base64.b64encode(result).decode("ascii") != value:
            raise ValueError
        return result
    except (binascii.Error, ValueError, TypeError):
        raise EvidenceRejected("INVALID_KEY_OR_SIGNATURE") from None


def validate_decision(record: Mapping, *, stored: bool = False) -> dict:
    """Geschlossene lokale Betreiberentscheidung, ohne Datenbankzeit aus Eingaben."""
    required = _INPUT_FIELDS - {"witnessId"}
    allowed = _INPUT_FIELDS | (_STORED_FIELDS if stored else set())
    if not isinstance(record, Mapping) or not required <= record.keys() <= allowed:
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    result = dict(record)
    for field in ("sourceId", "keyId", "policyVersion", "reason", "actorRef"):
        _opaque(result[field])
    try:
        if str(UUID(result["decisionId"])) != result["decisionId"]:
            raise ValueError
        if (
            result["predecessorDecisionId"] is not None
            and str(UUID(result["predecessorDecisionId"])) != result["predecessorDecisionId"]
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise EvidenceRejected("INVALID_TRUST_DECISION") from None
    if result["policyVersion"] != POLICY_VERSION or result["algorithm"] != "Ed25519":
        raise EvidenceRejected("UNSUPPORTED_TRUST_POLICY")
    if result["authorityType"] not in {"SOURCE", "WITNESS"}:
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if result["authorityType"] == "WITNESS":
        _opaque(result.get("witnessId"))
    elif result.get("witnessId") is not None:
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if any(
        type(result[field]) is not bool for field in ("revoked", "testOnly", "compromiseTimeKnown")
    ):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if not isinstance(result["documentFingerprint"], str) or not _HASH.fullmatch(
        result["documentFingerprint"]
    ):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    _decoded(result["publicKey"], 32)
    start, end = timestamp(result["validFrom"]), timestamp(result["validTo"])
    timestamp(result["effectiveAt"])
    if start >= end:
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if result["compromisedAt"] is not None:
        timestamp(result["compromisedAt"])
        if not result["compromiseTimeKnown"]:
            raise EvidenceRejected("INVALID_TRUST_DECISION")
    methods = result["methods"]
    accepted = {"RFC8785-UTF8-v1"} if result["authorityType"] == "SOURCE" else {TEMPORAL_METHOD}
    if not isinstance(methods, list) or not methods or len(set(methods)) != len(methods):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if set(methods) != accepted:
        raise EvidenceRejected("UNSUPPORTED_TRUST_METHOD")
    scope = result["scope"]
    if not isinstance(scope, dict) or not {"instrumentIds", "kinds"} <= scope.keys() <= {
        "instrumentIds",
        "kinds",
        "assertions",
        "startAt",
        "endAt",
    }:
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    instruments = scope["instrumentIds"]
    try:
        if not isinstance(instruments, list) or not 1 <= len(instruments) <= 200:
            raise ValueError
        if len(set(instruments)) != len(instruments):
            raise ValueError
        for instrument in instruments:
            if str(UUID(instrument)) != instrument:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise EvidenceRejected("INVALID_TRUST_DECISION") from None
    if (
        not isinstance(scope["kinds"], list)
        or not scope["kinds"]
        or not set(scope["kinds"]) <= _KINDS
    ):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if "assertions" in scope and (
        not isinstance(scope["assertions"], list)
        or not scope["assertions"]
        or not set(scope["assertions"]) <= _ASSERTIONS
    ):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if ("startAt" in scope) != ("endAt" in scope):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if "startAt" in scope and timestamp(scope["startAt"]) >= timestamp(scope["endAt"]):
        raise EvidenceRejected("INVALID_TRUST_DECISION")
    if stored:
        if not _STORED_FIELDS <= record.keys():
            raise EvidenceRejected("INVALID_TRUST_DECISION")
        timestamp(record["recordedAt"])
        if any(type(record[k]) is not int or record[k] < 1 for k in ("version", "commitVersion")):
            raise EvidenceRejected("INVALID_TRUST_DECISION")
    return result


def trust_result(status, method, reason, references=()):
    return {
        "status": status,
        "method": method,
        "ruleVersion": POLICY_VERSION,
        "reason": reason,
        "evidenceRefs": list(references),
    }


class TrustRegistry:
    """Ein gebundener Registerstand; Produktionsmodus lehnt Testautoritäten ab."""

    def __init__(self, snapshot: Mapping, now: datetime, *, production: bool = True):
        self.now, self.production = timestamp(now), production
        self.version = snapshot["version"]
        self.policy_version = snapshot["policy_version"]
        if (
            type(self.version) is not int
            or self.version < 0
            or (self.version > 0 and self.policy_version != POLICY_VERSION)
        ):
            raise EvidenceRejected("UNSUPPORTED_TRUST_POLICY")
        self.decisions = tuple(validate_decision(d, stored=True) for d in snapshot["decisions"])
        if len(self.decisions) > 100000:
            raise EvidenceRejected("TRUST_BUDGET_EXHAUSTED")
        by_id = {d["decisionId"]: d for d in self.decisions}
        if len(by_id) != len(self.decisions):
            raise EvidenceRejected("INVALID_TRUST_CHAIN")
        for decision in self.decisions:
            predecessor = decision["predecessorDecisionId"]
            if predecessor is not None and (
                predecessor not in by_id
                or by_id[predecessor]["version"] >= decision["version"]
                or by_id[predecessor]["sourceId"] != decision["sourceId"]
            ):
                raise EvidenceRejected("INVALID_TRUST_CHAIN")

    def authority(self, content, key_id, authority_type, *, witness_id=None, assertion=None):
        candidates = [
            d
            for d in self.decisions
            if d["sourceId"] == content["sourceId"]
            and d["keyId"] == key_id
            and d["authorityType"] == authority_type
            and d.get("witnessId") == witness_id
            and d["version"] <= self.version
            and timestamp(d["recordedAt"]) <= self.now
            and timestamp(d["effectiveAt"]) <= self.now
        ]
        if not candidates:
            raise EvidenceRejected("UNTRUSTED_AUTHORITY")
        selected = max(candidates, key=lambda d: d["version"])
        if self.production and (
            selected["testOnly"]
            or selected["sourceId"].startswith(("test-", "synthetic-"))
            or (witness_id or "").startswith(("test-", "synthetic-"))
        ):
            raise EvidenceRejected("TEST_AUTHORITY_FORBIDDEN")
        if selected["revoked"]:
            raise EvidenceRejected("AUTHORITY_REVOKED")
        # Unbekannter Kompromittierungszeitpunkt sperrt die gesamte betroffene Kette.
        if not selected["compromiseTimeKnown"] or selected["compromisedAt"] is not None:
            raise EvidenceRejected("AUTHORITY_COMPROMISED")
        signed = timestamp(content["signedAt"])
        if not timestamp(selected["validFrom"]) <= signed < timestamp(selected["validTo"]):
            raise EvidenceRejected("KEY_NOT_VALID_AT_SIGNING")
        if not timestamp(selected["validFrom"]) <= self.now < timestamp(selected["validTo"]):
            raise EvidenceRejected("KEY_NOT_CURRENTLY_VALID")
        if signed > self.now:
            raise EvidenceRejected("FUTURE_SIGNING_TIME")
        scope = selected["scope"]
        if (
            content["instrumentId"] not in scope["instrumentIds"]
            or content["kind"] not in scope["kinds"]
        ):
            raise EvidenceRejected("AUTHORITY_SCOPE_MISMATCH")
        interval = content["interval"]
        if "startAt" in scope and not (
            timestamp(scope["startAt"])
            <= timestamp(interval["startAt"])
            < timestamp(interval["endAt"])
            <= timestamp(scope["endAt"])
        ):
            raise EvidenceRejected("AUTHORITY_SCOPE_MISMATCH")
        if assertion is not None and assertion not in scope.get("assertions", []):
            raise EvidenceRejected("AUTHORITY_SCOPE_MISMATCH")
        return selected

    @staticmethod
    def verify(payload, signature, authority):
        if signature["keyId"] != payload["keyId"] or signature["keyId"] != authority["keyId"]:
            raise EvidenceRejected("SIGNATURE_KEY_MISMATCH")
        if (
            signature["algorithm"] != "Ed25519"
            or signature["canonicalization"] != "RFC8785-UTF8-v1"
        ):
            raise EvidenceRejected("UNSUPPORTED_SIGNATURE_METHOD")
        try:
            Ed25519PublicKey.from_public_bytes(_decoded(authority["publicKey"], 32)).verify(
                _decoded(signature["value"], 64), canonical_bytes(payload)
            )
        except (InvalidSignature, UnsupportedAlgorithm, ValueError):
            raise EvidenceRejected("SIGNATURE_INVALID") from None

    @staticmethod
    def references(authority):
        return (
            {
                "kind": "KEY",
                "id": authority["keyId"],
                "revision": str(authority["version"]),
                "fingerprint": hashlib.sha256(_decoded(authority["publicKey"], 32)).hexdigest(),
            },
            {
                "kind": "TRUST_DECISION",
                "id": authority["decisionId"],
                "revision": str(authority["version"]),
                "fingerprint": None,
            },
        )
