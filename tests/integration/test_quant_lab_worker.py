"""Quant worker against isolated PostgreSQL, using synthetic past market rows."""

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import event, text

from packages.backtesting.experiment_storage import ExperimentStorage, _validator, definition_domain
from packages.domain.models import utc_string
from packages.research.models import (
    AssetClass,
    BarIdentity,
    BarInterval,
    BarRevision,
    Instrument,
    SymbolMapping,
    content_fingerprint,
)
from packages.research.storage import ResearchStore, bar_from_row, identity_parameters
from services.control_api.quant_lab_service import QuantLabService
from services.worker.quant_lab import QuantLabWorker
from tests.integration import test_readiness_storage
from tests.integration.test_backtest_jobs import role_engine
from tests.integration.test_quant_lab_storage import TABLES, fixture, start_binding

SQL_DIAGNOSTICS = []


def host(module="tests.integration.test_quant_lab_worker"):
    old_tables = test_readiness_storage.TABLES
    try:
        test_readiness_storage.TABLES = (*old_tables, *TABLES)
        return test_readiness_storage.host(module)
    finally:
        test_readiness_storage.TABLES = old_tables


def worker_fixture(engine, *, strict=False, no_selection=True, bars_per_session=2):
    """Commit complete synthetic calendar, PIT-valid bars, and an accepted batch."""
    body, _ = fixture()
    if not strict:
        body.update(
            researchMode="RETROSPECTIVE_EXPLORATORY", dataSelectionPolicyVersion="retrospective-v1"
        )
    body["searchSpaces"] = [dict(strategyId="flat-v1", version="1", parameterValues={})]
    body["strategySelectionPolicy"]["minimumEntryCount"] = 1 if no_selection else None
    symbol = "QQQ" if strict else "SPY"
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", symbol, "USD")
    body.update(instrumentId=str(instrument.instrument_id), symbol=symbol)
    origin = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    days = tuple(
        origin + timedelta(days=i) for i in range(37) if (origin + timedelta(days=i)).weekday() < 5
    )
    session = timedelta(minutes=15 * bars_per_session)

    def period(start, last):
        return dict(start=utc_string(start), end=utc_string(last + session))

    validation_end = 1 if no_selection else 20
    body["protocol"]["folds"] = [
        dict(
            fold=0,
            training=period(days[0], days[0]),
            validation=period(days[1], days[validation_end]),
            test=period(days[validation_end + 1], days[validation_end + 1]),
        )
    ]
    body["protocol"]["holdout"] = period(days[validation_end + 2], days[validation_end + 2])
    days = days[: validation_end + 3]
    domain = definition_domain(body)
    source = ResearchStore()
    captured = origin - timedelta(days=3)
    calendar_version = content_fingerprint(
        ("quant-worker-synthetic-calendar", instrument.instrument_id)
    )
    historical = (
        "research.bar_revisions",
        "research.calendar_versions",
        "backtest.corporate_action_coverage",
    )
    with engine.begin() as connection:
        source.add_instrument(connection, instrument)
        source.add_mapping(
            connection, SymbolMapping(instrument.instrument_id, "alpaca", symbol, captured)
        )
        version = connection.execute(text("SELECT research.reserve_version()")).scalar_one()
        for table in historical:
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER a_stamp"))
        for opening in days:
            closing = opening + session
            connection.execute(
                text("""INSERT INTO research.calendar_versions
                VALUES(:id,:day,:opening,:closing,:calendar,'alpaca-v2-calendar',:version,:ingested)"""),
                dict(
                    id=uuid4(),
                    day=opening.date(),
                    opening=opening,
                    closing=closing,
                    calendar=calendar_version,
                    version=version,
                    ingested=captured,
                ),
            )
            for index in range(bars_per_session):
                start = opening + index * timedelta(minutes=15)
                end = start + timedelta(minutes=15)
                price = Decimal("100") + index
                bar = BarRevision(
                    uuid4(),
                    BarIdentity(
                        instrument.instrument_id,
                        "alpaca",
                        "iex",
                        BarInterval.FIFTEEN_MINUTES,
                        start,
                        end,
                    ),
                    price,
                    price,
                    price,
                    price,
                    Decimal("10"),
                    end,
                    end,
                )
                connection.execute(
                    text("""INSERT INTO research.bar_revisions
                    (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,
                     bar_end,adjustment,open,high,low,close,volume,fingerprint,local_revision,
                     predecessor_id,quality,commit_version,ingested_at)
                    VALUES(:id,:instrument_id,:provider,:symbol,:feed,:interval,:bar_start,:bar_end,
                     :adjustment,:price,:price,:price,:price,:volume,:fingerprint,1,NULL,'VALID',
                     :version,:ingested)"""),
                    dict(
                        identity_parameters(bar.identity),
                        id=bar.revision_id,
                        symbol=symbol,
                        price=price,
                        volume=bar.volume,
                        fingerprint=bar.fingerprint,
                        version=version,
                        ingested=end,
                    ),
                )
        connection.execute(
            text("""INSERT INTO backtest.corporate_action_coverage
            VALUES(:id,:instrument,:start,:end,:certified,'offline-quant-worker-fixture',
                   :fingerprint,true,:version,:ingested)"""),
            dict(
                id=uuid4(),
                instrument=instrument.instrument_id,
                start=origin - timedelta(days=1),
                end=days[-1] + session,
                certified=captured,
                fingerprint=content_fingerprint(
                    (instrument.instrument_id, "quant-worker-corporate-fixture")
                ),
                version=version,
                ingested=captured,
            ),
        )
        for table in historical:
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER a_stamp"))
    store = ExperimentStorage(role_engine(engine, "atl_control_api"))
    candidates = [
        dict(role=role, configuration=dict(strategyId=strategy, version="1", parameters={}))
        for role, strategy in (
            ("SEARCH", "flat-v1"),
            ("FLAT_REFERENCE", "flat-v1"),
            ("BUY_HOLD_REFERENCE", "buy-hold-v1"),
        )
    ]
    definition = store.create_definition(body, candidates)
    assert definition["fingerprint"] == domain.definition_hash
    validation = store.validate(
        definition["definition_id"],
        dict(
            definitionHash=definition["fingerprint"],
            calendarVerified=True,
            simulationBarSteps=3 * bars_per_session * len(days),
            snapshotRows=(bars_per_session + 1) * len(days) + 1,
            publicationBytes=1000000,
            seedBars=0,
        ),
    )
    batch = store.enqueue(
        definition["definition_id"],
        definition["fingerprint"],
        "local-operator",
        "qsl-worker-" + uuid4().hex,
        "a" * 32,
        "b" * 40,
        **start_binding(definition, validation),
    )
    return store, batch


def guest_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    payload = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=payload["role"],
            password=payload["password"],
            host="postgres",
            database=payload["database"],
        ),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


def _isolated_outcome_perturbation(engine, control, batch, worker):
    """A new version of one OOS close changes its actual ledger, never the selection."""
    definition = control.definition(batch["definition_id"])
    test = definition["body"]["protocol"]["folds"][0]["test"]
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM research.bar_revisions "
                    "WHERE instrument_id=:instrument AND bar_end=CAST(:end AS timestamptz)"
                ),
                {"instrument": definition["body"]["instrumentId"], "end": test["end"]},
            )
            .mappings()
            .one()
        )
        bar = bar_from_row(row)
        corrected = replace(bar, revision_id=uuid4(), high=Decimal("150"), close=Decimal("150"))
        revision = ResearchStore().append_bar(connection, corrected, symbol=row["provider_symbol"])
        assert revision.local_revision == 2 and revision.predecessor_id == bar.revision_id
        validation = (
            connection.execute(
                text("SELECT * FROM backtest.experiment_validations WHERE validation_id=:id"),
                {"id": batch["validation_id"]},
            )
            .mappings()
            .one()
        )
    later = control.enqueue(
        definition["definition_id"],
        definition["fingerprint"],
        "local-operator",
        "quant-perturbation-" + uuid4().hex,
        "b" * 32,
        worker.code_version,
        **start_binding(definition, validation),
    )
    assert later["snapshot_version"] > batch["snapshot_version"]
    lease = worker.storage.claim(worker.owner)
    assert lease.batch_id == later["batch_id"]
    worker._process(lease, lambda: None)

    def observed(connection, batch_id):
        params = {"id": batch_id}
        selections = tuple(
            connection.execute(
                text(
                    "SELECT fingerprint FROM "
                    "backtest.experiment_selections WHERE batch_id=:id ORDER BY scope"
                ),
                params,
            ).scalars()
        )
        validation = connection.execute(
            text(
                "SELECT fingerprint FROM backtest.experiment_result_links "
                "WHERE batch_id=:id AND role='SEARCH' AND phase_key='0:validation'"
            ),
            params,
        ).scalar_one()
        ledger = connection.execute(
            text(
                "SELECT r.body->'equity' FROM backtest.experiment_result_links l "
                "JOIN backtest.results r USING(run_id) WHERE l.batch_id=:id AND "
                "l.role='BUY_HOLD_REFERENCE' AND l.phase_key='0:test'"
            ),
            params,
        ).scalar_one()
        return selections, validation, ledger

    with engine.connect() as connection:
        first = observed(connection, batch["batch_id"])
        second = observed(connection, later["batch_id"])
    assert first[:2] == second[:2], "only future outcome input changed"
    assert first[2][-1]["equity"] != second[2][-1]["equity"]


def guest():
    from tests.integration.test_quant_lab_api import assert_published_http

    engine = guest_engine()
    for strict, no_selection in ((False, True), (True, False)):
        control, batch = worker_fixture(
            engine,
            strict=strict,
            no_selection=no_selection,
            bars_per_session=3 if no_selection else 2,
        )
        worker = QuantLabWorker(
            role_engine(engine, "atl_backtest_worker"), owner=uuid4(), code_version="b" * 40
        )

        @event.listens_for(worker.storage.engine, "handle_error")
        def diagnostic(context):
            diag = getattr(context.original_exception, "diag", None)
            SQL_DIAGNOSTICS.append(str(getattr(diag, "message_primary", ""))[:160])

        lease = worker.storage.claim(worker.owner)
        assert lease.batch_id == batch["batch_id"]
        # Invoke the real worker entry point with the same claim and no exception interception
        # at the test boundary so a contract/SQL failure can point to its exact synthetic stage.
        worker._process(lease, lambda: None)
        with engine.connect() as connection:
            terminal = connection.execute(
                text("SELECT status,bar_steps FROM backtest.experiment_batches WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).one()
            selections = tuple(
                connection.execute(
                    text(
                        "SELECT status FROM backtest.experiment_selections "
                        "WHERE batch_id=:id ORDER BY scope"
                    ),
                    {"id": lease.batch_id},
                ).scalars()
            )
            published = connection.execute(
                text("SELECT body FROM backtest.experiment_reports WHERE batch_id=:id"),
                {"id": lease.batch_id},
            ).scalar_one()
            results = tuple(
                connection.execute(
                    text(
                        "SELECT l.role,l.status,r.body "
                        "FROM backtest.experiment_result_links l LEFT JOIN backtest.results r "
                        "USING(run_id) "
                        "WHERE l.batch_id=:id"
                    ),
                    {"id": lease.batch_id},
                ).mappings()
            )
            manifest = connection.execute(
                text(
                    "SELECT body->'manifestReference' "
                    "FROM backtest.experiment_snapshots WHERE batch_id=:id"
                ),
                {"id": lease.batch_id},
            ).scalar_one()
        assert terminal.status == "SUCCEEDED"
        assert terminal.bar_steps == (30 if no_selection else 138)
        assert selections == (("NO_ELIGIBLE_CANDIDATE",) * 2 if no_selection else ("SELECTED",) * 2)
        assert published["comparison"]["status"] == "COMPLETE"
        _validator("QuantLabComparisonReport").validate(published["comparison"])
        _validator("QuantLabManifestReference").validate(manifest)
        assert len(results) == 12
        assert sum(item["status"] == "NOT_EVALUATED_NO_SELECTION" for item in results) == (
            2 if no_selection else 0
        )
        for item in results:
            if item["status"] == "COMPLETE":
                body = item["body"]
                _validator("QuantLabPerformanceMetrics").validate(body["metrics"])
                for equity, drawdown in zip(body["equity"], body["drawdown"], strict=True):
                    _validator("QuantLabEquityPoint").validate(equity)
                    _validator("QuantLabDrawdownPoint").validate(drawdown)
        assert_published_http(
            QuantLabService(control.engine, code_version="b" * 40),
            batch_id=str(batch["batch_id"]),
            definition_id=str(batch["definition_id"]),
            selected=not no_selection,
        )
        if no_selection:
            _isolated_outcome_perturbation(engine, control, batch, worker)
        else:
            _partial_failure(engine, control, batch, worker)
    print("PASS quant worker strict/retrospective/selection/nonselection")


def _partial_failure(engine, control, batch, worker):
    from unittest.mock import patch

    from packages.backtesting.models import BacktestBlocked
    from services.worker.quant_lab import simulate_quant

    definition = control.definition(batch["definition_id"])
    with engine.connect() as connection:
        validation = (
            connection.execute(
                text("SELECT * FROM backtest.experiment_validations WHERE validation_id=:id"),
                {"id": batch["validation_id"]},
            )
            .mappings()
            .one()
        )
    service = QuantLabService(control.engine, code_version=worker.code_version)

    def start():
        return str(
            control.enqueue(
                definition["definition_id"],
                definition["fingerprint"],
                "local-operator",
                "quant-failure-" + uuid4().hex,
                "b" * 32,
                worker.code_version,
                **start_binding(definition, validation),
            )["batch_id"]
        )

    early = start()
    with patch(
        "services.worker.quant_lab.load_quant_inputs",
        side_effect=BacktestBlocked("BLOCKED_QUALITY"),
    ):
        assert worker.tick()
    detail = service.get_experiment_batch(early, {}, scope="test")
    assert detail["state"] == "BLOCKED"
    assert all(
        detail[field][name] == 0
        for field in ("candidateCounts", "referenceCounts")
        for name in ("started", "failed", "blocked")
    )

    middle = start()
    test_flat_calls = 0

    def fail_selected(definition, phase, candidate, **kwargs):
        nonlocal test_flat_calls
        if phase.binding.phase == "test" and candidate.strategy_id == "flat-v1":
            test_flat_calls += 1
            if test_flat_calls == 2:
                raise RuntimeError("synthetic selected OOS simulation failure")
        return simulate_quant(definition, phase, candidate, **kwargs)

    with patch("services.worker.quant_lab.simulate_quant", side_effect=fail_selected):
        assert worker.tick()
    detail = service.get_experiment_batch(middle, {}, scope="test")
    assert detail["state"] == "FAILED"
    assert detail["candidateCounts"]["failed"] == detail["candidateCounts"]["started"] == 1
    assert detail["referenceCounts"]["started"] == 2 and detail["referenceCounts"]["failed"] == 0
    results = service.list_results(middle, {"limit": 20}, scope="test")["items"]
    failure = [row for row in results if row["evaluationStatus"] == "FAILED"]
    assert len(failure) == 1 and failure[0]["phase"] == "test"
    assert failure[0]["role"] == "SEARCH_CANDIDATE" and failure[0]["metrics"] is None
    assert failure[0]["reasonCodes"] == ["INCOMPLETE_CANDIDATE_SET"]
    assert sum(row["evaluationStatus"] == "COMPLETE" for row in results) == 8


def test_quant_lab_worker_actual_postgresql():
    assert host("tests.integration.test_quant_lab_worker").endswith(
        "selection/nonselection/restore"
    )


def test_terminal_batch_counts_only_observed_slot_failures():
    complete = dict(role="SEARCH", states=["COMPLETE"] * 4, test_complete=True)
    untouched = dict(role="SEARCH", states=[], test_complete=False)
    partial = dict(role="SEARCH", states=["COMPLETE"], test_complete=False)
    failed = dict(role="BUY_HOLD_REFERENCE", states=["FAILED"], test_complete=False)
    batch = dict(status="FAILED", snapshot_phases=[{}, {}, {}, {}])
    candidates, references = QuantLabService._counts(batch, (complete, untouched, partial, failed))
    assert (candidates["started"], candidates["complete"], candidates["failed"]) == (2, 1, 0)
    assert (references["started"], references["complete"], references["failed"]) == (1, 0, 1)


if __name__ == "__main__" and sys.argv[1:] == ["guest"]:
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        where = ",".join(
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}"
            for frame in frames
            if frame.filename.startswith("/opt/lab/")
        )
        primary = getattr(
            getattr(getattr(error, "orig", None), "diag", None), "message_primary", ""
        )
        reason = str(error) if isinstance(error, (AssertionError, ValueError)) else primary
        print(f"FAIL {type(error).__name__} {where} {reason}"[:600])
        if SQL_DIAGNOSTICS:
            print("Last synthetic SQL diagnostic: " + SQL_DIAGNOSTICS[-1])
        raise SystemExit(1) from None
