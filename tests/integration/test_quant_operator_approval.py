"""APF-05 transaction, role and replay checks against disposable PostgreSQL."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from packages.agent_research.quant_approval import ApprovalBinding
from packages.agent_research.registry import QUANT_RESEARCH_V1
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunKind, RunStore
from packages.agent_research.tools import ToolJournal
from packages.backtesting.experiment_storage import ExperimentStorage
from packages.backtesting.jobs import BacktestError
from tests.integration.test_backtest_jobs import role_engine
from tests.integration.test_quant_lab_storage import fixture, start_binding

_PROOF = "PASS fenced Quant approval, manual denial, atomic rollback and replay"
_OPERATOR = "local-operator"
_ADMISSION = "atl_agent_quant_admission"
_RESEARCH = "atl_agent_research"
_TRACE = "a" * 32
_CODE = "b" * 40
_TABLES = (
    "agent_research.runs", "agent_research.run_events", "agent_research.companion_ideas",
    "agent_research.companion_idea_versions", "agent_research.quant_approvals",
    "agent_research.quant_start_policies", "agent_research.quant_approximation_proposals",
    "agent_research.quant_approximation_decisions", "agent_research.quant_approval_denials",
    "backtest.experiment_agent_provenance",
    "backtest.experiment_batches", "backtest.experiment_source_receipts",
    "backtest.experiment_events", "backtest.test_exposures",
)


def test_quant_operator_approval_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, _RESEARCH, _ADMISSION)
        harness.TABLES = (*previous_tables, *_TABLES)
        assert harness.host("tests.integration.test_quant_operator_approval") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS = previous_groups
        harness.TABLES = previous_tables


def _seed_idea(owner):
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='CONFIRMED',policy_version='fixture-v1'
            WHERE singleton
        """))
        connection.execute(text("""
            INSERT INTO agent_research.companion_ideas(idea_id,actor,command_key,command_hash)
            VALUES ('idea',:actor,:key,:hash)
        """), {"actor": _OPERATOR, "key": "1" * 64, "hash": "2" * 64})
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions(idea_id,version,document)
            VALUES ('idea',1,'{"status":"REVIEWED","hypothesis":{"signal":"EMA cross"}}'::jsonb)
        """))
        connection.execute(text("""
            INSERT INTO agent_research.quant_start_policies
                (policy_id,revision,fingerprint,max_daily,enabled)
            VALUES ('global',1,:global,0,true),(:agent,1,:fingerprint,1,true)
        """), {"global": "3" * 64, "agent": f"agent:{QUANT_RESEARCH_V1.agent_id}:1",
                "fingerprint": "4" * 64})


def _draft_and_validate(owner, runs, control):
    request, candidates = fixture()
    run_id = runs.enqueue(
        actor=_OPERATOR, key="quant-approval-run-0001", kind=RunKind.DIALOGUE,
        input_ref="idea", trace_id=_TRACE, agent=QUANT_RESEARCH_V1,
    )
    claim = runs.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    draft = control.create_definition(request, candidates, agent_origin=(run_id, "idea", 1))
    with owner.begin() as connection:
        context = connection.execute(text("""
            SELECT backtest.agent_validation_context(version)
            FROM research.version_state WHERE singleton
        """)).scalar_one()
        document = {
            "status": "EXPERIMENT_DRAFTED", "hypothesis": {"signal": "EMA cross"},
            "experimentDraftIds": [str(draft["definition_id"])],
            "testability": {"classification": "EXACTLY_TESTABLE"},
        }
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions
                (idea_id,version,previous_version,document)
            VALUES ('idea',2,1,CAST(:document AS jsonb))
        """), {"document": json.dumps(document)})
    validation = control.record_validation_request(draft["definition_id"], "validation-fixture", {
        "definitionHash": draft["fingerprint"], "calendarVerified": True,
        "simulationBarSteps": 9510, "snapshotRows": 1000, "publicationBytes": 100000,
        "seedBars": 49, "validatedAt": "2026-01-12T15:00:00Z", "registryWatermark": 0,
        "researchHistory": json.loads(Path(
            "contracts/examples/v1/quant-lab-history-no-registered-prior-use.valid.json"
        ).read_text(encoding="utf-8")),
        "reasons": [], "agentValidationContext": context,
    }, valid=True)
    assert validation["valid"]
    runs.transition(claim, RunState.WAITING_FOR_APPROVAL)
    return run_id, draft, validation


def _binding(owner, run_id, draft, validation):
    with owner.connect() as connection:
        run = connection.execute(text("""
            SELECT version,deadline_at FROM agent_research.runs WHERE run_id=:id
        """), {"id": run_id}).one()
        hypothesis_hash = connection.execute(text("""
            SELECT encode(sha256(convert_to(backtest.quant_canonical(document->'hypothesis'),
                'UTF8')),'hex') FROM agent_research.companion_idea_versions
            WHERE idea_id='idea' AND version=2
        """)).scalar_one()
        policies = connection.execute(text("""
            SELECT policy_id,revision,fingerprint FROM agent_research.quant_start_policies
            ORDER BY policy_id
        """)).all()
    body = validation["body"]
    return ApprovalBinding({
        "runId": str(run_id), "runVersion": run.version, "operatorId": _OPERATOR,
        "ideaId": "idea", "ideaVersion": 2, "hypothesisVersion": 2,
        "hypothesisHash": hypothesis_hash, "approximation": None,
        "experimentId": str(draft["definition_id"]),
        "definitionVersion": draft["definition_version"], "definitionHash": draft["fingerprint"],
        "validationId": str(validation["validation_id"]),
        "validationHash": validation["fingerprint"], "researchMode": draft["body"]["researchMode"],
        "resourceBudget": {
            "schemaVersion": "quant-lab-v1", "candidateCount": draft["candidate_count"],
            "reservedQueueSlots": draft["candidate_count"] + 2,
            "snapshotRows": body["snapshotRows"], "simulationBarSteps": body["simulationBarSteps"],
            "publicationBytes": body["publicationBytes"], "calendarConfirmed": True,
            "withinLimits": True,
        },
        "activePolicies": [
            {"policyId": row.policy_id, "revision": row.revision, "hash": row.fingerprint}
            for row in policies
        ] + [{"policyId": "selection", "revision": 1, "hash": draft["policy_hash"]}],
        "expiresAt": min(run.deadline_at, datetime.now(UTC) + timedelta(minutes=2)).isoformat()
        .replace("+00:00", "Z"),
    })


def _approval_state(owner, run_id):
    with owner.connect() as connection:
        return connection.execute(text("""
            SELECT r.state,r.checkpoint_ref,
              (SELECT count(*) FROM agent_research.quant_approvals WHERE batch_id IS NOT NULL),
              (SELECT count(*) FROM backtest.experiment_batches),
              (SELECT count(*) FROM backtest.test_exposures WHERE source_kind='BATCH')
            FROM agent_research.runs r WHERE r.run_id=:id
        """), {"id": run_id}).one()


def _deny_manual(control, draft, validation):
    with pytest.raises(BacktestError) as failure:
        control.enqueue(
            draft["definition_id"], draft["fingerprint"], _OPERATOR, "manual-bypass-00001",
            _TRACE, _CODE, **start_binding(draft, validation),
        )
    assert failure.value.code == "APPROVAL_REQUIRED"


def _assert_descendant_guard(owner, control, draft, validation):
    _, candidates = fixture()
    successor = control.create_definition(draft["body"] | {
        "hypothesis": "Synthetic successor with the same registered Quant grid",
        "predecessorDefinitionId": str(draft["definition_id"]),
    }, candidates)
    with owner.connect() as connection:
        inherited = connection.scalar(text("""
            SELECT origin_definition_id FROM backtest.experiment_agent_provenance
            WHERE definition_id=:id
        """), {"id": successor["definition_id"]})
        assert inherited == draft["definition_id"]
    _deny_manual(control, successor, validation)


def _assert_manual_preserved(control, validation):
    request, candidates = fixture()
    draft = control.create_definition(request, candidates)
    body = validation["body"].copy()
    body.pop("agentValidationContext")
    body["definitionHash"] = draft["fingerprint"]
    checked = control.record_validation_request(
        draft["definition_id"], "manual-validation-fixture", body, valid=True,
    )
    batch = control.enqueue(
        draft["definition_id"], draft["fingerprint"], _OPERATOR, "ordinary-manual-start-1",
        _TRACE, _CODE, **start_binding(draft, checked),
    )
    assert batch["definition_id"] == draft["definition_id"]


def _assert_acls(owner):
    consume = "agent_research.consume_quant_approval(uuid,text,text,text,text,text,uuid)"
    prepare = "agent_research.prepare_quant_approval(uuid,jsonb,text,text)"
    arguments = "uuid,uuid,integer,text,uuid,text,text,text,text,text"
    private = f"backtest.request_experiment_batch_private({arguments})"
    approved = f"backtest.request_approved_experiment_batch({arguments})"
    with owner.connect() as connection:
        for signature in (consume, prepare, private, approved):
            for role in (_RESEARCH, "atl_control_api", "atl_backtest_worker"):
                assert connection.scalar(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"),
                                         {"r": role, "f": signature}) is False
        for signature in (consume, prepare):
            assert connection.scalar(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"),
                                     {"r": _ADMISSION, "f": signature}) is True
        for signature in (private, approved):
            assert connection.scalar(text("SELECT has_function_privilege(:r,:f,'EXECUTE')"),
                                     {"r": _ADMISSION, "f": signature}) is False
        assert connection.scalar(text("""
            SELECT has_table_privilege(:r,'public.jobs','SELECT,INSERT,UPDATE') OR
                   has_table_privilege(:r,'agent_research.quant_approvals','INSERT,UPDATE,DELETE')
        """), {"r": _ADMISSION}) is False


def _assert_admission(owner, runs, control, admission, backtest):
    _seed_idea(owner)
    run_id, draft, validation = _draft_and_validate(owner, runs, control)
    _deny_manual(control, draft, validation)
    _assert_descendant_guard(owner, control, draft, validation)
    assert _approval_state(owner, run_id) == ("WAITING_FOR_APPROVAL", None, 0, 0, 0)
    binding = _binding(owner, run_id, draft, validation)
    approval = admission.prepare_approval(binding, "prepare-approval-first")
    assert admission.prepare_approval(binding, "prepare-approval-first") == approval

    def consume(identifier, digest, key="operator-approval-consume-1"):
        return admission.enqueue_approved(identifier, digest, _OPERATOR, key, _TRACE, _CODE)

    with pytest.raises(BacktestError) as capped:
        consume(approval, binding.binding_hash)
    assert capped.value.code == "BUDGET_EXHAUSTED"
    assert _approval_state(owner, run_id) == ("WAITING_FOR_APPROVAL", None, 0, 0, 0)
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.quant_start_policies SET revision=2,fingerprint=:hash,max_daily=1
            WHERE policy_id='global'
        """), {"hash": "5" * 64})
    with pytest.raises(BacktestError) as stale:
        consume(approval, binding.binding_hash)
    assert stale.value.code == "APPROVAL_STALE"
    current = _binding(owner, run_id, draft, validation)
    with pytest.raises(BacktestError, match="IDEMPOTENCY_CONFLICT"):
        admission.prepare_approval(current, "prepare-approval-first")
    approval = admission.prepare_approval(current, "prepare-approval-second")
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='UNKNOWN' WHERE singleton
        """))
    with pytest.raises(BacktestError, match="APPROVAL_STALE"):
        consume(approval, current.binding_hash)
    assert _approval_state(owner, run_id) == ("WAITING_FOR_APPROVAL", None, 0, 0, 0)
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='CONFIRMED' WHERE singleton
        """))

    with owner.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION agent_research.test_apf_rollback() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.state='WAITING_FOR_BATCH' THEN RAISE EXCEPTION 'apf-fixture rollback'; END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER test_apf_rollback BEFORE UPDATE ON agent_research.runs
            FOR EACH ROW EXECUTE FUNCTION agent_research.test_apf_rollback();
        """)
    with pytest.raises(BacktestError) as rolled_back:
        consume(approval, current.binding_hash)
    assert rolled_back.value.code == "UNAVAILABLE"
    assert _approval_state(owner, run_id) == ("WAITING_FOR_APPROVAL", None, 0, 0, 0)
    with owner.begin() as connection:
        connection.exec_driver_sql("""
            DROP TRIGGER test_apf_rollback ON agent_research.runs;
            DROP FUNCTION agent_research.test_apf_rollback();
        """)

    barrier = Barrier(2)

    def replay(_):
        barrier.wait(timeout=5)
        return consume(approval, current.binding_hash)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(replay, range(2))
    assert first == second
    assert _approval_state(owner, run_id) == ("WAITING_FOR_BATCH", str(first), 1, 1, 4)
    assert backtest.approval_budget(first) == current.to_document()["resourceBudget"]
    with pytest.raises(BacktestError) as different:
        consume(approval, current.binding_hash, "operator-approval-different")
    assert different.value.code == "IDEMPOTENCY_CONFLICT"
    _assert_manual_preserved(control, validation)
    _assert_approximation(owner, control, admission)


def _assert_approximation(owner, control, admission):
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions
              (idea_id,version,previous_version,document)
            SELECT idea_id,3,2,jsonb_set(document,'{testability,classification}',
              '"TESTABLE_WITH_DECLARED_APPROXIMATION"'::jsonb)
            FROM agent_research.companion_idea_versions WHERE idea_id='idea' AND version=2
        """))
        definition_id = connection.scalar(text("""
            SELECT (document->'experimentDraftIds'->>0)::uuid
            FROM agent_research.companion_idea_versions WHERE idea_id='idea' AND version=3
        """))
    with pytest.raises(BacktestError, match="APPROVAL_STALE"):
        admission.record_approximation(_OPERATOR, "idea", 3, "decision", 1, "unknown", "a" * 64)
    with control.transaction() as connection:
        connection.execute(text("""
            SELECT agent_research.register_quant_approximation(
              'idea',3,:definition,'actual-proposal',:hash,'Substitute the documented rule')
        """), {"definition": definition_id, "hash": "a" * 64})
    with pytest.raises(BacktestError, match="APPROVAL_STALE"):
        admission.record_approximation(
            _OPERATOR, "idea", 3, "decision", 1, "actual-proposal", "b" * 64,
        )
    assert admission.record_approximation(
        _OPERATOR, "idea", 3, "decision", 1, "actual-proposal", "a" * 64,
    ) == "a" * 64


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
            command.upgrade(config, "0044_quant_operator_approval")
        engines.extend(role_engine(owner, role) for role in (
            _RESEARCH, "atl_control_api", _ADMISSION, "atl_backtest_worker",
        ))
        _assert_acls(owner)
        _assert_admission(
            owner, RunStore(engines[1]), *(ExperimentStorage(engine) for engine in engines[2:]),
        )
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0054_quant_prepare_handoff")
        _assert_claimed_template(owner, engines[1], ExperimentStorage(engines[2]))
    finally:
        for engine in reversed(engines):
            engine.dispose()


def _assert_claimed_template(owner, research, quant):
    from packages.agent_research.companion.dialogue import Confirmation, Dialogue, ExpertBinding
    from packages.agent_research.companion.ideas import IdeaLifecycle
    from packages.agent_research.companion.interpretation import InterpretationBoundary
    from packages.agent_research.companion.service import CompanionService
    from packages.agent_research.companion.store import CompanionStore
    from packages.agent_research.companion.testability import (
        HypothesisIdentity,
        preliminary_testability,
    )
    from services.control_api.quant_lab_service import QuantLabService
    from services.runtime.agent_control import ResearchControl
    from services.runtime.agent_service import _matches_quant_hypothesis, _quant_preflight
    from services.runtime.quant_preparation import prepare_next_quant

    request, candidates = fixture()
    params = {"fast": 10, "slow": 30}
    request["searchSpaces"][0]["parameterValues"] = {key: [value] for key, value in params.items()}
    draft = quant.create_definition(request, [
        item for item in candidates
        if item["role"] != "SEARCH" or item["configuration"]["parameters"] == params
    ])
    hypothesis = {
        "statement": "EMA crossover return compared to buy and hold",
        "market": "SPY", "trigger": "EMA crossover", "signal": "EMA(10) above EMA(30)",
        "observationWindow": "2026-01-05/2026-02-24", "target": "net return",
        "measurementHorizon": "entire evaluation window", "benchmark": "buy-hold-v1",
        "evaluationCriterion": "return greater than benchmark",
    }
    identity = HypothesisIdentity.from_document(hypothesis)
    idea = {
        "id": "template-idea", "version": 1, "previousVersion": None, "status": "PROPOSED",
        "origin": "USER", "initiatorId": _OPERATOR, "title": "EMA research",
        "description": "Compare EMA crossover against buy and hold.",
        "originalText": "Compare EMA crossover against buy and hold.",
        "createdAt": "2024-01-01T00:00:00Z", "dialogueId": "template-dialogue",
        "hypothesis": hypothesis, "openQuestions": [], "assumptions": [], "uncertainties": [],
        "instrumentIds": [], "evidence": [], "testability": preliminary_testability(identity),
        "experimentDraftIds": [], "experimentIds": [], "relatedIdeaIds": [],
    }
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='CONFIRMED' WHERE singleton
        """))
        source = connection.execute(text("""
            SELECT q.definition_id,q.definition_version,q.fingerprint,q.body
            FROM backtest.experiment_definitions q
            WHERE q.definition_id=:id
        """), {"id": draft["definition_id"]}).one()
        binding = {
            "definition_id": source.definition_id.hex.upper(),
            "definition_version": source.definition_version,
            "definition_hash": source.fingerprint,
        }
        connection.execute(text("""
            INSERT INTO agent_research.companion_ideas(idea_id,actor,command_key,command_hash)
            VALUES ('template-idea',:actor,:key,:hash)
        """), {"actor": _OPERATOR, "key": "8" * 64, "hash": "9" * 64})
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions(idea_id,version,document)
            VALUES ('template-idea',1,CAST(:document AS jsonb))
        """), {"document": json.dumps(idea)})
        state = Dialogue(
            "template-dialogue", 1, expert=ExpertBinding(**binding),
            confirmed_fields=tuple(
                (field, Confirmation(value, "review-decision", 1))
                for field, value in identity.fields()
            ),
        )
        connection.execute(text("""
            INSERT INTO agent_research.companion_dialogues(dialogue_id,idea_id)
            VALUES ('template-dialogue','template-idea')
        """))
        connection.execute(text("""
            INSERT INTO agent_research.companion_dialogue_versions
              (dialogue_id,idea_id,version,idea_version,document)
            VALUES ('template-dialogue','template-idea',1,1,CAST(:document AS jsonb))
        """), {"document": json.dumps(state.record())})
    contract = Path("/opt/lab/contracts/schemas/v1/agent-research.schema.json")
    ideas = CompanionStore(research, command_key=bytes(range(32)))
    companion = CompanionService(
        ideas, IdeaLifecycle(contract, permits=lambda _: True, verifies=lambda *_: True),
        InterpretationBoundary(contract, permits=lambda _: True),
        permits_local_text=lambda _: True,
    )
    runs = RunStore(research)
    control = ResearchControl(
        research, key=bytes(range(32)), companion=companion, ideas=ideas, runs=runs,
        schedules=None, reports=None, processing=None, schedule_id="daily-research",
    )
    review = (
        "reviseAgentIdea", {"id": "template-idea"}, {}, {"action": "REVIEW", "expectedVersion": 1},
        _OPERATOR, "review-quant-template", _TRACE, "test-review",
    )
    with owner.begin() as connection:
        connection.exec_driver_sql("""
            CREATE FUNCTION agent_research.test_intent_rollback() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic intent failure'; END $$;
            CREATE TRIGGER test_intent_rollback BEFORE INSERT
            ON agent_research.quant_prepare_intents FOR EACH ROW
            EXECUTE FUNCTION agent_research.test_intent_rollback();
        """)
    with pytest.raises(DBAPIError):
        control._execute(*review)
    assert ideas.read_idea(_OPERATOR, "template-idea")["version"] == 1
    with owner.begin() as connection:
        connection.exec_driver_sql("""
            DROP TRIGGER test_intent_rollback ON agent_research.quant_prepare_intents;
            DROP FUNCTION agent_research.test_intent_rollback();
        """)
    status, reviewed = control._execute(*review)
    assert status == 201 and reviewed["version"] == 2 and reviewed["status"] == "REVIEWED"
    assert control._execute(*review) == (status, reviewed)
    with research.begin() as connection:
        intent = connection.execute(text("""
            SELECT * FROM agent_research.quant_prepare_intents WHERE idea_id='template-idea'
        """)).one()
    assert (intent.dialogue_version, intent.idea_version, intent.template_id,
            intent.template_version, intent.template_hash) == (
        2, 2, source.definition_id, source.definition_version, source.fingerprint,
    )
    run_id = intent.run_id
    claim = runs.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    query = text("""
        SELECT agent_research.claimed_quant_template(:run,:owner,:fence,:version,:idea,:dialogue)
    """)
    values = {
        "run": run_id, "owner": claim.owner, "fence": claim.fence, "version": claim.version,
        "idea": 2, "dialogue": 2,
    }
    parameters = {name: value for name, value in source.body.items() if name != "hypothesis"}
    assert _matches_quant_hypothesis(hypothesis, state.record(), parameters)
    assert not _matches_quant_hypothesis(
        hypothesis, state.record(), parameters | {"searchSpaces": fixture()[0]["searchSpaces"]},
    )
    assert not _matches_quant_hypothesis(
        hypothesis, state.record(), parameters | {"symbol": "QQQ"},
    )
    assert not _matches_quant_hypothesis(
        hypothesis, state.record() | {"confirmed_fields": []}, parameters,
    )
    protocol = parameters["protocol"]
    shifted = protocol | {"holdout": protocol["holdout"] | {"end": "2026-02-23T21:00:00Z"}}
    assert not _matches_quant_hypothesis(
        hypothesis, state.record(), parameters | {"protocol": shifted},
    )
    with owner.connect() as connection:
        context = connection.scalar(text("""
            SELECT backtest.agent_validation_context(version)
            FROM research.version_state WHERE singleton
        """))
    assert context is not None
    expected = {
        "id": str(source.definition_id), "version": source.definition_version,
        "hash": source.fingerprint, "ideaId": "template-idea", "ideaVersion": 2,
        "parameters": parameters,
        "dataContext": context,
    }

    journal = ToolJournal(research)
    result = _quant_preflight(journal, claim, "template-dialogue", intent)
    assert result.target is RunState.WAITING_FOR_INPUT
    assert result.checkpoint_ref == "QUANT_PREPARATION_READY"
    with research.connect() as connection:
        assert connection.scalar(query, values) == expected
        assert connection.scalar(query, values | {"fence": claim.fence + 1}) is None
        assert connection.scalar(query, values | {"owner": uuid4()}) is None
        assert connection.scalar(query, values | {"version": claim.version + 1}) is None
        assert connection.scalar(query, values | {"idea": 3}) is None
        assert connection.scalar(query, values | {"dialogue": 3}) is None
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton
        """))
    result = _quant_preflight(journal, claim, "template-dialogue", intent)
    assert result.target is RunState.BLOCKED
    assert result.checkpoint_ref == "QUANT_DATA_RIGHTS_UNVERIFIED"
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='CONFIRMED' WHERE singleton
        """))
    lab = QuantLabService(quant.engine, code_version=_CODE)
    assert prepare_next_quant(lab) is False
    ready = _quant_preflight(journal, claim, "template-dialogue", intent)
    runs.transition(claim, ready.target, checkpoint_ref=ready.checkpoint_ref)
    failure = BacktestError(503, "UNAVAILABLE")
    with patch.object(lab, "validate_agent_experiment", side_effect=failure):
        assert prepare_next_quant(lab) is True
    assert prepare_next_quant(lab) is False
    with owner.begin() as connection:
        assert connection.scalar(text("""
            SELECT count(*) FROM backtest.experiment_agent_provenance WHERE run_id=:run
        """), {"run": run_id}) == 0
        connection.execute(text("""
            UPDATE agent_research.quant_prepare_results
            SET retry_after=clock_timestamp()-interval '1 second'
            WHERE run_id=:run AND retry_after IS NOT NULL
        """), {"run": run_id})
    barrier = Barrier(2)

    def prepare(_):
        barrier.wait(timeout=5)
        return prepare_next_quant(lab)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(prepare, range(2))) == [False, True]
    assert prepare_next_quant(lab) is False
    (_, projected), = control.read.runs(_OPERATOR, None, 1, run_id=run_id)
    assert projected["state"] == "BLOCKED" and projected["reasonCode"] == "EVIDENCE_NOT_ELIGIBLE"
    with owner.connect() as connection:
        definition, validation, valid, reasons = connection.execute(text("""
            SELECT p.definition_id,p.validation_id,v.valid,v.body->'reasons'
            FROM agent_research.quant_prepare_results p
            JOIN backtest.experiment_validation_requests v USING(definition_id,validation_id)
            WHERE p.run_id=:run
        """), {"run": run_id}).one()
        assert definition != source.definition_id and validation is not None and valid is False
        assert reasons == ["MISSING_CALENDAR"]
        assert connection.scalar(text("""
            SELECT count(*) FROM agent_research.quant_approvals WHERE run_id=:run
        """), {"run": run_id}) == 0
        assert connection.scalar(text("""
            SELECT count(*) FROM agent_research.run_events WHERE run_id=:run AND state='BLOCKED'
        """), {"run": run_id}) == 1


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        locations = ",".join(f"{Path(item.filename).name}:{item.lineno}" for item in relevant[-5:])
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', '')} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
