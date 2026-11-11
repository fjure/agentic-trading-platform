"""Recover an Agent Report from a real, operator-approved Quant batch."""

import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from packages.agent_research.registry import QUANT_RESEARCH_V1
from packages.agent_research.reporting.service import ResearchReports
from packages.agent_research.reporting.source import QuantReportSource
from packages.agent_research.reporting.store import ReportStore, ReportStoreError
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunKind, RunStore
from services.control_api.quant_lab_service import QuantLabService
from services.worker.backtesting import BacktestWorker
from tests.integration.test_backtest_jobs import role_engine
from tests.integration.test_quant_lab_acceptance import PERIODS, seed_market
from tests.integration.test_quant_lab_storage import fixture
from tests.integration.test_quant_operator_approval import (
    _ADMISSION,
    _CODE,
    _OPERATOR,
    _RESEARCH,
    _TRACE,
    _binding,
    _seed_idea,
)

_PROOF = "PASS approved Quant report publication, rights, immutable recovery and redacted audit"


def test_research_report_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, _RESEARCH, _ADMISSION)
        harness.TABLES = (*previous_tables, "agent_research.report_versions",
                          "agent_research.report_rejections")
        assert harness.host("tests.recovery.test_research_report_db") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS, harness.TABLES = previous_groups, previous_tables


def _approved_batch(owner, service, runs):
    instrument_id = seed_market(owner)
    _seed_idea(owner)
    request, _ = fixture()
    request.update(
        instrumentId=str(instrument_id), symbol="SPY", researchMode="RETROSPECTIVE_EXPLORATORY",
        dataSelectionPolicyVersion="retrospective-v1",
    )
    request["strategySelectionPolicy"].update(
        maxDrawdown="0.1", minValidationSessions=20, minimumEntryCount=None,
        minimumInvestedBarRatio=None,
    )
    windows = {
        name: dict(zip(("start", "end"), dates, strict=True)) for name, dates in PERIODS.items()
    }
    request["protocol"].update(
        folds=[dict(fold=0, **{key: windows[key] for key in ("training", "validation", "test")})],
        holdout=windows["holdout"],
    )
    run_id = runs.enqueue(
        actor=_OPERATOR, key="report-fixture-run", kind=RunKind.DIALOGUE, input_ref="idea",
        trace_id=_TRACE, agent=QUANT_RESEARCH_V1,
    )
    claim = runs.claim(uuid4())
    draft = service.create_agent_experiment(
        request, actor=_OPERATOR, key="report-fixture-definition", trace=_TRACE,
        run_id=str(run_id), idea_id="idea", idea_version=1,
    )
    idea = {
        "id": "idea", "version": 2, "status": "EXPERIMENT_DRAFTED",
        "title": "Synthetic EMA investigation", "description": "How did the EMA rule perform?",
        "hypothesis": {"signal": "EMA cross", "statement": "The rule may capture a trend."},
        "assumptions": [], "evidence": [], "experimentDraftIds": [draft["id"]],
        "testability": {"classification": "EXACTLY_TESTABLE"},
    }
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.quant_start_policies SET max_daily=1 WHERE policy_id='global'
        """))
        connection.execute(text("""
            UPDATE agent_research.companion_idea_versions
            SET document=jsonb_set(document,'{hypothesis}',CAST(:hypothesis AS jsonb))
            WHERE idea_id='idea' AND version=1
        """), {"hypothesis": json.dumps(idea["hypothesis"])})
        connection.execute(text("""
            INSERT INTO agent_research.companion_idea_versions
              (idea_id,version,previous_version,document)
            VALUES ('idea',2,1,CAST(:body AS jsonb))
        """), {"body": json.dumps(idea)})
    result = service.validate_agent_experiment(
        draft["id"], {
            "schemaVersion": "quant-lab-v1", "definitionVersion": draft["definitionVersion"],
            "definitionHash": draft["definitionHash"],
        }, actor=_OPERATOR, key="report-fixture-validation", trace=_TRACE,
    )
    assert result["status"] == "VALIDATED", result["reasons"]
    runs.transition(claim, RunState.WAITING_FOR_APPROVAL)
    definition = service.storage.definition(draft["id"])
    validation = service.storage.validation_request(
        definition["definition_id"], "report-fixture-validation",
    )
    binding = _binding(owner, run_id, definition, validation)
    approval_id, digest = service.prepare_agent_approval(
        binding.to_document(), actor=_OPERATOR, key="report-fixture-prepare", trace=_TRACE,
    )
    return service.start_approved_experiment_batch(
        str(approval_id), digest, actor=_OPERATOR, key="report-fixture-consume", trace=_TRACE,
    )["id"]


def _verify(owner, control, dispatch, research, admission):
    quant = QuantLabService(
        control, code_version=_CODE, approval_engine=admission,
        authorize_operator=lambda actor: actor == _OPERATOR,
    )
    batch = _approved_batch(owner, quant, RunStore(research))
    store = ReportStore(research)
    assert store.authorize(batch, actor=_OPERATOR) is None
    worker = BacktestWorker(dispatch, enabled=True, code_version=_CODE)
    assert worker.tick()
    with owner.connect() as connection:
        status, error = connection.execute(text("""
            SELECT status,error_code FROM backtest.experiment_batches WHERE batch_id=:batch
        """), {"batch": batch}).one()
    assert status == "SUCCEEDED", error
    assert store.authorize(batch, actor="another-operator") is None

    service = ResearchReports(QuantReportSource(research), store, permits=lambda _: True)
    proposal = b'{"raw":"synthetic-confidential-marker"}'
    published = service.publish(batch, actor=_OPERATOR, proposal=proposal)
    report_id = published["summary"]["reportId"]
    assert published["summary"]["interpretationStatus"] == "FACTS_ONLY"
    assert published["selection"]["status"] == "SELECTED"
    holdout = published["phases"]["holdout"]["metrics"]
    assert any(
        claim["reference"]["subject"] == "CANDIDATE"
        and claim["reference"]["fieldPath"] == "/metrics/netReturn/value"
        and Decimal(claim["value"]) < 0
        for claim in holdout
    )
    assert published["currentRightsWarnings"] == []
    assert ReportStore(research).get(report_id, actor=_OPERATOR) == published
    assert store.get(report_id, actor="another-operator") is None
    assert service.publish(batch, actor=_OPERATOR, proposal=proposal) == published
    with pytest.raises(ReportStoreError, match="REPORT_VERSION_CONFLICT"):
        store.publish(published | {"conclusion": "Changed statement"}, actor=_OPERATOR)
    with owner.connect() as connection:
        assert connection.execute(text("""
            SELECT code FROM agent_research.report_rejections WHERE report_id=:report
        """), {"report": report_id}).scalars().all() == ["REPORT_OUTPUT_SHAPE_INVALID"]
        assert connection.scalar(text("SELECT count(*) FROM agent_research.report_versions")) == 1
    with pytest.raises(DBAPIError), research.begin() as connection:
        connection.execute(text("DELETE FROM agent_research.report_versions"))
    with pytest.raises(DBAPIError), research.connect() as connection:
        connection.execute(text("SELECT 1 FROM backtest.experiment_result_links"))
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE research.source_status SET license_status='BLOCKED' WHERE singleton
        """))
    reloaded = ReportStore(research).get(report_id, actor=_OPERATOR)
    assert reloaded["currentRightsWarnings"][0]["status"] == "RIGHTS_REVOKED"
    assert reloaded | {"currentRightsWarnings": []} == published


def guest():
    from alembic import command
    from alembic.config import Config

    payload = json.load(sys.stdin)
    owner = create_engine(URL.create(
        "postgresql+psycopg", username=payload["role"], password=payload["password"],
        host="postgres", port=5432, database=payload["database"],
    ), hide_parameters=True)
    engines = [owner]
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        engines.extend(role_engine(owner, role) for role in (
            "atl_control_api", "atl_backtest_worker", _RESEARCH, _ADMISSION,
        ))
        _verify(*engines)
    finally:
        for engine in reversed(engines):
            engine.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        relevant = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        locations = ",".join(f"{Path(item.filename).name}:{item.lineno}" for item in relevant[-6:])
        print(f"FAIL {type(error).__name__} {getattr(error, 'code', '')} {locations}")
        raise SystemExit(1) from None
    print(_PROOF)
