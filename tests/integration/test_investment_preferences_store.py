"""Real temporary PostgreSQL preferences, concurrency, recovery and least privilege."""

import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.persistence import PostgresLedger
from services.control_api.investment_preferences_store import (
    InvestmentPreferencesStore,
    PreferencesError,
)
from tests.unit.test_investment_preferences_store import preference_payload


def test_preferences_store_actual_postgresql():
    from tests.integration.test_backtest_dataset import host

    assert host("tests.integration.test_investment_preferences_store") == "PREFERENCES_SQL_PASS"


def guest():
    from tests.integration.test_backtest_dataset import guest_engine

    engine = guest_engine()
    ledger = PostgresLedger(engine=engine)

    class ControlLedger:
        @contextmanager
        def transaction(self):
            with ledger.transaction() as tx:
                tx.connection.exec_driver_sql("SET LOCAL ROLE atl_control_api")
                yield tx

    store = InvestmentPreferencesStore(ControlLedger())
    initial = store.read("operator-a")
    assert initial["version"] == 0 and initial["updatedAt"] is None
    assert initial["contextReference"] == {"context": "FAKE", "namespace": "fake"}
    assert len(initial["onboardingSteps"]) == 5
    with engine.begin() as connection:
        assert connection.execute(text(
            "SELECT count(*) FROM investment_experience.preferences"
        )).scalar_one() == 0  # GET did not install defaults.
    payload = preference_payload()
    payload["experienceLevel"] = "EXPERIENCED"
    payload["lossTolerance"] = "HIGH"
    payload["onboardingSteps"] = [{
        "step": "EXPERIENCE", "state": "SKIPPED", "updatedAt": "2000-01-01T00:00:00Z",
    }]
    assert store.replay("operator-a", payload, "preferences-key-0001") is None
    changed = store.update("operator-a", payload, "preferences-key-0001")
    assert store.replay("operator-a", payload, "preferences-key-0001") == changed
    assert store.replay("operator-b", payload, "preferences-key-0001") is None
    assert changed["version"] == 1
    assert changed["onboardingSteps"][0]["updatedAt"] == changed["updatedAt"]
    assert changed["updatedAt"] != "2000-01-01T00:00:00Z"
    assert store.update("operator-a", payload, "preferences-key-0001") == changed
    assert store.read("operator-b")["version"] == 0
    # New service instance reads exactly the same persisted record.
    assert InvestmentPreferencesStore(ControlLedger()).read("operator-a") == changed
    for revised, key, code in [
        (payload | {"horizon": "LONG"}, "preferences-key-0001", "IDEMPOTENCY_CONFLICT"),
        (payload, "preferences-key-0002", "VERSION_CONFLICT"),
    ]:
        try:
            store.update("operator-a", revised, key)
        except PreferencesError as exc:
            assert (exc.status, exc.code) == (409, code)
        else:
            raise AssertionError("conflict accepted")
    try:
        store.replay("operator-a", payload | {"horizon": "LONG"}, "preferences-key-0001")
    except PreferencesError as exc:
        assert (exc.status, exc.code) == (409, "IDEMPOTENCY_CONFLICT")
    else:
        raise AssertionError("replay payload collision accepted")

    def competing(index):
        try:
            body = payload | {"expectedVersion": 1, "horizon": "LONG" if index else "SHORT"}
            return store.update("operator-a", body, f"concurrent-key-000{index}")
        except PreferencesError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(competing, [0, 1]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("VERSION_CONFLICT") == 1
    current = store.read("operator-a")
    assert current["version"] == 2
    assert current["onboardingSteps"][0] == changed["onboardingSteps"][0]
    reset = store.update("operator-a", payload | {"expectedVersion": 2, "reset": True},
                         "preferences-reset-01")
    assert reset["version"] == 3 and reset["experienceLevel"] == "UNSPECIFIED"
    assert reset["contextReference"] == initial["contextReference"]
    assert all(step["state"] == "UNSEEN" and step["updatedAt"] is None
               for step in reset["onboardingSteps"])
    assert store.update("operator-a", payload, "preferences-key-0001") == changed
    assert store.replay("operator-a", payload, "preferences-key-0001") == changed
    assert store.read("operator-a") == reset  # Old retry cannot restore old preferences.
    assert store.update("operator-b", payload, "preferences-key-0001")["version"] == 1
    # A concurrent identical retry produces one persisted version and response.
    with ThreadPoolExecutor(max_workers=2) as pool:
        replays = list(pool.map(lambda _: store.update(
            "operator-c", payload, "preferences-key-0001",
        ), range(2)))
    assert replays[0] == replays[1] and replays[0]["version"] == 1
    updated_step = store.update("operator-c", payload | {
        "expectedVersion": 1, "experienceLevel": "BEGINNER",
    }, "preferences-key-0002")
    assert updated_step["onboardingSteps"][0]["updatedAt"] == updated_step["updatedAt"]
    assert updated_step["onboardingSteps"][0]["updatedAt"] != replays[0]["updatedAt"]

    for role in ("atl_worker", "atl_execution", "atl_watchdog", "atl_backtest_worker",
                 "atl_agent_research", "atl_agent_quant_admission"):
        with engine.begin() as connection:
            assert not connection.execute(text(
                "SELECT has_schema_privilege(:role,'investment_experience','USAGE')"
            ), {"role": role}).scalar_one()
    for statement in (
        "SELECT * FROM investment_experience.preferences",
        "DELETE FROM investment_experience.preference_receipts",
        "UPDATE investment_experience.preferences SET version=99",
    ):
        try:
            with ControlLedger().transaction() as tx:
                tx.connection.exec_driver_sql(statement)
        except DBAPIError:
            pass
        else:
            raise AssertionError("control role escaped narrow functions")
    with ControlLedger().transaction() as tx:
        invalid = deepcopy(payload)
        invalid.pop("expectedVersion")
        invalid.pop("reset")
        invalid["lossTolerance"] = "BUY_NOW"
        import json
        result = tx.connection.execute(text("""
            SELECT investment_experience.update_preferences(
              'operator-a',:key,:hash,3,false,CAST(:body AS jsonb))
        """), {"key": "a" * 64, "hash": "b" * 64, "body": json.dumps(invalid)}).scalar_one()
        assert result == {"error": "INVALID_REQUEST"}
    engine.dispose()
    print("PREFERENCES_SQL_PASS")


if __name__ == "__main__" and sys.argv[-1] == "guest":
    guest()
