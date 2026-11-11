"""Actor-scoped browser diagnostics for the isolated Research service."""

import base64
import json
from dataclasses import asdict
from decimal import Decimal
from hmac import compare_digest, digest

from sqlalchemy import text

from packages.agent_research.registry import AGENTS
from packages.agent_research.reporting.source import QuantReportSource
from packages.backtesting.jobs import BacktestError
from packages.domain.models import utc_string
from services.control_api.backtest_service import BacktestService
from services.llm_gateway.content_policy import safe_result_content

_OUTCOMES = {
    "RESERVED": "NOT_DISPATCHED",
    "DISPATCH_AUTHORIZED": "IN_FLIGHT",
    "UNSENT": "REJECTED_BEFORE_EXECUTION",
    "COMPLETED": "ACCEPTED",
    "OUTCOME_UNKNOWN": "OUTCOME_UNKNOWN",
    "RESULT_UNAVAILABLE": "RESULT_UNAVAILABLE",
}
_REASONS = {
    "BUDGET_EXHAUSTED": "BUDGET_EXHAUSTED",
    "TIMED_OUT": "DEADLINE_EXCEEDED",
    "CANCELLED": "CANCELLED_BY_OPERATOR",
    "WAITING_FOR_APPROVAL": "APPROVAL_REQUIRED",
    "WAITING_FOR_PROCESSING_DECISION": "PROCESSING_GRANT_REQUIRED",
}


def _camel(values):
    def key(name):
        first, *rest = name.split("_")
        return first + "".join(part.title() for part in rest)

    return {
        key(name): format(value, "f") if isinstance(value, Decimal) else value
        for name, value in values.items()
    }


def registry_items():
    return [
        {
            "agentId": agent.agent_id,
            "version": agent.version,
            "policyVersion": agent.policy_version,
            "role": agent.role,
            "description": agent.description,
            "promptVersion": agent.prompt_version,
            "tools": [
                {"toolId": tool.tool_id, "toolVersion": tool.version} for tool in agent.tools
            ],
            "outputSchemaVersion": agent.output_schema_version,
            "limits": _camel(asdict(agent.limits)),
            "autonomyLevel": agent.autonomy_level,
            "abstentionRules": [
                "UNSUPPORTED_RESEARCH" if rule == "UNSUPPORTED_HYPOTHESIS" else rule
                for rule in agent.abstention_rules
            ],
            "implementationFingerprint": agent.implementation_fingerprint,
            "availability": "AVAILABLE",
        }
        | ({"modelBinding": _camel(asdict(agent.model_binding))} if agent.model_binding else {})
        for agent in AGENTS.values()
    ]


class AgentReadModels:
    _binding = staticmethod(BacktestService._binding)

    def __init__(self, engine, *, cursor_key):
        self.engine = engine
        self._cursor_key = cursor_key

    def _sign_cursor(self, position, binding):
        encoded = json.dumps(position, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        value = bytes.fromhex(binding)[:16] + encoded
        payload = value + digest(self._cursor_key, value, "sha256")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    def _read_cursor(self, token, binding):
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            expected = digest(self._cursor_key, raw[:-32], "sha256")
            if (len(raw) <= 48 or base64.urlsafe_b64encode(raw).decode().rstrip("=") != token
                    or not compare_digest(raw[:16], bytes.fromhex(binding)[:16])
                    or not compare_digest(raw[-32:], expected)):
                raise ValueError
            return json.loads(raw[16:-32])
        except (TypeError, ValueError):
            raise BacktestError(422, "INVALID_REQUEST") from None

    def page(self, name, query, actor, scope, load, *, nullable=False):
        limit = query.get("limit", 50)
        binding = self._binding("agent:" + name, {"actor": actor, "scope": scope, "limit": limit})
        cursor = query.get("cursor")
        after = self._read_cursor(cursor, binding) if cursor else None
        items = load(after, limit + 1)
        result = {"items": [document for _, document in items[:limit]]}
        if len(items) > limit:
            result["nextCursor"] = self._sign_cursor(items[limit - 1][0], binding)
        elif nullable:
            result["nextCursor"] = None
        return result

    def ideas(self, actor, after, limit):
        with self.engine.connect() as connection:
            return connection.execute(
                text("""
                SELECT i.idea_id,v.document FROM agent_research.companion_ideas i
                CROSS JOIN LATERAL (SELECT document FROM agent_research.companion_idea_versions
                  WHERE idea_id=i.idea_id ORDER BY version DESC LIMIT 1) v
                WHERE i.actor=:actor AND i.idea_id>coalesce(:after,'')
                ORDER BY i.idea_id LIMIT :limit
            """),
                {"actor": actor, "after": after, "limit": limit},
            ).all()

    def reports(self, actor, after, limit):
        with self.engine.connect() as connection:
            return connection.execute(
                text("""
                SELECT DISTINCT ON (report_id) report_id,document->'summary'
                FROM agent_research.report_versions
                WHERE actor=:actor AND report_id>coalesce(:after,'')
                ORDER BY report_id,version DESC LIMIT :limit
            """),
                {"actor": actor, "after": after, "limit": limit},
            ).all()

    def approvals(self, actor, after, limit, *, approval_id=None):
        rows = self._approval_rows(actor, after, limit, approval_id)
        source = QuantReportSource(self.engine)
        return [
            (
                str(row.approval_id),
                _approval(
                    row,
                    source.idea_references(row.idea | {"evidence": row.idea.get("evidence", [])}),
                ),
            )
            for row in rows
        ]

    def _approval_rows(self, actor, after, limit, approval_id):
        with self.engine.connect() as connection:
            return connection.execute(
                text("""
                  SELECT * FROM agent_research.agent_approval_page(
                    :actor,CAST(:id AS uuid),CAST(:after AS uuid),:limit)
                """),
                {"actor": actor, "id": approval_id, "after": after, "limit": limit},
            ).all()

    def runs(self, actor, after, limit, *, run_id=None):
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT r.run_id,r.kind,r.state,r.version,r.trace_id,r.request_hash,r.agent_binding,
                  r.created_at,r.updated_at,r.deadline_at,r.steps,r.tool_calls,r.input_ref,
                  (SELECT v.report_id FROM agent_research.report_versions v
                   WHERE v.report_id=r.checkpoint_ref AND v.actor=r.actor LIMIT 1) AS report_id,
                  a.calls,a.unknown,a.reported_input,a.reported_output,a.held_cost,a.confirmed_cost,
                  a.estimated_cost,a.pricing_version,a.outcome,a.unconfirmed_cost,
                  agent_research.agent_run_started(r.run_id,r.actor) AS started
                FROM agent_research.runs r
                CROSS JOIN LATERAL (
                  SELECT count(*) FILTER (WHERE status<>'UNSENT') AS calls,
                    bool_or(status IN ('DISPATCH_AUTHORIZED','OUTCOME_UNKNOWN','RESULT_UNAVAILABLE')
                      AND reported_input IS NULL) AS unknown,
                    sum(reported_input) AS reported_input,sum(reported_output) AS reported_output,
                    coalesce(sum(held_cost),0) AS held_cost,sum(confirmed_cost) AS confirmed_cost,
                    sum(estimated_cost) AS estimated_cost,max(pricing_version) AS pricing_version,
                    bool_or(status NOT IN ('UNSENT','RESERVED') AND confirmed_cost IS NULL)
                      AS unconfirmed_cost,
                    (array_agg(status ORDER BY created_at DESC,attempt_id DESC))[1] AS outcome
                  FROM agent_research.model_attempts WHERE run_id=r.run_id
                ) a
                WHERE r.actor=:actor AND r.kind<>'PROVIDER_PROBE'
                  AND (CAST(:id AS uuid) IS NULL OR r.run_id=CAST(:id AS uuid))
                  AND (CAST(:after AS uuid) IS NULL OR r.run_id>CAST(:after AS uuid))
                ORDER BY r.run_id LIMIT :limit
            """),
                    {"actor": actor, "after": after, "limit": limit, "id": run_id},
                )
                .mappings()
                .all()
            )
        return [(str(row["run_id"]), _run(row)) for row in rows]

    def events(self, actor, run_id, after, limit):
        after_time, after_id = after if after is not None else (None, None)
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("""
                WITH target AS (
                  SELECT run_id,trace_id FROM agent_research.runs
                  WHERE actor=:actor AND run_id=CAST(:run AS uuid)
                ), events AS (
                  SELECT e.version,e.occurred_at,'state:' || e.version AS event_id,e.state,
                    NULL::text AS tool_id,NULL::integer AS tool_version,NULL::text AS result_ref
                  FROM agent_research.run_events e JOIN target USING(run_id)
                  UNION ALL
                  SELECT v.version,t.occurred_at,
                    'tool:' || encode(sha256(convert_to(
                      t.step_id || ':' || t.tool_id || ':' || t.tool_version,'UTF8')),'hex'),
                    NULL,t.tool_id,t.tool_version,t.result_ref
                  FROM agent_research.tool_receipts t JOIN target USING(run_id)
                  CROSS JOIN LATERAL (
                    SELECT version FROM agent_research.run_events e
                    WHERE e.run_id=t.run_id AND e.fence=t.fence AND e.occurred_at<=t.occurred_at
                    ORDER BY e.version DESC LIMIT 1
                  ) v
                ) SELECT events.*,target.trace_id FROM events CROSS JOIN target
                WHERE CAST(:after AS timestamptz) IS NULL OR
                  (occurred_at,event_id)>(CAST(:after AS timestamptz),:after_id)
                ORDER BY occurred_at,event_id LIMIT :limit
            """),
                {"actor": actor, "run": run_id, "after": after_time, "after_id": after_id,
                 "limit": limit},
            ).all()
        result = []
        for row in rows:
            at = utc_string(row.occurred_at)
            event = {"eventId": f"{run_id}:{row.event_id}", "runId": run_id,
                     "runVersion": row.version, "occurredAt": at, "traceId": row.trace_id}
            if row.tool_id is not None:
                event.update(kind="TOOL_COMPLETED", resultRef=row.result_ref,
                             tool={"toolId": row.tool_id, "toolVersion": row.tool_version})
            else:
                kind = "CANCELLATION_RECORDED" if row.state == "CANCELLED" else "STATE_CHANGED"
                event.update(kind=kind, state=row.state)
            result.append(([at, row.event_id], event))
        return result


def _run(row):
    binding = row["agent_binding"]
    limits = binding["limits"]
    outcome = _OUTCOMES.get(row["outcome"], "NOT_DISPATCHED")
    usage = {
        "tokenUsageState": "UNKNOWN"
        if row["unknown"]
        else ("KNOWN" if row["reported_input"] is not None else "NOT_INCURRED"),
        "costState": "UNKNOWN"
        if row["unconfirmed_cost"]
        else ("KNOWN" if row["confirmed_cost"] is not None else "NOT_INCURRED"),
        "reservedCost": format(row["held_cost"], "f"),
        "currency": limits["currency"],
        "modelCalls": row["calls"],
        "steps": row["steps"],
        "toolCalls": row["tool_calls"],
        "experiments": int(row["started"]),
    }
    for wire, column in (
        ("reportedInputTokens", "reported_input"),
        ("reportedOutputTokens", "reported_output"),
        ("pricingVersion", "pricing_version"),
    ):
        if row[column] is not None and not row["unknown"]:
            usage[wire] = int(row[column])
    for wire, column in (("estimatedCost", "estimated_cost"), ("confirmedCost", "confirmed_cost")):
        if row[column] is not None and not row["unconfirmed_cost"]:
            usage[wire] = format(row[column], "f")
    document = {
        "runId": str(row["run_id"]),
        "version": row["version"],
        "kind": row["kind"],
        "state": row["state"],
        "agentId": binding["agent_id"],
        "agentVersion": binding["version"],
        "policyVersion": binding["policy_version"],
        "requestHash": row["request_hash"],
        "traceId": row["trace_id"],
        "createdAt": utc_string(row["created_at"]),
        "updatedAt": utc_string(row["updated_at"]),
        "deadlineAt": utc_string(row["deadline_at"]),
        "limits": _camel(limits),
        "usage": usage,
        "providerOutcome": outcome,
    }
    if binding["model_binding"] is not None:
        document["modelBinding"] = _camel(binding["model_binding"])
    reason = _REASONS.get(row["state"])
    if outcome == "OUTCOME_UNKNOWN":
        reason = "PROVIDER_OUTCOME_UNKNOWN"
    if reason is not None:
        document["reasonCode"] = reason
    if row["kind"] == "SCHEDULED":
        document["scheduleId"] = row["input_ref"]
    if row["report_id"] is not None:
        document.update(reportId=row["report_id"], result="REPORT_AVAILABLE")
    return document


def _approval(row, evidence):
    idea = row.idea
    question = idea.get("description") or "Research question unavailable."
    hypothesis = (idea.get("hypothesis") or {}).get("statement") or "Hypothesis unavailable."
    assumptions = [entry["statement"] for entry in idea.get("assumptions", ())]
    if not safe_result_content([question, hypothesis, assumptions, row.definition["hypothesis"]]):
        raise BacktestError(403, "FORBIDDEN")
    document = {
        "approvalId": str(row.approval_id),
        "approvalVersion": 2 if row.decided_at else 1,
        "action": "APPROVE_AND_START",
        "bindingHash": row.binding_hash,
        "state": row.state,
        "requestedAt": utc_string(row.requested_at),
        "binding": row.binding,
        "question": question,
        "hypothesis": hypothesis,
        "assumptions": assumptions,
        "definition": row.definition,
        "evidence": list(evidence),
    }
    if row.batch_id is not None:
        document["batchId"] = str(row.batch_id)
    return document
