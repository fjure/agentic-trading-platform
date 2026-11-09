"""Real PostgreSQL budgets with a fixed synthetic HTTP provider double."""

import json
import sys
from pathlib import Path
from uuid import uuid4

from tests.integration.test_agent_research_runtime import WORKER_ROLE

_PROOF = "PASS synthetic provider probe with shared PostgreSQL budgets"


def test_probe_uses_existing_ledger_and_honors_shared_caps():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, WORKER_ROLE)
        harness.TABLES = (*previous_tables, "agent_research.model_attempts")
        assert harness.host("tests.integration.test_llm_provider_probe") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS = previous_groups
        harness.TABLES = previous_tables


def guest():
    from dataclasses import asdict, replace
    from decimal import Decimal
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    import httpx
    import pytest
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL

    from packages.agent_research import registry
    from packages.agent_research.budget import BudgetDenied, TokenPrices, Tokens
    from packages.agent_research.model_attempts import ModelAttemptStore, model_budget_scopes
    from packages.agent_research.run_state import RunState
    from packages.agent_research.runs import RunConflict, RunKind, RunStore
    from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome
    from services.llm_gateway.probe import (
        PROBE_INPUT_TOKENS,
        PROBE_PROJECTION,
        PROBE_TIMEOUT_SECONDS,
        OfflineProbeGateway,
        probe_hash,
    )
    from services.llm_gateway.provider_http import OfflineProviderHttp
    from services.llm_gateway.provider_wire import Provider
    from services.runtime.agent_research import ResearchSupervisor
    from services.runtime.research_probe import BudgetedSyntheticProbe
    from services.setup.llm_credentials import (
        CredentialBinding,
        CredentialDenied,
        CredentialState,
        SyntheticCredentialStore,
    )
    from tests.unit.test_llm_provider_wire import response

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

    model = registry.ModelBinding("OPENAI", "probe-profile", 1, "fixture-model", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    binding = CredentialBinding(Provider.OPENAI, 1, model.profile_id, 1, 1)
    scopes = model_budget_scopes(agent.agent_id, agent.version, RunKind.PROVIDER_PROBE, model, "x")
    research_scopes = model_budget_scopes(
        agent.agent_id, agent.version, RunKind.REEVALUATION, model, "x",
    )
    prices = TokenPrices(1, "USD", Decimal("1"), Decimal("2"))
    seen = []

    def double(request):
        assert request.url == httpx.URL("https://api.openai.com/v1/responses")
        assert 0 < request.extensions["timeout"]["read"] < PROBE_TIMEOUT_SECONDS
        assert len(request.content) + 256 <= PROBE_INPUT_TOKENS
        assert request.headers["authorization"].startswith("Bearer offline-canary-")
        assert json.loads(request.content)["input"] == [{
            "role": "user", "content": PROBE_PROJECTION.decode(),
        }]
        seen.append(request.url.path)
        body = response(Provider.OPENAI, '{"ok":true}')
        if len(seen) == 1:
            body["usage"] = {"input_tokens": 40, "output_tokens": 7}
        return httpx.Response(200, json=body)

    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0041_agent_provider_probe")
            connection.execute(text("""
                INSERT INTO agent_research.model_budget_policies (scope,version,currency,max_cost,
                    max_input_tokens,max_output_tokens,max_calls,enabled)
                VALUES (:scope,1,'USD',0.10,4096,1024,10,true)
            """), [{"scope": scope} for scope in sorted(set(scopes) | set(research_scopes))])
            connection.execute(text("""
                INSERT INTO agent_research.model_prices (model_binding,version,currency,
                    input_price,output_price,bounds_unconfirmed_cost)
                VALUES (CAST(:model AS jsonb),1,'USD',1,2,false)
            """), {"model": json.dumps(asdict(model))})

        with TemporaryDirectory() as directory, patch.object(
            registry, "AGENTS", {(agent.agent_id, agent.version): agent},
        ):
            credentials = SyntheticCredentialStore.provision(Path(directory) / "credentials.sqlite")
            journal = DispatchJournal.provision(Path(directory) / "dispatch.sqlite")
            gateway = OfflineProbeGateway(
                journal, OfflineProviderHttp(httpx.MockTransport(double)), credentials,
                authorize=lambda request, digest: allowed.authorize_gateway(request, digest),
            )
            runs, attempts = RunStore(worker), ModelAttemptStore(worker)
            allowed = BudgetedSyntheticProbe(
                runs, attempts, gateway, agent=agent, prices=prices, permits_probe=lambda _: True,
            )
            denied = BudgetedSyntheticProbe(
                runs, attempts, gateway, agent=agent, prices=prices, permits_probe=lambda _: False,
            )
            stored = credentials.save(binding.provider, expected_version=1)
            with pytest.raises(CredentialDenied):
                credentials.read_active(binding)
            probe = credentials.begin_probe(binding, expected_version=stored.version)
            probes = {probe.probe_id: probe}
            supervisor = ResearchSupervisor(
                runs, {RunKind.PROVIDER_PROBE: allowed.supervisor_step(probes.get)},
                attempts=attempts,
            )

            def state(run_id):
                with owner.connect() as connection:
                    return connection.execute(text(
                        "SELECT state FROM agent_research.runs WHERE run_id=:run",
                    ), {"run": run_id}).scalar_one()

            assert seen == []
            with pytest.raises(RunConflict, match="^AGENT_POLICY_DENIED$"):
                denied.enqueue(probe, actor="local-operator", key="denied-probe-request",
                               trace_id="a" * 32)
            run = allowed.enqueue(probe, actor="local-operator", key="allowed-probe-request",
                                  trace_id="a" * 32)
            assert supervisor.run_once(uuid4()) and state(run) == RunState.COMPLETED
            verified = credentials.status(binding.provider, binding.credential_generation)
            assert verified.state is CredentialState.VERIFIED
            replay = allowed.enqueue(probe, actor="local-operator", key="replay-probe-request",
                                     trace_id="a" * 32)
            assert supervisor.run_once(uuid4()) and state(replay) == RunState.BLOCKED

            new_probe = credentials.begin_probe(binding, expected_version=verified.version)
            probes[new_probe.probe_id] = new_probe
            for scope_kind in ("work", "global", "provider"):
                scope = next(key for key in scopes if key.startswith(f"{scope_kind}:"))
                with owner.begin() as connection:
                    connection.execute(text("""
                        UPDATE agent_research.model_budget_policies SET version=version+1,
                            max_calls=CASE WHEN scope=:scope THEN 1 ELSE 10 END
                    """), {"scope": scope})
                run = allowed.enqueue(
                    new_probe, actor="local-operator", key=f"budget-blocked-{scope_kind}-probe",
                    trace_id="a" * 32,
                )
                assert supervisor.run_once(uuid4()) and state(run) == RunState.BUDGET_EXHAUSTED
                if scope_kind == "global":
                    regular_run = runs.enqueue(
                        actor="local-operator", key="ordinary-research-budget-check",
                        kind=RunKind.REEVALUATION, input_ref="fixture", trace_id="a" * 32,
                        agent=agent,
                    )
                    regular = runs.claim(uuid4())
                    assert regular is not None and regular.run_id == regular_run
                    with pytest.raises(BudgetDenied, match="^BUDGET_EXHAUSTED$"):
                        attempts.reserve(
                            regular, attempt_id=uuid4(), step_id="ordinary-research",
                            request_hash="a" * 64, model=model, prices=prices,
                            tokens=Tokens(128, 32), context_window=160, timeout_seconds=5,
                        )
                    runs.cancel(regular.run_id, regular.version)
            assert seen == ["/v1/responses"]

            with owner.begin() as connection:
                connection.execute(text("""
                    UPDATE agent_research.model_budget_policies SET version=version+1, max_calls=10
                """))
            unknown = allowed.enqueue(new_probe, actor="local-operator", key="unknown-usage-probe",
                                      trace_id="a" * 32)
            assert supervisor.run_once(uuid4()) and state(unknown) == RunState.BLOCKED
            assert seen == ["/v1/responses"] * 2
            assert credentials.status(binding.provider, binding.credential_generation).state is (
                CredentialState.STORED
            )
            with pytest.raises(CredentialDenied):
                credentials.read_active(binding)
            assert journal.status(new_probe.probe_id, probe_hash(new_probe, model.model_id)) is (
                DispatchOutcome.OUTCOME_UNKNOWN
            )

            with owner.connect() as connection:
                rows = connection.execute(text("""
                    SELECT status,scopes,held_cost,confirmed_cost,reported_input,reported_output
                    FROM agent_research.model_attempts
                """)).all()
                completed, row = sorted(rows, key=lambda attempt: attempt.status)
                assert completed.status == "COMPLETED" and set(completed.scopes) == set(scopes)
                assert (completed.reported_input, completed.reported_output) == (40, 7)
                assert row.status == "OUTCOME_UNKNOWN" and set(row.scopes) == set(scopes)
                assert row.held_cost > 0 and row.confirmed_cost is None
                assert row.reported_input is None and row.reported_output is None
    finally:
        worker.dispose()
        owner.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        locations = ",".join(
            f"{Path(frame.filename).name}:{frame.lineno}" for frame in relevant[-4:]
        )
        print(f"FAIL {type(error).__name__} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
