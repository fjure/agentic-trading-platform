"""Short SQL-only ports for immutable retrospective experiments.

The accepted assessment owns the DB snapshot. CPU work happens between these
transactions; recovery reads persisted references instead of selecting latest.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from packages.backtesting.jobs import BacktestError, encoded
from packages.backtesting.models import BacktestBudgetExceeded
from packages.domain.models import utc_string
from packages.research.models import _canonical, content_fingerprint

MODE = "RETROSPECTIVE_EXPLORATORY"
POLICY = "retrospective-v1"
_SOURCES = {
    "BAR": ("research.bar_revisions", "revision_id", "fingerprint", "bar_id"),
    "CALENDAR": ("research.calendar_versions", "session_id", "version", "calendar_id"),
    "QUALITY": ("research.quality_observations", "observation_id", None, "quality_id"),
    "EVIDENCE": ("research.evidence_revisions", "evidence_id", "fingerprint", "evidence_id"),
}
_IDENTITY = {
    "BAR": (
        "instrument_id",
        "provider",
        "feed",
        "interval",
        "bar_start",
        "bar_end",
        "adjustment",
        "local_revision",
    ),
    "CALENDAR": ("session_date", "opens_at", "closes_at", "version", "source"),
    "QUALITY": (
        "instrument_id",
        "start_at",
        "end_at",
        "interval",
        "quality",
        "rule_version",
        "code",
    ),
    "EVIDENCE": (
        "source_id",
        "evidence_key",
        "kind",
        "instrument_id",
        "start_at",
        "end_at",
        "revision",
    ),
}


@lru_cache(maxsize=3)
def _report_validator(component="BacktestReliabilityReportV1"):
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "contracts/schemas/v1/backtesting.schema.json"
        ).read_text(encoding="utf-8")
    )
    return Draft202012Validator({**schema, "$ref": "#/$defs/" + component})


@dataclass(frozen=True)
class RetrospectiveLease:
    assessment_id: UUID
    owner: UUID
    fence: int
    row: dict
    run_id: UUID | None = None


class RetrospectiveStorage:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, *, repeatable=False, lease=None):
        failure = None
        try:
            with self.engine.begin() as connection:
                if repeatable:
                    connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                connection.execute(text("SET LOCAL statement_timeout='2s'"))
                connection.execute(text("SET LOCAL lock_timeout='1s'"))
                connection.execute(
                    text("SELECT set_config('backtest.policy_version',:policy,true)"),
                    {"policy": POLICY},
                )
                if lease is not None:
                    connection.execute(
                        text(
                            "SELECT "
                            "set_config('backtest.owner',:owner,true),set_config('backtest.fence',:fence,true)"
                        ),
                        {"owner": str(lease.owner), "fence": str(lease.fence)},
                    )
                yield connection
        except SQLAlchemyError as error:
            message = getattr(
                getattr(getattr(error, "orig", None), "diag", None), "message_primary", None
            )
            status, code = {
                "backtest idempotency conflict": (409, "IDEMPOTENCY_CONFLICT"),
                "retrospective snapshot conflict": (409, "SNAPSHOT_CONFLICT"),
                "retrospective revision conflict": (409, "SNAPSHOT_CONFLICT"),
                "retrospective lease lost": (409, "LEASE_LOST"),
                "invalid retrospective request": (400, "INVALID_REQUEST"),
            }.get(message, (503, "UNAVAILABLE"))
            failure = BacktestError(status, code)
        if failure:
            raise failure from None

    def _enqueue(self, request, actor, key, trace, code_version, *, assessment):
        if actor != "local-operator":
            raise BacktestError(403, "FORBIDDEN")
        if (
            not isinstance(request, dict)
            or request.get("researchMode") != MODE
            or request.get("selectionPolicyVersion") != POLICY
            or not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key)
            or not isinstance(trace, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace)
            or not isinstance(code_version, str)
            or not re.fullmatch(r"[0-9a-f]{40}", code_version)
        ):
            raise BacktestError(400, "INVALID_REQUEST")
        function, table, id_field = (
            ("request_retrospective_assessment", "retrospective_assessments", "assessment_id")
            if assessment
            else ("request_retrospective_run", "runs", "run_id")
        )
        # Admission locks precede the one-statement snapshot capture in SQL.
        # READ COMMITTED lets a waiting enqueuer observe the previous commit.
        with self.transaction() as connection:
            identifier = connection.execute(
                text(
                    f"SELECT backtest.{function}("
                    ":id,:actor,:key,CAST(:body AS jsonb),:hash,:code,:trace)"
                ),
                dict(
                    id=uuid4(),
                    actor=actor,
                    key=key,
                    body=encoded(request),
                    hash=content_fingerprint(request),
                    code=code_version,
                    trace=trace,
                ),
            ).scalar_one()
            return dict(
                connection.execute(
                    text(f"SELECT * FROM backtest.{table} WHERE {id_field}=:id"), {"id": identifier}
                )
                .mappings()
                .one()
            )

    def enqueue_assessment(self, request, actor, key, trace, code_version):
        return self._enqueue(request, actor, key, trace, code_version, assessment=True)

    def enqueue_run(self, request, actor, key, trace, code_version):
        return self._enqueue(request, actor, key, trace, code_version, assessment=False)

    @staticmethod
    def current_license(connection):
        row = (
            connection.execute(text("SELECT * FROM backtest.source_license_status"))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BacktestError(503, "UNAVAILABLE")
        return dict(row)

    def license_receipt(self, assessment_id):
        """Read the immutable snapshot receipt, independently of current rights."""
        if not isinstance(assessment_id, UUID):
            raise ValueError("typed assessment id required")
        with self.transaction() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM backtest.license_receipts WHERE assessment_id=:id"),
                    {"id": assessment_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise BacktestError(404, "NOT_FOUND")
            return dict(row)

    @classmethod
    def require_current_license(cls, connection):
        if cls.current_license(connection)["status"] == "DENIED":
            raise BacktestError(409, "SNAPSHOT_CONFLICT")

    @staticmethod
    def _audit(connection, row, event, run=False):
        if run:
            from packages.backtesting.jobs import BacktestJobs

            BacktestJobs.audit(connection, row, event)
        else:
            connection.execute(
                text(
                    "INSERT INTO "
                    "backtest.retrospective_audit(assessment_id,event,fence,trace_id) "
                    "VALUES(:id,:event,:fence,:trace)"
                ),
                {
                    "id": row["assessment_id"],
                    "event": event,
                    "fence": row["fence"],
                    "trace": row["trace_id"],
                },
            )

    def claim(self, owner, *, run=False):
        """Lease bookkeeping, including jobs needing a terminal BLOCKED report.

        The worker must check current rights and trust before simulation CPU
        work. This SQL-only port never starts a simulation; positive publication
        rechecks the current license in this port and in the database trigger.
        """
        if not isinstance(owner, UUID):
            raise ValueError("typed owner required")
        table, id_field = (
            ("runs", "run_id") if run else ("retrospective_assessments", "assessment_id")
        )
        variant = " AND research_mode='RETROSPECTIVE_EXPLORATORY'" if run else ""
        with self.transaction() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(894217346)"))
            expired = (
                connection.execute(
                    text(f"""UPDATE backtest.{table} SET status='FAILED',
                error_code='BUDGET_EXHAUSTED',owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE status='RUNNING' AND lease_until<=clock_timestamp()
                  AND attempts>=3{variant} RETURNING *""")
                )
                .mappings()
                .all()
            )
            for row in expired:
                self._audit(connection, row, "FAILED", run)
            first = (
                connection.execute(text("SELECT * FROM backtest.oldest_job()"))
                .mappings()
                .one_or_none()
            )
            if first is None or first["kind"] != ("backtest" if run else "retrospective"):
                return None
            if (
                run
                and not connection.execute(
                    text("SELECT research_mode IS NOT NULL FROM backtest.runs WHERE run_id=:id"),
                    {"id": first["id"]},
                ).scalar_one()
            ):
                return None
            if connection.execute(
                text("""SELECT EXISTS(SELECT FROM backtest.runs
                  WHERE status='RUNNING' AND lease_until>clock_timestamp())
                OR EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING' AND
                  lease_until>clock_timestamp())
                OR EXISTS(SELECT FROM backtest.retrospective_assessments WHERE status='RUNNING'
                  AND lease_until>clock_timestamp())""")
            ).scalar_one():
                return None
            row = (
                connection.execute(
                    text(f"""UPDATE backtest.{table} SET status='RUNNING',owner=:owner,
                fence=fence+1,attempts=attempts+1,started_at=clock_timestamp(),
                lease_until=clock_timestamp()+interval '300 seconds',updated_at=clock_timestamp()
                WHERE {id_field}=:id{variant} RETURNING *"""),
                    {"id": first["id"], "owner": owner},
                )
                .mappings()
                .one()
            )
            self._audit(connection, row, "RUNNING", run)
            return RetrospectiveLease(
                row["assessment_id"], owner, row["fence"], dict(row), row["run_id"] if run else None
            )

    @staticmethod
    def fenced(connection, lease):
        if type(lease) is not RetrospectiveLease:
            raise ValueError("typed retrospective lease required")
        connection.execute(
            text("SELECT backtest.assert_retrospective_fence(:assessment,:run)"),
            {"assessment": lease.assessment_id, "run": lease.run_id},
        )

    def checkpoint(self, lease):
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)

    @staticmethod
    def _reference(connection, kind, identifier, fingerprint):
        if (
            kind not in _SOURCES
            or not isinstance(identifier, UUID)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        ):
            raise ValueError("closed typed revision reference required")
        table, key, hash_field, _ = _SOURCES[kind]
        expression = (
            f"r.{hash_field}"
            if hash_field
            else "encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex')"
        )
        row = (
            connection.execute(
                text(
                    f"SELECT r.*,{expression} AS binding_fingerprint FROM {table} r WHERE {key}=:id"
                ),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["binding_fingerprint"] != fingerprint:
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        # PostgreSQL's JSON timestamp representation is used for SQL identity
        # comparison; domain/wire UTC times remain unchanged in content.
        identity = connection.execute(
            text(f"SELECT to_jsonb(r) FROM {table} r WHERE {key}=:id"), {"id": identifier}
        ).scalar_one()
        return {
            "kind": kind,
            "referenceId": str(identifier),
            "fingerprint": fingerprint,
            "identity": {name: identity[name] for name in _IDENTITY[kind]},
        }

    def bind_dataset(self, lease, content, references=()):
        if lease.run_id is not None:
            raise ValueError("assessment owns dataset binding")
        references = tuple(references)
        if len(references) > 100000:
            raise BacktestBudgetExceeded("combined revision/metadata row budget exhausted")
        if len({(r[0], r[1]) for r in references}) != len(references):
            raise ValueError("duplicate revision binding")
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            bindings = [
                self._reference(connection, *reference)
                for reference in sorted(references, key=lambda r: (r[0], str(r[1])))
            ]
            license_fingerprint = connection.execute(
                text("SELECT fingerprint FROM backtest.license_receipts WHERE assessment_id=:id"),
                {"id": lease.assessment_id},
            ).scalar_one()
            body = {
                "researchMode": MODE,
                "selectionPolicyVersion": POLICY,
                "codeVersion": lease.row["code_version"],
                "trustVersion": lease.row["trust_version"],
                "trustPolicyVersion": lease.row["trust_policy_version"],
                "licenseReceiptFingerprint": license_fingerprint,
                "snapshotReference": {
                    "snapshotVersion": lease.row["snapshot_version"],
                    "snapshotAt": utc_string(lease.row["snapshot_at"]),
                },
                "protocolHash": lease.row["request_hash"],
                "content": _canonical(content),
                "bindings": bindings,
            }
            canonical = encoded(body)
            fingerprint = content_fingerprint(body)
            previous = connection.execute(
                text(
                    "SELECT fingerprint FROM backtest.retrospective_datasets WHERE "
                    "assessment_id=:id"
                ),
                {"id": lease.assessment_id},
            ).scalar_one_or_none()
            if previous is not None:
                if previous != fingerprint:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                return fingerprint
            connection.execute(
                text(
                    "INSERT INTO backtest.retrospective_datasets VALUES(:id,:hash,CAST(:body "
                    "AS jsonb),:canonical)"
                ),
                {
                    "id": lease.assessment_id,
                    "hash": fingerprint,
                    "body": canonical,
                    "canonical": canonical,
                },
            )
            for binding in bindings:
                column = _SOURCES[binding["kind"]][3]
                connection.execute(
                    text(f"""INSERT INTO backtest.retrospective_revision_bindings
                    (assessment_id,kind,reference_id,expected_fingerprint,expected_identity,{column})
                    VALUES(:id,:kind,:reference,:hash,CAST(:identity AS jsonb),:reference)"""),
                    {
                        "id": lease.assessment_id,
                        "kind": binding["kind"],
                        "reference": UUID(binding["referenceId"]),
                        "hash": binding["fingerprint"],
                        "identity": encoded(binding["identity"]),
                    },
                )
            self.fenced(connection, lease)
            return fingerprint

    def load_dataset(self, assessment_id):
        """Exact bound bodies and source rows, rechecking every stored fingerprint."""
        if not isinstance(assessment_id, UUID):
            raise ValueError("typed assessment id required")
        with self.transaction(repeatable=True) as connection:
            dataset = (
                connection.execute(
                    text("SELECT * FROM backtest.retrospective_datasets WHERE assessment_id=:id"),
                    {"id": assessment_id},
                )
                .mappings()
                .one_or_none()
            )
            if dataset is None:
                raise BacktestError(404, "NOT_FOUND")
            if content_fingerprint(dataset["body"]) != dataset["fingerprint"]:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
            rows = []
            for reference in dataset["body"]["bindings"]:
                restored = self._reference(
                    connection,
                    reference["kind"],
                    UUID(reference["referenceId"]),
                    reference["fingerprint"],
                )
                if restored != reference:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                table, key, _, _ = _SOURCES[reference["kind"]]
                row = (
                    connection.execute(
                        text(f"SELECT * FROM {table} WHERE {key}=:id"),
                        {"id": UUID(reference["referenceId"])},
                    )
                    .mappings()
                    .one()
                )
                rows.append((reference["kind"], dict(row)))
            return dict(dataset), tuple(rows)

    def _insert_report(self, connection, lease, report, findings=(), revision_references=()):
        findings, revision_references = list(findings), list(revision_references)
        envelope = {
            "report": report,
            "findings": findings,
            "revisionReferences": revision_references,
        }
        canonical = encoded(envelope)  # Enforce publication budget before schema traversal.
        try:
            _report_validator().validate(report)
            for item in findings:
                _report_validator("BacktestReliabilityFinding").validate(item)
            for item in revision_references:
                _report_validator("BacktestReliabilityReference").validate(item)
            if (
                report["findingsCount"] != len(findings)
                or report["revisionReferenceCount"] != len(revision_references)
                or len({item["findingId"] for item in findings}) != len(findings)
                or len({(item["kind"], item["referenceId"]) for item in revision_references})
                != len(revision_references)
            ):
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
        except ValidationError:
            raise BacktestError(409, "SNAPSHOT_CONFLICT") from None
        request_hash = connection.execute(
            text(
                "SELECT request_hash FROM backtest.retrospective_assessments "
                "WHERE assessment_id=:id"
            ),
            {"id": lease.assessment_id},
        ).scalar_one()
        if report["protocolHash"] != request_hash:
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        if (
            not isinstance(report, dict)
            or report.get("researchMode") != MODE
            or report.get("selectionPolicyVersion") != POLICY
            or report.get("assessmentId") != str(lease.assessment_id)
            or report.get("snapshotReference")
            != {
                "snapshotVersion": lease.row["snapshot_version"],
                "snapshotAt": utc_string(lease.row["snapshot_at"]),
            }
        ):
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        if report.get("runId") != (str(lease.run_id) if lease.run_id else None):
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        report_id = UUID(report["reportId"])
        connection.execute(
            text("""INSERT INTO backtest.retrospective_reports
            (report_id,assessment_id,run_id,phase,fingerprint,body,findings,revision_references,canonical_text)
            VALUES(:id,:assessment,:run,:phase,:hash,CAST(:body AS jsonb),
              CAST(:findings AS jsonb),CAST(:references AS jsonb),:canonical)"""),
            {
                "id": report_id,
                "assessment": lease.assessment_id,
                "run": lease.run_id,
                "phase": "FINAL" if lease.run_id else "PREVIEW",
                "hash": content_fingerprint(envelope),
                "body": encoded(report),
                "findings": encoded(findings),
                "references": encoded(revision_references),
                "canonical": canonical,
            },
        )

    def publish_assessment(
        self, lease, report=None, *, error_code=None, findings=(), revision_references=()
    ):
        if lease.run_id is not None:
            raise ValueError("assessment lease required")
        if (report is None) != (error_code is not None) or error_code not in (
            None,
            "BUDGET_EXHAUSTED",
            "INTERNAL_FAILURE",
        ):
            raise ValueError("complete report or explicit closed failure reason required")
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            status = "FAILED"
            if report is not None:
                if report.get("overallStatus") not in ("BLOCKED", "EXPLORATORY_WITH_LIMITATIONS"):
                    raise ValueError("closed report eligibility required")
                if report["overallStatus"] != "BLOCKED":
                    self.require_current_license(connection)
                self._insert_report(connection, lease, report, findings, revision_references)
                status = "BLOCKED" if report["overallStatus"] == "BLOCKED" else "SUCCEEDED"
            self.fenced(connection, lease)
            row = (
                connection.execute(
                    text("""UPDATE backtest.retrospective_assessments SET status=:status,
                error_code=:error,owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE assessment_id=:id RETURNING *"""),
                    {"id": lease.assessment_id, "status": status, "error": error_code},
                )
                .mappings()
                .one()
            )
            self._audit(connection, row, status)
            return dict(row)

    def _bind_run_manifest(self, connection, lease, content):
        dataset_hash = connection.execute(
            text("SELECT fingerprint FROM backtest.retrospective_datasets WHERE assessment_id=:id"),
            {"id": lease.assessment_id},
        ).scalar_one()
        body = {
            "researchMode": MODE,
            "selectionPolicyVersion": POLICY,
            "experimentVersion": "retrospective-experiment-v1",
            "assessmentId": str(lease.assessment_id),
            "datasetFingerprint": dataset_hash,
            "content": _canonical(content),
        }
        fingerprint = content_fingerprint(body)
        previous = connection.execute(
            text("SELECT fingerprint FROM backtest.manifests WHERE run_id=:id"),
            {"id": lease.run_id},
        ).scalar_one_or_none()
        if previous is not None:
            if previous != fingerprint:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
        else:
            connection.execute(
                text("INSERT INTO backtest.manifests VALUES(:id,:hash,CAST(:body AS jsonb))"),
                {"id": lease.run_id, "hash": fingerprint, "body": encoded(body)},
            )
        return fingerprint

    def bind_run_manifest(self, lease, content):
        if lease.run_id is None:
            raise ValueError("run lease required")
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            return self._bind_run_manifest(connection, lease, content)

    def checkpoint_run(self, lease, index, fold):
        if lease.run_id is None or type(index) is not int or not 0 <= index < 12:
            raise ValueError("bounded run checkpoint required")
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            previous = connection.execute(
                text(
                    "SELECT body FROM backtest.fold_results WHERE run_id=:id AND fold_index=:index"
                ),
                {"id": lease.run_id, "index": index},
            ).scalar_one_or_none()
            if previous is not None:
                if content_fingerprint(previous) != content_fingerprint(fold):
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                return
            connection.execute(
                text("INSERT INTO backtest.fold_results VALUES(:id,:index,CAST(:body AS jsonb))"),
                {"id": lease.run_id, "index": index, "body": encoded(fold)},
            )
            self.fenced(connection, lease)
            row = (
                connection.execute(
                    text(
                        "UPDATE backtest.runs SET "
                        "checkpoint=checkpoint+1,updated_at=clock_timestamp() WHERE run_id=:id "
                        "RETURNING *"
                    ),
                    {"id": lease.run_id},
                )
                .mappings()
                .one()
            )
            from packages.backtesting.jobs import BacktestJobs

            BacktestJobs.audit(connection, row, "CHECKPOINT")

    def publish_run(self, lease, result, report, *, manifest, findings=(), revision_references=()):
        if lease.run_id is None:
            raise ValueError("run lease required")
        # Bound the whole transaction, not just each individual JSON payload.
        encoded(
            {
                "result": result,
                "report": report,
                "manifest": manifest,
                "findings": findings,
                "revisionReferences": revision_references,
            }
        )
        eligibility = report.get("overallStatus")
        if eligibility not in ("BLOCKED", "EXPLORATORY_WITH_LIMITATIONS"):
            raise ValueError("closed report eligibility required")
        status = "BLOCKED" if eligibility == "BLOCKED" else "SUCCEEDED"
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            if status == "SUCCEEDED":
                self.require_current_license(connection)
            self._insert_report(connection, lease, report, findings, revision_references)
            self._bind_run_manifest(connection, lease, manifest)
            connection.execute(
                text(
                    "INSERT INTO backtest.results(run_id,fingerprint,body) "
                    "VALUES(:id,:hash,CAST(:body AS jsonb))"
                ),
                {"id": lease.run_id, "hash": content_fingerprint(result), "body": encoded(result)},
            )
            self.fenced(connection, lease)
            if status == "SUCCEEDED":
                self.require_current_license(connection)
            row = (
                connection.execute(
                    text("""UPDATE backtest.runs SET status=:status,owner=NULL,
                lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=:id RETURNING *"""),
                    {"id": lease.run_id, "status": status},
                )
                .mappings()
                .one()
            )
            self._audit(connection, row, status, True)
            return dict(row)

    def fail_run(self, lease, error_code):
        if lease.run_id is None or error_code not in ("BUDGET_EXHAUSTED", "INTERNAL_FAILURE"):
            raise ValueError("run lease and closed failure reason required")
        with self.transaction(lease=lease) as connection:
            self.fenced(connection, lease)
            row = (
                connection.execute(
                    text("""UPDATE backtest.runs SET status='FAILED',error_code=:error,
                owner=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE run_id=:id
                  RETURNING *"""),
                    {"id": lease.run_id, "error": error_code},
                )
                .mappings()
                .one()
            )
            self._audit(connection, row, "FAILED", True)
