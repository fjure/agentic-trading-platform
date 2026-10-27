"""Authentifizierter SQLport für gebundene Berichte; keine Beschaffung/Simulation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

from sqlalchemy import text

from packages.backtesting.readiness_jobs import ReadinessError, ReadinessJobs
from packages.backtesting.request import canonical_request
from packages.domain.models import utc_string
from packages.research.evidence_storage import (
    EvidenceBudgetExceeded,
    _currently_invalid,
    read_trust_snapshot,
)
from services.control_api.backtest_dto import BacktestRequest
from services.control_api.backtest_service import identity, pairs, stamp
from services.control_api.readiness_dto import RESPONSE_MODELS

BASE = "/api/v1/research/data-readiness/assessments"
CATEGORIES = frozenset(
    (
        "CALENDAR_UNKNOWN",
        "CALENDAR_CHANGED",
        "AMBIGUOUS_DATA",
        "BAR_MISSING",
        "OBSERVED_TOO_LATE",
        "WARMUP_MISSING",
        "QUALITY_REJECTED",
        "CORPORATE_ACTIONS_UNKNOWN",
        "CORPORATE_ACTIONS_CONFLICT",
        "TIME_UNPROVEN",
        "EVIDENCE_REVOKED",
        "NOT_EVALUATED",
    )
)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _pack(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unpack(value):
    result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _pack(result) != value:
        raise ValueError("noncanonical cursor")
    return result


class ReadinessService:
    def __init__(self, engine, *, code_version, cursor_key=None):
        if not isinstance(code_version, str) or not re.fullmatch(r"[0-9a-f]{40}", code_version):
            raise ValueError("verified code version required")
        self.jobs, self.engine, self.code_version = ReadinessJobs(engine), engine, code_version
        if cursor_key is None:
            with self.jobs.transaction() as connection:
                cursor_key = connection.execute(
                    text("SELECT signing_key FROM backtest.cursor_state WHERE singleton")
                ).scalar_one()
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 32:
            raise ValueError("persistent cursor signing material required")
        self._cursor_key = cursor_key

    @staticmethod
    def _row(connection, assessment_id):
        row = (
            connection.execute(
                text("""
            SELECT r.*,p.body AS report,p.fingerprint AS report_hash
            FROM backtest.readiness_jobs r LEFT JOIN backtest.readiness_reports p
              USING(assessment_id) WHERE assessment_id=:id
        """),
                dict(id=identity(assessment_id)),
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ReadinessError(404, "NOT_FOUND")
        return row

    def create_assessment(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise ReadinessError(403, "FORBIDDEN")
        value = BacktestRequest.model_validate(payload).model_dump()
        with self.jobs.transaction() as connection:
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        value = canonical_request(value, now)
        row = self.jobs.enqueue(value, actor, key, trace, self.code_version)
        # Replay preserves the original semantic acceptance receipt, never
        # substitutes current progress for the idempotent POST response.
        return dict(
            assessmentId=str(row["assessment_id"]),
            status="ACCEPTED",
            eligibility="NOT_EVALUATED",
            detailLink=BASE + "/" + str(row["assessment_id"]),
        )

    @staticmethod
    def _revocation(connection, row):
        try:
            state = read_trust_snapshot(connection, row["snapshot_at"], row["snapshot_version"])
        except EvidenceBudgetExceeded:
            raise ReadinessError(503, "UNAVAILABLE") from None
        bindings = (
            connection.execute(
                text("""
            SELECT kind,reference FROM backtest.readiness_bindings
            WHERE assessment_id=:id ORDER BY kind,reference
        """),
                dict(id=row["assessment_id"]),
            )
            .mappings()
            .all()
        )
        records = {(record["kind"], record["reference"]): record for record in state["records"]}
        normalized_kind = {
            "CALENDAR": "CALENDAR_REVISION",
            "QUALITY": "QUALITY_REVISION",
            "CORPORATE_ACTION": "CORPORATE_ACTIONS_REVISION",
            "BAR": "BAR_REVISION",
        }
        records.update(
            {
                (normalized_kind.get(kind, kind), reference): record
                for (kind, reference), record in tuple(records.items())
            }
        )
        metadata = {
            str(item["evidence_id"]): dict(item)
            for item in connection.execute(
                text("""
                SELECT e.* FROM research.evidence_revisions e
                JOIN backtest.readiness_bindings b ON e.evidence_id::text=b.reference
                WHERE b.assessment_id=:id ORDER BY e.evidence_id LIMIT 100001
            """),
                dict(id=row["assessment_id"]),
            ).mappings()
        }
        if len(metadata) > 100000:
            raise ReadinessError(503, "UNAVAILABLE")
        authorities = {}
        for decision in state["decisions"]:
            if datetime.fromisoformat(decision["effectiveAt"]) > state["snapshot_at"]:
                continue
            try:
                key = base64.b64decode(decision["publicKey"], validate=True)
                fingerprint = hashlib.sha256(key).hexdigest() if len(key) == 32 else None
            except (ValueError, TypeError):
                fingerprint = None
            authority = (
                decision["sourceId"],
                decision["keyId"],
                decision["authorityType"],
                decision.get("witnessId"),
            )
            authorities[authority] = {**decision, "pinned_fingerprint": fingerprint}
        grouped = {}
        complete = True
        for binding in bindings:
            record = records.get((binding["kind"], binding["reference"]))
            if record is None:
                complete = False
                continue
            if not record["revoked"]:
                continue
            evidence = metadata.get(binding["reference"])
            if evidence is None:
                complete = False
                continue
            content = evidence["provenance"].get("content", {})
            proof = evidence["provenance"].get("temporalProof", {}).get("payload")
            chains = [("SOURCE", content.get("keyId"), None)]
            if proof is not None:
                chains.append(("WITNESS", proof.get("keyId"), proof.get("witnessId")))
            found = False
            for authority_type, key_id, witness_id in chains:
                # Reuse the storage's EXACT current trust gate for the isolated
                # chain. Same key labels belonging to another source/authority
                # or future-effective decisions cannot supply a warning ref.
                isolated = {**evidence, "trust": dict(evidence["trust"])}
                if authority_type == "SOURCE":
                    isolated["provenance"] = {"content": content}
                else:
                    isolated["provenance"] = {"content": {}, "temporalProof": {"payload": proof}}
                    isolated["trust"]["sourceAuthenticity"] = {"status": "UNKNOWN"}
                if not _currently_invalid(isolated, authorities, state["snapshot_at"]):
                    continue
                decision = authorities.get(
                    (evidence["source_id"], key_id, authority_type, witness_id)
                )
                if decision is None:
                    complete = False
                    continue
                found = True
                group = grouped.setdefault(decision["decisionId"], [decision, []])
                group[1].append(
                    dict(
                        kind=normalized_kind.get(binding["kind"], binding["kind"]),
                        id=binding["reference"],
                        revision=None,
                        fingerprint=None,
                    )
                )
            if not found:
                complete = False
        warnings = []
        for identifier, (decision, references) in sorted(grouped.items()):
            for offset in range(0, len(references), 200):
                if len(warnings) == 200:
                    complete = False
                    break
                compromised = decision.get("compromisedAt") is not None or not decision.get(
                    "compromiseTimeKnown", True
                )
                effective = decision.get("effectiveAt")
                warnings.append(
                    dict(
                        decisionRef=dict(
                            kind="TRUST_DECISION",
                            id=identifier,
                            revision=str(decision["version"]),
                            fingerprint=None,
                        ),
                        affectedReferences=references[offset : offset + 200],
                        reason="KEY_COMPROMISED"
                        if compromised
                        else "KEY_REVOKED"
                        if decision.get("revoked")
                        else "TRUST_DECISION_REVISED",
                        effectiveAt=utc_string(effective)
                        if hasattr(effective, "tzinfo")
                        else effective,
                        recordedAt=utc_string(decision["recordedAt"]),
                        compromiseTimeKnown=decision.get("compromiseTimeKnown", True),
                    )
                )
        return dict(
            checkedAt=utc_string(state["snapshot_at"]),
            currentTrustVersion=state["version"],
            currentTrustPolicyVersion=state["policy_version"],
            warnings=warnings,
            complete=complete,
        )

    def read_assessment(self, assessment_id):
        with self.jobs.transaction(repeatable=True) as connection:
            row = self._row(connection, assessment_id)
            report = row["report"]
            if report is not None and report["reportHash"] != row["report_hash"]:
                raise ReadinessError(503, "UNAVAILABLE")
            value = dict(
                assessmentId=str(row["assessment_id"]),
                status=row["status"],
                eligibility=row["eligibility"],
                request=row["request"],
                requestHash=row["request_hash"],
                acceptedAt=utc_string(row["accepted_at"]),
                updatedAt=utc_string(row["updated_at"]),
                completedAt=utc_string(row["updated_at"])
                if row["status"] in ("SUCCEEDED", "FAILED")
                else None,
                attempt=row["attempts"],
                failureCode=row["error_code"],
                publicationComplete=row["status"] == "SUCCEEDED" and report is not None,
                report=report,
                currentRevocation=self._revocation(connection, row),
                detailLink=BASE + "/" + str(row["assessment_id"]),
            )
        return RESPONSE_MODELS["read_assessment"].model_validate(value).model_dump()

    def _sign(self, value):
        raw = _encoded(value)
        token = _pack(raw) + "." + _pack(hmac.digest(self._cursor_key, raw, "sha256"))
        if len(token) > 2048:
            raise ReadinessError(503, "UNAVAILABLE")
        return token

    def _decode(self, token):
        try:
            if (
                not isinstance(token, str)
                or len(token) > 2048
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token)
            ):
                raise ValueError()
            payload, signature = token.split(".")
            raw = _unpack(payload)
            if not hmac.compare_digest(
                _unpack(signature), hmac.digest(self._cursor_key, raw, "sha256")
            ):
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=pairs)
            if set(value) != {
                "assessment",
                "report",
                "snapshotVersion",
                "trustVersion",
                "trustPolicyVersion",
                "category",
                "position",
                "expiresAt",
            }:
                raise ValueError()
            if type(value["position"]) is not int or not 0 <= value["position"] < 10000:
                raise ValueError()
            stamp(value["expiresAt"])
            return value
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ReadinessError(400, "CURSOR_INVALID") from None

    def read_findings(self, assessment_id, values):
        if set(values) - {"limit", "cursor", "category"}:
            raise ReadinessError(400, "INVALID_REQUEST")
        limit = int(values.get("limit", 100))
        category = values.get("category")
        if not 1 <= limit <= 200 or (category is not None and category not in CATEGORIES):
            raise ReadinessError(400, "INVALID_REQUEST")
        cursor = self._decode(values["cursor"]) if "cursor" in values else None
        with self.jobs.transaction(repeatable=True) as connection:
            row = self._row(connection, assessment_id)
            if row["status"] != "SUCCEEDED" or row["report"] is None:
                raise ReadinessError(409, "REPORT_NOT_READY")
            report = row["report"]
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            binding = dict(
                assessment=assessment_id,
                report=row["report_hash"],
                snapshotVersion=report["snapshot"]["snapshotVersion"],
                trustVersion=report["snapshot"]["trustVersion"],
                trustPolicyVersion=report["snapshot"]["trustPolicyVersion"],
                category=category,
            )
            if cursor and (
                any(cursor[key] != value for key, value in binding.items())
                or stamp(cursor["expiresAt"]) <= now
            ):
                raise ReadinessError(400, "CURSOR_INVALID")
            position = cursor["position"] if cursor else -1
            params = dict(
                id=identity(assessment_id), category=category, after=position, limit=limit + 1
            )
            items = (
                connection.execute(
                    text("""
                SELECT finding_index,body FROM backtest.readiness_findings
                WHERE assessment_id=:id AND finding_index>:after
                  AND(CAST(:category AS text) IS NULL OR category=:category)
                ORDER BY finding_index LIMIT :limit
            """),
                    params,
                )
                .mappings()
                .all()
            )
            total = connection.execute(
                text("""
                SELECT count(*) FROM backtest.readiness_findings WHERE assessment_id=:id
                  AND(CAST(:category AS text) IS NULL OR category=:category)
            """),
                params,
            ).scalar_one()
            next_cursor = None
            if len(items) > limit:
                next_cursor = self._sign(
                    dict(
                        **binding,
                        position=items[limit - 1]["finding_index"],
                        expiresAt=cursor["expiresAt"]
                        if cursor
                        else utc_string(now + timedelta(hours=1)),
                    )
                )
            value = dict(
                assessmentId=assessment_id,
                reportHash=row["report_hash"],
                snapshot=report["snapshot"],
                category=category,
                items=[item["body"] for item in items[:limit]],
                nextCursor=next_cursor,
                totalMatching=total,
            )
        return RESPONSE_MODELS["read_findings"].model_validate(value).model_dump()
