"""Crash and revocation recovery across the durable dispatch boundary."""

import json
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.agent_research import registry
from packages.agent_research.budget import ModelOutcome, Tokens
from packages.agent_research.model_attempts import ModelAttemptStore
from packages.agent_research.runs import RunConflict, RunKind, RunState, RunStore
from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome
from services.llm_gateway.offline_gateway import ProviderGateway
from services.llm_gateway.probe import PROBE_PROJECTION
from services.llm_gateway.provider_http import provider_request_bytes
from services.llm_gateway.provider_wire import ResultState
from services.runtime.agent_research import ResearchSupervisor
from services.runtime.research_smoke import ProductionSmoke
from services.runtime.smoke_authority import SmokeAuthority
from tests.integration import test_arp_provider_smoke as fixture
from tests.integration.test_agent_research_runtime import WORKER_ROLE

PROOF = "PASS pre-send revoke and post-commit crash, fenced recovery, replay and late accounting"


def test_arp_dispatch_recovery_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (
            *tables,
            "agent_research.runs",
            "agent_research.provider_smoke_grants",
            "agent_research.model_attempts",
            "agent_research.model_accounting_receipts",
        )
        assert harness.host("tests.recovery.test_arp_dispatch_recovery") == PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


class WorkerCrashed(BaseException):
    """Leave the database and journal at the chosen crash point."""


class FixtureCredential:
    def read_active(self, binding):
        return "synthetic-recovery-credential"


def exercise(owner, worker, model, agent, store, binding, epoch, plan, *, committed):
    capability = fixture.fixture_capability(model)
    authority = SmokeAuthority(
        worker,
        store,
        binding=binding,
        epoch=epoch,
        plan=lambda _: plan,
        agent=agent,
    )
    runs, attempts = RunStore(worker), ModelAttemptStore(worker)
    actor = "local-operator"
    grant = authority.preview(actor, uuid4())
    run_id = authority.approve(
        grant,
        actor,
        grant.commitment,
        key=f"arp-crash-{grant.grant_id.hex}",
        trace_id=uuid4().hex,
        agent=agent,
    )
    grant = authority.lookup(grant.grant_id)
    old_claim = runs.claim(uuid4())
    assert old_claim.run_id == run_id
    requests, writes, replacement_steps = [], [], []
    revoked = None

    def authorize(request):
        nonlocal revoked
        requests.append(request)
        if committed:
            return smoke.consume(request)
        revoked = store.revoke(binding, epoch)
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            smoke.consume(request)
        raise WorkerCrashed

    class CrashGateway(ProviderGateway):
        def _send(self, capability, projection, stopped, credential):
            assert committed and projection == PROBE_PROJECTION and not stopped()
            writes.append(projection)
            raise WorkerCrashed

    def resumed(claim, work, recovered):
        replacement_steps.append(claim)
        return smoke.step(claim, work, recovered)

    with TemporaryDirectory() as directory:
        journal_path = Path(directory) / "dispatch.sqlite"
        gateway = CrashGateway(
            DispatchJournal.provision(journal_path),
            authorize,
            credentials=FixtureCredential(),
            capabilities=(capability,),
        )
        smoke = ProductionSmoke(authority, gateway, capability, model)
        with pytest.raises(WorkerCrashed):
            smoke.step(old_claim, runs.load_input(old_claim), ())
        assert len(requests) == 1 and requests[0].attempt_id == grant.grant_id
        assert len(writes) == int(committed)
        if committed:
            revoked = store.revoke(binding, epoch)
        assert revoked.state == "REVOKED"
        # Reopen the durable gateway journal after the simulated process loss.
        reopened = DispatchJournal(journal_path)
        request = requests[0]
        assert (
            reopened.status(request.attempt_id, request.request_hash)
            is DispatchOutcome.OUTCOME_UNKNOWN
        )
        restarted = CrashGateway(
            reopened,
            authorize,
            credentials=FixtureCredential(),
            capabilities=(capability,),
        )
        assert restarted.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
        assert len(requests) == 1 and len(writes) == int(committed)

    # Advance only the expired lease, then reclaim through the ordinary supervisor.
    with owner.begin() as connection:
        connection.execute(
            text("""
            UPDATE agent_research.runs
            SET claimed_at=clock_timestamp()-interval '2 seconds',
                lease_until=clock_timestamp()-interval '1 second'
            WHERE run_id=:run
        """),
            {"run": run_id},
        )
    supervisor = ResearchSupervisor(runs, {RunKind.PROVIDER_PROBE: resumed}, attempts=attempts)
    assert supervisor.run_once(uuid4())
    assert not supervisor.run_once(uuid4())
    assert len(replacement_steps) == (0 if committed else 1)
    assert len(requests) == 1 and len(writes) == int(committed)
    with pytest.raises(RunConflict, match="LEASE_LOST"):
        runs.transition(old_claim, RunState.COMPLETED)
    with pytest.raises(RunConflict, match="LEASE_LOST"):
        authority.settle(grant, old_claim, ModelOutcome.COMPLETED, reported_tokens=Tokens(3, 2))

    def persisted():
        with owner.connect() as connection:
            return connection.execute(
                text("""
                SELECT a.attempt_id,a.status,a.result_ref,a.held_cost,a.cost_bound,
                       a.confirmed_cost,a.exceeded_bound,a.period,a.send_period,
                       a.pricing_version,a.input_price,a.output_price,a.fence,a.request_hash,
                       r.fence AS run_fence,r.state
                FROM agent_research.model_attempts a JOIN agent_research.runs r USING(run_id)
                WHERE a.run_id=:run
            """),
                {"run": run_id},
            ).one()

    recovered = persisted()
    assert (recovered.attempt_id, recovered.request_hash) == (grant.grant_id, grant.commitment)
    assert recovered.fence == old_claim.fence and recovered.run_fence > old_claim.fence
    assert recovered.state == "BLOCKED" and recovered.result_ref is None
    if committed:
        assert recovered.status == "OUTCOME_UNKNOWN" and recovered.held_cost == recovered.cost_bound
        assert recovered.send_period == recovered.period
        captured_tariff = (recovered.pricing_version, recovered.input_price, recovered.output_price)
        assert captured_tariff == (
            plan.prices.version,
            plan.prices.input_per_million,
            plan.prices.output_per_million,
        )
        # Change the active price row while retaining the original attempt's quote.
        with owner.begin() as connection:
            current_tariff = connection.execute(
                text("""
                UPDATE agent_research.model_prices
                SET version=version+1,input_price=input_price*10,output_price=output_price*10
                WHERE model_binding=CAST(:binding AS jsonb)
                RETURNING version,input_price,output_price
                """),
                {"binding": json.dumps(asdict(model))},
            ).one()
        assert current_tariff.version == recovered.pricing_version + 1
        invoice_cost = recovered.cost_bound + Decimal("0.0001")
        for _ in range(2):
            attempts.reconcile_confirmed_cost(grant.grant_id, "recovery-invoice", invoice_cost)
        with pytest.raises(RunConflict, match="IDEMPOTENCY_CONFLICT"):
            attempts.reconcile_confirmed_cost(grant.grant_id, "other-invoice", invoice_cost)
        settled = persisted()
        assert settled.status == "OUTCOME_UNKNOWN" and settled.result_ref is None
        assert settled.state == "BLOCKED" and settled.send_period == recovered.send_period
        assert (
            settled.pricing_version,
            settled.input_price,
            settled.output_price,
        ) == captured_tariff
        assert settled.held_cost == 0 and settled.confirmed_cost == invoice_cost
        assert settled.exceeded_bound
    else:
        assert recovered.status == "UNSENT" and recovered.held_cost == 0
        assert recovered.send_period is None
        with pytest.raises(RunConflict, match="AGENT_POLICY_DENIED"):
            attempts.reconcile_confirmed_cost(grant.grant_id, "unsent-invoice", Decimal("0.0001"))
    return revoked


def check_scenarios(owner, worker, model, agent, store, binding, epoch, plan):
    revoked = exercise(owner, worker, model, agent, store, binding, epoch, plan, committed=False)
    # Re-stage a new generation through the normal authority path. The cost
    # policies retain the same model and profile scopes across generations.
    model = replace(model, profile_generation=model.profile_generation + 1)
    agent = replace(agent, model_binding=model)
    binding = replace(
        binding,
        profile_generation=model.profile_generation,
        evidence_revision=binding.evidence_revision + 1,
    )
    with owner.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO agent_research.model_prices
                (model_binding,version,currency,input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:binding AS jsonb),:revision,'USD',1,2,true)
        """),
            {"binding": json.dumps(asdict(model)), "revision": plan.prices.version},
        )
        connection.execute(
            text("""
            UPDATE agent_research.provider_cost_evidence
            SET revision=:revision,binding_digest=:digest,valid_until=:expires
            WHERE provider=:provider AND profile_id=:profile
        """),
            {
                "provider": binding.provider,
                "profile": binding.profile_id,
                "revision": binding.evidence_revision,
                "digest": binding.digest,
                "expires": binding.evidence_expires_at,
            },
        )
    staged = store.stage(binding, revoked.epoch)
    current_plan = replace(
        plan,
        wire_sha256=sha256(
            provider_request_bytes(fixture.fixture_capability(model), PROBE_PROJECTION)
        ).hexdigest(),
    )
    with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}):
        exercise(
            owner, worker, model, agent, store, binding, staged.epoch, current_plan, committed=True
        )


if __name__ == "__main__":
    try:
        fixture.guest(check_scenarios)
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own = [
            frame
            for frame in frames
            if frame.filename.startswith("/opt/lab/") and "/site-packages/" not in frame.filename
        ]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own[-5:])
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', state) or '-'} {where}")
        raise SystemExit(1) from None
    print(PROOF)
