"""PostgreSQL verification of versioned model quotas and send-period fencing."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event, text

from packages.agent_research import registry
from packages.agent_research.budget import BudgetDenied, ModelOutcome, TokenPrices, Tokens
from packages.agent_research.model_attempts import ModelAttemptStore, production_model_budget_scopes
from packages.agent_research.provider_authority import (
    ProviderAuthorityStore,
    ProviderBinding,
    ProviderPurpose,
)
from packages.agent_research.runs import Claim, RunConflict, RunKind, RunStore
from tests.integration.test_agent_research_runtime import WORKER_ROLE

PROOF = "PASS ARP model quota contention, UTC rollover, unknown holds and overrun"


def test_arp_model_attempts_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE)
        harness.TABLES = (*tables, "agent_research.model_attempts")
        assert harness.host("tests.integration.test_arp_model_attempts") == PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def guest():
    fixture = json.load(sys.stdin)
    url = URL.create(
        "postgresql+psycopg", username=fixture["role"], password=fixture["password"],
        host="postgres", port=5432, database=fixture["database"],
        query={"connect_timeout": "5"},
    )
    owner, worker = (create_engine(url, hide_parameters=True) for _ in range(2))

    @event.listens_for(worker, "begin")
    def set_role(connection):
        connection.exec_driver_sql(f"SET LOCAL ROLE {WORKER_ROLE}")

    model = registry.ModelBinding("OPENAI", "arp-test-profile", 1, "arp-fixture", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    prices = TokenPrices(3, "USD", Decimal(1), Decimal(2))
    kind = RunKind.PROVIDER_PROBE
    scopes = production_model_budget_scopes(agent.agent_id, agent.version, kind, model, "fixture")
    assert {scope.partition(":")[0] for scope in scopes} >= {
        "probe", "profile", "provider", "utc_day", "global",
    }
    try:
        with owner.begin() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "0057_model_budget_periods")
            connection.execute(text("""
                INSERT INTO agent_research.model_budget_policies
                    (scope,version,currency,max_cost,max_input_tokens,max_output_tokens,max_calls,
                     enabled,max_parallelism)
                VALUES (:scope,1,'USD',0.0003,4096,1024,20,true,1)
            """), [{"scope": scope} for scope in scopes])
            connection.execute(text("""
                INSERT INTO agent_research.model_prices
                    (model_binding,version,currency,input_price,output_price,bounds_unconfirmed_cost)
                VALUES (CAST(:binding AS jsonb),3,'USD',1,2,false)
            """), {"binding": json.dumps(asdict(model))})

        with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}):
            verify_attempts(owner, worker, model, agent, kind, prices)
        verify_credential_authority(owner, worker)
    finally:
        worker.dispose()
        owner.dispose()


def verify_credential_authority(owner, engine):
    import os
    from tempfile import TemporaryDirectory

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.control_api.auth import LocalAuth
    from services.llm_gateway.provider_wire import Provider
    from services.runtime.provider_credential_authority import (
        CredentialAuthorityClient,
        register_credential_authority,
    )
    from services.runtime.provider_profiles import check_persisted_evidence
    from services.setup.research_app import GRANT_HEADER, create_app
    from services.setup.research_credentials import ResearchCredentialStore

    store = ProviderAuthorityStore(engine, check_evidence=check_persisted_evidence)
    key = bytes(range(32))
    app = FastAPI()
    register_credential_authority(app, store, key)
    with TemporaryDirectory() as temporary, TestClient(app) as supervisor:
        root = Path(temporary)
        root.chmod(0o2750)
        storage = ResearchCredentialStore(root, owner_uid=os.getuid(), reader_gid=os.getgid())
        client = CredentialAuthorityClient(supervisor, key)
        origin = "http://127.0.0.1:8000"
        setup = create_app(storage, LocalAuth(origin), credential_authority=client)
        url = "/api/v1/integrations/research-setup/providers/OPENAI"
        with TestClient(setup, base_url=origin) as browser:
            save_grant = setup.state.issue_grant("OPENAI", "save")
            saved = browser.put(
                url, headers={"Origin": origin, GRANT_HEADER: save_grant},
                json={"expectedCredentialVersion": 1, "credential": "synthetic-arp04-fixture"},
            )
            assert saved.status_code == 200
            _, digest = storage.authority_metadata(Provider.OPENAI)
            verify_profile_evidence(owner, engine, store, digest)
            revoke_grant = setup.state.issue_grant("OPENAI", "revoke")
            response = browser.post(
                url + "/revoke",
                headers={"Origin": origin, GRANT_HEADER: revoke_grant},
                json={"expectedCredentialVersion": 2, "credentialGeneration": 1},
            )
            assert response.status_code == 200
    with owner.connect() as connection:
        row = connection.execute(text("""
            SELECT version,generation,digest,blocked
            FROM agent_research.provider_credential_authority WHERE provider='OPENAI'
        """)).one()
        assert tuple(row) == (3, 1, None, True)


def verify_profile_evidence(owner, engine, store, digest):
    binding = ProviderBinding(
        profile_id="arp-test-profile", profile_generation=1, provider="OPENAI",
        model_id="arp-fixture", response_model_id="arp-fixture", model_generation=1,
        capability_generation=1, credential_generation=1, credential_version=2,
        credential_digest=digest, api_version="v1", rights_generation=1,
        policy_generation=1, prompt_sha256="a" * 64, schema_sha256="b" * 64,
        evidence_revision=4, tariff_revision=3,
        evidence_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        data_classes=("OPERATOR_RESEARCH_EXCERPT",), envelope_version="v1",
        max_request_bytes=65536, max_response_bytes=262144, timeout_seconds=15,
        max_input_tokens=100, max_output_tokens=50, max_cost=Decimal("0.01"),
    )
    with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
        store.stage(binding, 0)
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.model_prices SET bounds_unconfirmed_cost=true
            WHERE model_binding->>'profile_id'=:profile
        """), {"profile": binding.profile_id})
        connection.execute(text("""
            INSERT INTO agent_research.provider_cost_evidence
                (provider,profile_id,revision,binding_digest,valid_until)
            VALUES ('OPENAI',:profile,:revision,:digest,:expires)
        """), {
            "profile": binding.profile_id, "revision": binding.evidence_revision,
            "digest": binding.digest, "expires": binding.evidence_expires_at,
        })
    profile = store.stage(binding, 0)
    with engine.begin() as connection:
        store.require(binding, profile.epoch, ProviderPurpose.PROBE, scope=connection)
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            store.load_active(
                binding.provider, binding.profile_id, ProviderPurpose.DIALOGUE, scope=connection,
            )
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            store.require(binding, profile.epoch, ProviderPurpose.DISCOVERY, scope=connection)
    started = Event()
    finished = Event()

    def revoke_evidence():
        with owner.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            started.set()
            connection.execute(text("""
                DELETE FROM agent_research.provider_cost_evidence WHERE profile_id=:profile
            """), {"profile": binding.profile_id})
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with engine.begin() as connection:
            store.require(binding, profile.epoch, ProviderPurpose.PROBE, scope=connection)
            revoke_result = executor.submit(revoke_evidence)
            assert started.wait(5)
            assert not finished.wait(0.2)
        revoke_result.result(timeout=5)
    with engine.begin() as connection:
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            store.require(binding, profile.epoch, ProviderPurpose.PROBE, scope=connection)
    store.block_credential("OPENAI", 2)
    with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
        store.attest_credential("OPENAI", 1, 2, digest, expected_version=1)
    with engine.begin() as connection:
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            store.require(binding, profile.epoch, ProviderPurpose.PROBE, scope=connection)


def verify_attempts(owner, worker, model, agent, kind, prices):
    runs, attempts = RunStore(worker), ModelAttemptStore(worker)
    authority = {"evidence_revision": 4, "authority_hash": "a" * 64}
    reservation = {
        "step_id": "fixture-smoke", "request_hash": "b" * 64, "model": model, "prices": prices,
        "tokens": Tokens(100, 50), "context_window": 1000, "timeout_seconds": 120, **authority,
    }
    claims = []
    for index in range(2):
        run = runs.enqueue(
            actor="local-operator", key=f"arp-fixture-probe-{index}", kind=kind,
            input_ref="fixture", trace_id="a" * 32, agent=agent,
        )
        # Fixture two simultaneous leases to exercise the ledger independently
        # of the single-run scheduler admission limit.
        owner_id = uuid4()
        with owner.begin() as connection:
            row = connection.execute(text("""
                UPDATE agent_research.runs
                SET state='RUNNING', owner=:owner, fence=fence+1, version=version+1,
                    claimed_at=clock_timestamp(), lease_until=clock_timestamp()+interval '2 minutes'
                WHERE run_id=:run RETURNING fence,version
            """), {"owner": owner_id, "run": run}).one()
        claim = Claim(run, owner_id, row.fence, row.version)
        claims.append(claim)
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        attempts.reserve(
            claims[0], attempt_id=uuid4(), budget_binding_sha256="0" * 64, **reservation,
        )
    barrier = Barrier(2, timeout=5)

    def race(claim):
        barrier.wait()
        try:
            return attempts.reserve(claim, attempt_id=uuid4(), **reservation)
        except BudgetDenied as error:
            assert error.code == "BUDGET_EXHAUSTED"
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(race, claims))
    assert outcomes.count(None) == 1
    winner = 1 - outcomes.index(None)
    initial, claim, other = outcomes[winner], claims[winner], claims[1 - winner]
    assert initial is not None

    def authorize(attempt, *, binding=authority):
        with worker.begin() as connection:
            return attempts.authorize_in_transaction(
                connection, claim, attempt.attempt_id, reservation["request_hash"], **binding,
            )

    with pytest.raises(RunConflict, match="AGENT_POLICY_DENIED"):
        authorize(initial, binding=authority | {"evidence_revision": 5})
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.model_attempts SET period=period-1 WHERE attempt_id=:id
        """), {"id": initial.attempt_id})
    with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
        authorize(initial)
    attempts.settle(claim, initial.attempt_id, ModelOutcome.UNSENT)
    current = attempts.reserve(claim, attempt_id=uuid4(), **reservation)
    assert authorize(current).status == "DISPATCH_AUTHORIZED"
    unknown = attempts.settle(
        claim, current.attempt_id, ModelOutcome.COMPLETED, result_ref="unusable", **authority,
    )
    assert unknown.status == "OUTCOME_UNKNOWN" and unknown.result_ref is None
    with owner.begin() as connection:
        row = connection.execute(text("""
            SELECT send_period=period AS pinned, held_cost=cost_bound AS carried
            FROM agent_research.model_attempts WHERE attempt_id=:id
        """), {"id": current.attempt_id}).one()
        assert row.pinned and row.carried
    with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
        attempts.reserve(other, attempt_id=uuid4(), **reservation)
    attempts.reconcile_confirmed_cost(current.attempt_id, "fixture-invoice", Decimal("0.0004"))
    with owner.connect() as connection:
        row = connection.execute(text("""
            SELECT exceeded_bound, confirmed_cost, held_cost, send_period=period AS pinned
            FROM agent_research.model_attempts WHERE attempt_id=:id
        """), {"id": current.attempt_id}).one()
        assert row.exceeded_bound and row.pinned and row.held_cost == 0
        assert row.confirmed_cost == Decimal("0.0004")
    with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
        attempts.reserve(other, attempt_id=uuid4(), **reservation)


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own[-4:])
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        print(f"FAIL {type(error).__name__} {state or '-'} {where}")
        raise SystemExit(1) from None
    print(PROOF)
