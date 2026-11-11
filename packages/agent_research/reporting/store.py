"""Persist approved research report versions and project current rights on reads."""

import json
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator
from sqlalchemy import text

_CONTRACT = Path(__file__).resolve().parents[3] / "contracts/schemas/v1/agent-research.schema.json"
_REJECTION_CODES = frozenset({
    "REPORT_OUTPUT_SHAPE_INVALID",
    "REPORT_OUTPUT_REFERENCE_INVALID",
    "REPORT_OUTPUT_NOT_COMPARABLE",
    "REPORT_OUTPUT_PREDICATE_MISMATCH",
})
_RIGHTS_REVOKED = frozenset({"LICENSE_DENIED", "TRUST_REVOKED"})


class ReportStoreError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema | {"$ref": "#/$defs/AgentReportV1"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def _scope(connection, batch_id, actor):
    scope = connection.execute(text("""
        SELECT agent_research.report_scope(:batch,:actor)
    """), {"batch": batch_id, "actor": actor}).scalar_one()
    if scope is not None:
        scope["createdAt"] = (
            datetime.fromisoformat(scope["createdAt"]).astimezone(UTC).isoformat()
            .replace("+00:00", "Z")
        )
    return scope


def _seal(document, created_at):
    try:
        historical = dict(document)
        historical.pop("currentRightsWarnings", None)
        summary = dict(historical["summary"])
        summary.pop("reportHash", None)
        summary["createdAt"] = created_at
        historical["summary"] = summary
        canonical = json.dumps(
            historical, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        )
        summary["reportHash"] = sha256(canonical.encode("ascii")).hexdigest()
        if len(canonical) > 12582912 or not _validator().is_valid(
            historical | {"currentRightsWarnings": []},
        ):
            raise ValueError
        return historical, canonical
    except (TypeError, ValueError, KeyError, RecursionError, OverflowError):
        raise ReportStoreError("REPORT_INVALID") from None


class ReportStore:
    """SQL port for documents assembled by the server-side report builder."""

    def __init__(self, engine):
        self._engine = engine

    def authorize(self, batch_id, *, actor):
        with self._engine.connect() as connection:
            return _scope(connection, batch_id, actor)

    def publish(self, document, *, actor, rejection_code=None, claim=None):
        try:
            batch_id = UUID(document["summary"]["batchId"])
        except (AttributeError, KeyError, ValueError, TypeError):
            raise ReportStoreError("REPORT_INVALID") from None

        with self._engine.begin() as connection:
            if claim is not None:
                self._require_claim(connection, claim, actor, batch_id)
            scope = _scope(connection, batch_id, actor)
            if scope is None:
                raise ReportStoreError("REPORT_SCOPE_DENIED")
            historical, canonical = _seal(document, scope["createdAt"])
            summary = historical["summary"]
            binding = scope["binding"]
            if (
                summary["ideaId"] != binding["ideaId"]
                or summary["ideaVersion"] != binding["ideaVersion"]
                or historical["hypothesisVersion"] != binding["hypothesisVersion"]
                or historical["experimentId"] != binding["experimentId"]
                or historical["definitionVersion"] != binding["definitionVersion"]
                or historical["definitionHash"] != binding["definitionHash"]
                or historical["manifest"]["manifestHash"] != scope["manifestHash"]
                or historical["definition"] != scope["definition"]
            ):
                raise ReportStoreError("REPORT_SCOPE_DENIED")

            if rejection_code is not None:
                if summary["interpretationStatus"] != "FACTS_ONLY":
                    raise ReportStoreError("REPORT_INVALID")
                if type(rejection_code) is not str or rejection_code not in _REJECTION_CODES:
                    rejection_code = "REPORT_OUTPUT_SHAPE_INVALID"

            params = {
                "report": summary["reportId"], "version": summary["reportVersion"],
                "idea": summary["ideaId"], "idea_version": summary["ideaVersion"],
                "batch": batch_id, "approval": scope["approvalId"], "actor": actor,
                "hash": summary["reportHash"], "canonical": canonical,
                "body": json.dumps(historical, ensure_ascii=True, allow_nan=False),
                "code": rejection_code,
            }
            connection.execute(text("""
                SELECT pg_advisory_xact_lock(hashtextextended('report:' || :report,0))
            """), params)
            connection.execute(text("""
                SELECT pg_advisory_xact_lock(
                    hashtextextended('report-batch:' || CAST(:batch AS text),0)
                )
            """), params)
            matches = connection.execute(text("""
                SELECT report_id,batch_id,version,canonical_text
                FROM agent_research.report_versions
                WHERE report_id=:report OR batch_id=:batch
                ORDER BY version DESC
            """), params).mappings().all()
            if any(row["report_id"] != params["report"] or row["batch_id"] != batch_id
                   for row in matches):
                raise ReportStoreError("REPORT_VERSION_CONFLICT")
            previous = next((row for row in matches if row["version"] == params["version"]), None)
            if previous is not None:
                if previous["canonical_text"] != canonical:
                    raise ReportStoreError("REPORT_VERSION_CONFLICT")
            else:
                expected_version = matches[0]["version"] + 1 if matches else 1
                if params["version"] != expected_version:
                    raise ReportStoreError("REPORT_VERSION_CONFLICT")
                connection.execute(text("""
                    INSERT INTO agent_research.report_versions
                      (report_id,version,idea_id,idea_version,batch_id,approval_id,actor,
                       content_hash,canonical_text,document)
                    VALUES (:report,:version,:idea,:idea_version,:batch,:approval,:actor,
                      :hash,:canonical,CAST(:body AS jsonb))
                """), params)
            if rejection_code is not None:
                connection.execute(text("""
                    INSERT INTO agent_research.report_rejections (report_id,version,code)
                    VALUES (:report,:version,:code) ON CONFLICT DO NOTHING
                """), params)
            if claim is not None:
                self._require_claim(connection, claim, actor, batch_id)
            return self._project(connection, historical, actor)

    @staticmethod
    def _require_claim(connection, claim, actor, batch_id):
        held = connection.execute(text("""
            SELECT run_id FROM agent_research.runs
            WHERE run_id=:run AND actor=:actor AND owner=:owner AND fence=:fence
              AND state='RUNNING' AND lease_until>clock_timestamp()
              AND deadline_at>clock_timestamp() FOR UPDATE
        """), {"run": claim.run_id, "actor": actor, "owner": claim.owner,
                "fence": claim.fence}).scalar_one_or_none()
        if held is None:
            raise ReportStoreError("REPORT_SCOPE_DENIED")
        bound = connection.execute(text("""
            SELECT batch_id FROM agent_research.claimed_quant_result(:run,:owner,:fence)
        """), {"run": claim.run_id, "owner": claim.owner,
                "fence": claim.fence}).scalar_one_or_none()
        if bound != batch_id:
            raise ReportStoreError("REPORT_SCOPE_DENIED")

    def get(self, report_id, *, actor, version=None):
        with self._engine.connect() as connection:
            row = connection.execute(text("""
                SELECT document FROM agent_research.report_versions
                WHERE report_id=:report AND actor=:actor
                  AND (CAST(:version AS bigint) IS NULL OR version=:version)
                ORDER BY version DESC LIMIT 1
            """), {"report": report_id, "actor": actor, "version": version}).scalar_one_or_none()
            return None if row is None else self._project(connection, row, actor)

    def summaries(self, *, actor):
        with self._engine.connect() as connection:
            return tuple(connection.execute(text("""
                SELECT DISTINCT ON (report_id) document->'summary'
                FROM agent_research.report_versions
                WHERE actor=:actor ORDER BY report_id,version DESC
            """), {"actor": actor}).scalars())

    @staticmethod
    def _project(connection, historical, actor):
        batch_id = historical["summary"]["batchId"]
        row = connection.execute(text("""
            SELECT evidence_id,reason,statement_timestamp() AS observed_at
            FROM agent_research.report_rights(:batch,:actor)
        """), {"batch": batch_id, "actor": actor}).one()
        warnings = []
        if row.reason is not None:
            warnings.append({
                "evidenceId": row.evidence_id,
                "status": "RIGHTS_REVOKED" if row.reason in _RIGHTS_REVOKED else "STALE",
                "observedAt": row.observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            })
        return historical | {"currentRightsWarnings": warnings}
