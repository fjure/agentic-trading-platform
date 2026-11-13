"""Recover an Agent Report from a real, operator-approved Quant batch."""

import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from packages.agent_research.registry import QUANT_RESEARCH_V1
from packages.agent_research.reporting.service import ResearchReports
from packages.agent_research.reporting.source import QuantReportSource
from packages.agent_research.reporting.store import ReportStore, ReportStoreError
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunConflict, RunKind, RunStore
from packages.agent_research.tools import ToolCall, ToolJournal
from packages.persistence.transaction import ScopedConnection
from packages.research.models import content_fingerprint
from services.control_api.quant_lab_service import QuantLabService
from services.worker.backtesting import BacktestWorker
from tests.integration.test_backtest_jobs import role_engine
from tests.integration.test_quant_lab_acceptance import PERIODS, seed_market
from tests.integration.test_quant_lab_storage import fixture
from tests.integration.test_quant_operator_approval import (
    _ADMISSION,
    _CODE,
    _OPERATOR,
    _RESEARCH,
    _TRACE,
    _binding,
    _seed_idea,
)

_PROOF = "PASS approved Quant report publication, rights, immutable recovery and redacted audit"


def test_research_report_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, _RESEARCH, _ADMISSION)
        harness.TABLES = (*previous_tables, "agent_research.report_versions",
                          "agent_research.report_rejections")
        assert harness.host("tests.recovery.test_research_report_db") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = previous_groups, previous_tables


def _approved_batch(owner, service, runs, *, start):
    instrument_id = seed_market(owner)
    _seed_idea(owner)
    request, _ = fixture()
    request.update(
        instrumentId=str(instrument_id), symbol="SPY", researchMode="RETROSPECTIVE_EXPLORATORY",
        dataSelectionPolicyVersion="retrospective-v1",
    )
    request["strategySelectionPolicy"].update(
        maxDrawdown="0.1", minValidationSessions=20, minimumEntryCount=None,
        minimumInvestedBarRatio=None,
    )
    windows = {
        name: dict(zip(("start", "end"), dates, strict=True)) for name, dates in PERIODS.items()
    }
    request["protocol"].update(
        folds=[dict(fold=0, **{key: windows[key] for key in ("training", "validation", "test")})],
        holdout=windows["holdout"],
    )
    run_id = runs.enqueue(
        actor=_OPERATOR, key="report-fixture-run", kind=RunKind.DIALOGUE, input_ref="idea",
        trace_id=_TRACE, agent=QUANT_RESEARCH_V1,
    )
    claim = runs.claim(uuid4())
    drafts = []

    def create_draft(scope):
        draft = service.create_agent_experiment(
            request, actor=_OPERATOR, key="report-fixture-definition", trace=_TRACE,
            run_id=str(run_id), idea_id="idea", idea_version=1, transaction=scope,
        )
        drafts.append(draft)
        return draft["id"]

    # This fixture owns both schemas. Production role separation is tested separately.
    journal = ToolJournal(owner)
    tool = ToolCall("quant-draft", "QUANT_PREPARE", 1, content_fingerprint(request))

    def authorize(scope, expected_version=1):
        assert scope.execute(text("""
            SELECT max(v.version) FROM agent_research.companion_idea_versions v
            JOIN agent_research.companion_ideas i USING (idea_id)
            WHERE i.idea_id='idea' AND i.actor=:actor
        """), {"actor": _OPERATOR}).scalar_one() == expected_version

    def invalid_receipt(scope):
        create_draft(scope)
        return ""

    with pytest.raises(RunConflict, match="INVALID_TOOL_RESULT"):
        journal.execute(claim, tool, authorize=authorize, operation=invalid_receipt)
    rolled_back = drafts.pop()["id"]
    with owner.connect() as connection:
        assert connection.execute(text("""
            SELECT count(*) FROM backtest.experiment_definitions WHERE definition_id=:id
        """), {"id": rolled_back}).scalar_one() == 0
        assert connection.execute(text("""
            SELECT count(*) FROM agent_research.tool_receipts
            WHERE run_id=:run AND step_id='quant-draft'
        """), {"run": run_id}).scalar_one() == 0
    draft_id = journal.execute(claim, tool, authorize=authorize, operation=create_draft)
    assert draft_id == rolled_back
    assert journal.execute(claim, tool, authorize=authorize, operation=create_draft) == draft_id
    assert len(drafts) == 1
    draft = drafts[0]
    idea = {
        "id": "idea", "version": 2, "status": "EXPERIMENT_DRAFTED",
        "title": "Synthetic EMA investigation", "description": "How did the EMA rule perform?",
        "hypothesis": {"signal": "EMA cross", "statement": "The rule may capture a trend."},
        "assumptions": [], "evidence": [], "experimentDraftIds": [draft["id"]],
        "testability": {"classification": "EXACTLY_TESTABLE"},
    }
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.quant_start_policies SET max_daily=1 WHERE policy_id='global'
        """))
        connection.execute(text("""
            UPDATE agent_research.companion_idea_versions
            SET document=jsonb_set(document,'{hypothesis}',CAST(:hypothesis AS jsonb))
            WHERE idea_id='idea' AND version=1
        """), {"hypothesis": json.dumps(idea["hypothesis"])})
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions
              (idea_id,version,previous_version,document)
            VALUES ('idea',2,1,CAST(:body AS jsonb))
        """), {"body": json.dumps(idea)})
    validation_request = {
        "schemaVersion": "quant-lab-v1", "definitionVersion": draft["definitionVersion"],
        "definitionHash": draft["definitionHash"],
    }

    def validate_draft(scope):
        result = service.validate_agent_experiment(
            draft_id, validation_request, actor=_OPERATOR, key="report-fixture-validation",
            trace=_TRACE, transaction=scope,
            input_deadline=datetime.now(UTC) + timedelta(minutes=2),
        )
        assert result["status"] == "VALIDATED", result["reasons"]
        return result["id"]

    validation_tool = ToolCall(
        "quant-validate", "QUANT_PREPARE", 1, content_fingerprint((draft_id, validation_request)),
    )
    validation_id = journal.execute(
        claim, validation_tool, authorize=lambda scope: authorize(scope, 2),
        operation=validate_draft,
    )
    with service.storage.transaction() as connection:
        checked = service.validate_agent_experiment(
            draft_id, validation_request, actor=_OPERATOR, key="report-fixture-control-check",
            trace=_TRACE, transaction=ScopedConnection(connection),
            input_deadline=datetime.now(UTC) + timedelta(minutes=2),
        )
    assert checked["status"] == "VALIDATED" and checked["id"] == validation_id, checked["reasons"]
    runs.transition(claim, RunState.WAITING_FOR_APPROVAL)
    definition = service.storage.definition(draft["id"])
    validation = service.storage.validation_request(
        definition["definition_id"], "report-fixture-validation",
    )
    assert str(validation["validation_id"]) == validation_id
    assert validation["body"]["agentDataVerified"] is True
    binding = _binding(owner, run_id, definition, validation)
    approval_id, digest = service.prepare_agent_approval(
        binding.to_document(), actor=_OPERATOR, key="report-fixture-prepare", trace=_TRACE,
    )
    return start(approval_id, digest)


def _verify_role_boundaries(owner, control, research, admission):
    # Resolve object identities as owner so this check also works for roles that
    # have no USAGE on the corresponding schema.
    with owner.connect() as connection:
        objects = connection.execute(text("""
            SELECT
              'backtest.request_agent_experiment_definition('
                'uuid,text,jsonb,text,text,text,text,jsonb,uuid,text,bigint)'::regprocedure::oid
                AS draft,
              'agent_research.prepare_quant_approval(uuid,jsonb,text,text)'::regprocedure::oid
                AS prepare,
              'agent_research.consume_quant_approval('
                'uuid,text,text,text,text,text,uuid)'::regprocedure::oid AS consume,
              'public.broker_orders'::regclass::oid AS orders
        """)).mappings().one()
    permissions = text("""
        SELECT current_user,
          has_function_privilege(CAST(:draft AS oid), 'EXECUTE'),
          has_function_privilege(CAST(:prepare AS oid), 'EXECUTE'),
          has_function_privilege(CAST(:consume AS oid), 'EXECUTE'),
          has_any_column_privilege(CAST(:orders AS oid), 'INSERT, UPDATE')
            OR has_table_privilege(CAST(:orders AS oid), 'DELETE, TRUNCATE')
    """)
    for engine, expected in (
        (control, ("atl_control_api", True, False, False, False)),
        (research, (_RESEARCH, False, False, False, False)),
        (admission, (_ADMISSION, False, True, True, False)),
    ):
        with engine.connect() as connection:
            assert tuple(connection.execute(permissions, dict(objects)).one()) == expected


def _prepare_native_approval(owner, research, quant, source_batch):
    from packages.agent_research.companion.dialogue import Confirmation, Dialogue, ExpertBinding
    from packages.agent_research.companion.ideas import IdeaLifecycle
    from packages.agent_research.companion.interpretation import InterpretationBoundary
    from packages.agent_research.companion.service import CompanionService
    from packages.agent_research.companion.store import CompanionStore
    from packages.agent_research.companion.testability import (
        HypothesisIdentity,
        preliminary_testability,
    )
    from services.runtime.agent_control import ResearchControl
    from services.runtime.agent_service import _complete_quant_preparation, _quant_preflight
    from services.runtime.quant_preparation import prepare_next_quant

    with owner.connect() as connection:
        definition_id = connection.scalar(text("""
            SELECT definition_id FROM backtest.experiment_batches WHERE batch_id=:id
        """), {"id": source_batch})
    request = quant.storage.definition(definition_id)["body"]
    request["searchSpaces"][0]["parameterValues"] = {"fast": [10], "slow": [30]}
    saved = quant.create_experiment(request, actor=_OPERATOR, key="native-approval-template",
                                    trace=_TRACE)
    hypothesis = {
        "statement": "EMA crossover return compared to buy and hold.",
        "market": "SPY", "trigger": "EMA crossover", "signal": "EMA(10) above EMA(30)",
        "observationWindow": "2026-01-05/2026-02-24", "target": "net return",
        "measurementHorizon": "entire evaluation window", "benchmark": "buy-hold-v1",
        "evaluationCriterion": "return greater than benchmark",
    }
    identity = HypothesisIdentity.from_document(hypothesis)
    idea = {
        "id": "native-idea", "version": 1, "previousVersion": None, "status": "PROPOSED",
        "origin": "USER", "initiatorId": _OPERATOR, "title": "EMA research",
        "description": hypothesis["statement"], "originalText": hypothesis["statement"],
        "createdAt": "2026-01-01T00:00:00Z", "dialogueId": "native-dialogue",
        "hypothesis": hypothesis, "openQuestions": [], "assumptions": [], "uncertainties": [],
        "instrumentIds": [], "evidence": [], "testability": preliminary_testability(identity),
        "experimentDraftIds": [], "experimentIds": [], "relatedIdeaIds": [],
    }
    dialogue = Dialogue(
        "native-dialogue", 1,
        expert=ExpertBinding(saved["id"], saved["definitionVersion"], saved["definitionHash"]),
        confirmed_fields=tuple(
            (field, Confirmation(value, "confirmed-native", 1))
            for field, value in identity.fields()
        ),
    )
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.companion_ideas(idea_id,actor,command_key,command_hash)
            VALUES ('native-idea',:actor,:key,:hash)
        """), {"actor": _OPERATOR, "key": "8"*64, "hash": "9"*64})
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions(idea_id,version,document)
            VALUES ('native-idea',1,CAST(:document AS jsonb))
        """), {"document": json.dumps(idea)})
        connection.execute(text("""
            INSERT INTO agent_research.companion_dialogues(dialogue_id,idea_id)
            VALUES ('native-dialogue','native-idea')
        """))
        connection.execute(text("""
            INSERT INTO agent_research.companion_dialogue_versions
              (dialogue_id,idea_id,version,idea_version,document)
            VALUES ('native-dialogue','native-idea',1,1,CAST(:document AS jsonb))
        """), {"document": json.dumps(dialogue.record())})

    contract = Path("contracts/schemas/v1/agent-research.schema.json")
    ideas = CompanionStore(research, command_key=bytes(range(32)))
    companion = CompanionService(
        ideas, IdeaLifecycle(contract, permits=lambda _: True, verifies=lambda *_: True),
        InterpretationBoundary(contract, permits=lambda _: True), permits_local_text=lambda _: True,
    )
    runs = RunStore(research)
    control = ResearchControl(
        research, key=bytes(range(32)), companion=companion, ideas=ideas, runs=runs,
        schedules=None, reports=None, processing=None, schedule_id="daily-research",
    )
    status, reviewed = control._execute(
        "reviseAgentIdea", {"id": "native-idea"}, {}, {"action": "REVIEW", "expectedVersion": 1},
        _OPERATOR, "review-native-approval", _TRACE, "test-review",
    )
    assert status == 201 and reviewed["version"] == 2
    with research.connect() as connection:
        intent = connection.execute(text("""
            SELECT * FROM agent_research.quant_prepare_intents WHERE idea_id='native-idea'
        """)).one()
    worker = uuid4()
    claim = runs.claim(worker)
    assert claim is not None and claim.run_id == intent.run_id
    step = _quant_preflight(ToolJournal(research), claim, "native-dialogue", intent)
    assert step.checkpoint_ref == "QUANT_PREPARATION_READY", step
    runs.transition(claim, step.target, checkpoint_ref=step.checkpoint_ref)
    assert prepare_next_quant(quant)
    with research.begin() as connection:
        assert connection.scalar(text("SELECT agent_research.ready_prepared_quant_runs()")) == 1
        assert connection.scalar(text("SELECT agent_research.ready_prepared_quant_runs()")) == 0
    claim = runs.claim(worker)
    assert claim is not None and claim.run_id == intent.run_id
    step = _complete_quant_preparation(research, companion, claim, intent)
    assert step.target is RunState.WAITING_FOR_APPROVAL and step.after_transition is not None, step
    with research.begin() as connection:
        scope = ScopedConnection(connection)
        runs.transition(claim, step.target, scope=scope)
        step.after_transition(scope)
        assert scope.execute(text("""
            SELECT agent_research.prepare_result_approval(:run)
        """), {"run": intent.run_id}).scalar_one() is not None
    prepared = ideas.read_idea(_OPERATOR, "native-idea")
    assert prepared["status"] == "EXPERIMENT_DRAFTED"
    assert prepared["version"] == 3
    assert prepared["testability"]["classification"] == "EXACTLY_TESTABLE"
    with owner.connect() as connection:
        approval = connection.execute(text("""
            SELECT a.batch_id,a.binding,a.definition_id,r.state,r.version
            FROM agent_research.quant_approvals a JOIN agent_research.runs r USING(run_id)
            WHERE a.run_id=:run
        """), {"run": intent.run_id}).one()
        assert approval.batch_id is None and approval.state == "WAITING_FOR_APPROVAL"
        assert approval.binding["runVersion"] == approval.version
        assert approval.binding["ideaVersion"] == 3
        assert prepared["experimentDraftIds"] == [str(approval.definition_id)]


def _verify(owner, control, dispatch, research, admission):
    import httpx
    from fastapi.testclient import TestClient

    from services.control_api.auth import LocalAuth
    from services.runtime.agent_quant_admission import (
        PATH,
        QuantAdmissionAuthentication,
        QuantAdmissionClient,
        create_quant_admission_app,
        started_receipt_reader,
    )
    from tests.contracts.test_agent_research_routes import activate, client

    _verify_role_boundaries(owner, control, research, admission)

    quant = QuantLabService(
        control, code_version=_CODE, approval_engine=admission,
        authorize_operator=lambda actor: actor == _OPERATOR,
    )
    def start(approval_id, digest):
        relay_auth = QuantAdmissionAuthentication(bytes(range(32)))
        auth = LocalAuth()
        app = create_quant_admission_app(
            relay_auth, service=quant, read_started_at=started_receipt_reader(admission),
        )
        with TestClient(app) as authority:
            def forward(request):
                response = authority.post(PATH, headers=request.headers, content=request.content)
                return httpx.Response(response.status_code, headers=response.headers,
                                      stream=httpx.ByteStream(response.content))

            approved = QuantAdmissionClient(relay_auth, transport=httpx.MockTransport(forward))
            with client(auth, approval_handler=approved) as http:
                activate(http, auth, "report-control-authenticate")
                path = f"/api/v1/research/agent/approvals/{approval_id}/decision"
                headers = {"Origin": auth.origin, "Idempotency-Key": "report-fixture-consume"}
                payload = {"action": "APPROVE_AND_START", "expectedApprovalVersion": 1,
                           "bindingHash": digest}
                response = http.post(path, headers=headers, json=payload)
                assert response.status_code == 202
                receipt = response.json()
                assert receipt["approvalVersion"] == 2 and receipt["state"] == "STARTED"
                assert http.post(path, headers=headers, json=payload).json() == receipt
                return receipt["batchId"]

    batch = _approved_batch(owner, quant, RunStore(research), start=start)
    store = ReportStore(research)
    assert store.authorize(batch, actor=_OPERATOR) is None
    worker = BacktestWorker(dispatch, enabled=True, code_version=_CODE)
    worked = worker.tick()
    with owner.connect() as connection:
        status, error = connection.execute(text("""
            SELECT status,error_code FROM backtest.experiment_batches WHERE batch_id=:batch
        """), {"batch": batch}).one()
    if not worked or status != "SUCCEEDED":
        print(f"QUANT_WORKER_STATUS {status} {error or 'NO_REASON'}")
        raise AssertionError("native Quant worker did not complete")
    assert store.authorize(batch, actor="another-operator") is None

    service = ResearchReports(QuantReportSource(research), store, permits=lambda _: True)
    proposal = b'{"raw":"synthetic-confidential-marker"}'
    with research.begin() as connection:
        assert connection.scalar(text("SELECT agent_research.ready_quant_runs()")) == 1
        assert connection.scalar(text("SELECT agent_research.ready_quant_runs()")) == 0
    runs = RunStore(research)
    claim = runs.claim(uuid4())
    assert claim is not None
    published = service.publish(batch, actor=_OPERATOR, proposal=proposal, claim=claim)
    report_id = published["summary"]["reportId"]
    runs.transition(claim, RunState.COMPLETED, checkpoint_ref=report_id)
    with pytest.raises(ReportStoreError, match="REPORT_SCOPE_DENIED"):
        store.publish(published, actor=_OPERATOR, claim=claim)
    assert published["summary"]["interpretationStatus"] == "FACTS_ONLY"
    assert published["selection"]["status"] == "SELECTED"
    holdout = published["phases"]["holdout"]["metrics"]
    assert any(
        claim["reference"]["subject"] == "CANDIDATE"
        and claim["reference"]["fieldPath"] == "/metrics/netReturn/value"
        and Decimal(claim["value"]) < 0
        for claim in holdout
    )
    assert published["currentRightsWarnings"] == []
    assert ReportStore(research).get(report_id, actor=_OPERATOR) == published
    assert store.get(report_id, actor="another-operator") is None
    assert service.publish(batch, actor=_OPERATOR, proposal=proposal) == published
    _prepare_native_approval(owner, research, quant, batch)
    with pytest.raises(ReportStoreError, match="REPORT_VERSION_CONFLICT"):
        store.publish(published | {"conclusion": "Changed statement"}, actor=_OPERATOR)
    with owner.connect() as connection:
        assert connection.execute(text("""
            SELECT code FROM agent_research.report_rejections WHERE report_id=:report
        """), {"report": report_id}).scalars().all() == ["REPORT_OUTPUT_SHAPE_INVALID"]
        assert connection.scalar(text("SELECT count(*) FROM agent_research.report_versions")) == 1
    with pytest.raises(DBAPIError), research.begin() as connection:
        connection.execute(text("DELETE FROM agent_research.report_versions"))
    with pytest.raises(DBAPIError), research.connect() as connection:
        connection.execute(text("SELECT 1 FROM backtest.experiment_result_links"))
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton
        """))
    reloaded = ReportStore(research).get(report_id, actor=_OPERATOR)
    assert reloaded["currentRightsWarnings"][0]["status"] == "RIGHTS_REVOKED"
    assert reloaded | {"currentRightsWarnings": []} == published


def guest():
    from alembic import command
    from alembic.config import Config

    payload = json.load(sys.stdin)
    owner = create_engine(URL.create(
        "postgresql+psycopg", username=payload["role"], password=payload["password"],
        host="postgres", port=5432, database=payload["database"],
    ), hide_parameters=True)
    engines = [owner]
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        engines.extend(role_engine(owner, role) for role in (
            "atl_control_api", "atl_backtest_worker", _RESEARCH, _ADMISSION,
        ))
        _verify(*engines)
    finally:
        for engine in reversed(engines):
            engine.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        locations = ",".join(f"{Path(item.filename).name}:{item.lineno}" for item in relevant[-6:])
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', '')} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
