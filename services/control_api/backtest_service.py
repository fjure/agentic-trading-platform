"""Bounded read projections and idempotent request admission; no simulation/provider calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from packages.backtesting.jobs import BacktestError, BacktestJobs
from packages.backtesting.request import canonical_request
from packages.backtesting.storage import ASSUMPTIONS, BUDGETS
from packages.domain.models import utc_string
from packages.research.registry import definitions
from services.control_api.backtest_dto import BacktestRequest

STATES = frozenset({"ACCEPTED", "RUNNING", "SUCCEEDED", "BLOCKED", "FAILED"})
REASONS = frozenset(
    {
        "BLOCKED_DATA_UNAVAILABLE",
        "BLOCKED_FIRST_OBSERVED",
        "BLOCKED_CALENDAR_UNKNOWN",
        "BLOCKED_QUALITY_UNKNOWN",
        "BLOCKED_CORPORATE_ACTIONS_UNKNOWN",
        "BLOCKED_CORPORATE_ACTION",
        "BLOCKED_WARMUP_MISSING",
        "BLOCKED_NEXT_BAR_MISSING",
        "BLOCKED_OUTCOME_UNAVAILABLE",
        "BLOCKED_SELL_FEES",
        "BUDGET_EXHAUSTED",
        "INTERNAL_FAILURE",
    }
)
REASON_MAP = {
    "BLOCKED_SELL_FEE_EXCEEDS_PROCEEDS": "BLOCKED_SELL_FEES",
    "BLOCKED_OUTCOME_DATA_MISSING": "BLOCKED_OUTCOME_UNAVAILABLE",
    "BLOCKED_OUTCOME_DATA_INVALID": "BLOCKED_OUTCOME_UNAVAILABLE",
    "BLOCKED_POINT_IN_TIME_INPUT": "BLOCKED_FIRST_OBSERVED",
    "BLOCKED_FILL_DATA_MISSING": "BLOCKED_NEXT_BAR_MISSING",
    "BLOCKED_AMBIGUOUS_DATA": "BLOCKED_DATA_UNAVAILABLE",
    "BLOCKED_MISSING_DATA": "BLOCKED_DATA_UNAVAILABLE",
    "AMBIGUOUS_DATA": "BLOCKED_DATA_UNAVAILABLE",
    "FOLD_DATA_MISSING": "BLOCKED_DATA_UNAVAILABLE",
    "BLOCKED_OUTCOME_MISSING": "BLOCKED_OUTCOME_UNAVAILABLE",
    "BLOCKED_QUALITY": "BLOCKED_QUALITY_UNKNOWN",
    "BLOCKED_CALENDAR_CHANGED": "BLOCKED_CALENDAR_UNKNOWN",
}


def identity(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("canonical UUID required")
    return UUID(value)


def stamp(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("UTC required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class BacktestService:
    def __init__(self, engine, *, code_version, cursor_key=None):
        if not isinstance(code_version, str) or not re.fullmatch(r"[0-9a-f]{40}", code_version):
            raise ValueError("verified code version required")
        self.engine, self.code_version = engine, code_version
        self.jobs = BacktestJobs(engine)
        if cursor_key is None:
            with self.jobs.transaction() as connection:
                cursor_key = connection.execute(
                    text("SELECT signing_key FROM backtest.cursor_state WHERE singleton")
                ).scalar_one()
        if not isinstance(cursor_key, bytes) or len(cursor_key) < 32:
            raise ValueError("cursor signing material required")
        self._cursor_key = cursor_key

    def _sign(self, value):
        raw = encoded(value)
        packed = raw + hmac.digest(self._cursor_key, raw, "sha256")
        token = base64.urlsafe_b64encode(packed).decode().rstrip("=")
        if len(token) > 2048:
            raise BacktestError(503, "UNAVAILABLE")
        return token

    def _verify(self, token, binding):
        try:
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,2048}", token):
                raise ValueError()
            packed = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
            if base64.urlsafe_b64encode(packed).decode().rstrip("=") != token:
                raise ValueError()
            raw, signature = packed[:-32], packed[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._cursor_key, raw, "sha256")):
                raise ValueError()
            value = json.loads(raw, object_pairs_hook=pairs)
            if set(value) != {"binding", "snapshot", "position"}:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise BacktestError(400, "CURSOR_INVALID") from None
        if value["binding"] != binding:
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        return value

    @staticmethod
    def _binding(kind, filters):
        return hashlib.sha256(encoded({"resource": kind, "filters": filters})).hexdigest()

    @staticmethod
    def _row(connection, run_id):
        row = (
            connection.execute(
                text("SELECT * FROM backtest.runs WHERE run_id=:id"), {"id": identity(run_id)}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BacktestError(404, "NOT_FOUND")
        return row

    @staticmethod
    def _published(connection, run_id):
        return (
            connection.execute(
                text("SELECT fingerprint FROM backtest.results WHERE run_id=:id"),
                {"id": run_id},
            )
            .mappings()
            .one_or_none()
        )

    def _run(self, connection, row, *, include_manifest=False):
        identifier = row["run_id"]
        metadata = (
            connection.execute(
                text("""SELECT
          COALESCE(body->'public'->>'manifestHash',fingerprint) AS hash,
          body->'public' IS NOT NULL AND body->'public'<>'null'::jsonb AS complete,
          body->'domain'->'blocker' AS blocker
          FROM backtest.manifests WHERE run_id=:id"""),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        manifest = None
        if include_manifest:
            manifest = connection.execute(
                text("SELECT body->'public' FROM backtest.manifests WHERE run_id=:id"),
                {"id": identifier},
            ).scalar_one_or_none()
        folds = (
            connection.execute(
                text(
                    "SELECT fold_index,body->'summary'->>'resultHash' AS hash "
                    "FROM backtest.fold_results "
                    "WHERE run_id=:id ORDER BY fold_index"
                ),
                {"id": identifier},
            )
            .mappings()
            .all()
        )
        result = self._published(connection, identifier)
        successful = row["status"] == "SUCCEEDED"
        if successful and (result is None or metadata is None or not metadata["complete"]):
            raise BacktestError(503, "UNAVAILABLE")
        completed = [
            {"foldIndex": item["fold_index"], "resultHash": item["hash"]} for item in folds
        ]
        blockers = []
        if row["status"] in {"BLOCKED", "FAILED"}:
            reason = REASON_MAP.get(row["error_code"], row["error_code"])
            if reason not in REASONS:
                reason = "INTERNAL_FAILURE"
            reference = (metadata["blocker"] or {}).get("reference") if metadata else None
            if reference is None:
                reference = connection.execute(
                    text(
                        "SELECT body->'_blocker'->>'reference' "
                        "FROM backtest.results WHERE run_id=:id"
                    ),
                    {"id": identifier},
                ).scalar_one_or_none()
            bar_start, evidence = None, None
            if isinstance(reference, str):
                try:
                    parsed = datetime.fromisoformat(reference.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0:
                        bar_start = utc_string(parsed)
                except ValueError:
                    pass
                if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", reference):
                    evidence = reference
            blockers = [
                {
                    "code": reason,
                    "foldIndex": len(completed)
                    if len(completed) < len(row["request"]["folds"])
                    else None,
                    "barStart": bar_start,
                    "evidenceId": evidence,
                }
            ]
        return dict(
            runId=str(identifier),
            status=row["status"],
            request=row["request"],
            acceptedAt=utc_string(row["accepted_at"]),
            updatedAt=utc_string(row["updated_at"]),
            completedAt=utc_string(row["updated_at"])
            if row["status"] in {"SUCCEEDED", "BLOCKED", "FAILED"}
            else None,
            snapshot={
                "version": row["snapshot_version"],
                "committedAt": utc_string(row["snapshot_at"]),
            },
            manifestHash=metadata["hash"] if metadata else None,
            resultHash=result["fingerprint"] if successful else None,
            completedFolds=completed,
            blockers=blockers,
            attempt=row["attempts"],
            publicationComplete=successful,
        ), manifest

    def read_strategies(self):
        with self.jobs.transaction() as connection:
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        items = []
        for strategy in ["flat-v1", "buy-hold-v1", "sma-cross-v1"]:
            sma = strategy == "sma-cross-v1"
            items.append(
                dict(
                    strategyId=strategy,
                    version="1",
                    parameterBounds={
                        "fastMinimum": 2,
                        "slowMaximum": 100,
                        "ordering": "fast < slow",
                    }
                    if sma
                    else None,
                    defaultParameters={"fast": 10, "slow": 30} if sma else {},
                )
            )
        return dict(
            items=items,
            instruments=[
                dict(instrumentId=str(i.instrument_id), symbol=m.symbol) for i, m in definitions()
            ],
            assumptions=dict(ASSUMPTIONS),
            budgets=dict(BUDGETS),
            observedAt=utc_string(now),
        )

    def create_run(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise BacktestError(403, "FORBIDDEN")
        value = BacktestRequest.model_validate(payload).model_dump()
        with self.jobs.transaction() as connection:
            now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        value = canonical_request(value, now)
        row = self.jobs.enqueue(value, actor, key, trace, self.code_version)
        with self.jobs.transaction() as connection:
            return self._run(connection, row)[0]

    def read_run(self, run_id):
        with self.jobs.transaction() as connection:
            run, manifest = self._run(
                connection, self._row(connection, run_id), include_manifest=True
            )
        return {"run": run, "manifest": manifest}

    def read_runs(self, values):
        limit = int(values.get("limit", 50))
        filters = {key: values[key] for key in ["status", "instrumentId"] if key in values}
        binding = self._binding("runs", filters)
        token = self._verify(values["cursor"], binding) if "cursor" in values else None
        with self.jobs.transaction() as connection:
            snapshot = (
                stamp(token["snapshot"])
                if token
                else connection.execute(text("SELECT clock_timestamp()")).scalar_one()
            )
            params = {"snapshot": snapshot, "limit": limit + 1}
            clauses = ["accepted_at<=:snapshot"]
            if "status" in filters:
                clauses.append("status=:status")
                params["status"] = filters["status"]
            if "instrumentId" in filters:
                identity(filters["instrumentId"])
                clauses.append("request->>'instrumentId'=:instrument")
                params["instrument"] = filters["instrumentId"]
            if token:
                position = token["position"]
                if not isinstance(position, list) or len(position) != 2:
                    raise BacktestError(400, "CURSOR_INVALID")
                params.update(at=stamp(position[0]), id=identity(position[1]))
                clauses.append("(accepted_at,run_id)<(:at,:id)")
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM backtest.runs WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY accepted_at DESC,run_id DESC LIMIT :limit"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
            page = rows[:limit]
            items = [self._run(connection, row)[0] for row in page]
            next_cursor = (
                self._sign(
                    {
                        "binding": binding,
                        "snapshot": utc_string(snapshot),
                        "position": [utc_string(page[-1]["accepted_at"]), str(page[-1]["run_id"])],
                    }
                )
                if len(rows) > limit
                else None
            )
        return dict(items=items, nextCursor=next_cursor, snapshotAt=utc_string(snapshot))

    def read_results(self, run_id, values):
        kind = values.get("resultType", "summary")
        limit = int(values.get("limit", 50))
        filters = {"runId": run_id, "resultType": kind, "foldIndex": values.get("foldIndex")}
        binding = self._binding("results", filters)
        token = self._verify(values["cursor"], binding) if "cursor" in values else None
        with self.jobs.transaction() as connection:
            row = self._row(connection, run_id)
            run, _ = self._run(connection, row)
            result = self._published(connection, row["run_id"])
            items, next_cursor = [], None
            if run["publicationComplete"]:
                if token and token["snapshot"] != result["fingerprint"]:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                position = token["position"] if token else 0
                if type(position) is not int or position < 0 or position > 3600000:
                    raise BacktestError(400, "CURSOR_INVALID")
                if kind not in {"summary", "equity", "fills"}:
                    raise BacktestError(400, "INVALID_REQUEST")
                rows = (
                    connection.execute(
                        text("""SELECT item FROM backtest.results,
                  LATERAL jsonb_array_elements(body->:kind) WITH ORDINALITY AS page(item,ordinal)
                  WHERE run_id=:id AND (CAST(:fold AS integer) IS NULL
                    OR (item->>'foldIndex')::int=CAST(:fold AS integer))
                  ORDER BY ordinal LIMIT :limit OFFSET :offset"""),
                        dict(
                            id=row["run_id"],
                            kind=kind,
                            fold=int(values["foldIndex"]) if "foldIndex" in values else None,
                            limit=limit + 1,
                            offset=position,
                        ),
                    )
                    .scalars()
                    .all()
                )
                items = rows[:limit]
                if len(rows) > limit:
                    next_cursor = self._sign(
                        {
                            "binding": binding,
                            "snapshot": result["fingerprint"],
                            "position": position + limit,
                        }
                    )
            elif token:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
        return dict(
            runId=run_id,
            status=run["status"],
            resultType=kind,
            publicationComplete=run["publicationComplete"],
            completedFolds=run["completedFolds"],
            items=items,
            nextCursor=next_cursor,
            manifestHash=run["manifestHash"],
            resultHash=run["resultHash"],
        )
