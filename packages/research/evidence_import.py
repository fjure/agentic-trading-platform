"""Begrenzter lokaler Evidenzimport mit echter Herkunftsprüfung und atomarem Port.

Nutzdaten verlassen den Prozess nur als normalisierte lokale Datenbankschreibwerte.
Weder Netzwerkzugriff noch Credentials, Rohproviderpayloads oder Signierfunktion.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator
from sqlalchemy import text

from packages.research.calendar import ResearchSession
from packages.research.evidence_storage import EvidenceInput, EvidenceStore, read_trust_snapshot
from packages.research.evidence_trust import (
    POLICY_VERSION,
    TEMPORAL_METHOD,
    EvidenceRejected,
    TrustRegistry,
    canonical_bytes,
    fingerprint,
    timestamp,
    trust_result,
    validate_decision,
)
from packages.research.quality import QUALITY_RULE_VERSION
from scripts.contract_formats import contract_formats

MAX_BYTES = 1048576
_NAMESPACE = UUID("4b99437d-63e3-4768-9107-502527584a22")
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "contracts/schemas/research-evidence-bundle.schema.json"
)


def parse_document(data: bytes, *, maximum: int = MAX_BYTES) -> dict:
    """Bytebudget vor Decode/Parse; doppelte Schlüssel, NaN und ungültiges Unicode sperren."""
    if type(data) is not bytes or not 0 < len(data) <= maximum:
        raise EvidenceRejected("INPUT_BUDGET_EXHAUSTED")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise EvidenceRejected("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_number(_):
        raise EvidenceRejected("INVALID_JSON_NUMBER")

    try:
        result = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_number,
            parse_float=invalid_number,
        )
        if not isinstance(result, dict):
            raise ValueError
        canonical_bytes(result)  # I-JSON-Zahlenbereich und Unicode, keine eigene Kanonisierung.
        return result
    except EvidenceRejected:
        raise
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise EvidenceRejected("INVALID_JSON_INPUT") from None


def parse_bundle(data: bytes) -> dict:
    value = parse_document(data)
    schema = json.loads(_SCHEMA_PATH.read_text("utf-8"))
    validator = Draft202012Validator(schema, format_checker=contract_formats())
    if not validator.is_valid(value):
        raise EvidenceRejected("INVALID_BUNDLE_SCHEMA")
    return value


def _domain_results(content):
    """Quellenbehauptung und unabhängige Domainnormalisierung getrennt halten."""
    start, end = timestamp(content["interval"]["startAt"]), timestamp(content["interval"]["endAt"])
    statement = content["statement"]
    method = "normalized-domain-v1"
    good = trust_result("VERIFIED", method, "NORMALIZED_STATEMENT_VALID")
    incomplete = trust_result("UNKNOWN", method, "COMPLETENESS_UNPROVEN")
    if content["kind"] == "CALENDAR":
        sessions = []
        try:
            for item in statement["sessions"]:
                session = ResearchSession(
                    date.fromisoformat(item["sessionDate"]),
                    timestamp(item["opensAt"]),
                    timestamp(item["closesAt"]),
                    statement["calendarVersion"],
                )
                if not start <= session.opens_at < session.closes_at <= end:
                    raise ValueError
                sessions.append(session)
            days = [s.session_date for s in sessions]
            if days != sorted(set(days)):
                raise ValueError
        except (ValueError, TypeError):
            return trust_result("REJECTED", method, "INVALID_CALENDAR_STATEMENT"), incomplete
        # Nur durch Sessions eingerahmte Tage sind autoritativ; Randfeiertage bleiben unbekannt.
        first, last = sessions[0], sessions[-1]
        if first.opens_at <= start and end <= last.closes_at:
            incomplete = trust_result("VERIFIED", method, "EXPLICIT_SESSION_SCOPE_COMPLETE")
        return good, incomplete
    if content["kind"] == "QUALITY":
        quality = statement["quality"]
        if statement["ruleVersion"] != QUALITY_RULE_VERSION:
            return trust_result("UNKNOWN", method, "QUALITY_RULE_UNSUPPORTED"), incomplete
        if quality in {"INVALID", "CONFLICT", "MISSING_EXPECTED"}:
            return trust_result("REJECTED", method, statement["reason"]), incomplete
        if quality in {"NO_OBSERVATION", "UNKNOWN_CALENDAR"}:
            return trust_result("UNKNOWN", method, "INSUFFICIENT_EVIDENCE"), incomplete
        if statement["reason"] != "NORMALIZED_RULES_PASSED":
            return trust_result("REJECTED", method, "CONFLICTING_QUALITY_ASSERTION"), incomplete
        if timestamp(content["sourceObservedAt"]) < end:
            return trust_result("REJECTED", method, "FUTURE_QUALITY_ASSERTION"), incomplete
        # Eine VALID-Aussage liefert keine OHLC-Bars und behauptet deshalb keine Barvollständigkeit.
        return good, incomplete
    for action in statement["actions"]:
        effective = timestamp(action["effectiveAt"])
        if not start <= effective < end:
            return trust_result("REJECTED", method, "ACTION_OUTSIDE_SCOPE"), incomplete
        amount = Decimal(action.get("ratio", action.get("amount")))
        if (action["type"] == "SPLIT" and amount <= 0) or amount < 0:
            return trust_result("REJECTED", method, "INVALID_ACTION_VALUE"), incomplete
    if statement["conclusion"] == "UNKNOWN":
        return trust_result("UNKNOWN", method, "ACTION_COVERAGE_UNKNOWN"), incomplete
    if statement["conclusion"] == "NO_ACTIONS" and timestamp(content["sourceObservedAt"]) < end:
        return trust_result("REJECTED", method, "FUTURE_ACTION_FREEDOM_UNPROVEN"), incomplete
    return good, trust_result("VERIFIED", method, "EXPLICIT_ACTION_SCOPE_COMPLETE")


def verify_entry(entry: Mapping, envelope: Mapping, registry: TrustRegistry) -> dict:
    """Verifiziert nur diese Aussage; liefert niemals Research Eligibility."""
    content = entry["content"]
    for field in ("bundleVersion", "sourceId", "policyVersion", "sourceDocumentFingerprint"):
        if content[field] != envelope[field]:
            raise EvidenceRejected("ENVELOPE_BINDING_MISMATCH")
    start, end = timestamp(content["interval"]["startAt"]), timestamp(content["interval"]["endAt"])
    observed, signed = timestamp(content["sourceObservedAt"]), timestamp(content["signedAt"])
    if start >= end or observed > registry.now or observed > signed:
        raise EvidenceRejected("INVALID_EVIDENCE_CHRONOLOGY")
    if content["publishedAt"] is not None and timestamp(content["publishedAt"]) > observed:
        raise EvidenceRejected("INVALID_EVIDENCE_CHRONOLOGY")
    if fingerprint(content) != entry["contentFingerprint"]:
        raise EvidenceRejected("CONTENT_DIGEST_MISMATCH")
    authority = registry.authority(content, content["keyId"], "SOURCE")
    registry.verify(content, entry["signature"], authority)
    refs = registry.references(authority)
    dimensions = {
        "integrity": trust_result("VERIFIED", "RFC8785-SHA256-v1", "CONTENT_DIGEST_MATCHED", refs),
        "sourceAuthenticity": trust_result(
            "VERIFIED", "Ed25519", "PINNED_SOURCE_SIGNATURE_VALID", refs
        ),
        "temporalAuthenticity": trust_result("UNKNOWN", "none-v1", "TIME_UNPROVEN"),
    }
    proof = entry.get("temporalProof")
    publication = None
    if proof is not None:
        payload = proof["payload"]
        if payload["method"] != TEMPORAL_METHOD:
            raise EvidenceRejected("UNSUPPORTED_TEMPORAL_METHOD")
        expected_scope = {
            key: content[key]
            for key in ("sourceId", "evidenceKey", "revision", "kind", "instrumentId", "interval")
        }
        expected_scope.update(
            contentFingerprint=entry["contentFingerprint"],
            statementFingerprint=fingerprint(content["statement"]),
        )
        if payload["scope"] != expected_scope:
            raise EvidenceRejected("TEMPORAL_SCOPE_MISMATCH")
        witness_observed, witness_signed = (
            timestamp(payload["witnessObservedAt"]),
            timestamp(payload["signedAt"]),
        )
        if witness_observed > witness_signed or witness_observed > registry.now:
            raise EvidenceRejected("INVALID_TEMPORAL_CHRONOLOGY")
        witness_content = {**content, "signedAt": payload["signedAt"]}
        witness = registry.authority(
            witness_content,
            payload["keyId"],
            "WITNESS",
            witness_id=payload["witnessId"],
            assertion=payload["assertion"],
        )
        if (
            witness["publicKey"] == authority["publicKey"]
            or payload["witnessId"] == content["sourceId"]
        ):
            raise EvidenceRejected("WITNESS_NOT_INDEPENDENT")
        registry.verify(payload, proof["signature"], witness)
        witness_refs = registry.references(witness)
        if payload["assertion"] == "PUBLICATION_OBSERVED_BY":
            dimensions["temporalAuthenticity"] = trust_result(
                "VERIFIED", TEMPORAL_METHOD, "PUBLICATION_OBSERVED_IN_EXACT_SCOPE", witness_refs
            )
            # Spätestens beobachtete Verfügbarkeit, keine früheste Provideremission.
            publication = witness_observed
        else:
            dimensions["temporalAuthenticity"] = trust_result(
                "UNKNOWN", TEMPORAL_METHOD, "EXISTENCE_ONLY_TIME_UNPROVEN", witness_refs
            )
    domain, complete = _domain_results(content)
    dimensions["domainQuality"], dimensions["completeness"] = domain, complete
    return {
        "content": content,
        "fingerprint": entry["contentFingerprint"],
        "trust": dimensions,
        "publicationObservedAt": publication,
        "provenance": dict(entry),
    }


class EvidenceImporter:
    def __init__(self, engine, *, production: bool = True, store=None):
        if type(production) is not bool:
            raise ValueError("explicit production mode required")
        self.engine, self.production, self.store = engine, production, store or EvidenceStore()

    def admit(self, document: bytes, expected_version: int):
        """Explizite lokale Betreiberentscheidung; kein automatisches Quellen-Onboarding."""
        record = validate_decision(parse_document(document, maximum=65536))
        if self.production and (
            record["testOnly"] or record["sourceId"].startswith(("test-", "synthetic-"))
        ):
            raise EvidenceRejected("TEST_AUTHORITY_FORBIDDEN")
        with self.engine.begin() as connection:
            snapshot = read_trust_snapshot(connection)
            if any(d["decisionId"] == record["decisionId"] for d in snapshot["decisions"]):
                return self.store.append_trust_decision(connection, record, expected_version)
            for prior in snapshot["decisions"]:
                if prior["sourceId"] == record["sourceId"] and prior["keyId"] == record["keyId"]:
                    if any(prior[field] != record[field] for field in ("publicKey", "algorithm")):
                        raise EvidenceRejected("KEY_ID_REUSE_FORBIDDEN")
            # Ein kompletter Kandidatenstand prüft auch Vorgänger/Scope vor dem Port.
            candidate = {
                **record,
                "recordedAt": snapshot["snapshot_at"],
                "version": snapshot["version"] + 1,
                "commitVersion": snapshot["data_version"] + 1,
            }
            TrustRegistry(
                {
                    "version": snapshot["version"] + 1,
                    "policy_version": POLICY_VERSION,
                    "decisions": (*snapshot["decisions"], candidate),
                },
                snapshot["snapshot_at"],
                production=self.production,
            )
            return self.store.append_trust_decision(connection, record, expected_version)

    def import_bytes(self, document: bytes, *, bundle_key: str | None = None):
        try:
            return self._import_bytes(document, bundle_key=bundle_key)
        except EvidenceRejected as error:
            try:
                # Eigene Transaktion NACH Rollback; keine ungültigen Bytes im Evidenzbestand.
                with self.engine.begin() as connection:
                    self.store.record_rejection(connection, error.code)
            except Exception:
                raise EvidenceRejected("REJECTION_AUDIT_UNAVAILABLE") from None
            raise

    def _import_bytes(self, document: bytes, *, bundle_key: str | None = None):
        bundle = parse_bundle(document)
        digest = fingerprint(bundle)
        key = digest if bundle_key is None else bundle_key
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 128
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in key
            )
        ):
            raise EvidenceRejected("INVALID_BUNDLE_KEY")
        try:
            with self.engine.begin() as connection:
                previous_rows = (
                    connection.execute(
                        text("""
                    SELECT receipt_id,bundle_key,fingerprint FROM research.import_receipts
                    WHERE bundle_key=:key OR fingerprint=:digest LIMIT 2
                """),
                        dict(key=key, digest=digest),
                    )
                    .mappings()
                    .all()
                )
                if any(
                    row["bundle_key"] == key and row["fingerprint"] != digest
                    for row in previous_rows
                ):
                    raise EvidenceRejected("BUNDLE_IDEMPOTENCY_CONFLICT")
                previous = next(
                    (row for row in previous_rows if row["fingerprint"] == digest), None
                )
                if previous is not None:
                    if previous["fingerprint"] != digest:
                        raise EvidenceRejected("BUNDLE_IDEMPOTENCY_CONFLICT")
                    receipt = self.store.read_receipt(connection, previous["receipt_id"])
                else:
                    snapshot = read_trust_snapshot(connection)
                    registry = TrustRegistry(
                        snapshot, snapshot["snapshot_at"], production=self.production
                    )
                    verified = [
                        verify_entry(entry, bundle, registry) for entry in bundle["entries"]
                    ]
                    entries, local_predecessors, seen = [], {}, set()
                    for item in sorted(
                        verified,
                        key=lambda item: (
                            item["content"]["evidenceKey"],
                            item["content"]["revision"],
                        ),
                    ):
                        c = item["content"]
                        identity = (c["sourceId"], c["evidenceKey"], c["revision"])
                        if identity in seen:
                            raise EvidenceRejected("DUPLICATE_EVIDENCE_IDENTITY")
                        seen.add(identity)
                        predecessor = None
                        if c["predecessorFingerprint"] is not None:
                            predecessor = local_predecessors.get(
                                (c["sourceId"], c["evidenceKey"], c["predecessorFingerprint"])
                            )
                            if predecessor is None:
                                predecessor = self.store.resolve_predecessor(
                                    connection,
                                    c["sourceId"],
                                    c["evidenceKey"],
                                    c["predecessorFingerprint"],
                                )
                            if predecessor is None:
                                raise EvidenceRejected("PREDECESSOR_UNPROVEN")
                        evidence_id = uuid5(
                            _NAMESPACE, "/".join(map(str, (*identity, item["fingerprint"])))
                        )
                        local_predecessors[
                            (c["sourceId"], c["evidenceKey"], item["fingerprint"])
                        ] = evidence_id
                        entries.append(
                            EvidenceInput(
                                evidence_id,
                                c["sourceId"],
                                c["evidenceKey"],
                                c["kind"],
                                UUID(c["instrumentId"]),
                                timestamp(c["interval"]["startAt"]),
                                timestamp(c["interval"]["endAt"]),
                                timestamp(c["sourceObservedAt"]),
                                timestamp(c["publishedAt"])
                                if c["publishedAt"] is not None
                                else None,
                                c["revision"],
                                predecessor,
                                item["fingerprint"],
                                c["statement"],
                                item["trust"],
                                item["provenance"],
                            )
                        )
                    receipt = self.store.append_bundle(
                        connection, key, digest, tuple(entries), registry.version
                    )
        except EvidenceRejected:
            raise
        except Exception:
            # SQL-/Treiberfehler enthalten möglicherweise Parameter; nie als Nutzerfehler ausgeben.
            raise EvidenceRejected("ATOMIC_IMPORT_REJECTED") from None
        try:
            # Bestätigungszeit ausschließlich nach erfolgreichem Commit in neuer Transaktion.
            with self.engine.begin() as connection:
                self.store.confirm_commit(connection, receipt.receipt_id)
                return self.store.read_receipt(connection, receipt.receipt_id)
        except Exception:
            # Inhalt ist committed; fehlende Bestätigung behauptet keinen Rollback.
            raise EvidenceRejected("COMMIT_CONFIRMATION_UNKNOWN") from None
