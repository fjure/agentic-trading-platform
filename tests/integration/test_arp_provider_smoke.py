"""Check durable smoke approvals and usage settlement on a disposable PostgreSQL database."""

import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event, text

from packages.agent_research import registry
from packages.agent_research.budget import BudgetDenied, TokenPrices, Tokens
from packages.agent_research.model_attempts import production_model_budget_scopes
from packages.agent_research.provider_authority import ProviderAuthorityStore, ProviderBinding
from packages.agent_research.runs import RunConflict, RunKind, RunState, RunStore
from services.llm_gateway.probe import PROBE_PROJECTION, production_probe_capability
from services.llm_gateway.provider_http import provider_request_bytes
from services.llm_gateway.provider_wire import Provider, ProviderResult, ResultState, Usage
from services.runtime.provider_profiles import check_persisted_evidence
from services.runtime.research_smoke import ProductionSmoke
from services.runtime.smoke_authority import SmokeAuthority, SmokePlan
from tests.integration.test_agent_research_runtime import WORKER_ROLE
from tests.unit.test_llm_provider_http import PROFILE

PROOF = "PASS durable production smoke consent, replay, and usage settlement"


def test_arp_provider_smoke_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (*tables, "agent_research.provider_smoke_grants")
        assert harness.host("tests.integration.test_arp_provider_smoke") == PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def guest():
    fixture = json.load(sys.stdin)
    url = URL.create(
        "postgresql+psycopg",
        username=fixture["role"],
        password=fixture["password"],
        host="postgres",
        port=5432,
        database=fixture["database"],
        query={"connect_timeout": "5"},
    )
    owner, worker = (create_engine(url, hide_parameters=True) for _ in range(2))

    @event.listens_for(worker, "begin")
    def set_role(connection):
        connection.exec_driver_sql(f"SET LOCAL ROLE {WORKER_ROLE}")

    model = registry.ModelBinding("OPENAI", "arp-smoke-fixture", 1, "arp-fixture", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    prices = TokenPrices(3, "USD", Decimal(1), Decimal(2), True)
    binding = ProviderBinding(
        profile_id=model.profile_id,
        profile_generation=model.profile_generation,
        provider=model.provider,
        model_id=model.model_id,
        response_model_id=model.model_id,
        model_generation=model.model_generation,
        capability_generation=1,
        credential_generation=1,
        credential_version=2,
        credential_digest="a" * 64,
        api_version="v1",
        rights_generation=1,
        policy_generation=1,
        prompt_sha256="b" * 64,
        schema_sha256="c" * 64,
        evidence_revision=4,
        tariff_revision=prices.version,
        evidence_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        data_classes=("OPERATOR_RESEARCH_EXCERPT",),
        envelope_version="v1",
        max_request_bytes=65536,
        max_response_bytes=262144,
        timeout_seconds=15,
        max_input_tokens=100,
        max_output_tokens=50,
        max_cost=Decimal("0.01"),
    )
    try:
        configure(owner, model, agent, binding)
        store = ProviderAuthorityStore(worker, check_evidence=check_persisted_evidence)
        store.attest_credential("OPENAI", 1, 2, binding.credential_digest, expected_version=1)
        staged = store.stage(binding, 0)
        capability = fixture_capability(model)
        plan = SmokePlan(
            "d" * 64,
            sha256(provider_request_bytes(capability, PROBE_PROJECTION)).hexdigest(),
            prices,
            Tokens(100, 50),
            Decimal("0.0002"),
            Decimal("0.01"),
            15,
            input_bound_kind="REQUEST",
        )
        with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}):
            assert_scenarios(owner, worker, model, agent, store, binding, staged.epoch, plan)
    finally:
        worker.dispose()
        owner.dispose()


def fixture_capability(model):
    return production_probe_capability(
        replace(
            PROFILE,
            provider=Provider(model.provider),
            profile_id=model.profile_id,
            model=model.model_id,
            timeout_seconds=15,
        ),
        50,
    )


def configure(owner, model, agent, binding):
    with owner.begin() as connection:
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        command.upgrade(config, "0058_provider_smoke")
        scopes = production_model_budget_scopes(
            agent.agent_id,
            agent.version,
            RunKind.PROVIDER_PROBE,
            model,
            "fixture",
        )
        connection.execute(
            text("""
            INSERT INTO agent_research.model_budget_policies
                (scope,version,currency,max_cost,max_input_tokens,max_output_tokens,max_calls,
                 enabled,max_parallelism)
            VALUES (:scope,1,'USD',0.001,4096,1024,20,true,1)
        """),
            [{"scope": scope} for scope in scopes],
        )
        connection.execute(
            text("""
            INSERT INTO agent_research.model_prices
                (model_binding,version,currency,input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:binding AS jsonb),3,'USD',1,2,true)
        """),
            {"binding": json.dumps(asdict(model))},
        )
        connection.execute(
            text("""
            INSERT INTO agent_research.provider_cost_evidence
                (provider,profile_id,revision,binding_digest,valid_until)
            VALUES ('OPENAI',:profile,:revision,:digest,:expires)
        """),
            {
                "profile": binding.profile_id,
                "revision": binding.evidence_revision,
                "digest": binding.digest,
                "expires": binding.evidence_expires_at,
            },
        )


def assert_scenarios(owner, worker, model, agent, store, binding, epoch, plan):
    current = replace(plan, operator_limit=Decimal("0.0001"))
    authority = SmokeAuthority(
        worker, store, binding=binding, epoch=epoch, plan=lambda _: current, agent=agent
    )
    runs = RunStore(worker)
    actor = "local-operator"
    grant = authority.preview(actor, uuid4())
    assert grant.public()["blockedReason"] == "BUDGET_EXHAUSTED"
    kwargs = dict(key="synthetic-smoke-key", trace_id=uuid4().hex, agent=agent)
    with pytest.raises(BudgetDenied):
        authority.approve(grant, actor, grant.commitment, **kwargs)
    assert runs.claim(uuid4()) is None

    current = plan
    stale = authority.preview(actor, uuid4())
    with owner.begin() as connection:
        connection.execute(
            text("""
            UPDATE agent_research.model_budget_policies SET version=2 WHERE scope LIKE 'global:%'
        """)
        )
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        authority.approve(stale, actor, stale.commitment, **kwargs)
    grant = authority.preview(actor, uuid4())
    with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
        authority.approve(grant, actor, "e" * 64, **kwargs)
    assert runs.claim(uuid4()) is None
    run_id = authority.approve(grant, actor, grant.commitment, **kwargs)
    assert authority.approve(grant, actor, grant.commitment, **kwargs) == run_id
    claim = runs.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    approved = authority.lookup(grant.grant_id)
    assert approved is not None

    class Boundary:
        def dispatch(self, request):
            nonlocal current
            current = replace(plan, policy_sha256="f" * 64)
            with pytest.raises(RunConflict, match="PROCESSING_GRANT_INVALID"):
                smoke.consume(request)
            current = plan
            assert smoke.consume(request).projection == PROBE_PROJECTION
            with pytest.raises(RunConflict):
                smoke.consume(request)
            with pytest.raises(RunConflict, match="PROVIDER_OUTCOME_UNKNOWN"):
                authority.preview(actor, uuid4())
            return ProviderResult(ResultState.ACCEPTED, Usage(3, 2), {"ok": True})

    smoke = ProductionSmoke(authority, Boundary(), fixture_capability(model), model)
    result = smoke.step(claim, runs.load_input(claim), ())
    assert result.target == RunState.COMPLETED
    runs.transition(claim, result.target)
    with worker.connect() as connection:
        usage = connection.execute(
            text("""
            SELECT reported_input,reported_output,confirmed_cost,held_cost
            FROM agent_research.model_attempts WHERE attempt_id=:id
        """),
            {"id": grant.grant_id},
        ).one()
        assert tuple(usage) == (3, 2, None, Decimal("0.0002"))
    assert authority.verify(approved).state == "VERIFIED"


if __name__ == "__main__":
    try:
        guest()
    except (Exception, pytest.fail.Exception) as error:
        from pathlib import Path
        from traceback import extract_tb

        locations = ",".join(
            f"{Path(frame.filename).name}:{frame.lineno}"
            for frame in extract_tb(error.__traceback__)
            if "/site-packages/" not in frame.filename
        )
        print(f"FAIL {type(error).__name__} {locations}")
        raise SystemExit(1) from None
    print(PROOF)
