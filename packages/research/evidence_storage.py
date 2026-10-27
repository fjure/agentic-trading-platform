"""RDR-03: atomare unveränderliche Evidenzports, ohne Kryptografie oder Quellenzulassung.

Nur die separat berechtigte Offlinerolle darf den Importport verwenden. Die
aufrufende RDR-09-Schicht verifiziert Herkunft/Verfahren; dieser Port bindet deren
normalisierte Ergebnisse an echte Datenbankzeiten, Revisionen und Trustversionen.
"""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from sqlalchemy import text

from packages.domain.models import utc_time

DIMENSIONS = (
    "integrity",
    "sourceAuthenticity",
    "temporalAuthenticity",
    "completeness",
    "domainQuality",
)
TRUST_STATUSES = frozenset({"VERIFIED", "REJECTED", "UNKNOWN", "NOT_EVALUATED"})
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceBudgetExceeded(ValueError):
    """Begrenzter Metadatenscan konnte keinen vollständigen Snapshot herstellen."""


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat().replace("+00:00", "Z")
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def unknown_trust():
    """Lokale Beobachtung verleiht keine extern bestätigte Vertrauensdimension."""
    return _freeze(
        {
            name: {
                "status": "UNKNOWN",
                "method": "unproven-v1",
                "ruleVersion": "readiness-v1",
                "reason": "TIME_UNPROVEN"
                if name == "temporalAuthenticity"
                else "NO_EXTERNAL_PROOF",
                "evidenceRefs": [],
            }
            for name in DIMENSIONS
        }
    )


@dataclass(frozen=True)
class EvidenceInput:
    evidence_id: UUID
    source_id: str
    evidence_key: str
    kind: str
    instrument_id: UUID
    start_at: datetime
    end_at: datetime
    source_observed_at: datetime
    published_at: datetime | None
    revision: int
    predecessor_id: UUID | None
    fingerprint: str
    statement: Mapping
    trust: Mapping
    provenance: Mapping

    def __post_init__(self):
        for name in ("source_id", "evidence_key"):
            if not isinstance(getattr(self, name), str) or not _LABEL.fullmatch(
                getattr(self, name)
            ):
                raise ValueError("opaque evidence identity required")
        if self.kind not in {"CALENDAR", "QUALITY", "CORPORATE_ACTION"}:
            raise ValueError("typed evidence kind required")
        if not all(isinstance(value, UUID) for value in (self.evidence_id, self.instrument_id)):
            raise ValueError("typed UUID required")
        for value in (self.start_at, self.end_at, self.source_observed_at):
            utc_time(value)
        if self.published_at is not None:
            utc_time(self.published_at)
            if self.published_at > self.source_observed_at:
                raise ValueError("publication after source observation")
        if self.end_at <= self.start_at:
            raise ValueError("invalid evidence interval")
        if type(self.revision) is not int or not 1 <= self.revision <= 2147483647:
            raise ValueError("positive bounded revision required")
        if (self.revision == 1) != (self.predecessor_id is None) or (
            self.predecessor_id is not None and not isinstance(self.predecessor_id, UUID)
        ):
            raise ValueError("typed predecessor binding required")
        if not isinstance(self.fingerprint, str) or not _HASH.fullmatch(self.fingerprint):
            raise ValueError("typed fingerprint required")
        for name in ("statement", "trust", "provenance"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError("normalized object required")
            object.__setattr__(self, name, _freeze(value))
        if set(self.trust) != set(DIMENSIONS):
            raise ValueError("five independent trust dimensions required")
        for value in self.trust.values():
            if (
                not isinstance(value, Mapping)
                or set(value) != {"status", "method", "ruleVersion", "reason", "evidenceRefs"}
                or value["status"] not in TRUST_STATUSES
            ):
                raise ValueError("closed typed trust result required")
            if not isinstance(value["evidenceRefs"], tuple):
                raise ValueError("typed evidence references required")
        json.dumps(_plain(self.statement), allow_nan=False)
        json.dumps(_plain(self.provenance), allow_nan=False)

    def as_mapping(self):
        return {
            "evidenceId": str(self.evidence_id),
            "sourceId": self.source_id,
            "evidenceKey": self.evidence_key,
            "kind": self.kind,
            "instrumentId": str(self.instrument_id),
            "startAt": _plain(self.start_at),
            "endAt": _plain(self.end_at),
            "sourceObservedAt": _plain(self.source_observed_at),
            "publishedAt": _plain(self.published_at),
            "revision": self.revision,
            "predecessorId": _plain(self.predecessor_id),
            "fingerprint": self.fingerprint,
            "statement": _plain(self.statement),
            "trust": _plain(self.trust),
            "provenance": _plain(self.provenance),
        }


@dataclass(frozen=True)
class EvidenceReceipt:
    receipt_id: UUID
    entry_ids: tuple[UUID, ...]
    commit_version: int
    ingested_at: datetime
    trust_version: int
    commit_confirmed_at: datetime | None = None


class EvidenceStore:
    def append_bundle(
        self,
        connection,
        bundle_key: str,
        bundle_fingerprint: str,
        entries: tuple[EvidenceInput, ...],
        expected_trust_version: int,
    ) -> EvidenceReceipt:
        """Ein SQL-Aufruf; Transaktionscommit bleibt beim Aufrufer, kein Teilwrite."""
        if (
            not isinstance(entries, tuple)
            or not 1 <= len(entries) <= 1000
            or any(not isinstance(entry, EvidenceInput) for entry in entries)
        ):
            raise ValueError("bounded typed evidence entries required")
        if type(expected_trust_version) is not int or expected_trust_version < 0:
            raise ValueError("explicit trust snapshot required")
        payload = json.dumps([entry.as_mapping() for entry in entries], allow_nan=False)
        receipt_id = connection.execute(
            text(
                "SELECT research.append_evidence_bundle(:id,:key,:fingerprint,"
                "CAST(:entries AS jsonb),:expected)"
            ),
            dict(
                id=uuid4(),
                key=bundle_key,
                fingerprint=bundle_fingerprint,
                entries=payload,
                expected=expected_trust_version,
            ),
        ).scalar_one()
        return self.read_receipt(connection, receipt_id)

    def read_receipt(self, connection, receipt_id: UUID) -> EvidenceReceipt:
        row = (
            connection.execute(
                text("""
          SELECT r.*,c.confirmed_at FROM research.import_receipts r
          LEFT JOIN research.commit_confirmations c USING(receipt_id)
          WHERE r.receipt_id=:id
        """),
                {"id": receipt_id},
            )
            .mappings()
            .one()
        )
        return EvidenceReceipt(
            row["receipt_id"],
            tuple(row["entry_ids"]),
            row["commit_version"],
            row["ingested_at"],
            row["trust_version"],
            row["confirmed_at"],
        )

    def resolve_predecessor(self, connection, source_id, evidence_key, fingerprint):
        return connection.execute(
            text("""
          SELECT evidence_id FROM research.evidence_revisions
          WHERE source_id=:source AND evidence_key=:key AND fingerprint=:fingerprint
          ORDER BY revision DESC LIMIT 1
        """),
            dict(source=source_id, key=evidence_key, fingerprint=fingerprint),
        ).scalar_one_or_none()

    def append_trust_decision(self, connection, record: Mapping, expected_version: int):
        if not isinstance(record, Mapping) or type(expected_version) is not int:
            raise ValueError("typed versioned trust decision required")
        decision_id = connection.execute(
            text("SELECT research.append_trust_decision(CAST(:record AS jsonb),:expected)"),
            dict(record=json.dumps(_plain(record), allow_nan=False), expected=expected_version),
        ).scalar_one()
        return next(
            decision
            for decision in read_trust_snapshot(connection)["decisions"]
            if decision["decisionId"] == str(decision_id)
        )

    def confirm_commit(self, connection, receipt_id: UUID, kind="IMPORT"):
        """Neue Transaktion erforderlich; Datenbank lehnt Bestätigung im Write-XID ab."""
        return connection.execute(
            text("SELECT research.confirm_receipt(:id,:kind)"), dict(id=receipt_id, kind=kind)
        ).scalar_one()

    def record_rejection(self, connection, code: str):
        """Nur redigierten internen Code in separater Audittabelle aufzeichnen."""
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
            raise ValueError("redacted rejection code required")
        return connection.execute(
            text("SELECT research.record_import_rejection(:id,:code)"), dict(id=uuid4(), code=code)
        ).scalar_one()


def read_trust_snapshot(connection, knowledge_at=None, snapshot_version=None):
    """Aktueller Truststand und eingefroren lesbare Datenrevisionen bleiben getrennt."""
    state = (
        connection.execute(
            text("""
      SELECT t.version,t.policy_version,v.version AS data_version,clock_timestamp() AS at
      FROM research.trust_version_state t CROSS JOIN research.version_state v
      WHERE t.singleton AND v.singleton
    """)
        )
        .mappings()
        .one()
    )
    decisions = []
    for row in connection.execute(
        text("""
      SELECT * FROM research.trust_decisions WHERE version<=:version ORDER BY version LIMIT 100001
    """),
        {"version": state["version"]},
    ).mappings():
        decisions.append(
            {
                **row["body"],
                "recordedAt": row["recorded_at"],
                "version": row["version"],
                "commitVersion": row["commit_version"],
            }
        )
        if len(decisions) > 100000:
            raise EvidenceBudgetExceeded("trust decision budget exhausted")
    records = []
    authorities = {}
    for decision in decisions:
        if datetime.fromisoformat(decision["effectiveAt"]) <= state["at"]:
            try:
                key = base64.b64decode(decision["publicKey"], validate=True)
                pinned_fingerprint = hashlib.sha256(key).hexdigest() if len(key) == 32 else None
            except (binascii.Error, TypeError, ValueError):
                pinned_fingerprint = None
            authorities[
                (
                    decision["sourceId"],
                    decision["keyId"],
                    decision["authorityType"],
                    decision.get("witnessId"),
                )
            ] = {**decision, "pinned_fingerprint": pinned_fingerprint}
    visible_version = state["data_version"] if snapshot_version is None else snapshot_version
    visible_at = state["at"] if knowledge_at is None else knowledge_at
    for row in connection.execute(
        text("""
      SELECT * FROM research.evidence_revisions
      WHERE commit_version<=:version AND ingested_at<=:at
      ORDER BY commit_version,evidence_id LIMIT 100001
    """),
        dict(version=visible_version, at=visible_at),
    ).mappings():
        revoked = _currently_invalid(row, authorities, state["at"])
        records.append(
            {
                "reference": str(row["evidence_id"]),
                "kind": row["kind"],
                "dimensions": row["trust"],
                "revoked": revoked,
                "observed_at": row["source_observed_at"],
                "ingested_at": row["ingested_at"],
                "commit_version": row["commit_version"],
                "revision": row["revision"],
                "predecessor_id": row["predecessor_id"],
            }
        )
        if len(records) > 100000:
            raise EvidenceBudgetExceeded("evidence metadata budget exhausted")
    return {
        "version": state["version"],
        "policy_version": state["policy_version"],
        "decisions": tuple(decisions),
        "records": tuple(records),
        "data_version": state["data_version"],
        "snapshot_at": state["at"],
    }


def _currently_invalid(row, authorities, now):
    """Aktuelle Autoritätsentscheidungen werden nie vom alten Datensnapshot ersetzt."""
    content = row["provenance"].get("content", {})
    identities = [("SOURCE", content.get("keyId"), None)]
    proof = row["provenance"].get("temporalProof", {}).get("payload")
    if proof is not None:
        identities.append(("WITNESS", proof.get("keyId"), proof.get("witnessId")))

    def at(value):
        return value if isinstance(value, datetime) else datetime.fromisoformat(value)

    for authority_type, key_id, witness_id in identities:
        dimension = "sourceAuthenticity" if authority_type == "SOURCE" else "temporalAuthenticity"
        result = row["trust"][dimension]
        verified = result["status"] == "VERIFIED"
        latest = authorities.get((row["source_id"], key_id, authority_type, witness_id))
        if latest is None:
            if verified:
                return True
            # Lokale unkonfigurierte Beobachtungen bleiben ausdrücklich unbekannt.
            continue
        scope = latest["scope"]
        if (
            latest["revoked"]
            or not latest["compromiseTimeKnown"]
            or (latest["compromisedAt"] is not None)
            or not at(latest["validFrom"]) <= now < at(latest["validTo"])
        ):
            return True
        signed_payload = content if authority_type == "SOURCE" else proof
        if verified and (
            signed_payload is None
            or signed_payload.get("signedAt") is None
            or not at(latest["validFrom"]) <= at(signed_payload["signedAt"]) < at(latest["validTo"])
            or at(signed_payload["signedAt"]) > now
        ):
            return True
        key_references = [
            reference
            for reference in result.get("evidenceRefs", ())
            if isinstance(reference, Mapping)
            and reference.get("kind") == "KEY"
            and reference.get("id") == key_id
        ]
        if verified and not key_references:
            return True
        if any(
            reference.get("fingerprint") != latest["pinned_fingerprint"]
            for reference in key_references
        ) or (verified and latest["pinned_fingerprint"] is None):
            return True
        if str(row["instrument_id"]) not in scope["instrumentIds"] or (
            row["kind"] not in scope["kinds"]
        ):
            return True
        if "startAt" in scope and not (
            at(scope["startAt"]) <= row["start_at"] < row["end_at"] <= at(scope["endAt"])
        ):
            return True
        if (
            proof is not None
            and authority_type == "WITNESS"
            and (proof["assertion"] not in scope.get("assertions", ()))
        ):
            return True
    return False


def snapshot(connection):
    state = read_trust_snapshot(connection)
    return {
        "snapshot_version": state["data_version"],
        "snapshot_at": state["snapshot_at"],
        "trust_version": state["version"],
        "trust_policy_version": state["policy_version"],
    }
