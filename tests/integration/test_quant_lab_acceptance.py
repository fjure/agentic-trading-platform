"""Normative full-session synthetic Quant Strategy Lab acceptance workflow."""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from packages.research.models import (
    BarIdentity,
    BarInterval,
    BarRevision,
    content_fingerprint,
)
from packages.research.registry import definitions, register
from packages.research.storage import identity_parameters
from tests.integration.test_quant_lab_api import BASE, WRITE, activate, connect
from tests.integration.test_quant_lab_storage import fixture

CODE_VERSION = "b" * 40
PERIODS = {
    "training": ("2026-01-05T14:30:00Z", "2026-01-09T21:00:00Z"),
    "validation": ("2026-01-12T14:30:00Z", "2026-02-10T21:00:00Z"),
    "test": ("2026-02-11T14:30:00Z", "2026-02-17T21:00:00Z"),
    "holdout": ("2026-02-18T14:30:00Z", "2026-02-24T21:00:00Z"),
}


def seed_market(admin):
    instrument, _ = definitions()[0]
    first = datetime(2025, 12, 31, 14, 30, tzinfo=UTC)
    last = datetime(2026, 2, 24, 14, 30, tzinfo=UTC)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    sessions = [
        day
        for day in days
        if day.weekday() < 5 and (day.month, day.day) not in {(1, 1), (1, 19), (2, 16)}
    ]
    calendar = content_fingerprint(("qsl-full-calendar-v1", instrument.instrument_id))
    captured = first - timedelta(days=1)
    holdout_reversal = datetime.fromisoformat(PERIODS["holdout"][0]) + timedelta(minutes=150)
    with admin.begin() as connection:
        register(connection)
        version = connection.execute(text("SELECT research.reserve_version()")).scalar_one()
        tables = (
            "research.bar_revisions",
            "research.calendar_versions",
            "backtest.corporate_action_coverage",
        )
        for table in tables:
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER a_stamp"))
        session_rows, bar_rows = [], []
        for session_index, opening in enumerate(sessions):
            session_rows.append(
                dict(
                    id=uuid4(),
                    day=opening.date(),
                    opening=opening,
                    closing=opening + timedelta(minutes=390),
                    calendar=calendar,
                    version=version,
                    ingested=captured,
                )
            )
            for index in range(26):
                start = opening + timedelta(minutes=15 * index)
                end = start + timedelta(minutes=15)
                serial = session_index * 26 + index
                price = Decimal("100") + Decimal(serial) / 100
                # Establish a long position, then materially reverse only the holdout.
                if start >= holdout_reversal:
                    price -= Decimal("5")
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
                    Decimal("100"),
                    end,
                    end,
                )
                bar_rows.append(
                    dict(
                        identity_parameters(bar.identity),
                        id=bar.revision_id,
                        price=price,
                        volume=bar.volume,
                        fingerprint=bar.fingerprint,
                        version=version,
                        ingested=end,
                    )
                )
        connection.execute(
            text("""INSERT INTO research.calendar_versions
            VALUES(:id,:day,:opening,:closing,:calendar,'alpaca-v2-calendar',:version,:ingested)"""),
            session_rows,
        )
        connection.execute(
            text("""INSERT INTO research.bar_revisions
            (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,bar_end,
             adjustment,open,high,low,close,volume,fingerprint,local_revision,predecessor_id,quality,
             commit_version,ingested_at)
            VALUES(:id,:instrument_id,:provider,'SPY',:feed,:interval,:bar_start,:bar_end,
             :adjustment,:price,:price,:price,:price,:volume,:fingerprint,1,NULL,'VALID',
             :version,:ingested)"""),
            bar_rows,
        )
        connection.execute(
            text("""INSERT INTO backtest.corporate_action_coverage
            VALUES(:id,:instrument,:start,:end,:certified,'offline-quant-acceptance-fixture',
                   :fingerprint,true,:version,:ingested)"""),
            dict(
                id=uuid4(),
                instrument=instrument.instrument_id,
                start=first,
                end=last + timedelta(days=1),
                certified=captured,
                fingerprint=content_fingerprint((calendar, "no-actions")),
                version=version,
                ingested=captured,
            ),
        )
        for table in tables:
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER a_stamp"))
    return instrument.instrument_id


def seed_workflow(admin, service, *, minimum_entries=None, instrument_id=None, strict=False):
    instrument_id = instrument_id or seed_market(admin)
    payload, _ = fixture()
    payload.update(instrumentId=str(instrument_id), symbol="SPY")
    if not strict:
        payload.update(
            researchMode="RETROSPECTIVE_EXPLORATORY",
            dataSelectionPolicyVersion="retrospective-v1",
        )
    payload["searchSpaces"] = [
        dict(
            strategyId="ema-cross-v1",
            version="1",
            parameterValues=dict(fast=[5, 10, 15], slow=[30, 40, 50]),
        )
    ]
    payload["strategySelectionPolicy"].update(
        maxDrawdown="0.1",
        minValidationSessions=20,
        minimumEntryCount=minimum_entries,
        minimumInvestedBarRatio=None,
    )
    windows = {
        name: dict(zip(("start", "end"), dates, strict=True)) for name, dates in PERIODS.items()
    }
    payload["protocol"].update(
        folds=[dict(fold=0, **{key: windows[key] for key in ("training", "validation", "test")})],
        holdout=windows["holdout"],
    )

    with connect(service) as (auth, _, _, _, http, _):
        activate(http, auth, "qsl-normative")

        def post(path, body, expected):
            response = http.post(
                BASE + path, json=body, headers={**WRITE, "Idempotency-Key": str(uuid4())}
            )
            assert response.status_code == expected, (
                response.status_code,
                response.json().get("code"),
            )
            return response.json()

        catalog = http.get(BASE + "strategies").json()
        ema = next(item for item in catalog["items"] if item["strategyId"] == "ema-cross-v1")
        assert ema["fittingStatus"] == "NOT_TRAINABLE" and ema["version"] == "1"
        assert (ema["adjustment"], ema["feed"], ema["interval"]) == ("raw", "IEX", "15Min")
        draft = post("experiments", payload, 201)
        assert draft["candidateCount"] == 9
        revision = dict(
            schemaVersion="quant-lab-v1",
            definitionVersion=draft["definitionVersion"],
            definitionHash=draft["definitionHash"],
        )
        path = "experiments/" + draft["id"]
        validation = post(path + "/validations", revision, 201)
        budget = validation["budgetEstimate"]
        assert validation["status"] == "VALIDATED", (budget, validation["reasons"])
        assert budget["reservedQueueSlots"] == 11 and budget["simulationBarSteps"] == 9510
        return post(
            path + "/batches",
            dict(
                revision, validationId=validation["id"], validationHash=validation["validationHash"]
            ),
            202,
        )


def verify_workflow(service, batch, *, selected=True, strict=False):
    path = BASE + "experiment-batches/" + batch["id"]
    with connect(service) as (auth, _, _, _, http, _):
        activate(http, auth, "qsl-normative-read")

        def get(suffix="", **params):
            response = http.get(path + suffix, params=params)
            assert response.status_code == 200, (response.status_code, response.json().get("code"))
            return response.json()

        terminal = get()
        assert terminal["state"] == "SUCCEEDED", terminal["reasons"]
        assert terminal["manifest"]["commonWarmupBars"] == 49
        bindings = terminal["manifest"]["phaseBindings"]
        assert [item["phase"] for item in bindings] == list(PERIODS)
        assert [item["researchRole"] for item in bindings] == [
            "IN_SAMPLE_ANALYSIS",
            "PARAMETER_SELECTION",
            "WALK_FORWARD_OOS",
            "FINAL_HOLDOUT",
        ]
        for item in bindings:
            start, end = PERIODS[item["phase"]]
            assert item["interval"] == {"start": start, "end": end}
            assert item["seedBarCount"] == 49
            assert datetime.fromisoformat(item["seedInterval"]["end"]) <= datetime.fromisoformat(
                start
            )
            assert item["fittingStatus"] == "NOT_TRAINABLE"
        assert terminal["candidateCounts"]["planned"] == 9
        assert terminal["referenceCounts"]["complete"] == 2
        report = get("/comparison")
        assert report["status"] == "COMPLETE" and report["reportHash"] == terminal["reportHash"]
        assert report["strategySelectionPolicyHash"] == terminal["strategySelectionPolicyHash"]
        choices = report["selections"]
        expected = "SELECTED" if selected else "NO_ELIGIBLE_CANDIDATE"
        assert len(choices) == 2 and all(choice["status"] == expected for choice in choices)
        assert choices[0]["selectedCandidateId"] == choices[1]["selectedCandidateId"]
        assert {choice["scope"] for choice in choices} == {"FOLD", "FINAL"}
        for choice in choices:
            assert choice["candidateCount"] == 9
            assert choice["strategySelectionPolicyHash"] == report["strategySelectionPolicyHash"]
            assert bool(choice["eligibleCount"]) == selected
        assert len(report["sensitivity"]) == 9
        if selected:
            best = next(cell for cell in report["sensitivity"] if cell["rank"] == 1)
            assert best["candidateId"] == choices[0]["selectedCandidateId"]
        assert "SMALL_FOLD_SAMPLE" in report["foldOverview"]["warnings"]
        for phase, count in zip(PERIODS, (130, 546, 104, 130), strict=True):
            filters = {"phase": phase, "limit": 20}
            if phase != "holdout":
                filters["fold"] = 0
            rows = get("/results", **filters)["items"]
            assert len(rows) == 11 and sum(row["role"] != "SEARCH_CANDIDATE" for row in rows) == 2
            statuses = [row["evaluationStatus"] for row in rows]
            for row in rows:
                if row["evaluationStatus"] == "COMPLETE":
                    metrics = row["metrics"]
                    assert metrics["expectedBarCount"] == metrics["observedBarCount"] == count
                    expected_evidence = (
                        "STRICT_PIT_EVIDENCE_COMPLETE" if strict else "EXPLORATORY_WITH_LIMITATIONS"
                    )
                    assert row["reliability"]["evidenceStatus"] == expected_evidence
                    if phase == "validation":
                        assert metrics["completeValidationSessions"] == 21
            if phase in ("training", "validation"):
                assert statuses == ["COMPLETE"] * 11
            elif selected:
                assert statuses.count("COMPLETE") == 3
                assert statuses.count("NOT_EVALUATED_BY_PROTOCOL") == 8
                winner = next(
                    row for row in rows if row["candidateId"] == choices[0]["selectedCandidateId"]
                )
                assert winner["metrics"]["sharpe"]["value"] is None
                if phase == "holdout":
                    assert Decimal(winner["metrics"]["netReturn"]["value"]) < 0
            else:
                assert statuses.count("COMPLETE") == 2
                assert statuses.count("NOT_EVALUATED_NO_SELECTION") == 9
        assert get("/comparison")["selections"] == choices
        return report


def guest():
    from time import monotonic

    from services.control_api.quant_lab_service import QuantLabService
    from services.worker.backtesting import BacktestWorker
    from tests.integration.test_backtest_jobs import role_engine
    from tests.integration.test_quant_lab_worker import guest_engine

    admin = guest_engine()
    control, dispatch = (
        role_engine(admin, role) for role in ("atl_control_api", "atl_backtest_worker")
    )
    service = QuantLabService(control, code_version=CODE_VERSION)
    instrument = seed_market(admin)
    worker = BacktestWorker(dispatch, enabled=True, code_version=CODE_VERSION)
    for minimum, expected_steps in ((None, 9510), (50, 9178)):
        batch = seed_workflow(
            admin,
            service,
            minimum_entries=minimum,
            instrument_id=instrument,
            strict=minimum is not None,
        )
        started = monotonic()
        assert worker.tick()
        elapsed = monotonic() - started
        with admin.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status,error_code,bar_steps,publication_bytes "
                    "FROM backtest.experiment_batches WHERE batch_id=:id"
                ),
                {"id": batch["id"]},
            ).one()
            frozen, first_test = connection.execute(
                text("""SELECT
                    (SELECT max((body->>'frozenAt')::timestamptz)
                       FROM backtest.experiment_selections WHERE batch_id=:id),
                    (SELECT min(r.accepted_at)
                       FROM backtest.experiment_result_links l JOIN backtest.runs r USING(run_id)
                       WHERE l.batch_id=:id AND l.phase_key IN ('0:test','holdout'))"""),
                {"id": batch["id"]},
            ).one()
        print(
            "WORKFLOW",
            "RETROSPECTIVE_SELECTED" if minimum is None else "STRICT_NONSELECTED",
            row.status,
            row.error_code,
            row.bar_steps,
            row.publication_bytes,
            round(elapsed, 2),
            flush=True,
        )
        assert row.status == "SUCCEEDED" and row.bar_steps == expected_steps, (
            row.status,
            row.error_code,
            row.bar_steps,
        )
        assert 0 < row.publication_bytes <= 12 * 1024**2
        assert frozen <= first_test
        report = verify_workflow(
            service, batch, selected=minimum is None, strict=minimum is not None
        )
        if minimum is not None:
            history = report["researchHistory"]
            assert history["reuseStatus"] == "REGISTERED_REUSE"
            assert history["externalKnowledgeStatus"] == "EXTERNAL_KNOWLEDGE_UNKNOWN"
            assert {"WALK_FORWARD_OOS", "FINAL_HOLDOUT"} <= {
                row["priorRole"] for row in history["overlaps"]
            }
    assert not BacktestWorker(dispatch, enabled=True, code_version=CODE_VERSION).tick()
    print("PASS quant normative selected/nonselection/reuse", flush=True)


def test_quant_lab_normative_workflow_actual_postgresql():
    from tests.integration.test_quant_lab_worker import host

    outcome = host("tests.integration.test_quant_lab_acceptance")
    assert outcome.endswith("nonselection/reuse/restore")
    print(outcome)


if __name__ == "__main__" and sys.argv[1:] == ["guest"]:
    import traceback

    try:
        guest()
    except Exception as error:
        frames = ",".join(
            f"{Path(frame.filename).name}:{frame.lineno}"
            for frame in traceback.extract_tb(error.__traceback__)[-5:]
        )
        print(
            "FAIL",
            type(error).__name__,
            frames,
            str(error)[:160] if isinstance(error, AssertionError) else "",
        )
        raise SystemExit(1) from None
