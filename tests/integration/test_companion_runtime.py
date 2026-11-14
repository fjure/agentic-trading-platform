"""PostgreSQL companion history, version fencing and protected original storage."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from tests.integration.test_agent_research_runtime import WORKER_ROLE

_PROOF = "PASS companion immutable versions, original isolation and dialogue replay"
_TABLES = tuple(f"agent_research.companion_{name}" for name in (
    "ideas", "idea_versions", "dialogues", "dialogue_versions", "originals", "events",
)) + ("agent_research.processing_operations",)


def test_companion_history_and_dialogue_in_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (*previous_tables, *_TABLES)
        assert harness.host("tests.integration.test_companion_runtime") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS = previous_groups
        harness.TABLES = previous_tables


def guest():
    import pytest
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL

    from packages.agent_research.companion.dialogue import Dialogue, DialogueError, local_questions
    from packages.agent_research.companion.history import companion_history
    from packages.agent_research.companion.ideas import IdeaLifecycle
    from packages.agent_research.companion.interpretation import InterpretationBoundary
    from packages.agent_research.companion.service import CompanionService, DialogueCommand
    from packages.agent_research.companion.store import CompanionConflict, CompanionStore
    from services.llm_gateway.content_policy import safe_result_content
    from tests.unit.test_companion_interpretation import CONTRACT

    payload = json.load(sys.stdin)
    url = URL.create(
        "postgresql+psycopg", username=payload["role"], password=payload["password"],
        host="postgres", port=5432, database=payload["database"],
    )
    owner = create_engine(url, hide_parameters=True)
    worker = create_engine(url, hide_parameters=True)

    @event.listens_for(worker, "begin")
    def set_role(connection):
        connection.exec_driver_sql(f"SET LOCAL ROLE {WORKER_ROLE}")

    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0049_processing_operations")
        store = CompanionStore(worker, command_key=bytes(range(32)))
        lifecycle = IdeaLifecycle(
            CONTRACT, permits=safe_result_content, verifies=lambda *_: True,
        )
        service = CompanionService(
            store, lifecycle, InterpretationBoundary(CONTRACT, permits=safe_result_content),
            permits_local_text=safe_result_content,
        )
        content = {
            "title": "Earnings reversal", "originalText": "Can an initial reaction reverse?",
        }
        first = service.create("operator", "idea", "dialogue", content, key="idea-create")
        assert service.create(
            "operator", "replacement-idea", "replacement-dialogue", content, key="idea-create",
        ) == first
        with pytest.raises(CompanionConflict, match="IDEMPOTENCY_CONFLICT"):
            service.create(
                "operator", "replacement-idea", "replacement-dialogue",
                content | {"title": "New title"}, key="idea-create",
            )
        initial = DialogueCommand("operator", "idea", "dialogue", 1, 1, "submit-1")
        with pytest.raises(DialogueError, match="MODEL_CONTEXT_UNCLASSIFIED"):
            service.submit_original(
                initial, turn_id="turn", original_id="original",
                text="synthetic@example.invalid",
            )
        assert store.read_dialogue("operator", "idea", "dialogue").version == 1
        original = "Synthetic observation about surprising earnings"
        submitted = service.submit_original(
            initial, turn_id="turn", original_id="original", text=original,
        )
        assert original not in json.dumps(submitted.document)
        assert store.read_original("operator", "idea", "dialogue", "original") == original
        with pytest.raises(CompanionConflict, match="COMPANION_NOT_FOUND"):
            store.read_original("other-operator", "idea", "dialogue", "original")
        assert service.submit_original(
            initial, turn_id="turn", original_id="original", text=original,
        ) == submitted
        with pytest.raises(CompanionConflict, match="IDEMPOTENCY_CONFLICT"):
            service.submit_original(
                initial, turn_id="turn", original_id="original", text="changed",
            )

        submitted_state = Dialogue.from_record(submitted.document)
        question = local_questions(submitted_state)[0]
        local = service.answer_local(
            replace(initial, dialogue_version=2, key="answer-1"), question_id=question.question_id,
            field=question.field, value="Technology shares", turn_id="local-turn",
            proposal_id="local-proposal",
        )
        proposal = Dialogue.from_record(local.document).proposal
        decision = replace(initial, dialogue_version=3, key="confirm-1")
        confirmed = service.confirm(
            decision, proposal_id=proposal["proposalId"], proposal_hash=proposal["proposalHash"],
            decision_id="decision", confirmed_fields={question.field: "Technology shares"},
            confirmed_assumption_ids=(),
        )
        assert confirmed.version == 4 and confirmed.idea_version == 2
        assert service.confirm(
            decision, proposal_id=proposal["proposalId"], proposal_hash=proposal["proposalHash"],
            decision_id="decision", confirmed_fields={question.field: "Technology shares"},
            confirmed_assumption_ids=(),
        ) == confirmed
        assert store.read_idea("operator", "idea", 1) == first
        successor = store.read_idea("operator", "idea")
        assert successor["hypothesis"]["market"] == "Technology shares"
        assert successor["previousVersion"] == 1 and successor["decisionId"] == "decision"
        assert Dialogue.from_record(confirmed.document).confirmed == {
            question.field: "Technology shares",
        }
        with worker.connect() as connection:
            events = connection.execute(text("""
                SELECT code FROM agent_research.companion_events ORDER BY version
            """)).scalars().all()
            assert events == ["ORIGINAL_ACCEPTED", "LOCAL_CLARIFICATION", "HYPOTHESIS_CONFIRMED"]
            assert connection.scalar(text("""
                SELECT has_table_privilege(
                    current_user,'agent_research.companion_idea_versions','UPDATE,DELETE'
                )
            """)) is False
        from tests.integration.companion_model_support import check_model_publication

        check_model_publication(owner, worker, service, store)
        structured = {
            "statement": "Test the initial signal",
            "market": "SPY", "trigger": "EMA crossover", "signal": "EMA(10) above EMA(30)",
            "observationWindow": "2020-01-01/2021-01-01", "target": "net return",
            "measurementHorizon": "entire evaluation window", "benchmark": "buy-hold-v1",
            "evaluationCriterion": "return greater than benchmark",
        }
        for actor, name in (
            ("operator", "earlier"), ("other-operator", "foreign"), ("operator", "repeated"),
        ):
            service.create(actor, name, f"dialogue-{name}", {
                "title": name, "originalText": "Synthetic catalog question",
                "hypothesis": structured,
            }, key=f"create-{name}")
        windows = {"start": "2020-01-01T00:00:00Z", "end": "2020-02-01T00:00:00Z"}
        definition = {"candidate_count": 1, "body": {
            "instrumentId": str(uuid4()),
            "protocol": {"folds": [
                {"training": windows, "validation": windows, "test": windows},
            ], "holdout": windows},
        }}
        with owner.begin() as quant:
            quant.exec_driver_sql("SET TRANSACTION READ ONLY")
            quant.exec_driver_sql("SET LOCAL ROLE atl_control_api")
            history = companion_history(
                store, "operator", "repeated", 1,
                quant_connection=quant, quant_definition=definition,
            )
        assert history.exact_repetitions == (("earlier", 1),)
        assert history.exposure["reuseStatus"] == "NO_REGISTERED_PRIOR_USE"
        assert history.exposure["externalKnowledgeStatus"] == "EXTERNAL_KNOWLEDGE_UNKNOWN"
        check_disabled_provider_installation(worker)
    finally:
        owner.dispose()
        worker.dispose()


def check_disabled_provider_installation(engine):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.research_client import AgentResearchClient
    from services.runtime import agent_research_relay as relay
    from services.runtime.agent_service import create_agent_research_service
    from tests.e2e.agent_research_browser import serve

    origin = "http://127.0.0.1:8000"
    auth = LocalAuth(origin)
    relay_key = bytes(range(32))
    control = create_app(
        auth=auth, agent_research=AgentResearchClient(relay.AgentRelayAuthentication(relay_key)),
    )
    supervisor = create_agent_research_service(
        engine, relay_key=relay_key, command_key=bytes(reversed(range(32))),
    )
    base = "/api/v1/research/agent"
    with (
        patch.object(relay, "RELAY_URL", "http://127.0.0.1:8013" + relay.RELAY_PATH),
        serve(supervisor, 8013),
        TestClient(control, base_url=origin, client=("127.0.0.1", 42000)) as http,
    ):
        def post(path, key, payload):
            return http.post(
                path, headers={"Origin": origin, "Idempotency-Key": key}, json=payload,
            )

        assert post("/api/v1/session", "arp01-session-0001", {
            "activationCode": auth.issue_activation_code(),
        }).status_code == 204
        status = http.get("/api/v1/integrations/research-provider-readiness")
        assert status.status_code == 200 and status.json()["state"] == "DISABLED"
        candidates = status.json()["candidates"]
        assert candidates and all(item["blockingReasons"] for item in candidates)
        idea = post(base + "/ideas", "arp01-create-idea-0001", {
            "title": "Quartalszahlen", "originalText": "Erholen sich Aktien nach Quartalszahlen?",
        })
        assert idea.status_code == 201, idea.json().get("code")
        dialogue = base + "/dialogues/" + idea.json()["dialogueId"]
        turn = post(dialogue + "/turns", "arp01-local-question-0001", {
            "kind": "FREE_TEXT_RESEARCH",
            "expectedIdeaVersion": 1,
            "expectedDialogueVersion": 1,
            "text": "Erholen sich Aktien nach Quartalszahlen?",
        })
        assert turn.status_code == 202, turn.json().get("code")
        question = turn.json()["localQuestions"][0]
        answer = post(dialogue + "/turns", "arp01-local-answer-0001", {
            "kind": "LOCAL_STRUCTURED_ANSWER",
            "expectedIdeaVersion": turn.json()["ideaVersion"],
            "expectedDialogueVersion": turn.json()["dialogueVersion"],
            "questionId": question["id"],
            "field": question["field"],
            "value": "Technologieaktien",
        })
        assert answer.status_code == 202, answer.json().get("code")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [
            frame for frame in frames
            if frame.filename.startswith("/opt/lab/") and "/site-packages/" not in frame.filename
        ]
        locations = ",".join(
            f"{Path(frame.filename).name}:{frame.lineno}" for frame in relevant[-4:]
        )
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        print(f"FAIL {type(error).__name__} {state or '-'} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
