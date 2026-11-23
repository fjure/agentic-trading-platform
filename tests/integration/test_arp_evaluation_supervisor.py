"""Execute the shipped evaluation supervisor through real consent, SQL and dispatch."""

import json
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from packages.agent_research import registry
from packages.agent_research.model_attempts import model_budget_scopes
from packages.agent_research.runs import RunKind
from services.llm_gateway import provider_http
from services.llm_gateway.supervisor_rpc import create_admission_app
from services.runtime.agent_service import create_agent_research_service
from services.runtime.research_evaluation import (
    ENCODING,
    EvaluationSupervisor,
    evaluation_supervisor,
)
from tests.e2e.agent_provider_browser import (
    CREDENTIAL,
    bind_dialogue,
    fixture_tls,
    start_synthetic_egress,
)
from tests.e2e.agent_research_browser import serve
from tests.evaluation.harness import Approval, EvaluationInput, RunBinding
from tests.evaluation.operator_store import OperatorStore
from tests.integration import test_arp_provider_smoke as fixture
from tests.integration.test_agent_research_runtime import WORKER_ROLE

PROOF = "PASS evaluation SQL consent, exact expert context, private result and replay denial"


def test_arp_evaluation_supervisor_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (
            *tables,
            "agent_research.runs",
            "agent_research.model_attempts",
            "agent_research.processing_grants",
            "agent_research.processing_operations",
            *(
                "agent_research.companion_" + name
                for name in (
                    "ideas",
                    "idea_versions",
                    "dialogues",
                    "dialogue_versions",
                    "originals",
                    "events",
                )
            ),
        )
        assert (
            harness.host("tests.integration.test_arp_evaluation_supervisor") == PROOF + "/restore"
        )
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def check(owner, worker, model, agent, authority, binding, epoch, plan):
    model = replace(model, profile_id="arp-evaluation-production")
    agent = replace(agent, model_binding=model)
    scopes = model_budget_scopes(
        agent.agent_id, agent.version, RunKind.DIALOGUE, model, "evaluation"
    )
    with owner.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO agent_research.model_budget_policies
              (scope,version,currency,max_cost,max_input_tokens,max_output_tokens,max_calls,
               enabled,max_parallelism)
            VALUES (:scope,1,'USD',1,65536,8192,12,true,1)
            ON CONFLICT(scope) DO UPDATE SET max_cost=1,max_input_tokens=65536,
              max_output_tokens=8192,max_calls=12
        """),
            [{"scope": scope} for scope in scopes],
        )
    calls = []

    def provider(request):
        envelope = json.loads(request.content)
        projected = json.loads(envelope["input"][0]["content"])
        case = json.loads(projected["excerpt"])
        assert projected["confirmedContext"] == {}
        assert case == {
            "format": ENCODING,
            "input": "Compare the registered EMA hypothesis.",
            "dialogueState": {
                "status": "CONFIRMED_CONSTRAINTS",
                "confirmed": {"registry": "ema-cross-v1@1", "parameters": "fast=5;slow=20"},
            },
        }
        calls.append(case)
        return httpx.Response(
            200,
            json={
                "model": model.model_id,
                "status": "completed",
                "service_tier": "default",
                "usage": {
                    "input_tokens": 100, "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"result":"registered observation"}'}
                        ],
                    }
                ],
            },
        )

    with ExitStack() as stack:
        root = Path(stack.enter_context(TemporaryDirectory()))
        stack.enter_context(patch.object(registry, "AGENTS", {
            (agent.agent_id, agent.version): agent,
        }))
        capability = replace(
            fixture.fixture_capability(model),
            envelope_version="evaluation-v1",
            instructions="Evaluate the registered case envelope.",
            schema_json='{"type":"object","required":["result"],"properties":{"result":{"type":"string"}},"additionalProperties":false}',
            max_output_tokens=128,
        )
        tls, trust = fixture_tls(root)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                assert self.path == "/v1/responses"
                assert self.headers.get("Authorization") == "Bearer " + CREDENTIAL
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                response = provider(httpx.Request("POST", "https://api.openai.com/v1/responses",
                                                 content=payload))
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_provider():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive()

        stack.callback(stop_provider)
        start_synthetic_egress(stack, server.server_port)
        stack.enter_context(patch.object(provider_http, "_provider_tls_context", lambda: trust))
        client = stack.enter_context(httpx.Client(
            base_url="http://127.0.0.1:8015", timeout=10, trust_env=False,
        ))
        profile, auth, configured = bind_dialogue(
            stack, owner, worker, agent, capability, root, serve, client, bytes(range(32)),
        )
        # The smoke fixture's shared global ceiling is deliberately tiny.
        # Set the explicit synthetic evaluation ceiling before its preview binds it.
        with owner.begin() as connection:
            connection.execute(text("""
                UPDATE agent_research.model_budget_policies SET version=version+1,
                    max_cost=1,max_input_tokens=65536,max_output_tokens=8192,max_calls=16
            """))
        service = create_agent_research_service(
            worker, relay_key=bytes(range(32)), command_key=bytes(range(32)),
            provider_profile=profile, admission_auth=auth,
        )
        private = root / "private"
        private.mkdir(mode=0o700)
        OperatorStore.provision(private)
        store = OperatorStore(private)
        evaluation_binding = RunBinding(model.profile_id, model.model_id, "v1", *["a" * 64] * 6)
        approval = Approval(
            "evaluation-fixture",
            evaluation_binding,
            datetime.now(UTC) + timedelta(minutes=1),
            3,
            Decimal("0.03"),
            Decimal("0.01"),
        )
        store.register(approval, ("DE-01",))
        evaluator = EvaluationSupervisor(service.state.research_evaluation, store, approval)
        stack.enter_context(serve(
            create_admission_app(auth, evaluator.step.admission.consume), 8013,
        ))
        assert evaluator.consume(approval, ("DE-01",))
        assert not evaluator.consume(approval, ("DE-01",))
        ports = service.state.research_evaluation
        unrelated = ports["runs"].enqueue(
            actor="local-operator",
            key="unrelated-evaluation-queue",
            kind=RunKind.DIALOGUE,
            input_ref="unrelated",
            trace_id=uuid4().hex,
            agent=agent,
        )
        assert ports["runs"].claim(uuid4(), run_id=uuid4()) is None
        assert ports["runs"].snapshot(unrelated, actor="local-operator").state == "QUEUED"
        case = EvaluationInput(
            "DE-01",
            "Compare the registered EMA hypothesis.",
            {
                "status": "CONFIRMED_CONSTRAINTS",
                "confirmed": {"registry": "ema-cross-v1@1", "parameters": "fast=5;slow=20"},
            },
        )
        outcome = evaluator.execute(approval, 1, case)
        assert outcome.status == "answered" and outcome.confirmed_cost is None, outcome.status
        with pytest.raises(ValueError, match="EVALUATION_RESERVATION_DENIED"):
            evaluator.execute(approval, 1, case)
        assert len(calls) == 1
        assert ports["runs"].snapshot(unrelated, actor="local-operator").state == "QUEUED"
        normal_claim = ports["runs"].claim(uuid4())
        assert normal_claim is not None and normal_claim.run_id == unrelated
        with pytest.raises(ValueError, match="EVALUATION_SUPERVISOR_BUSY"):
            evaluator.execute(approval, 2, case)
        assert ports["runs"].snapshot(unrelated, actor="local-operator").state == "RUNNING"
        ports["runs"].transition(normal_claim, "BLOCKED")
        assert len(calls) == 1
        assert store.states(approval) == {
            1: {"DE-01": "answered"},
            2: {"DE-01": "reserved"},
        }
        assert store.raw_result(approval, 1, "DE-01") == {"result": "registered observation"}
        check_factory_guards(root, capability, configured, approval)
        with owner.connect() as connection:
            for name in (
                "companion_ideas", "companion_idea_versions", "companion_dialogues",
                "companion_dialogue_versions", "companion_originals", "companion_events",
            ):
                assert connection.scalar(text(
                    f"SELECT count(*) FROM agent_research.{name}"
                )) == 0, f"Evaluation content escaped to {name}"
            for name in (
                "runs", "run_events", "processing_grants", "processing_operations",
                "model_attempts", "model_attempt_events",
            ):
                retained = connection.scalar(text(
                    f"SELECT coalesce(jsonb_agg(to_jsonb(t))::text,'') "
                    f"FROM agent_research.{name} t"
                ))
                assert all(secret not in retained for secret in (
                    case.text, "fast=5;slow=20", "registered observation",
                )), f"Private evaluation content escaped to {name}"
            row = connection.execute(
                text("""
                SELECT a.status,a.result_ref,a.confirmed_cost,a.held_cost,g.state,r.state
                FROM agent_research.model_attempts a
                JOIN agent_research.processing_grants g USING(attempt_id)
                JOIN agent_research.runs r ON r.run_id=a.run_id
                WHERE a.result_ref LIKE 'evaluation:%'
            """)
            ).one()
            assert tuple(row)[0] == "COMPLETED"
            assert tuple(row)[2] is None and tuple(row)[3] > 0
            assert tuple(row)[4:] == ("ACCEPTED", "COMPLETED")
            assert (
                connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one()
                == 0
            )

        actual_dispatch = evaluator.step.gateway.dispatch

        def lose_usage(request):
            result = actual_dispatch(request)
            assert result.state.value == "ACCEPTED" and result.usage is not None
            return replace(result, usage=None)

        with patch.object(evaluator.step.gateway, "dispatch", lose_usage):
            unknown = evaluator.execute(approval, 3, case)
        assert unknown.status == "timeout" and unknown.confirmed_cost is None
        assert len(calls) == 2
        assert store.raw_result(approval, 3, "DE-01") is None
        assert store.states(approval)[3] == {"DE-01": "timeout"}
        with pytest.raises(ValueError, match="EVALUATION_RESERVATION_DENIED"):
            evaluator.execute(approval, 3, case)
        assert len(calls) == 2
        with owner.connect() as connection:
            unknown_attempt = connection.execute(text("""
                SELECT a.result_ref,a.confirmed_cost,a.held_cost,a.cost_bound,g.state,r.state
                FROM agent_research.model_attempts a
                JOIN agent_research.processing_grants g USING(attempt_id)
                JOIN agent_research.runs r ON r.run_id=a.run_id
                WHERE a.status='OUTCOME_UNKNOWN'
            """)).one()
            assert unknown_attempt.result_ref is None and unknown_attempt.confirmed_cost is None
            assert unknown_attempt.held_cost == unknown_attempt.cost_bound > 0
            assert tuple(unknown_attempt)[4:] == ("DISPATCHED", "WAITING_FOR_INPUT")


def check_factory_guards(root, capability, configured, approval):
    from services.llm_gateway import provider_installation
    from services.runtime import agent_service

    descriptor = root / "profile.json"
    settings = root / "settings.json"
    document = {
        "binding": configured.record(),
        "instructions": capability.instructions,
        "schema": json.loads(capability.schema_json),
    }

    def configuration(*, encoding=ENCODING, probe=False, expired=False):
        value = document | ({"probeOutputTokens": 32} if probe else {})
        descriptor.write_text(json.dumps(value))
        settings.write_text(json.dumps({
            "projectionEncoding": encoding,
            "providerInstallationSha256": sha256(descriptor.read_bytes()).hexdigest(),
        }))
        binding = replace(
            approval.binding,
            prompt_sha256=configured.prompt_sha256,
            schema_sha256=configured.schema_sha256,
            settings_sha256=sha256(settings.read_bytes()).hexdigest(),
        )
        return replace(
            approval, binding=binding,
            expires_at=(datetime.now(UTC) - timedelta(seconds=1)) if expired
            else approval.expires_at,
        )

    def forbidden_database():
        raise AssertionError("Rejected configuration opened the runtime database")

    with (
        patch.object(provider_installation, "PROFILE_PATH", descriptor),
        patch.object(agent_service, "_database_engine", forbidden_database),
    ):
        for options in ({"encoding": "unregistered"}, {"probe": True}, {"expired": True}):
            invalid = configuration(**options)
            with pytest.raises(ValueError, match="EVALUATION_BINDING_MISMATCH"):
                evaluation_supervisor(None, invalid, files={"settings": settings})


if __name__ == "__main__":
    try:
        fixture.guest(check)
    except Exception as error:
        from traceback import extract_tb

        own = [
            f"{Path(frame.filename).name}:{frame.lineno}"
            for frame in extract_tb(error.__traceback__)
            if "/site-packages/" not in frame.filename
        ]
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', '-')} {','.join(own[-5:])}")
        raise SystemExit(1) from None
    print(PROOF)
