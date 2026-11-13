"""Run the APF browser against disposable PostgreSQL and loopback HTTP services."""

import json
import logging
import sys
import traceback
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch

from tests.integration.research_browser_support import ORIGIN, host

ORIGINAL = "Erholen sich Technologieaktien nach enttäuschenden Quartalszahlen?"
TRIGGER = "Quartalszahlen unter den Erwartungen"
VARIANTS = {
    "Können sich Technologiewerte nach enttäuschenden Quartalsberichten erholen?": TRIGGER,
    "Was folgt auf drei negative Handelstage?": "Drei negative Handelstage",
}
PROOF = "PASS SQL: Agent Research browser, one approved Quant batch and report, no broker orders"


@contextmanager
def serve(application, port):
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_config=None,
            proxy_headers=False,
        )
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started:
            if not thread.is_alive() or monotonic() >= deadline:
                raise RuntimeError("offline service unavailable")
            sleep(0.02)
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def provider_double(capability):
    from tests.unit.test_companion_interpretation import INTERPRETATION

    calls = []
    proposal = INTERPRETATION | {
        "hypothesis": INTERPRETATION["hypothesis"]
        | {
            "statement": ORIGINAL,
            "market": "Technologieaktien",
            "trigger": TRIGGER,
            "signal": "Negative Erstreaktion",
            "target": "Spätere Rendite",
        },
        "openQuestions": [
            {"field": "BENCHMARK", "question": "Welcher Vergleichsindex ist gemeint?"}
        ],
    }
    def reply(excerpt):
        trigger = VARIANTS.get(excerpt, TRIGGER)
        body = proposal | {
            "hypothesis": proposal["hypothesis"] | {"statement": excerpt, "trigger": trigger},
            "requiredDataTypes": [] if trigger != TRIGGER else proposal["requiredDataTypes"],
        }
        return json.dumps({
            "model": capability.model,
            "status": "completed",
            "usage": {"input_tokens": 93, "output_tokens": 46},
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(body)}],
                }
            ],
        }).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/responses" or not 0 < size < 100_000:
                self.send_error(400)
                return
            request = json.loads(self.rfile.read(size))
            excerpt = json.loads(request["input"][0]["content"])["excerpt"]
            if excerpt not in {ORIGINAL, *VARIANTS}:
                self.send_error(400)
                return
            calls.append(request)
            response = reply(excerpt)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1/responses", calls
        finally:
            server.shutdown()
            thread.join(timeout=5)


def guest():
    import httpx
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, text

    from packages.agent_research import registry
    from packages.agent_research.budget import TokenPrices, Tokens
    from packages.agent_research.model_attempts import model_budget_scopes
    from packages.agent_research.runs import RunKind
    from packages.persistence.ledger import PostgresLedger
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.events import EventFeed
    from services.control_api.quant_lab_routes import service_handler
    from services.control_api.quant_lab_service import QuantLabService
    from services.control_api.research_client import AgentResearchClient
    from services.control_api.snapshot import SnapshotReader
    from services.llm_gateway.dispatch_journal import DispatchJournal
    from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
    from services.llm_gateway.provider_wire import Provider
    from services.runtime import agent_quant_admission as admission_api
    from services.runtime import agent_research_relay as relay
    from services.runtime.agent_interpretation import OfflineDialogueProfile
    from services.runtime.agent_service import create_agent_research_service
    from services.runtime.api import create_bridge
    from services.runtime.provision import seed
    from services.worker.backtesting import BacktestWorker
    from tests.integration.companion_model_support import (
        _credentials,
        _provider_schema,
    )
    from tests.integration.test_backtest_jobs import role_engine

    logging.disable(logging.CRITICAL)
    with ExitStack() as stack:
        owner = create_engine(
            URL.create(
                "postgresql+psycopg", username="lab_migration", host="postgres", database="lab"
            ),
            hide_parameters=True,
        )
        stack.callback(owner.dispose)
        migration = Config("/opt/lab/alembic.ini")
        with owner.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
        seed(PostgresLedger(engine=owner), Decimal("10000"))
        control = role_engine(owner, "atl_control_api")
        research = role_engine(owner, "atl_agent_research")
        admission = role_engine(owner, "atl_agent_quant_admission")
        dispatch = role_engine(owner, "atl_backtest_worker")
        stack.callback(control.dispose)
        stack.callback(research.dispose)
        stack.callback(admission.dispose)
        stack.callback(dispatch.dispose)
        quant = QuantLabService(
            control,
            code_version="a" * 40,
            approval_engine=admission,
            authorize_operator=lambda actor: actor == "local-operator",
        )
        stack.enter_context(
            run_quant_workers(
                quant,
                BacktestWorker(
                    dispatch,
                    enabled=True,
                    code_version="a" * 40,
                ),
            )
        )
        approval_auth = admission_api.QuantAdmissionAuthentication(token_bytes(32))
        stack.enter_context(
            patch.object(
                admission_api,
                "URL",
                "http://127.0.0.1:8014" + admission_api.PATH,
            )
        )
        stack.enter_context(
            serve(
                admission_api.create_quant_admission_app(
                    approval_auth,
                    service=quant,
                    read_started_at=admission_api.started_receipt_reader(admission),
                ),
                8014,
            )
        )
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
            _provider_schema(),
            512,
            2,
        )
        expert = seed_quant_template(owner, research, quant)
        dialogue_scope, = (
            scope for scope in model_budget_scopes(
                agent.agent_id, agent.version, RunKind.DIALOGUE, model, "dialogue",
            ) if scope.startswith("work:")
        )
        with owner.begin() as connection:
            connection.execute(text("""
                INSERT INTO agent_research.model_budget_policies
                  (scope,version,currency,max_cost,max_input_tokens,max_output_tokens,max_calls,enabled)
                VALUES (:scope,1,'USD',0.10,16384,4096,3,true)
            """), {"scope": dialogue_scope})
        stack.enter_context(
            patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent})
        )
        stack.enter_context(
            patch.object(relay, "RELAY_URL", "http://127.0.0.1:8013" + relay.RELAY_PATH)
        )
        provider_url, calls = stack.enter_context(provider_double(capability))
        temporary = Path(stack.enter_context(TemporaryDirectory()))

        def provider(request):
            with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
                # Only the encoded body crosses the synthetic local provider boundary.
                return client.post(provider_url, content=request.content)

        profile = OfflineDialogueProfile(
            agent,
            capability,
            1,
            lambda *_: True,
            lambda _: True,
            TokenPrices(1, "USD", Decimal(1), Decimal(2)),
            Tokens(4096, 512),
            8192,
            DispatchJournal.provision(temporary / "dispatch.sqlite"),
            OfflineProviderHttp(httpx.MockTransport(provider)),
            _credentials(capability),
        )
        key = token_bytes(32)
        stack.enter_context(
            serve(
                create_agent_research_service(
                    research,
                    relay_key=key,
                    command_key=token_bytes(32),
                    offline_profile=profile,
                    extra={"listAgentProviders": lambda *_args, **_kwargs: provider_status(model)},
                ),
                8013,
            )
        )
        auth = LocalAuth(ORIGIN, transport_origin="http://127.0.0.1:8001")
        ledger = PostgresLedger(engine=control)
        stack.enter_context(
            serve(
                create_app(
                    auth=auth,
                    backend=ControlBackend(ledger, "paper"),
                    supervisor=SnapshotReader(ledger, "paper", account_alias="isolated-apf"),
                    events=EventFeed(ledger, "paper"),
                    agent_research=AgentResearchClient(relay.AgentRelayAuthentication(key)),
                    agent_quant_approval=admission_api.QuantAdmissionClient(approval_auth),
                    quant_lab=service_handler(quant),
                    health=lambda: {"status": "HEALTHY", "checks": {"DATABASE": "HEALTHY"}},
                ),
                8001,
            )
        )
        stack.enter_context(serve(create_bridge(SimpleNamespace(origin=ORIGIN)), 8000))
        print(
            json.dumps({"activationCode": auth.issue_activation_code(), "expert": expert}),
            flush=True,
        )
        if sys.stdin.readline().strip() == "verify":
            verify(owner, calls, capability)
            print(PROOF, flush=True)


def seed_quant_template(owner, research, quant):
    from sqlalchemy import text

    from runpy import run_path

    from tests.integration.test_quant_lab_acceptance import PERIODS, seed_market
    from tests.integration.test_quant_lab_storage import fixture

    instrument = seed_market(owner)
    seed_discovery(owner, research)
    with owner.begin() as connection:
        connection.execute(
            text("""
            UPDATE research.source_status SET license_status='CONFIRMED',
              policy_version='apf-browser-fixture' WHERE singleton
        """)
        )
        configure = run_path("scripts/configure-agent-quant-start.py")["configure"]
        configure(connection, global_daily=1, agent_daily=1)
        configure(connection, global_daily=1, agent_daily=1)
        assert connection.execute(text("""
            SELECT revision FROM agent_research.quant_start_policies ORDER BY policy_id
        """)).scalars().all() == [1, 1]
    request, _ = fixture()
    request.update(
        instrumentId=str(instrument),
        researchMode="RETROSPECTIVE_EXPLORATORY",
        dataSelectionPolicyVersion="retrospective-v1",
    )
    request["searchSpaces"][0]["parameterValues"] = {"fast": [10], "slow": [30]}
    windows = {
        key: dict(zip(("start", "end"), dates, strict=True)) for key, dates in PERIODS.items()
    }
    request["protocol"].update(
        folds=[dict(fold=0, **{key: windows[key] for key in ("training", "validation", "test")})],
        holdout=windows["holdout"],
    )
    return quant.create_experiment(
        request,
        actor="local-operator",
        key="apf-browser-quant-template",
        trace="a" * 32,
    )["id"]


@contextmanager
def run_quant_workers(quant, worker):
    from services.runtime.quant_preparation import prepare_next_quant

    stop = Event()

    def tick():
        while not stop.is_set():
            prepare_next_quant(quant)
            worker.tick()
            stop.wait(0.2)

    thread = Thread(target=tick, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def seed_discovery(owner, research):
    from tests.recovery.test_discovery_runtime import _check

    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _check(owner, research, now)


def provider_status(model):
    """Publish the isolated fixture's redacted, contract-validated capability."""
    return 200, {
        "items": [
            {
                "binding": {
                    "provider": model.provider,
                    "profileId": model.profile_id,
                    "profileGeneration": 1,
                    "modelId": model.model_id,
                    "modelGeneration": 1,
                },
                "credential": {
                    "credentialVersion": 4,
                    "credentialGeneration": 1,
                    "state": "ACTIVE",
                    "providerRevocationState": "NOT_CONFIRMED",
                    "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
                "availability": "AVAILABLE",
                "adapter": "OPENAI_RESPONSES",
                "outputSchemaVersion": 1,
                "schemaSubsetVersion": 1,
                "structuredOutputSupported": True,
                "maxInputTokens": 4096,
                "maxOutputTokens": 512,
                "timeoutSeconds": 2,
                "cancelSupport": "NONE",
                "rateLimitSupport": "NONE",
                "dataHandlingPolicyId": "offline",
                "dataHandlingPolicyVersion": 1,
                "pricingVersion": 1,
                "probeState": "NOT_TESTED",
            }
        ]
    }


def verify(owner, calls, capability):
    from sqlalchemy import text

    assert len(calls) == 3
    request = calls[0]
    assert set(request) == {"model", "store", "instructions", "input", "max_output_tokens", "text"}
    assert request["model"] == capability.model and request["store"] is False
    assert request["instructions"] == capability.instructions
    assert request["max_output_tokens"] == capability.max_output_tokens
    assert request["text"] == {
        "format": {
            "type": "json_schema",
            "name": "research_result",
            "strict": True,
            "schema": json.loads(capability.schema_json),
        }
    }
    assert len(request["input"]) == 1 and set(request["input"][0]) == {"role", "content"}
    assert request["input"][0]["role"] == "user"
    assert json.loads(request["input"][0]["content"]) == {
        "excerpt": ORIGINAL,
        "confirmedContext": {},
    }
    assert [json.loads(call["input"][0]["content"]) for call in calls[1:]] == [
        {"excerpt": excerpt, "confirmedContext": {}} for excerpt in VARIANTS
    ]
    with owner.connect() as connection:
        triggers = (
            connection.execute(
                text("""
            SELECT document->'hypothesis'->>'trigger'
            FROM agent_research.companion_idea_versions
            WHERE version > 1 AND document->>'title'='Synthetische Quartalsanalyse'
        """)
            )
            .scalars()
            .all()
        )
        assert triggers == [TRIGGER]
        assert connection.scalar(text("SELECT count(*) FROM backtest.experiment_batches")) == 1
        assert connection.scalar(text("SELECT count(*) FROM agent_research.report_versions")) == 1
        assert (
            connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        )


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["guest"]:
            guest()
        else:
            host(agent=True)
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        frame = next((item for item in reversed(frames) if "/tests/" in item.filename),
                     frames[-1])
        print(
            f"RESEARCH_BROWSER_FAIL:{type(error).__name__}:{Path(frame.filename).name}:{frame.lineno}",
            flush=True,
        )
        raise SystemExit(1) from None
