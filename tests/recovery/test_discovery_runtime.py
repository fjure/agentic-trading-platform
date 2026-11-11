"""Actual PostgreSQL discovery outbox, offline dispatch and restart publication."""

import json
import sys
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from packages.agent_research import registry
from packages.agent_research.budget import TokenPrices, Tokens
from packages.agent_research.companion.ideas import IdeaLifecycle
from packages.agent_research.companion.store import CompanionStore
from packages.agent_research.discovery.evidence import (
    EvaluationBinding,
    EvidenceAccess,
    Readiness,
    Rights,
    TriggerPolicy,
)
from packages.agent_research.discovery.publication import (
    PUBLICATION_SCHEMA,
    EvidenceClaim,
    EvidenceKind,
)
from packages.agent_research.discovery.schedule import DailySchedule, ScheduleStore
from packages.agent_research.discovery.store import DiscoveryStore
from packages.agent_research.model_attempts import ModelAttemptStore, model_budget_scopes
from packages.agent_research.runs import RunKind, RunStore
from packages.research.registry import definitions, register
from services.llm_gateway.content_policy import safe_result_content
from services.llm_gateway.discovery import OfflineDiscoveryDispatch
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
from services.llm_gateway.provider_wire import Provider
from services.runtime.agent_research import ResearchSupervisor
from services.runtime.research_discovery import DiscoveryConfiguration, ResearchDiscovery
from tests.integration.test_backtest_jobs import role_engine
from tests.unit.test_companion_interpretation import CONTRACT
from tests.unit.test_discovery_schedule import schedule_document

_ROLE = "atl_agent_research"
_PROOF = "PASS discovery outbox, restart, dedup, revocation, backpressure and unknown outcome"
_QUOTE = "Synthetic market observation recorded a lower close."
_SCHEDULES = (
    "first-discovery", "repeat-discovery", "revoked-discovery", "bounded-discovery",
    "recovered-discovery",
)
_TABLES = tuple(f"agent_research.{name}" for name in (
    "runs", "run_events", "discovery_schedules", "discovery_occurrences", "discovery_source_clock",
    "discovery_sources", "discovery_cursors", "discovery_work", "discovery_events",
    "discovery_publications", "model_attempts", "companion_ideas", "companion_idea_versions",
))


def test_discovery_runtime_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    groups, tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*groups, _ROLE, "atl_agent_quant_admission")
        harness.TABLES = (*tables, *_TABLES)
        assert harness.host("tests.recovery.test_discovery_runtime") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = groups, tables


def _seed_source(owner, instrument, now, number):
    start = now - timedelta(minutes=120 - 20 * number)
    end = start + timedelta(minutes=15)
    revision = UUID(f"abcdefab-cdef-4abc-abcd-abcdefabcde{number}")
    params = {"revision": revision, "evidence": uuid4(), "instrument": instrument.instrument_id,
              "start": start, "end": end, "hash": "abcdef"[number - 1] * 64}
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO research.bar_revisions(revision_id,instrument_id,provider,provider_symbol,
              feed,interval,bar_start,bar_end,adjustment,open,high,low,close,volume,fingerprint,
              local_revision,quality)
            VALUES (:revision,:instrument,'alpaca','SPY','iex','15Min',:start,:end,'raw',
              100,101,98,99,1000,:hash,1,'VALID')
        """), params)
        connection.execute(text("""
            INSERT INTO research.evidence(evidence_id,revision_id,fingerprint,source_reference)
            VALUES (:evidence,:revision,:hash,'synthetic:discovery')
        """), params)
    return revision


def _configure(owner, instrument_id, agent, model, now):
    schedules = ScheduleStore(owner)
    scope_keys = set()
    for identifier in _SCHEDULES:
        document = schedule_document("UTC", (now - timedelta(hours=1)).strftime("%H:%M"),
                                     "DISABLED")
        document["scheduleId"] = identifier
        document["scope"]["instrumentIds"] = [instrument_id]
        document["limits"]["maxInputTokens"] = 4096
        document["limits"]["maxPendingItems"] = 1
        schedules.create(DailySchedule.parse(document))
        schedules.replace(DailySchedule.parse(document | {"state": "ACTIVE", "version": 2}),
                          expected_version=1)
        scope_keys.update(model_budget_scopes(
            agent.agent_id, agent.version, RunKind.SCHEDULED, model, identifier,
        ))
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.discovery_schedules SET active_since=clock_timestamp()
              - interval '2 days'
        """))
        connection.execute(text("""
            UPDATE research.source_status SET license_status='CONFIRMED' WHERE singleton
        """))
        connection.execute(text("""
            INSERT INTO agent_research.work_budget_policies(scope,version,max_steps,max_tool_calls,
              enabled) VALUES (:scope,1,24,16,true)
        """), [{"scope": f"schedule:{identifier}"} for identifier in _SCHEDULES])
        connection.execute(text("""
            INSERT INTO agent_research.model_budget_policies (scope,version,currency,max_cost,
                max_input_tokens,max_output_tokens,max_calls,enabled)
            VALUES (:scope,1,'USD',0.10,32768,4096,8,true)
        """), [{"scope": key} for key in sorted(scope_keys)])
        connection.execute(text("""
            INSERT INTO agent_research.model_prices (model_binding,version,currency,
                input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:model AS jsonb),1,'USD',1,2,false)
        """), {"model": json.dumps(asdict(model))})


def _provider(calls, *, missing_usage):
    def send(request):
        projection = json.loads(json.loads(request.content)["input"][0]["content"])
        assert projection["purpose"] == "PROACTIVE_RESEARCH_DISCOVERY_V1"
        evidence = projection["evidence"]
        assert projection["quotes"] == [_QUOTE]
        calls.append(evidence["sourceId"])
        proposal = {
            "title": "Does the market recover after a decline?",
            "hypothesis": {
                "statement": "Does a rebound follow the observed decline?", "market": "SPY",
                "trigger": "Price decline", "signal": "Negative close", "target": "Recovery",
                "observationWindow": None, "measurementHorizon": None,
                "benchmark": None, "evaluationCriterion": None,
            },
            "direction": "INCREASE", "instrumentId": evidence["instrumentId"],
            "observationStart": evidence["windowStart"], "observationEnd": evidence["windowEnd"],
            "proof": [{"kind": evidence["kind"], "evidenceId": evidence["sourceId"],
                       "revision": evidence["revision"], "quote": _QUOTE}],
        }
        response = {
            "model": "offline-model", "status": "completed",
            "usage": {"input_tokens": 120, "output_tokens": 80}, "output": [{
                "type": "message", "role": "assistant", "status": "completed", "content": [{
                    "type": "output_text", "text": json.dumps({"ideas": [proposal]}),
                }],
            }],
        }
        if evidence["sourceId"] in missing_usage:
            response.pop("usage")
        return httpx.Response(200, json=response)

    return send


def _new_runtime(worker, instrument_id, agent, calls, journal_path, *, missing_usage=()):
    schedule_store, attempts = ScheduleStore(worker), ModelAttemptStore(worker)
    store = DiscoveryStore(worker, require_active=ScheduleStore.require_active)
    model = agent.model_binding
    config = DiscoveryConfiguration(
        agent, TriggerPolicy("rule-v1", "scope-v1", frozenset({instrument_id}), timedelta(days=1)),
        EvaluationBinding(
            "rule-v1", "scope-v1", agent.agent_id, agent.version, "prompt-v1", model.provider,
            model.profile_id, model.profile_generation, model.model_id, model.model_generation,
            "discovery-result-v1",
        ),
        TokenPrices(1, "USD", Decimal(1), Decimal(2)), Tokens(4096, 400), 8192,
    )
    capability = Capability(
        Provider.OPENAI, model.model_id, model.profile_id, model.profile_generation,
        model.model_generation, 1, "discovery-envelope-v1", "Suggest grounded research questions.",
        json.dumps(PUBLICATION_SCHEMA, separators=(",", ":")), 400, 5,
    )
    journal = (DispatchJournal(journal_path) if journal_path.exists()
               else DispatchJournal.provision(journal_path))
    gateway = OfflineDiscoveryDispatch(
        journal, OfflineProviderHttp(httpx.MockTransport(
            _provider(calls, missing_usage=missing_usage),
        )), capability,
    )
    def permits_idea(document):
        # The closed lifecycle schema checks server-generated IDs and timestamps.
        # Apply the sensitive-content policy to displayable prose instead.
        prose = [document[name] for name in ("title", "description", "hypothesis")]
        prose.extend(source["qualityFindings"] for source in document["evidence"])
        return safe_result_content(prose)

    lifecycle = IdeaLifecycle(CONTRACT, permits=permits_idea, verifies=lambda *_: True)

    def evidence_claim(row):
        return EvidenceClaim(
            EvidenceKind(row.position.kind), row.position.source_id, row.revision,
            row.content_hash, row.instrument_id, row.observed_at, row.window_start, row.window_end,
            (_QUOTE,), market="SPY", rights_allowed=True, quality_ok=True,
        )

    runtime = ResearchDiscovery(
        schedule_store, store, attempts, gateway, CompanionStore(worker, command_key=bytes(32)),
        lifecycle, configure=lambda _: config, access=lambda *_: EvidenceAccess(
            Readiness.READY, Rights.ALLOWED, Rights.ALLOWED,
        ), recheck=lambda *_: True, evidence_claim=evidence_claim, permits=safe_result_content,
    )
    return runtime, ResearchSupervisor(RunStore(worker), {RunKind.SCHEDULED: runtime.step},
                                       attempts=attempts)


def _assert_state(owner, expected, *, publications):
    with owner.connect() as connection:
        states = connection.execute(text("""
            SELECT w.state,w.reason FROM agent_research.discovery_work w
            ORDER BY source_position
        """)).all()
        assert states == expected
        assert connection.scalar(text("""
            SELECT count(*) FROM agent_research.discovery_publications
        """)) == publications
        assert connection.scalar(text("SELECT count(*) FROM backtest.experiment_batches")) == 0
        assert connection.scalar(text("SELECT count(*) FROM backtest.experiment_definitions")) == 0


def _drain(supervisor):
    worker_id = uuid4()
    for _ in range(6):
        if not supervisor.run_once(worker_id):
            return
    raise AssertionError("discovery queue did not settle within the bounded fixture steps")


def _check(owner, worker, now):
    instrument = definitions()[0][0]
    instrument_id = str(instrument.instrument_id)
    model = registry.ModelBinding("OPENAI", "offline-profile", 1, "offline-model", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    _configure(owner, instrument_id, agent, model, now)
    first_revision = _seed_source(owner, instrument, now, 1)
    calls = []
    with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}), (
        TemporaryDirectory()
    ) as directory:
        path = Path(directory) / "journal.sqlite"
        runtime, supervisor = _new_runtime(worker, instrument_id, agent, calls, path)
        assert runtime.tick(_SCHEDULES[0]) is not None
        assert supervisor.run_once(uuid4())
        _assert_state(owner, [("READY", "COMPLETED")], publications=0)
        assert calls == [str(first_revision)]

        runtime, restarted = _new_runtime(worker, instrument_id, agent, calls, path)
        _drain(restarted)
        _assert_state(owner, [("COMPLETED", "PUBLISHED")], publications=1)
        assert runtime.tick(_SCHEDULES[0]) is None
        assert runtime.tick(_SCHEDULES[1]) is not None
        _drain(restarted)
        _assert_state(owner, [("COMPLETED", "PUBLISHED")], publications=1)
        assert calls == [str(first_revision)]

        second_revision = _seed_source(owner, instrument, now, 2)
        assert runtime.tick(_SCHEDULES[2]) is not None
        assert restarted.run_once(uuid4())
        assert calls == [str(first_revision), str(second_revision)]
        with owner.begin() as connection:
            connection.execute(text("""
                UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton
            """))
        _drain(restarted)
        _assert_state(owner, [("COMPLETED", "PUBLISHED"), ("BLOCKED", "SOURCE_NOT_ALLOWED")],
                      publications=1)
        assert len(calls) == 2

        with owner.begin() as connection:
            connection.execute(text("""
                UPDATE research.source_status SET license_status='CONFIRMED' WHERE singleton
            """))
        third_revision = _seed_source(owner, instrument, now, 3)
        fourth_revision = _seed_source(owner, instrument, now, 4)
        runtime, uncertain = _new_runtime(
            worker, instrument_id, agent, calls, path, missing_usage={str(third_revision)},
        )
        assert runtime.tick(_SCHEDULES[3]) is not None
        assert uncertain.run_once(uuid4())
        with owner.connect() as connection:
            cursor, upper = connection.execute(text("""
                SELECT c.position,(SELECT max(position) FROM agent_research.discovery_sources)
                FROM agent_research.discovery_cursors c WHERE c.schedule_id=:schedule
            """), {"schedule": _SCHEDULES[3]}).one()
            assert cursor < upper
            assert connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_events
                WHERE reason='PENDING_BACKPRESSURE'
            """)) == 1
        assert calls[-1] == str(third_revision)
        runtime, recovered = _new_runtime(worker, instrument_id, agent, calls, path)
        assert runtime.tick(_SCHEDULES[4]) is not None
        _drain(recovered)
        assert calls == list(map(str, (first_revision, second_revision, third_revision,
                                       fourth_revision)))
        with owner.connect() as connection:
            assert connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_work WHERE state='OUTCOME_UNKNOWN'
            """)) == 1

    with owner.connect() as connection:
        idea = connection.execute(text("""
            SELECT v.document FROM agent_research.discovery_publications p
            JOIN agent_research.companion_idea_versions v
              ON (v.idea_id,v.version)=(p.idea_id,p.idea_version)
        """)).scalar_one()
        assert idea["status"] == "PROPOSED" and idea["discovery"]["freshness"] == "CURRENT"
        assert idea["experimentIds"] == [] and _QUOTE not in json.dumps(idea)


def guest():
    from alembic import command
    from alembic.config import Config

    payload = json.load(sys.stdin)
    owner = create_engine(URL.create(
        "postgresql+psycopg", username=payload["role"], password=payload["password"],
        host="postgres", port=5432, database=payload["database"],
    ), hide_parameters=True)
    worker = role_engine(owner, _ROLE)
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0046_discovery_evidence")
            register(connection)
            now = connection.scalar(text("SELECT clock_timestamp()"))
        _check(owner, worker, now)
    finally:
        worker.dispose()
        owner.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [frame for frame in frames if frame.filename.startswith((
            "/opt/lab/tests/", "/opt/lab/packages/", "/opt/lab/services/",
        ))]
        locations = ",".join(f"{Path(item.filename).name}:{item.lineno}" for item in relevant[-5:])
        original = getattr(error, "orig", None)
        code = getattr(original, "sqlstate", "") or getattr(error, "code", "")
        print(f"FAIL {type(error).__name__} {type(original).__name__} {code} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
