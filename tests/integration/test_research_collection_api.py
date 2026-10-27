"""RDR-06: echte PostgreSQL-Planlocks, CAS/Replay, ACL und authentifizierte API."""

from pathlib import Path


def test_collection_control_actual_postgresql_http_and_restore():
    from tests.integration.test_readiness_storage import host

    assert host("tests.integration.test_research_collection_api").startswith("PASS collection-api")


def guest():
    import json
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Event
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.models import BacktestBudgetExceeded
    from packages.research.collection import CollectionStore
    from packages.research.registry import definitions, register
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.collection_service import CollectionError, CollectionPlanService
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    control = role_engine(engine, "atl_control_api")
    execution = role_engine(engine, "atl_execution")
    with engine.begin() as connection:
        register(connection)
    instrument = definitions()[0][0].instrument_id
    other = definitions()[1][0].instrument_id
    service = CollectionPlanService(control)
    origin = "http://127.0.0.1:8000"
    auth = LocalAuth()
    http = TestClient(
        create_app(auth=auth, collection=service),
        base_url=origin,
        client=("127.0.0.1", 46120),
        raise_server_exceptions=False,
    )
    base = "/api/v1/research/collection/plans"
    assert http.get(base).status_code == 401
    assert (
        http.post(
            "/api/v1/session",
            json={"activationCode": auth.issue_activation_code()},
            headers={"Origin": origin, "Idempotency-Key": "activate-plans-001"},
        ).status_code
        == 204
    )
    page = http.get(base)
    assert page.status_code == 200, page.text
    plans = page.json()["items"]
    assert len(plans) == 2 and all(p["status"] == "DISABLED" and p["version"] == 0 for p in plans)
    assert all(not p["globalEnabled"] and not p["enabled"] for p in plans)
    assert all(p["metrics"] and not p["windows"]["complete"] for p in plans)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM research.collection_plans")).scalar_one()
            == 0
        )

    def put(identifier, enabled, version, key):
        return http.put(
            base + "/" + str(identifier),
            json={"enabled": enabled, "expectedVersion": version},
            headers={"Origin": origin, "Idempotency-Key": key},
        )

    assert put("not-a-uuid", True, 0, "plans-invalid-0001").status_code == 400
    assert put(uuid4(), True, 0, "plans-foreign-0001").status_code == 404
    active = put(instrument, True, 0, "plans-activate-0001")
    assert active.status_code == 200, active.text
    first = active.json()
    assert first["enabled"] and first["status"] == "BLOCKED" and first["version"] == 1
    assert "GLOBAL_DISABLED" in first["reasons"] and "RIGHTS_UNVERIFIED" in first["reasons"]
    assert first["providerRights"]["providerVerification"] == "UNVERIFIED"
    paused = put(instrument, False, 1, "plans-pause-000001")
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED" and paused.json()["version"] == 2
    assert put(instrument, True, 0, "plans-activate-0001").json() == first
    conflict = put(instrument, False, 0, "plans-activate-0001")
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    conflict = put(instrument, True, 1, "plans-stale-000001")
    assert conflict.status_code == 409 and conflict.json()["code"] == "VERSION_CONFLICT"

    def update(version, key):
        try:
            return service.update_plan(
                str(instrument),
                {"enabled": False, "expectedVersion": version},
                "local-operator",
                key,
                "a" * 32,
            )
        except CollectionError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda key: update(2, key), ["plans-cas-first-001", "plans-cas-second-01"])
        )
    assert sum(isinstance(result, dict) for result in results) == 1, results
    assert "VERSION_CONFLICT" in results
    current = next(
        value
        for value in service.read_plans({})["items"]
        if value["instrumentId"] == str(instrument)
    )
    assert current["version"] == 3 and current["generation"] == 3

    class Exhausted(CollectionPlanService):
        def _statistics(self, connection, instrument_id):
            raise BacktestBudgetExceeded("synthetic bounded projection")

    exhausted = Exhausted(control)
    limited = exhausted.update_plan(
        str(instrument),
        {"enabled": False, "expectedVersion": 3},
        "local-operator",
        "plans-budget-pause01",
        "b" * 32,
    )
    assert limited["status"] == "PAUSED" and limited["version"] == 4
    assert limited["metrics"] == [] and not limited["windows"]["complete"]
    try:
        exhausted.update_plan(
            str(instrument),
            {"enabled": True, "expectedVersion": 4},
            "local-operator",
            "plans-budget-enable1",
            "b" * 32,
        )
    except CollectionError as error:
        assert (error.status, error.code) == (503, "UNAVAILABLE")
    else:
        raise AssertionError("enable ignored metric budget")

    class InvalidSQL(CollectionPlanService):
        def _statistics(self, connection, instrument_id):
            connection.execute(text("SELECT 1/0"))

    for degraded in (exhausted, InvalidSQL(control)):
        with TestClient(
            create_app(auth=auth, collection=degraded),
            base_url=origin,
            client=("127.0.0.1", 46120),
            cookies=http.cookies,
            raise_server_exceptions=False,
        ) as fresh:
            projection = fresh.get(base)
            assert projection.status_code == 200, projection.text
            items = projection.json()["items"]
            assert len(items) == 2
            assert all("METRICS_UNAVAILABLE" in item["reasons"] for item in items)
            assert all(not item["metrics"] and not item["windows"]["complete"] for item in items)
            current = next(item for item in items if item["instrumentId"] == str(instrument))
            assert current["version"] == 4 and current["status"] == "PAUSED"

    repaired = InvalidSQL(control).update_plan(
        str(instrument),
        {"enabled": False, "expectedVersion": 4},
        "local-operator",
        "plans-sql-pause-001",
        "b" * 32,
    )
    assert repaired["version"] == 5 and repaired["status"] == "PAUSED"

    # Nur synthetischer Datenbankstatus; keine Providerkonfiguration/keine Zugangsdaten.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.source_status SET state='IMPLEMENTED',"
                "license_status='CONFIRMED' WHERE singleton"
            )
        )
    enabled = service.update_plan(
        str(instrument),
        {"enabled": True, "expectedVersion": 5},
        "local-operator",
        "plans-fixture-enable1",
        "c" * 32,
    )
    assert enabled["status"] == "ACTIVE" and enabled["version"] == 6
    due, retired = uuid4(), uuid4()
    with execution.begin() as connection:
        now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        for identifier, offset in ((due, 0), (retired, 1)):
            connection.execute(
                text(
                    "INSERT INTO research.due_jobs(due_id,instrument_id,generation,"
                    "purpose,due_at,start_at,end_at) VALUES(:id,:inst,6,'CALENDAR',"
                    ":at,:start,:end)"
                ),
                dict(
                    id=identifier,
                    inst=instrument,
                    at=now + timedelta(seconds=offset),
                    start=now,
                    end=now + timedelta(days=1),
                ),
            )
    dispatch = CollectionStore(execution).admit(None, 1, global_enabled=True, due_id=due)
    assert dispatch
    response = service.update_plan(
        str(instrument),
        {"enabled": False, "expectedVersion": 6},
        "local-operator",
        "plans-pause-inflight1",
        "d" * 32,
    )
    assert response["outstandingStartedDispatches"] == 1 and response["status"] == "PAUSED"
    assert not CollectionStore(execution).admit(None, 2, global_enabled=True, due_id=retired)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT state FROM research.due_jobs WHERE due_id=:id"), dict(id=retired)
            ).scalar_one()
            == "BLOCKED"
        )
    with execution.begin() as connection:
        CollectionStore.record(connection, dispatch, state="COMPLETED", body={"synthetic": True})
    current = next(
        value
        for value in service.read_plans({})["items"]
        if value["instrumentId"] == str(instrument)
    )
    assert current["outstandingStartedDispatches"] == 0
    assert (
        service.update_plan(
            str(instrument),
            {"enabled": False, "expectedVersion": 6},
            "local-operator",
            "plans-pause-inflight1",
            "d" * 32,
        )
        == response
    )

    # Eine echte fremde SQLsitzung hält denselben Planlock bis zum Commit.
    locked, release = Event(), Event()

    def hold_plan():
        with execution.begin() as connection:
            connection.execute(
                text("SELECT 1 FROM research.collection_plans WHERE instrument_id=:id FOR UPDATE"),
                dict(id=other),
            )
            locked.set()
            assert release.wait(5)

    service.update_plan(
        str(other),
        {"enabled": False, "expectedVersion": 0},
        "local-operator",
        "plans-other-pause01",
        "e" * 32,
    )
    with ThreadPoolExecutor(2) as pool:
        lock = pool.submit(hold_plan)
        assert locked.wait(5)
        pending = pool.submit(
            service.update_plan,
            str(other),
            {"enabled": False, "expectedVersion": 1},
            "local-operator",
            "plans-other-pause02",
            "e" * 32,
        )
        try:
            pending.result(timeout=0.1)
        except TimeoutError:
            pass
        else:
            raise AssertionError("pause bypassed dispatch planlock")
        release.set()
        lock.result(5)
        assert pending.result(5)["version"] == 2

    # Runtimegruppen dürfen weder Planport noch gespeicherte Antworten verändern.
    for role in ("atl_execution", "atl_backtest_worker", "atl_evidence_importer"):
        selected = role_engine(engine, role)
        try:
            with selected.begin() as connection:
                connection.execute(
                    text(
                        "SELECT research.set_collection_plan(:id,false,7,"
                        "'local-operator','plans-forbidden-001',:trace,CAST(:stats AS jsonb))"
                    ),
                    dict(
                        id=instrument,
                        trace="f" * 32,
                        stats=json.dumps({k: response[k] for k in ("metrics", "windows")}),
                    ),
                )
        except DBAPIError:
            pass
        else:
            raise AssertionError("foreign role changed plan")
        finally:
            selected.dispose()
    for command in (
        "UPDATE research.collection_plans SET enabled=true",
        "UPDATE research.collection_commands SET response='{}'",
        "DELETE FROM research.collection_commands",
    ):
        try:
            with control.begin() as connection:
                connection.execute(text(command))
        except DBAPIError:
            pass
        else:
            raise AssertionError("control bypassed immutable command port")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM backtest.runs")).scalar_one() == 0
        assert (
            connection.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one() == 0
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.collection_commands")
            ).scalar_one()
            == 9
        )
        assert (
            connection.execute(text("SELECT count(*) FROM research.plan_revisions")).scalar_one()
            == 9
        )
    http.close()
    control.dispose()
    execution.dispose()
    engine.dispose()
    print(
        "PASS collection-api HTTP/defaults/CAS/replay/budget-read-pause/SQL-rollback/"
        "planlock/ACL/noorders"
    )


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = [
            frame
            for frame in traceback.extract_tb(error.__traceback__)
            if frame.filename.startswith("/opt/lab/")
        ]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-5:])
        primary = getattr(
            getattr(getattr(error, "orig", None), "diag", None), "message_primary", ""
        )
        # Ausschließlich synthetische Testfehler; keine Verbindungsdaten ausgeben.
        print("FAIL " + type(error).__name__ + " " + where + " " + (primary or "")[:180])
        raise SystemExit(1) from None
