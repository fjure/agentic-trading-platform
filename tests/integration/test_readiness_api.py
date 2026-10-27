"""RDR-05: authentifiziertes HTTP gegen echte rollenbegrenzte PostgreSQLports."""

import sys
from pathlib import Path

from tests.integration.test_backtest_dataset import guest_engine, host


def check_observations(engine, worker, seed):
    """Explizit synthetische alte Empfangszeiten; reale Receipt-/Commitzeit."""
    import json
    from datetime import UTC, datetime, timedelta
    from uuid import UUID, uuid4

    from sqlalchemy import text

    from packages.backtesting.dataset import DatasetPort
    from packages.backtesting.readiness_jobs import load_observations

    start, end = datetime.fromisoformat(seed["start"]), datetime.fromisoformat(seed["end"])
    instrument = UUID(seed["instrument"])
    port, due, receipts = DatasetPort(), uuid4(), (uuid4(), uuid4())
    with worker.connect() as connection:
        snapshot = port.snapshot(connection)
        bar = port.page(connection, instrument, start, end, snapshot).items[0]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO research.collection_plans(instrument_id) VALUES(:id) "
                "ON CONFLICT DO NOTHING"
            ),
            dict(id=instrument),
        )
        connection.execute(
            text(
                "INSERT INTO research.due_jobs(due_id,instrument_id,generation,"
                "purpose,due_at,start_at,end_at) "
                "VALUES(:id,:instrument,0,'INITIAL_BAR',:start,:start,:end)"
            ),
            dict(id=due, instrument=instrument, start=start, end=end),
        )
        for index, identifier in enumerate(receipts):
            body = dict(
                revisionId=str(bar.revision_id),
                contentFingerprint=bar.fingerprint,
                duplicate=bool(index),
            )
            connection.execute(
                text(
                    "INSERT INTO research.observation_receipts(receipt_id,due_id,"
                    "instrument_id,generation,purpose,due_at,fence,state,event_at,"
                    "bar_close_at,first_observed_at,body,ingested_at,commit_version) "
                    "VALUES(:id,:due,:instrument,0,'INITIAL_BAR',:due_at,1,'COMPLETED',"
                    ":start,:end,:first,CAST(:body AS jsonb),:now,0)"
                ),
                dict(
                    id=identifier,
                    due=due,
                    instrument=instrument,
                    due_at=start,
                    start=bar.identity.bar_start,
                    end=bar.identity.bar_end,
                    first=bar.identity.bar_end + timedelta(seconds=30 if index == 0 else 240),
                    body=json.dumps(body),
                    now=datetime.now(UTC),
                ),
            )
    with worker.connect() as connection:
        before = port.snapshot(connection)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_execution"))
        for identifier in receipts:
            connection.execute(
                text("SELECT research.confirm_receipt(:id,'OBSERVATION')"), dict(id=identifier)
            )
    with worker.connect() as connection:
        after = port.snapshot(connection)
        old = load_observations(connection, instrument, start, end, before, (bar,))
        current = load_observations(connection, instrument, start, end, after, (bar,))
    assert len(old) == len(current) == 2
    assert all(item.commit_confirmed_at is None for item in old)
    assert all(item.commit_confirmed_at is not None for item in current)
    assert current[0].first_observed_at == bar.identity.bar_end + timedelta(seconds=30)
    assert current[1].first_observed_at == bar.identity.bar_end + timedelta(seconds=240)
    assert all(item.ingested_at == bar.ingested_at for item in current)
    assert all(item.commit_version == bar.commit_version for item in current)
    assert [item.duplicate for item in current] == [False, True]
    assert all(
        item.clock_status == "UNKNOWN" and item.provider_published_at is None for item in current
    )


def guest():
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    from dataclasses import replace
    from decimal import Decimal
    from unittest.mock import patch
    from uuid import uuid4

    import httpx
    from sqlalchemy import text

    from packages.backtesting.dataset import DatasetPort, FrozenDataset, Snapshot
    from packages.backtesting.readiness import assess
    from packages.backtesting.readiness_jobs import load_observations
    from packages.backtesting.request import parse_request
    from packages.research.models import content_fingerprint
    from packages.research.registry import definitions
    from packages.research.storage import ResearchStore
    from services.control_api.readiness_service import ReadinessService
    from services.worker.backtesting import BacktestWorker
    from tests.contracts.test_readiness_transport import BASE, ORIGIN, client, example
    from tests.integration.backtest_browser_support import seed_backtest_fixture
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    seed = seed_backtest_fixture(engine)
    control, worker = (
        role_engine(engine, "atl_control_api"),
        role_engine(engine, "atl_backtest_worker"),
    )
    check_observations(engine, worker, seed)
    service = ReadinessService(control, code_version="b" * 40)
    http = client(service)
    request = example("request")
    request.update(
        instrumentId=seed["instrument"],
        strategyId="flat-v1",
        parameters={},
        feeUsd="0",
        slippageBps="0",
    )
    request["folds"] = [
        dict(
            trainingStart="2026-10-18T13:30:00Z",
            trainingEnd=seed["start"],
            testStart=seed["start"],
            testEnd=seed["end"],
        )
    ]
    headers = {"Origin": ORIGIN, "Idempotency-Key": "readiness-pg-api-0001"}
    assert (
        client(service, activate=False).post(BASE, json=request, headers=headers).status_code == 401
    )
    assert (
        http.post(
            BASE, json=request, headers={**headers, "Origin": "https://foreign.invalid"}
        ).status_code
        == 403
    )
    response = http.post(BASE, json=request, headers=headers)
    assert response.status_code == 202, response.text
    accepted = response.json()
    identifier = accepted["assessmentId"]
    detail_path = BASE + "/" + identifier
    assert http.post(BASE, json=request, headers=headers).json() == accepted
    conflict = http.post(BASE, json={**request, "feeUsd": "1"}, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    pending = http.get(detail_path)
    assert pending.status_code == 200, pending.text
    assert pending.json()["status"] == "ACCEPTED" and pending.json()["report"] is None
    assert http.get(detail_path + "/findings").status_code == 409
    assert http.get(BASE + "/" + str(uuid4())).status_code == 404
    dispatcher = BacktestWorker(worker, enabled=True, code_version="b" * 40)
    with control.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM backtest.readiness_jobs WHERE assessment_id=:id"),
                dict(id=identifier),
            )
            .mappings()
            .one()
        )
    snapshot = Snapshot(row["snapshot_version"], row["snapshot_at"])
    config = parse_request(row["request"], snapshot.at)
    calendars, quality, coverage, trust, checked_at = dispatcher._metadata(config, snapshot)
    with worker.connect() as connection:
        baseline_bars = (
            DatasetPort()
            .page(
                connection,
                config.instrument_id,
                config.folds[0].training_start,
                config.folds[0].test_end,
                snapshot,
            )
            .items
        )
        observations = load_observations(
            connection,
            config.instrument_id,
            config.folds[0].training_start,
            config.folds[0].test_end,
            snapshot,
            baseline_bars,
        )
    baseline = replace(
        assess(
            config,
            FrozenDataset(snapshot, baseline_bars),
            calendars,
            quality,
            coverage,
            trust=trust,
            observations=observations,
        ),
        code_version="b" * 40,
    ).report_dict(row["assessment_id"], checked_at)
    baseline["requestHash"] = row["request_hash"]
    page_method, seen, appended = DatasetPort.page, [], []

    def append_parallel():
        old = baseline_bars[0]
        changed = replace(
            old,
            revision_id=uuid4(),
            close=Decimal("15"),
            high=Decimal("15"),
            local_revision=2,
            predecessor_id=old.revision_id,
        )
        with engine.begin() as connection:
            appended.append(ResearchStore().append_bar(connection, changed, symbol="SPY"))

    def page_with_parallel_writer(port, connection, *args, **values):
        values["limit"] = 1
        result = page_method(port, connection, *args, **values)
        seen.extend(bar.revision_id for bar in result.items)
        if not appended:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(append_parallel).result(timeout=10)
        return result

    # Kein Transportaufruf ist für Diagnose, Kalender oder Simulation zulässig.
    with (
        patch.object(httpx.Client, "send", side_effect=AssertionError("provider access forbidden")),
        patch.object(DatasetPort, "page", page_with_parallel_writer),
    ):
        assert dispatcher.tick()
    done = http.get(detail_path)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "SUCCEEDED", body["failureCode"]
    assert body["eligibility"] == "ELIGIBLE" and body["publicationComplete"]
    report = deepcopy(body["report"])
    assert len(seen) == 4 and appended[0].revision_id not in seen
    assert appended[0].commit_version > snapshot.version
    assert report["snapshot"]["snapshotVersion"] == snapshot.version
    baseline["createdAt"] = report["createdAt"]
    del baseline["reportHash"]
    baseline["reportHash"] = content_fingerprint(baseline)
    assert report == baseline
    assert report["findingCount"] == 0
    assert report["codeVersion"] == "b" * 40
    assert http.post(BASE, json=request, headers=headers).json() == accepted
    assert http.get(detail_path + "/findings").json()["items"] == []

    # Ein anderes registriertes Instrument liefert unabhängige Befunde trotz
    # vorhandener Kalenderbelege; keine Kalenderlücke verdeckt fehlende Bars.
    missing = {**request, "instrumentId": str(definitions()[1][0].instrument_id)}
    blocked_response = http.post(
        BASE, json=missing, headers={**headers, "Idempotency-Key": "readiness-pg-api-0002"}
    )
    assert blocked_response.status_code == 202, blocked_response.text
    blocked_id = blocked_response.json()["assessmentId"]
    with patch.object(
        httpx.Client, "send", side_effect=AssertionError("provider access forbidden")
    ):
        assert dispatcher.tick()
    blocked_path = BASE + "/" + blocked_id
    blocked = http.get(blocked_path)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "SUCCEEDED", blocked.json()["failureCode"]
    assert blocked.json()["eligibility"] == "BLOCKED"
    first = http.get(blocked_path + "/findings?limit=1")
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["totalMatching"] > 1 and page["nextCursor"]
    restarted = client(ReadinessService(control, code_version="b" * 40))
    token = page["nextCursor"]
    second = restarted.get(blocked_path + "/findings", params={"limit": 1, "cursor": token})
    assert second.status_code == 200, second.text
    assert second.json()["items"] != page["items"]
    for path, values in (
        (blocked_path, {"cursor": token, "category": "BAR_MISSING"}),
        (detail_path, {"cursor": token}),
        (blocked_path, {"cursor": token[:-1] + ("A" if token[-1] != "A" else "B")}),
    ):
        invalid = restarted.get(path + "/findings", params=values)
        assert invalid.status_code == 400 and invalid.json()["code"] == "CURSOR_INVALID"
    expired = service._decode(token)
    expired["expiresAt"] = "2020-01-01T00:00:00Z"
    assert (
        restarted.get(
            blocked_path + "/findings", params={"cursor": service._sign(expired)}
        ).status_code
        == 400
    )
    assert http.get(detail_path).json()["report"] == report
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM backtest.readiness_jobs")).scalar_one()
            == 2
        )
        assert (
            connection.execute(text("SELECT count(*) FROM backtest.readiness_reports")).scalar_one()
            == 2
        )
        assert connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        )
        assert (
            connection.execute(text("SELECT count(*) FROM research.bar_revisions")).scalar_one()
            == 5
        )
    print(
        "PASS readiness real PG/HTTP auth/idempotency/worker/reload/"
        "immutable-pages/cursors/no-provider"
    )


def test_readiness_api_real_postgresql():
    assert host("tests.integration.test_readiness_api").startswith("PASS readiness real PG/HTTP")


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            location = ",".join(f"{Path(f.filename).name}:{f.lineno}" for f in frames[-4:])
            print("FAIL " + type(error).__name__ + " " + location)
            raise SystemExit(1) from None
    else:
        print(host("tests.integration.test_readiness_api"))
