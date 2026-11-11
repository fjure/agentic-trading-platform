"""Control, signed Relay and the native Research data stores in one offline path."""

import json
import sys
from pathlib import Path

from tests.integration.test_agent_research_runtime import WORKER_ROLE

_PROOF = "PASS authenticated Control, private Relay and PostgreSQL Research operations"


def test_agent_control_http_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (
            *tables,
            "agent_research.control_commands",
            "agent_research.runs",
            "agent_research.companion_ideas",
            "agent_research.discovery_schedules",
        )
        assert harness.host("tests.recovery.test_agent_control_http") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def _exercise(owner, research):
    from time import monotonic, sleep

    import httpx
    from fastapi.testclient import TestClient

    from packages.agent_research.discovery.schedule import DailySchedule, ScheduleStore
    from packages.agent_research.registry import QUANT_RESEARCH_V1
    from packages.agent_research.runs import RunKind, RunStore
    from packages.backtesting.experiment_storage import ExperimentStorage
    from services.control_api.auth import LocalAuth
    from services.control_api.research_client import AgentResearchClient
    from services.runtime.agent_research_relay import RELAY_PATH, AgentRelayAuthentication
    from services.runtime.agent_service import create_agent_research_service
    from tests.contracts.test_agent_research_routes import activate, client
    from tests.integration.test_backtest_jobs import role_engine
    from tests.integration.test_quant_operator_approval import (
        _ADMISSION,
        _binding,
        _draft_and_validate,
        _seed_idea,
    )
    from tests.unit.test_discovery_schedule import schedule_document

    schedule = schedule_document(state="DISABLED")
    ScheduleStore(owner).create(DailySchedule.parse(schedule))
    run_id = RunStore(research).enqueue(
        actor="local-operator",
        key="cancel-control-fixture",
        kind=RunKind.DIALOGUE,
        input_ref="synthetic-idea",
        trace_id="a" * 32,
        agent=QUANT_RESEARCH_V1,
    )
    relay_key = bytes(range(32))
    service = create_agent_research_service(
        research,
        relay_key=relay_key,
        command_key=bytes(reversed(range(32))),
        schedule_id=schedule["scheduleId"],
    )

    def relay_request(incoming):
        response = TestClient(service).post(
            RELAY_PATH,
            headers=incoming.headers,
            content=incoming.content,
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=httpx.ByteStream(response.content),
        )

    backend = AgentResearchClient(
        AgentRelayAuthentication(relay_key),
        transport=httpx.MockTransport(relay_request),
    )
    auth = LocalAuth()
    base = "/api/v1/research/agent"
    with client(auth, backend) as http:
        activate(http, auth, "native-control-login")

        def write(path, payload, *, key, method="POST"):
            return http.request(
                method,
                base + path,
                json=payload,
                headers={
                    "Origin": auth.origin,
                    "Idempotency-Key": key,
                },
            )

        idea_payload = {"title": "Synthetic observation", "originalText": "Does price recover?"}
        made = write("/ideas", idea_payload, key="native-control-idea")
        assert made.status_code == 201
        idea = made.json()
        assert write("/ideas", idea_payload, key="native-control-idea").json() == idea
        assert (
            write(
                "/ideas", idea_payload | {"title": "Changed"}, key="native-control-idea"
            ).status_code
            == 409
        )
        assert http.get(base + "/ideas?limit=1").json()["items"] == [idea]
        dialogue = idea["dialogueId"]
        turn = write(
            f"/dialogues/{dialogue}/turns",
            {
                "kind": "FREE_TEXT_RESEARCH",
                "expectedDialogueVersion": 1,
                "expectedIdeaVersion": 1,
                "text": "Does volume change after earnings?",
            },
            key="native-control-turn",
        )
        assert turn.status_code == 202
        assert turn.json()["processing"]["state"] == "LOCAL_CLARIFICATION"
        assert http.get(f"{base}/dialogues/{dialogue}").json()["turns"] == [turn.json()]
        turn_id = turn.json()["turnId"]
        processing_path = f"/dialogues/{dialogue}/turns/{turn_id}/processing"
        versions = {"expectedTurnVersion": 1, "expectedDialogueVersion": 2}
        declined = write(
            processing_path + "-decisions",
            versions | {"action": "DECLINE"},
            key="native-control-decline",
        )
        assert declined.status_code == 200 and declined.json()["state"] == "DECLINED"
        assert http.get(base + processing_path).json() == declined.json()
        assert http.get(base + "/registry").status_code == 200
        assert http.get(base + "/reports").json() == {"items": [], "nextCursor": None}
        run_path = f"/runs/{run_id}"
        assert http.get(base + run_path).json()["state"] == "QUEUED"
        cancelled = write(run_path + "/cancel", {"expectedVersion": 1}, key="native-control-cancel")
        assert cancelled.status_code == 200 and cancelled.json()["state"] == "CANCELLED"
        assert (
            write(run_path + "/cancel", {"expectedVersion": 1}, key="native-control-cancel").json()
            == cancelled.json()
        )
        assert http.get(base + run_path + "/events").json()["items"][-1]["state"] == "CANCELLED"
        event_path = base + run_path + "/events"
        first_event = http.get(event_path, params={"limit": 1}).json()
        assert first_event["items"][0]["state"] == "QUEUED"
        next_event = http.get(event_path, params={"limit": 1, "cursor": first_event["nextCursor"]})
        assert next_event.json()["items"][0]["state"] == "CANCELLED"
        assert http.get(base + "/schedule").json()["state"] == "DISABLED"
        fields = {name: schedule[name] for name in ("timeZone", "localTime", "scope", "limits")}
        updated = write(
            "/schedule",
            fields | {"expectedVersion": 1},
            key="native-control-schedule",
            method="PUT",
        )
        assert updated.status_code == 200 and updated.json()["version"] == 2

        _seed_idea(owner)
        control, admission = (role_engine(owner, role) for role in ("atl_control_api", _ADMISSION))
        try:
            approval_run, draft, validation = _draft_and_validate(
                owner,
                RunStore(research),
                ExperimentStorage(control),
            )
            binding = _binding(owner, approval_run, draft, validation)
            expert_idea = write("/ideas", idea_payload, key="native-control-expert-idea").json()
            first_page = http.get(base + "/ideas", params={"limit": 1}).json()
            second_page = http.get(base + "/ideas", params={
                "limit": 1, "cursor": first_page["nextCursor"],
            }).json()
            assert {page["items"][0]["id"] for page in (first_page, second_page)} == {
                idea["id"], expert_idea["id"],
            }
            expert = {
                "definitionId": str(draft["definition_id"]),
                "definitionVersion": draft["definition_version"],
                "definitionHash": draft["fingerprint"],
            }
            dialogue_request = {
                "ideaId": expert_idea["id"],
                "expectedIdeaVersion": 1,
                "expertExperiment": expert,
            }
            bound = write("/dialogues", dialogue_request, key="native-control-expert-bind")
            assert bound.status_code == 201 and bound.json()["expertExperiment"] == expert
            assert (
                write("/dialogues", dialogue_request, key="native-control-expert-bind").json()
                == bound.json()
            )
            approval_id = ExperimentStorage(admission).prepare_approval(
                binding,
                "native-control-prepare",
            )
            approval_path = f"/approvals/{approval_id}"
            assert http.get(base + approval_path).json()["state"] == "PENDING"
            decision = {
                "action": "REJECT",
                "expectedApprovalVersion": 1,
                "bindingHash": binding.binding_hash,
            }
            rejected = write(approval_path + "/decision", decision, key="native-control-reject")
            assert rejected.status_code == 200 and rejected.json()["state"] == "REJECTED"
            assert (
                write(approval_path + "/decision", decision, key="native-control-reject").json()
                == rejected.json()
            )
            assert http.get(base + approval_path).json()["approvalVersion"] == 2
        finally:
            control.dispose()
            admission.dispose()

    runs = RunStore(research)
    queued = runs.enqueue(
        actor="local-operator",
        key="supervisor-control-fixture",
        kind=RunKind.DIALOGUE,
        input_ref="synthetic-dialogue",
        trace_id="c" * 32,
        agent=QUANT_RESEARCH_V1,
    )
    with TestClient(service):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            if runs.snapshot(queued, actor="local-operator").state == "WAITING_FOR_INPUT":
                break
            sleep(0.05)
        else:
            raise AssertionError("the supervisor did not claim the queued Research run")


def _offline_processing(owner, research):
    from dataclasses import replace
    from decimal import Decimal
    from tempfile import TemporaryDirectory
    from time import monotonic, sleep
    from unittest.mock import patch

    import httpx
    from fastapi.testclient import TestClient

    from packages.agent_research import registry
    from packages.agent_research.budget import TokenPrices, Tokens
    from services.control_api.auth import LocalAuth
    from services.control_api.research_client import AgentResearchClient
    from services.llm_gateway.dispatch_journal import DispatchJournal
    from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
    from services.llm_gateway.provider_wire import Provider
    from services.runtime.agent_interpretation import OfflineDialogueProfile
    from services.runtime.agent_research_relay import RELAY_PATH, AgentRelayAuthentication
    from services.runtime.agent_service import create_agent_research_service
    from tests.contracts.test_agent_research_routes import activate, client
    from tests.integration.companion_model_support import _configure_budget, _credentials
    from tests.integration.companion_model_support import _provider_schema as provider_schema
    from tests.unit.test_companion_interpretation import INTERPRETATION

    model = registry.ModelBinding("OPENAI", "offline-profile", 1, "offline-model", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    capability = Capability(
        Provider.OPENAI,
        model.model_id,
        model.profile_id,
        1,
        1,
        1,
        "offline-envelope-v1",
        "Return a structured hypothesis only.",
        provider_schema(),
        512,
        2,
    )
    _configure_budget(owner, agent, model)
    original = "Can technology shares rebound after disappointing earnings?"
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(json.loads(body["input"][0]["content"]))
        return httpx.Response(
            200,
            json={
                "model": model.model_id,
                "status": "completed",
                "usage": {"input_tokens": 93, "output_tokens": 46},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(INTERPRETATION)}],
                    }
                ],
            },
        )

    with (
        TemporaryDirectory() as temporary,
        patch.object(
            registry,
            "AGENTS",
            {(agent.agent_id, agent.version): agent},
        ),
    ):
        profile = OfflineDialogueProfile(
            agent,
            capability,
            1,
            lambda *_: True,
            lambda _: True,
            TokenPrices(1, "USD", Decimal(1), Decimal(2)),
            Tokens(4096, 512),
            8192,
            DispatchJournal.provision(Path(temporary) / "dispatch.sqlite"),
            OfflineProviderHttp(httpx.MockTransport(provider)),
            _credentials(capability),
        )
        relay_key = bytes(range(32))
        app = create_agent_research_service(
            research,
            relay_key=relay_key,
            command_key=bytes(reversed(range(32))),
            offline_profile=profile,
        )
        with TestClient(app) as relay_http:

            def relay_request(incoming):
                response = relay_http.post(
                    RELAY_PATH,
                    headers=incoming.headers,
                    content=incoming.content,
                )
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    stream=httpx.ByteStream(response.content),
                )

            backend = AgentResearchClient(
                AgentRelayAuthentication(relay_key),
                transport=httpx.MockTransport(relay_request),
            )
            auth = LocalAuth()
            base = "/api/v1/research/agent"
            with client(auth, backend) as http:
                activate(http, auth, "offline-control-login")

                def post(path, data, key, target=http):
                    return target.post(
                        base + path,
                        json=data,
                        headers={
                            "Origin": auth.origin,
                            "Idempotency-Key": key,
                        },
                    )

                idea = post(
                    "/ideas",
                    {"title": "Synthetic model test", "originalText": original},
                    "offline-control-idea",
                ).json()
                dialogue = f"/dialogues/{idea['dialogueId']}"
                turn = post(
                    dialogue + "/turns",
                    {
                        "kind": "FREE_TEXT_RESEARCH",
                        "expectedDialogueVersion": 1,
                        "expectedIdeaVersion": 1,
                        "text": original,
                    },
                    "offline-control-turn",
                )
                assert turn.status_code == 202
                turn = turn.json()
                run = f"{base}/runs/{turn['runId']}"
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    if http.get(run).json()["state"] == "WAITING_FOR_PROCESSING_DECISION":
                        break
                    sleep(0.05)
                else:
                    raise AssertionError("interpretation did not await explicit consent")
                path = dialogue + f"/turns/{turn['turnId']}/processing"
                versions = {"expectedDialogueVersion": 2, "expectedTurnVersion": 1}
                preview = post(
                    path + "-previews",
                    versions | {"profileId": model.profile_id},
                    "offline-control-preview",
                )
                assert preview.status_code == 201
                preview = preview.json()
                assert preview["projection"] == {"excerpt": original, "confirmedContext": {}}
                assert calls == []
                consent = versions | {
                    "action": "SEND",
                    "previewId": preview["previewId"],
                    "projectionBinding": preview["projectionBinding"],
                }
                with client(auth, backend) as another:
                    activate(another, auth, "offline-other-login")
                    denied = post(path + "-decisions", consent, "offline-other-consent", another)
                    assert denied.status_code == 409 and calls == []
                sent = post(path + "-decisions", consent, "offline-control-consent")
                assert sent.status_code == 202
                assert (
                    post(path + "-decisions", consent, "offline-control-consent").json()
                    == sent.json()
                )
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    if http.get(base + path).json()["state"] == "PROPOSAL_VALIDATED":
                        break
                    sleep(0.05)
                else:
                    raise AssertionError("the bound model proposal was not published")
                assert calls == [preview["projection"]]
                current = http.get(base + dialogue).json()
                assert current["confirmedHypothesis"] is None
                proposal = current["turns"][-1]["proposal"]
                assert proposal["interpretation"] == INTERPRETATION
                confirmed = post(
                    dialogue + "/decisions",
                    {
                        "action": "CONFIRM_HYPOTHESIS",
                        "expectedIdeaVersion": current["ideaVersion"],
                        "expectedDialogueVersion": current["version"],
                        "proposalId": proposal["proposalId"],
                        "proposalHash": proposal["proposalHash"],
                        "confirmedFields": [
                            {"field": "TRIGGER", "value": INTERPRETATION["hypothesis"]["trigger"]}
                        ],
                        "confirmedAssumptionIds": [],
                    },
                    "offline-control-confirm",
                )
                assert confirmed.status_code == 200 and confirmed.json()["ideaVersion"] == 2
                reloaded = http.get(f"{base}/ideas/{idea['id']}").json()
                assert reloaded["hypothesis"]["trigger"] == INTERPRETATION["hypothesis"]["trigger"]
                assert reloaded["experimentIds"] == [] and len(calls) == 1
                next_turn = post(dialogue + "/turns", {
                    "kind": "FREE_TEXT_RESEARCH",
                    "expectedDialogueVersion": confirmed.json()["dialogueVersion"],
                    "expectedIdeaVersion": confirmed.json()["ideaVersion"],
                    "text": "Does a different research window change the observation?",
                }, "offline-control-follow-up")
                assert next_turn.status_code == 202
                prior = http.get(base + path).json()
                assert prior["state"] == "PROPOSAL_VALIDATED" and prior["delivery"] == "SENT"
                assert len(calls) == 1


def guest():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    from tests.integration.test_backtest_jobs import role_engine

    payload = json.load(sys.stdin)
    owner = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=payload["role"],
            password=payload["password"],
            host="postgres",
            port=5432,
            database=payload["database"],
        ),
        hide_parameters=True,
    )
    research = role_engine(owner, WORKER_ROLE)
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        _exercise(owner, research)
        _offline_processing(owner, research)
    finally:
        research.dispose()
        owner.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        relevant = [
            frame
            for frame in traceback.extract_tb(error.__traceback__)
            if frame.filename.startswith("/opt/lab/")
        ]
        locations = ",".join(f"{Path(item.filename).name}:{item.lineno}" for item in relevant[-6:])
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', '')} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
