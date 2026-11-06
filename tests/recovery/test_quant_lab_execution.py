"""Real restart after the selection and child-publication commit boundaries."""

import sys
import traceback
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


def verify_quant_restart(engine):
    from sqlalchemy import text

    from packages.backtesting.experiment_storage import ExperimentStorage
    from services.worker.backtesting import BacktestWorker
    from services.worker.quant_lab import QuantLabWorker, simulate_quant
    from tests.integration.test_backtest_jobs import role_engine
    from tests.integration.test_quant_lab_worker import worker_fixture

    class ProcessLost(BaseException):
        pass

    control, batch = worker_fixture(engine, no_selection=True)
    batch_id = batch["batch_id"]
    worker_engine = role_engine(engine, "atl_backtest_worker")
    version = batch["code_version"]

    def simulate_outside_transaction(*args, **kwargs):
        assert worker_engine.pool.checkedout() == 0
        return simulate_quant(*args, **kwargs)

    def tick(worker):
        with patch("services.worker.quant_lab.simulate_quant", simulate_outside_transaction):
            return worker.tick()

    def state():
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status,attempts,bar_steps FROM backtest.experiment_batches "
                    "WHERE batch_id=:id"
                ),
                {"id": batch_id},
            ).one()
            selections = connection.execute(
                text(
                    "SELECT scope,fingerprint,body->>'frozenAt' FROM "
                    "backtest.experiment_selections WHERE batch_id=:id ORDER BY scope"
                ),
                {"id": batch_id},
            ).all()
            links = connection.execute(
                text(
                    "SELECT phase_key,role,fingerprint,run_id,reliability_report_id FROM "
                    "backtest.experiment_result_links WHERE batch_id=:id "
                    "ORDER BY phase_key,role,candidate_id"
                ),
                {"id": batch_id},
            ).all()
        return row, selections, links

    def expire():
        # Test-only clock simulation in the disposable migration-owner database.
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE backtest.experiment_batches DISABLE TRIGGER a_quant_batch")
            )
            connection.execute(
                text(
                    "UPDATE backtest.experiment_batches SET "
                    "lease_until=clock_timestamp()-interval '1 second' WHERE batch_id=:id"
                ),
                {"id": batch_id},
            )
            connection.execute(
                text("ALTER TABLE backtest.experiment_batches ENABLE TRIGGER a_quant_batch")
            )

    def run_until_loss(worker):
        try:
            tick(worker)
        except ProcessLost:
            return
        raise AssertionError("configured commit boundary was not reached")

    first = QuantLabWorker(worker_engine, owner=uuid4(), code_version=version)
    freeze = first.storage.freeze_selections

    def stop_after_selection(*args, **kwargs):
        freeze(*args, **kwargs)
        raise ProcessLost

    first.storage.freeze_selections = stop_after_selection
    run_until_loss(first)
    phase_one, frozen, analysis_links = state()
    assert phase_one.status == "RUNNING" and phase_one.attempts == 1
    assert {item.scope for item in frozen} == {"final", "0"}
    assert analysis_links and all(
        link.phase_key.endswith((":training", ":validation")) for link in analysis_links
    )
    expire()

    second = QuantLabWorker(worker_engine, owner=uuid4(), code_version=version)
    publish_child = second.storage.publish_child

    def stop_after_test_child(lease, request, *args, **kwargs):
        result = publish_child(lease, request, *args, **kwargs)
        if request["phaseKey"].endswith(":test"):
            raise ProcessLost
        return result

    second.storage.publish_child = stop_after_test_child
    run_until_loss(second)
    phase_two, frozen_again, partial_links = state()
    assert phase_two.status == "RUNNING" and phase_two.attempts == 2
    assert frozen_again == frozen and set(analysis_links) <= set(partial_links)
    assert any(
        row.phase_key.endswith(":test") and row.run_id and row.reliability_report_id
        for row in partial_links
    )
    assert phase_two.bar_steps >= phase_one.bar_steps
    expire()

    third = BacktestWorker(worker_engine, enabled=True, code_version=version)
    publish = third.quant_lab_worker.storage.publish

    def stop_after_report(*args, **kwargs):
        result = publish(*args, **kwargs)
        if kwargs.get("status", "SUCCEEDED") == "SUCCEEDED":
            raise ProcessLost
        return result

    third.quant_lab_worker.storage.publish = stop_after_report
    run_until_loss(third)
    final, frozen_final, final_links = state()
    assert final.status == "SUCCEEDED" and final.attempts == 3
    assert final.bar_steps >= phase_two.bar_steps and frozen_final == frozen
    assert set(partial_links) <= set(final_links)
    assert all(
        row.run_id is None
        for row in final_links
        if row.role == "SEARCH" and row.phase_key.endswith((":test", ":holdout"))
    )
    assert ExperimentStorage(worker_engine).claim(uuid4()) is None
    control.engine.dispose()
    worker_engine.dispose()


def test_quant_lab_execution_restart_actual_postgresql():
    from tests.integration.test_quant_lab_worker import host

    assert host("tests.recovery.test_quant_lab_execution").endswith("/restart/restore")


if __name__ == "__main__" and sys.argv[1:] == ["guest"]:
    from tests.integration.test_quant_lab_worker import guest_engine

    try:
        engine = guest_engine()
        verify_quant_restart(engine)
        engine.dispose()
        print("PASS quant worker/selection/child/restart")
    except Exception as error:
        locations = ",".join(
            f"{Path(frame.filename).name}:{frame.lineno}"
            for frame in traceback.extract_tb(error.__traceback__)[-5:]
        )
        print(f"FAIL {type(error).__name__} {locations}")
        raise SystemExit(1) from None
