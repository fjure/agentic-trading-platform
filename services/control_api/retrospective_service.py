"""SQL-only retrospective admission and immutable, bounded public projections."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from packages.backtesting.jobs import BacktestError
from packages.backtesting.request import canonical_request
from packages.backtesting.retrospective import RESULT_LIMITATIONS
from packages.backtesting.retrospective_storage import RetrospectiveStorage
from packages.domain.models import utc_string
from services.control_api.backtest_service import identity
from services.control_api.retrospective_dto import COMPONENT_MODELS, BacktestCreateRequest

MODE = "RETROSPECTIVE_EXPLORATORY"
TERMINAL = {"SUCCEEDED", "BLOCKED", "FAILED"}
CONTEXT_KEYS = (
    "researchMode",
    "selectionPolicyVersion",
    "experimentVersion",
    "snapshotReference",
    "reliabilityReportId",
    "resultLimitations",
)


def context(row, report_id=None):
    retrospective = row.get("research_mode") == MODE
    return {
        "researchMode": MODE if retrospective else "STRICT_PIT",
        "selectionPolicyVersion": "retrospective-v1" if retrospective else "strict-pit-v1",
        "experimentVersion": "retrospective-experiment-v1"
        if retrospective
        else "backtest-experiment-v1",
        "snapshotReference": {
            "snapshotVersion": row["snapshot_version"],
            "snapshotAt": utc_string(row["snapshot_at"]),
        },
        "reliabilityReportId": str(report_id) if report_id else None,
        "resultLimitations": list(RESULT_LIMITATIONS) if retrospective else [],
    }


def normalize_request(payload, now, *, assessment=False):
    model = COMPONENT_MODELS["BacktestAssessmentRequest"] if assessment else BacktestCreateRequest
    value = model.model_validate(payload).model_dump()
    extra = {
        k: value[k]
        for k in ("researchMode", "selectionPolicyVersion", "assessmentId", "assessmentHash")
        if k in value
    }
    base = canonical_request({k: v for k, v in value.items() if k not in extra}, now)
    if extra.get("researchMode", "STRICT_PIT") == "STRICT_PIT":
        return base
    return {**base, **extra}


def failure_finding(identifier, code):
    return {
        "findingId": str(uuid5(NAMESPACE_URL, str(identifier) + ":" + code)),
        "category": "INTEGRITY",
        "code": code,
        "severity": "BLOCKER",
        "foldIndex": None,
        "period": None,
        "references": [],
    }


class RetrospectiveService:
    def __init__(self, parent):
        self.parent = parent
        self.storage = RetrospectiveStorage(parent.engine)

    @staticmethod
    def _report(connection, assessment_id, run_id=None, *, complete=True):
        clause = "r.run_id=:run" if run_id else "r.assessment_id=:id AND r.phase='PREVIEW'"
        columns = "r.report_id"
        if complete:
            columns += """,r.body,COALESCE((
              SELECT jsonb_agg(selected.item ORDER BY selected.ordinal) FROM (
                SELECT item,ordinal FROM jsonb_array_elements(r.findings)
                  WITH ORDINALITY entries(item,ordinal)
                WHERE item->>'severity'='BLOCKER' ORDER BY ordinal LIMIT 100
              ) selected
            ),'[]'::jsonb) AS findings"""
        return (
            connection.execute(
                text(f"SELECT {columns} FROM backtest.retrospective_reports r WHERE " + clause),
                {"id": assessment_id, "run": run_id},
            )
            .mappings()
            .one_or_none()
        )

    def assessment(self, connection, row):
        report = self._report(connection, row["assessment_id"])
        terminal = row["status"] in TERMINAL
        if row["status"] == "SUCCEEDED" and (report is None or not row["assessment_hash"]):
            raise BacktestError(503, "UNAVAILABLE")
        blockers = list(report["findings"]) if report else []
        if row["status"] in {"BLOCKED", "FAILED"} and not blockers:
            code = (
                "BUDGET_EXHAUSTED" if row["error_code"] == "BUDGET_EXHAUSTED" else "NOT_EVALUATED"
            )
            blockers = [failure_finding(row["assessment_id"], code)]
        return {
            "assessmentId": str(row["assessment_id"]),
            "assessmentHash": row["assessment_hash"],
            "status": row["status"],
            "request": row["request"],
            "inputHash": row["request_hash"],
            "acceptedAt": utc_string(row["accepted_at"]),
            "updatedAt": utc_string(row["updated_at"]),
            "completedAt": utc_string(row["updated_at"]) if terminal else None,
            **context({**row, "research_mode": MODE}, report["report_id"] if report else None),
            "eligibility": "BLOCKED"
            if row["status"] in {"BLOCKED", "FAILED"}
            else report["body"]["overallStatus"]
            if report
            else None,
            "blockers": blockers,
            "attempt": row["attempts"],
        }

    def create_assessment(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise BacktestError(403, "FORBIDDEN")
        with self.storage.transaction() as connection:
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        value = normalize_request(payload, now, assessment=True)
        row = self.storage.enqueue_assessment(value, actor, key, trace, self.parent.code_version)
        with self.storage.transaction() as connection:
            return self.assessment(connection, row)

    def read_assessment(self, assessment_id):
        with self.storage.transaction() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM backtest.retrospective_assessments WHERE assessment_id=:id"
                    ),
                    {"id": identity(assessment_id)},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise BacktestError(404, "NOT_FOUND")
            return self.assessment(connection, row)

    def run(self, connection, row, *, include_manifest=False):
        report = self._report(connection, row["assessment_id"], row["run_id"], complete=False)
        metadata = (
            connection.execute(
                text("""SELECT body->'content'->>'manifestHash' AS hash,
              CASE WHEN :include THEN body->'content' ELSE NULL::jsonb END AS public
              FROM backtest.manifests WHERE run_id=:id"""),
                {"id": row["run_id"], "include": include_manifest},
            )
            .mappings()
            .one_or_none()
        )
        manifest = metadata["public"] if metadata else None
        result = self.parent._published(connection, row["run_id"])
        success = row["status"] == "SUCCEEDED"
        if success and (
            report is None or metadata is None or not metadata["hash"] or result is None
        ):
            raise BacktestError(503, "UNAVAILABLE")
        if manifest is not None:
            COMPONENT_MODELS["BacktestRetrospectiveManifest"].model_validate(manifest)
        folds = (
            connection.execute(
                text(
                    "SELECT fold_index,body->'summary'->>'resultHash' AS hash "
                    "FROM backtest.fold_results WHERE run_id=:id ORDER BY fold_index"
                ),
                {"id": row["run_id"]},
            )
            .mappings()
            .all()
        )
        completed = [{"foldIndex": f["fold_index"], "resultHash": f["hash"]} for f in folds]
        blockers = []
        if row["status"] in {"BLOCKED", "FAILED"}:
            code = (
                row["error_code"]
                if row["error_code"] in {"BUDGET_EXHAUSTED", "INTERNAL_FAILURE"}
                else "BLOCKED_DATA_UNAVAILABLE"
            )
            blockers = [{"code": code, "foldIndex": None, "barStart": None, "evidenceId": None}]
        return {
            "runId": str(row["run_id"]),
            "status": row["status"],
            "request": row["request"],
            "acceptedAt": utc_string(row["accepted_at"]),
            "updatedAt": utc_string(row["updated_at"]),
            "completedAt": utc_string(row["updated_at"]) if row["status"] in TERMINAL else None,
            "snapshot": {
                "version": row["snapshot_version"],
                "committedAt": utc_string(row["snapshot_at"]),
            },
            "manifestHash": metadata["hash"] if metadata else None,
            "resultHash": result["fingerprint"] if success else None,
            "completedFolds": completed,
            "blockers": blockers,
            "attempt": row["attempts"],
            "publicationComplete": success,
            **context(row, report["report_id"] if report else None),
        }, manifest if include_manifest else None

    def current_warnings(self, assessment_id):
        from datetime import UTC, datetime

        checked = datetime.now(UTC)
        warnings = []

        def append(code):
            if any(value["code"] == code for value in warnings):
                return
            warnings.append(
                {
                    "code": code,
                    "category": "LICENSE"
                    if code.startswith("LICENSE_")
                    else "CORPORATE_ACTIONS"
                    if code in {"STRUCTURAL_CORPORATE_ACTION", "CORPORATE_ACTION_CONFLICT"}
                    else "HISTORICAL_AVAILABILITY"
                    if code == "EX_POST_REVISIONS"
                    else "INTEGRITY",
                    "checkedAt": utc_string(checked),
                    "blocksNewExecution": code not in {"LICENSE_UNKNOWN", "EX_POST_REVISIONS"},
                }
            )

        try:
            with self.storage.transaction() as connection:
                checked = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                code = self.storage.current_assessment_gate(connection, assessment_id)
                if code is None:
                    status = self.storage.current_license(connection)["status"]
                    code = "LICENSE_UNKNOWN" if status == "UNKNOWN" else None
                if code is not None:
                    append(code)
                if self.storage.has_retrospective_correction(connection, assessment_id):
                    append("EX_POST_REVISIONS")
        except Exception:
            append("TRUST_UNAVAILABLE")
        return warnings

    def read_reliability_report(self, report_id, values):
        section = values.get("section", "findings")
        limit = int(values.get("limit", 50))
        filters = {k: values[k] for k in ("category", "foldIndex") if k in values}
        binding = self.parent._binding(
            "reliability-report", {"reportId": report_id, "section": section, **filters}
        )
        token = self.parent._verify(values["cursor"], binding) if "cursor" in values else None
        with self.storage.transaction() as connection:
            report = (
                connection.execute(
                    text("""SELECT report_id,assessment_id,fingerprint,body
                      FROM backtest.retrospective_reports WHERE report_id=:id"""),
                    {"id": identity(report_id)},
                )
                .mappings()
                .one_or_none()
            )
            if report is None:
                raise BacktestError(404, "NOT_FOUND")
            snapshot = report["fingerprint"] + ":" + report["body"]["version"]
            if token and token["snapshot"] != snapshot:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
            position = token["position"] if token else 0
            if type(position) is not int or not 0 <= position <= 100000:
                raise BacktestError(400, "CURSOR_INVALID")
            # References are selected from immutable stored arrays. Category/fold
            # filters select the references attached to matching stored findings.
            column = "findings" if section == "findings" else "revision_references"
            prefix, join = "", ""
            predicate = """(CAST(:category AS text) IS NULL OR
              item->>'category'=CAST(:category AS text)) AND
              (CAST(:fold AS integer) IS NULL OR
                (item->>'foldIndex')::int=CAST(:fold AS integer))"""
            if section == "revisions":
                predicate = "TRUE"
                if filters:
                    # Build matching keys once, rather than rescanning every
                    # finding for each revision. DISTINCT prevents duplicate
                    # references from duplicating a page item.
                    prefix = """WITH matching_keys AS MATERIALIZED (
                      SELECT DISTINCT ref->>'kind' AS kind,ref->>'referenceId' AS id
                      FROM backtest.retrospective_reports source
                      CROSS JOIN LATERAL jsonb_array_elements(source.findings) f(finding)
                      CROSS JOIN LATERAL jsonb_array_elements(finding->'references') refs(ref)
                      WHERE source.report_id=:id AND
                        (CAST(:category AS text) IS NULL OR
                          finding->>'category'=CAST(:category AS text)) AND
                        (CAST(:fold AS integer) IS NULL OR
                          (finding->>'foldIndex')::int=CAST(:fold AS integer))
                    ) """
                    join = """JOIN matching_keys keys ON keys.kind=item->>'kind'
                      AND keys.id=item->>'referenceId'"""
            rows = (
                connection.execute(
                    text(f"""{prefix}SELECT item FROM backtest.retrospective_reports r
                CROSS JOIN LATERAL jsonb_array_elements(r.{column})
                  WITH ORDINALITY page(item,ordinal) {join}
                WHERE r.report_id=:id AND ({predicate})
                ORDER BY ordinal LIMIT :limit OFFSET :offset"""),
                    {
                        "id": identity(report_id),
                        "category": filters.get("category"),
                        "fold": int(filters["foldIndex"]) if "foldIndex" in filters else None,
                        "limit": limit + 1,
                        "offset": position,
                    },
                )
                .scalars()
                .all()
            )
            next_cursor = (
                self.parent._sign(
                    {"binding": binding, "snapshot": snapshot, "position": position + limit}
                )
                if len(rows) > limit
                else None
            )
            overview = dict(report["body"])
            assessment_id = report["assessment_id"]
        return {
            "report": overview,
            "section": section,
            "findings": rows[:limit] if section == "findings" else [],
            "revisionReferences": rows[:limit] if section == "revisions" else [],
            "nextCursor": next_cursor,
            "currentWarnings": self.current_warnings(assessment_id),
        }
