"""Verify clean migration and fail-closed application rollback on disposable PostgreSQL."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from packages.agent_research.discovery.schedule import DailySchedule, ScheduleDenied, ScheduleStore
from packages.agent_research.model_attempts import ModelAttemptStore
from packages.agent_research.provider_authority import ProviderPurpose
from packages.agent_research.runs import RunConflict, RunKind, RunState, RunStore
from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome
from services.llm_gateway.offline_gateway import ProviderGateway
from services.llm_gateway.probe import PROBE_PROJECTION
from services.llm_gateway.provider_installation import load_installation
from services.llm_gateway.provider_wire import Provider, ProviderResult, ResultState, Usage
from services.runtime.agent_research import ResearchSupervisor
from services.runtime.provider_credential_authority import (
    CredentialAuthorityClient,
    register_credential_authority,
)
from services.runtime.research_smoke import ProductionSmoke
from services.runtime.smoke_authority import SmokeAuthority
from services.setup.llm_credentials import CredentialDenied
from tests.integration import test_arp_provider_smoke as fixture
from tests.integration.test_agent_research_runtime import WORKER_ROLE
from tests.integration.test_companion_runtime import check_disabled_provider_installation
from tests.integration.test_readiness_acceptance import financial_fingerprints
from tests.unit.test_discovery_schedule import schedule_document

PROOF = "PASS release forward migration, signed revocation, rollback and local fallback"


def test_arp_release_rollback_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, WORKER_ROLE, "atl_agent_quant_admission")
        harness.TABLES = (
            *tables,
            *(
                f"agent_research.{name}"
                for name in (
                    "provider_bindings",
                    "provider_authority",
                    "provider_credential_authority",
                    "provider_smoke_grants",
                    "model_attempts",
                    "run_events",
                    "discovery_schedules",
                    "discovery_schedule_events",
                )
            ),
        )
        assert harness.host("tests.recovery.test_arp_release_rollback") == PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def persisted_evidence(owner):
    with owner.connect() as connection:
        return (
            financial_fingerprints(connection),
            tuple(
                connection.execute(
                    text("""
                SELECT attempt_id,status,cost_bound,held_cost,reported_input,reported_output
                FROM agent_research.model_attempts ORDER BY attempt_id
            """)
                )
            ),
            tuple(
                connection.execute(
                    text("""
                SELECT to_jsonb(e)::text FROM agent_research.run_events e ORDER BY to_jsonb(e)::text
            """)
                )
            ),
            connection.scalar(text("SELECT version_num FROM alembic_version")),
        )


def exercise(owner, worker, model, agent, store, binding, epoch, plan):
    with owner.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        )
        assert (
            connection.scalar(
                text("""
            SELECT blocked FROM agent_research.provider_credential_authority
            WHERE provider='ANTHROPIC'
        """)
            )
            is True
        )
    schedules = ScheduleStore(owner)
    document = schedule_document("UTC", "07:00", "DISABLED")
    schedule_id = document["scheduleId"]
    initial = DailySchedule.parse(document)
    schedules.create(initial)
    assert schedules.tick(schedule_id, enqueue=None) is None
    with (
        worker.begin() as scope,
        pytest.raises(
            RunConflict,
            match="PROVIDER_AUTHORITY_UNAVAILABLE",
        ),
    ):
        store.require(binding, epoch, ProviderPurpose.DIALOGUE, scope=scope)

    runs = RunStore(worker)
    actor = "local-operator"
    authority = SmokeAuthority(
        worker,
        store,
        binding=binding,
        epoch=epoch,
        plan=lambda _: plan,
        agent=agent,
    )
    grant = authority.preview(actor, uuid4())
    run_id = authority.approve(
        grant,
        actor,
        grant.commitment,
        key="release-rollback-smoke",
        trace_id="a" * 32,
        agent=agent,
    )
    capability = fixture.fixture_capability(model)
    accepted, calls = [], []
    blocked = False

    class FixtureCredential:
        def read_active(self, _binding):
            if blocked:
                raise CredentialDenied("DISPATCH_DENIED")
            return "synthetic-release-rollback"

    class SyntheticGateway(ProviderGateway):
        def _send(self, capability, projection, stopped, credential):
            assert projection == PROBE_PROJECTION and not stopped()
            calls.append(projection)
            return ProviderResult(ResultState.ACCEPTED, Usage(3, 2), {"ok": True})

    def authorize(request):
        accepted.append(request)
        return smoke.consume(request)

    with TemporaryDirectory() as directory:
        journal_path = Path(directory) / "dispatch.sqlite"
        gateway = SyntheticGateway(
            DispatchJournal.provision(journal_path),
            authorize,
            credentials=FixtureCredential(),
            capabilities=(capability,),
        )
        smoke = ProductionSmoke(authority, gateway, capability, model)
        supervisor = ResearchSupervisor(
            runs,
            {RunKind.PROVIDER_PROBE: smoke.step},
            attempts=ModelAttemptStore(worker),
        )
        assert supervisor.run_once(uuid4())
        assert runs.snapshot(run_id, actor=actor).state is RunState.COMPLETED
        assert len(accepted) == len(calls) == 1
        verified = authority.verify(authority.lookup(grant.grant_id))
        active = store.set_purpose(binding, verified.epoch, ProviderPurpose.DISCOVERY, True)
        schedules.replace(
            DailySchedule.parse(
                document
                | {
                    "version": 2,
                    "state": "ACTIVE",
                }
            ),
            expected_version=1,
        )
        with worker.begin() as scope:
            schedules.require_active(scope, schedule_id, 2)
            store.require(binding, active.epoch, ProviderPurpose.DISCOVERY, scope=scope)

        before = persisted_evidence(owner)
        schedules.replace(
            DailySchedule.parse(
                document
                | {
                    "version": 3,
                    "state": "PAUSED",
                }
            ),
            expected_version=2,
        )
        app = FastAPI()
        key = bytes(range(32))
        register_credential_authority(app, store, key)
        with TestClient(app) as http:
            CredentialAuthorityClient(http, key).block(Provider.OPENAI, binding.credential_version)
        blocked = True

        # Restart with no installed provider profile. Keep the journal and DB.
        assert load_installation(Path(directory) / "disabled-profile.json") is None
        restarted = SyntheticGateway(
            DispatchJournal(journal_path),
            authorize,
            credentials=FixtureCredential(),
            capabilities=(capability,),
        )
        (request,) = accepted
        assert restarted.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
        assert len(accepted) == len(calls) == 1
        assert (
            DispatchJournal(journal_path).status(
                request.attempt_id,
                request.request_hash,
            )
            is DispatchOutcome.COMPLETED
        )

        with worker.begin() as scope:
            with pytest.raises(ScheduleDenied, match="DISCOVERY_SCHEDULE_STALE"):
                schedules.require_active(scope, schedule_id, 2)
            with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
                store.load_active(
                    binding.provider, binding.profile_id, ProviderPurpose.DISCOVERY, scope=scope
                )
        with pytest.raises(RunConflict, match="PROVIDER_AUTHORITY_UNAVAILABLE"):
            store.set_purpose(binding, active.epoch, ProviderPurpose.DISCOVERY, True)
        with pytest.raises(RunConflict):
            authority.verify(authority.lookup(grant.grant_id))
        fresh = replace(request, attempt_id=uuid4())
        assert restarted.dispatch(fresh).state is ResultState.DISPATCH_DENIED
        assert len(calls) == 1

    assert schedules.read(schedule_id).state == "PAUSED"
    assert schedules.tick(schedule_id, enqueue=None) is None
    assert runs.claim(uuid4()) is None
    assert persisted_evidence(owner) == before
    with owner.connect() as connection:
        assert connection.execute(
            text("""
            SELECT action FROM agent_research.discovery_schedule_events ORDER BY version
        """)
        ).scalars().all() == ["CREATE", "RESUME", "PAUSE"]
        assert connection.execute(
            text("""
            SELECT state,dialogue,discovery FROM agent_research.provider_authority
        """)
        ).one() == ("REVOKED", False, False)
    check_disabled_provider_installation(worker)


if __name__ == "__main__":
    try:
        fixture.guest(exercise)
    except Exception as error:
        from traceback import extract_tb

        frames = [
            frame
            for frame in extract_tb(error.__traceback__)
            if frame.filename.startswith("/opt/lab/") and "/site-packages/" not in frame.filename
        ]
        locations = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-5:])
        code = getattr(error, "code", None) or getattr(
            getattr(error, "orig", None), "sqlstate", "-"
        )
        print(f"FAIL {type(error).__name__} {code} {locations}")
        raise SystemExit(1) from None
    print(PROOF)
