"""Research-side application port for the private Control relay."""

import json
from hashlib import sha256
from hmac import digest
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.agent_research.companion.dialogue import Dialogue, DialogueError, local_questions
from packages.agent_research.companion.ideas import IdeaAction, IdeaDenied
from packages.agent_research.companion.service import DialogueCommand
from packages.agent_research.companion.store import CompanionConflict
from packages.agent_research.companion.testability import Classification, HypothesisIdentity, Reason
from packages.agent_research.discovery.schedule import DailySchedule, ScheduleDenied, tzdata_version
from packages.agent_research.registry import QUANT_RESEARCH_V1, require_agent
from packages.agent_research.run_state import InvalidRunTransition
from packages.agent_research.runs import RunConflict, RunKind
from packages.backtesting.jobs import BacktestError
from packages.domain.models import utc_string
from packages.persistence.transaction import ScopedConnection
from services.control_api.agent_research_routes import AgentResearchError
from services.llm_gateway.content_policy import safe_result_content

from .agent_status import AgentReadModels, registry_items

_NOT_FOUND = {"COMPANION_NOT_FOUND", "RESOURCE_NOT_FOUND", "PROCESSING_NOT_FOUND"}
_FORBIDDEN = {
    "AGENT_POLICY_DENIED",
    "MODEL_CONTEXT_DENIED",
    "PROJECTION_NOT_ALLOWED",
    "EXPERT_REFERENCE_DENIED",
}
_CONFLICT = {
    "VERSION_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "STALE_SCHEDULE_VERSION",
    "STALE_DIALOGUE_VERSION",
    "STALE_IDEA_VERSION",
    "INVALID_RUN_STATE",
    "EXPERT_FIELD_CONFLICT",
}


def _required(value):
    if value is None:
        raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
    return value


class ResearchControl:
    def __init__(
        self,
        engine,
        *,
        key,
        companion,
        ideas,
        runs,
        schedules,
        reports,
        processing,
        schedule_id,
        extra=None,
        discovery=None,
        enqueue_interpretation=None,
    ):
        self.engine, self.key = engine, key
        self.companion, self.ideas, self.runs = companion, ideas, runs
        self.schedules, self.reports, self.processing = schedules, reports, processing
        self.schedule_id = schedule_id
        self.read = AgentReadModels(engine, cursor_key=digest(key, b"research-cursor-v1", "sha256"))
        self.extra = dict(extra or {})
        self.discovery = discovery
        self.enqueue_interpretation = enqueue_interpretation

    def _hash(self, *parts):
        value = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return digest(self.key, value.encode("ascii"), "sha256").hex()

    def __call__(self, operation, params, query, payload, *, actor, key, trace, scope):
        if actor != "local-operator":
            raise AgentResearchError(403, "FORBIDDEN")
        try:
            return self._execute(
                operation.operation_id, params, query, payload, actor, key, trace, scope
            )
        except (
            CompanionConflict,
            DialogueError,
            IdeaDenied,
            RunConflict,
            InvalidRunTransition,
            ScheduleDenied,
            BacktestError,
        ) as error:
            code = getattr(error, "code", str(error))
            status = (
                404
                if code in _NOT_FOUND
                else 403
                if code in _FORBIDDEN
                else (409 if code in _CONFLICT or code.startswith("PROCESSING_") else 422)
            )
            raise AgentResearchError(getattr(error, "status", status), code) from None

    def _execute(self, name, params, query, payload, actor, key, trace, scope):
        identifier = params.get("id")
        turn = params.get("turnId")
        if name == "createAgentProcessingPreview":
            return 201, self.processing.preview(actor, scope, identifier, turn, payload, key)
        if name == "getAgentProcessingStatus":
            return 200, self.processing.status(actor, identifier, turn)
        if name == "decideAgentProcessing":
            result = self.processing.decide(actor, scope, identifier, turn, payload, key)
            return (202 if payload["action"] == "SEND" else 200), result
        if name == "listAgentIdeas":
            return 200, self.read.page(
                name, query, actor, scope, lambda p, n: self.read.ideas(actor, p, n), nullable=True
            )
        if name == "getAgentIdea":
            return 200, self.ideas.read_idea(actor, identifier, query.get("version"))
        if name == "createAgentIdea":
            idea_id = self._hash("idea", actor, key)
            dialogue_id = self._hash("dialogue", actor, key)
            content = payload | {"description": "Vom Betreiber eingereichte Forschungsfrage."}
            return 201, self.companion.create(actor, idea_id, dialogue_id, content, key=key)
        if name == "reviseAgentIdea":
            old = self.ideas.read_idea(actor, identifier, payload["expectedVersion"])
            dialogue_id = _required(old.get("dialogueId"))
            with self.engine.connect() as connection:
                prior_version = connection.execute(
                    text("""
                    SELECT max(version) FROM agent_research.companion_dialogue_versions
                    WHERE dialogue_id=:dialogue AND idea_id=:idea AND idea_version=:version
                """),
                    {"dialogue": dialogue_id, "idea": identifier, "version": old["version"]},
                ).scalar_one()
            command = DialogueCommand(
                actor, identifier, dialogue_id, old["version"], _required(prior_version), key
            )
            action = IdeaAction(payload["action"])
            changes = {
                field: payload[field]
                for field in ("title", "description", "hypothesis")
                if field in payload
            }
            with self.engine.begin() as connection:
                transaction = ScopedConnection(connection)
                result = self.companion.revise_idea(
                    command, action=action, decision_id=self._hash(name, actor, key),
                    changes=changes, scope=transaction,
                )
                if action is IdeaAction.REVIEW:
                    self._record_quant_intent(transaction, actor, trace, identifier, result)
            return 201, self.ideas.read_idea(actor, identifier, result.idea_version)
        if name in {
            "createAgentDialogue",
            "getAgentDialogue",
            "createAgentDialogueTurn",
            "decideAgentHypothesis",
        }:
            return self._dialogue(name, identifier, payload, actor, key, query, scope, trace)
        if name == "getAgentRegistry":
            entries = [(item["version"], item) for item in registry_items()]
            return 200, self.read.page(
                name,
                query,
                actor,
                scope,
                lambda p, n: [row for row in entries if row[0] > (p or 0)][:n],
            )
        if name == "listResearchAgentRuns":
            return 200, self.read.page(
                name, query, actor, scope, lambda p, n: self.read.runs(actor, p, n)
            )
        if name in {"getAgentRun", "listAgentRunEvents"}:
            identifier = self._run_id(identifier)
            rows = self.read.runs(actor, None, 1, run_id=identifier)
            if not rows:
                raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
            if name == "getAgentRun":
                return 200, rows[0][1]
            return 200, self.read.page(
                name + identifier,
                query,
                actor,
                scope,
                lambda p, n: self.read.events(actor, identifier, p, n),
            )
        if name == "cancelAgentRun":
            return 200, self._command(name, actor, key, params, payload, self._cancel)
        if name == "getAgentSchedule":
            return 200, _required(self.schedules.read(self.schedule_id)).to_document()
        if name in {"updateAgentSchedule", "commandAgentSchedule"}:
            return 200, self._command(name, actor, key, params, payload, self._schedule)
        if name == "listAgentResearchReports":
            return 200, self.read.page(
                name,
                query,
                actor,
                scope,
                lambda p, n: self.read.reports(actor, p, n),
                nullable=True,
            )
        if name == "getAgentResearchReport":
            return 200, _required(
                self.reports.get(identifier, actor=actor, version=query.get("version"))
            )
        if name == "listAgentQuantApprovals":
            return 200, self.read.page(
                name,
                query,
                actor,
                scope,
                lambda p, n: self.read.approvals(actor, p, n),
                nullable=True,
            )
        if name == "getAgentQuantApproval":
            rows = self.read.approvals(actor, None, 1, approval_id=self._run_id(identifier))
            return 200, _required(rows[0][1] if rows else None)
        if name == "decideAgentQuantApproval":
            if payload["action"] == "REJECT":
                return 200, self._command(name, actor, key, params, payload, self._reject_approval)
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        if name == "listAgentProviders" and name not in self.extra:
            return 200, self.read.page(name, query, actor, scope, lambda _p, _n: [])
        action = self.extra.get(name)
        if action is None:
            raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")
        return action(params, query, payload, actor=actor, key=key, trace=trace, scope=scope)

    def _record_quant_intent(self, transaction, actor, trace, idea_id, revision):
        dialogue = Dialogue.from_record(revision.document)
        if dialogue.expert is None:
            return
        idea = transaction.execute(text("""
            SELECT i.document FROM agent_research.companion_idea_versions i
            WHERE i.idea_id=:idea AND i.version=:version
              AND i.document->>'status'='REVIEWED'
              AND NOT EXISTS (
                SELECT FROM agent_research.companion_idea_versions newer
                WHERE newer.idea_id=i.idea_id AND newer.version>i.version
              )
              AND :dialogue_version=(
                SELECT max(version) FROM agent_research.companion_dialogue_versions
                WHERE dialogue_id=:dialogue AND idea_id=i.idea_id
              )
        """), {
            "idea": idea_id, "version": revision.idea_version,
            "dialogue": dialogue.dialogue_id, "dialogue_version": revision.version,
        }).scalar_one_or_none()
        if idea is None:
            return
        assessment = idea.get("testability") or {}
        hypothesis = idea.get("hypothesis")
        if (
            assessment.get("classification") != Classification.INSUFFICIENT_DATA
            or assessment.get("reasons") != [Reason.DATA_NOT_VERIFIED]
            or hypothesis is None
            or dialogue.confirmed != dict(HypothesisIdentity.from_document(hypothesis).fields())
        ):
            return
        template = dialogue.expert
        agent = require_agent(
            QUANT_RESEARCH_V1.agent_id, QUANT_RESEARCH_V1.version, QUANT_RESEARCH_V1.policy_version,
        )
        run_id = self.runs.enqueue_in_transaction(
            transaction, actor=actor, key=self._hash("quant-prepare", actor, idea_id,
                                                     revision.idea_version),
            kind=RunKind.DIALOGUE, input_ref=dialogue.dialogue_id, trace_id=trace, agent=agent,
        )
        transaction.execute(text("""
            INSERT INTO agent_research.quant_prepare_intents
              (run_id,dialogue_id,dialogue_version,idea_id,idea_version,template_id,template_version,
               template_hash)
            VALUES (:run,:dialogue,:dialogue_version,:idea,:idea_version,:template,:version,:hash)
            ON CONFLICT (idea_id,idea_version) DO NOTHING
        """), {
            "run": run_id, "dialogue": dialogue.dialogue_id, "dialogue_version": revision.version,
            "idea": idea_id, "idea_version": revision.idea_version,
            "template": UUID(template.definition_id), "version": template.definition_version,
            "hash": template.definition_hash,
        })

    @staticmethod
    def _run_id(identifier):
        try:
            return str(UUID(identifier))
        except (TypeError, ValueError, AttributeError):
            raise AgentResearchError(404, "RESOURCE_NOT_FOUND") from None

    def _command(self, name, actor, key, params, payload, mutate):
        values = {
            "actor": actor,
            "operation": name,
            "key": self._hash(name, actor, key),
            "hash": self._hash(name, params, payload),
        }
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), values
            )
            previous = connection.execute(
                text("""
                SELECT payload_hash,response FROM agent_research.control_commands
                WHERE actor=:actor AND operation=:operation AND key_hash=:key
            """),
                values,
            ).one_or_none()
            if previous is not None:
                if previous.payload_hash != values["hash"]:
                    raise AgentResearchError(409, "IDEMPOTENCY_CONFLICT")
                return previous.response
            result = mutate(connection, actor, params, payload, values["key"])
            connection.execute(
                text("""
                INSERT INTO agent_research.control_commands
                  (actor,operation,key_hash,payload_hash,response)
                VALUES(:actor,:operation,:key,:hash,CAST(:response AS jsonb))
            """),
                values | {"response": json.dumps(result)},
            )
            return result

    def _cancel(self, connection, actor, params, payload, receipt):
        identifier = UUID(self._run_id(params["id"]))
        row = connection.execute(
            text("""
            SELECT run_id FROM agent_research.runs WHERE run_id=:id AND actor=:actor FOR UPDATE
        """),
            {"id": identifier, "actor": actor},
        ).scalar_one_or_none()
        _required(row)
        self.runs.cancel(identifier, payload["expectedVersion"], connection=connection)
        row = connection.execute(
            text("""
            SELECT version,state,updated_at FROM agent_research.runs WHERE run_id=:id
        """),
            {"id": identifier},
        ).one()
        return {
            "runId": str(identifier),
            "runVersion": row.version,
            "state": row.state,
            "recordedAt": utc_string(row.updated_at),
            "receiptId": receipt,
        }

    def _reject_approval(self, connection, actor, params, payload, _receipt):
        if payload["expectedApprovalVersion"] != 1:
            raise AgentResearchError(409, "APPROVAL_STALE")
        identifier = self._run_id(params["id"])
        at = connection.execute(
            text("""
            SELECT agent_research.record_approval_rejection(:actor,CAST(:id AS uuid),:hash)
        """),
            {"actor": actor, "id": identifier, "hash": payload["bindingHash"]},
        ).scalar_one()
        if at is None:
            raise AgentResearchError(409, "APPROVAL_STALE")
        return {
            "approvalId": identifier,
            "approvalVersion": 2,
            "state": "REJECTED",
            "decidedAt": utc_string(at),
        }

    def _schedule(self, connection, actor, params, payload, receipt):
        old = _required(self.schedules.read(self.schedule_id, connection=connection)).to_document()
        action, version = payload.get("action"), payload["expectedVersion"]
        if action in {"RESUME", "REEVALUATE"} and self.discovery is None:
            raise AgentResearchError(503, "PROVIDER_UNAVAILABLE")
        if action == "REEVALUATE":
            return self._reevaluate(connection, actor, payload, receipt, old)
        document = old | {"version": version + 1}
        for field in ("nextExecutionAt", "lastExecutionAt", "checkpointId"):
            document.pop(field, None)
        if action:
            document["state"] = "PAUSED" if action == "PAUSE" else "ACTIVE"
        else:
            document.update(
                {field: payload[field] for field in ("timeZone", "localTime", "scope", "limits")}
            )
            document["tzdataVersion"] = tzdata_version(document["timeZone"])
        self.schedules.replace(
            DailySchedule.parse(document), expected_version=version, connection=connection
        )
        if action:
            return {
                "commandId": receipt,
                "scheduleId": self.schedule_id,
                "scheduleVersion": document["version"],
                "state": document["state"],
            }
        return self.schedules.read(self.schedule_id, connection=connection).to_document()

    def _reevaluate(self, connection, actor, payload, receipt, old):
        row = connection.execute(
            text("""
            SELECT version,epoch,state FROM agent_research.lock_discovery_schedule(:id)
        """),
            {"id": self.schedule_id},
        ).one_or_none()
        if (
            row is None
            or row.version != payload["expectedVersion"]
            or row.state != "ACTIVE"
            or payload["agentVersion"] != self.discovery.configure(self.schedule_id).agent.version
        ):
            raise AgentResearchError(409, "VERSION_CONFLICT")
        if not safe_result_content(payload["reason"]):
            raise AgentResearchError(422, "MODEL_CONTEXT_UNCLASSIFIED")
        evaluation = UUID(self._run_id(payload["evidenceSetId"]))
        self.discovery.request_reanalysis(
            self.schedule_id,
            row.epoch,
            evaluation,
            receipt,
            connection=connection,
            permits=lambda _scope, owner, bound, _: owner == actor and bound == evaluation,
        )
        return {
            "commandId": receipt,
            "scheduleId": self.schedule_id,
            "scheduleVersion": old["version"],
            "state": old["state"],
        }

    def _dialogue(self, name, identifier, payload, actor, key, query, scope, trace):
        if name == "createAgentDialogue":
            idea = self.ideas.read_idea(actor, payload["ideaId"])
            if idea["version"] != payload["expectedIdeaVersion"]:
                raise AgentResearchError(409, "VERSION_CONFLICT")
            identifier = _required(idea.get("dialogueId"))
            if "expertExperiment" in payload:
                expert = payload["expertExperiment"]
                command = DialogueCommand(actor, idea["id"], identifier, idea["version"], 1, key)
                revision = self.companion.bind_expert(
                    command,
                    (
                        expert["definitionId"],
                        expert["definitionVersion"],
                        expert["definitionHash"],
                    ),
                )
                return 201, self._dialogue_document(
                    actor, idea["id"], identifier, revision, query, scope
                )
        idea_id, current = self.ideas.read_owned_dialogue(actor, identifier)
        if name in {"createAgentDialogue", "getAgentDialogue"}:
            status = 201 if name == "createAgentDialogue" else 200
            return status, self._dialogue_document(
                actor, idea_id, identifier, current, query, scope
            )
        command = DialogueCommand(
            actor,
            idea_id,
            identifier,
            payload["expectedIdeaVersion"],
            payload["expectedDialogueVersion"],
            key,
        )
        if name == "createAgentDialogueTurn":
            turn_id = self._hash("turn", actor, identifier, key)
            if payload["kind"] == "FREE_TEXT_RESEARCH":
                revision = self.companion.submit_original(
                    command,
                    turn_id=turn_id,
                    original_id=self._hash("original", actor, identifier, key),
                    text=payload["text"],
                )
                if self.enqueue_interpretation is not None:
                    self.enqueue_interpretation(actor, identifier, turn_id, trace)
            else:
                revision = self.companion.answer_local(
                    command,
                    question_id=payload["questionId"],
                    field=payload["field"],
                    value=payload["value"],
                    turn_id=turn_id,
                    proposal_id=self._hash("proposal", actor, identifier, key),
                )
            return 202, self._turn(actor, idea_id, identifier, revision.document)
        decision = self._hash("hypothesis", actor, identifier, key)
        common = {
            "proposal_id": payload["proposalId"],
            "proposal_hash": payload["proposalHash"],
            "decision_id": decision,
        }
        action = payload["action"]
        if action == "CONFIRM_HYPOTHESIS":
            fields = {entry["field"]: entry["value"] for entry in payload["confirmedFields"]}
            if len(fields) != len(payload["confirmedFields"]):
                raise AgentResearchError(422, "INVALID_REQUEST")
            revision = self.companion.confirm(
                command,
                **common,
                confirmed_fields=fields,
                confirmed_assumption_ids=tuple(payload["confirmedAssumptionIds"]),
            )
        elif action == "REQUEST_CHANGES":
            revision = self.companion.request_changes(
                command,
                **common,
                requested_fields=tuple(payload["requestedFields"]),
                clarification=payload["clarification"],
            )
        else:
            return 200, self._command(
                name,
                actor,
                key,
                {"id": identifier, "idea": idea_id},
                payload,
                self._accept_approximation,
            )
        return 200, {
            "decisionId": decision,
            "action": action,
            "ideaId": idea_id,
            "ideaVersion": revision.idea_version,
            "dialogueId": identifier,
            "dialogueVersion": revision.version,
            "proposalId": payload["proposalId"],
            "recordedAt": self._dialogue_time(idea_id, identifier, revision.version),
        }

    @staticmethod
    def _accept_approximation(connection, actor, params, payload, receipt):
        values = {
            "actor": actor,
            "idea": params["idea"],
            "dialogue": params["id"],
            "idea_version": payload["expectedIdeaVersion"],
            "decision": receipt,
            "dialogue_version": payload["expectedDialogueVersion"],
            "semantic": payload["proposalId"],
            "semantic_hash": payload["proposalHash"],
            "approximation": payload["approximationId"],
            "hash": payload["approximationHash"],
        }
        try:
            at = connection.execute(
                text("""
                SELECT agent_research.record_control_approximation(
                  :actor,:idea,:idea_version,:dialogue,:dialogue_version,
                  :semantic,:semantic_hash,:decision,:approximation,:hash)
            """),
                values,
            ).scalar_one()
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "P0001":
                raise
            raise AgentResearchError(409, "VERSION_CONFLICT") from None
        if at is None:
            raise AgentResearchError(409, "VERSION_CONFLICT")
        return {
            "decisionId": receipt,
            "action": "ACCEPT_APPROXIMATION",
            "ideaId": params["idea"],
            "ideaVersion": values["idea_version"],
            "dialogueId": params["id"],
            "dialogueVersion": values["dialogue_version"],
            "proposalId": values["semantic"],
            "recordedAt": utc_string(at),
        }

    def _dialogue_time(self, idea_id, dialogue_id, version):
        with self.engine.connect() as connection:
            at = connection.execute(
                text("""
                SELECT created_at FROM agent_research.companion_dialogue_versions
                WHERE idea_id=:idea AND dialogue_id=:dialogue AND version=:version
            """),
                {"idea": idea_id, "dialogue": dialogue_id, "version": version},
            ).scalar_one()
        return utc_string(at)

    def _turn(self, actor, idea_id, dialogue_id, record):
        state = Dialogue.from_record(record)
        processing = self.processing.status(actor, dialogue_id, state.turn_id)
        turn = {
            "turnId": state.turn_id,
            "turnVersion": 1,
            "dialogueId": dialogue_id,
            "dialogueVersion": state.version,
            "ideaVersion": state.idea_version,
            "originalId": state.original_id,
            "processing": processing,
            "createdAt": self._dialogue_time(idea_id, dialogue_id, state.version),
            "localQuestions": [
                {
                    "id": question.question_id,
                    "field": question.field,
                    "question": question.question,
                }
                for question in local_questions(state)
            ],
        }
        with self.engine.connect() as connection:
            run_id = connection.execute(text("""
                SELECT run_id FROM agent_research.runs
                WHERE actor=:actor AND kind='DIALOGUE' AND input_ref=:dialogue
                  AND request_key_hash=:turn
            """), {"actor": actor, "dialogue": dialogue_id,
                    "turn": sha256(state.turn_id.encode()).hexdigest()}).scalar_one_or_none()
        if run_id is not None:
            turn["runId"] = str(run_id)
        return turn | ({"proposal": state.proposal} if state.proposal is not None else {})

    def _dialogue_document(self, actor, idea_id, identifier, revision, query, scope):
        def turns(after, limit):
            with self.engine.connect() as connection:
                rows = connection.execute(
                    text("""
                    SELECT version,document FROM (
                      SELECT DISTINCT ON (document->>'turn_id') version,document
                      FROM agent_research.companion_dialogue_versions
                      WHERE dialogue_id=:dialogue AND idea_id=:idea AND version<=:current
                        AND document->>'turn_id' IS NOT NULL
                      ORDER BY document->>'turn_id',version DESC
                    ) turns WHERE version>:after ORDER BY version LIMIT :limit
                """),
                    {
                        "dialogue": identifier,
                        "idea": idea_id,
                        "current": revision.version,
                        "after": after or 0,
                        "limit": limit,
                    },
                ).all()
            return [
                (
                    row.version,
                    self._turn(actor, idea_id, identifier, row.document),
                )
                for row in rows
            ]

        state = Dialogue.from_record(revision.document)
        page = self.read.page(
            f"dialogue:{identifier}:{revision.version}", query, actor, scope, turns, nullable=True
        )
        idea = self.ideas.read_idea(actor, idea_id, revision.idea_version)
        result = {
            "id": identifier,
            "version": revision.version,
            "ideaId": idea_id,
            "ideaVersion": revision.idea_version,
            "createdAt": self._dialogue_time(idea_id, identifier, 1),
            "turns": page["items"],
            "nextCursor": page["nextCursor"],
            "confirmedHypothesis": idea["hypothesis"] if state.confirmed else None,
            "confirmedAssumptions": [
                {"id": key, "statement": value.value} for key, value in state.confirmed_assumptions
            ],
            "confirmedFields": [
                {"field": key, "value": value} for key, value in state.confirmed.items()
            ],
        }
        if state.expert is not None:
            result["expertExperiment"] = {
                "definitionId": state.expert.definition_id,
                "definitionVersion": state.expert.definition_version,
                "definitionHash": state.expert.definition_hash,
            }
        return result
