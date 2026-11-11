"""APF-D01 durable calendar recovery with actual isolated PostgreSQL."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from packages.agent_research.discovery.schedule import DailySchedule, ScheduleDenied, ScheduleStore
from packages.agent_research.registry import QUANT_RESEARCH_V1
from packages.agent_research.runs import RunKind, RunStore
from packages.domain.models import utc_string
from tests.integration.test_backtest_jobs import role_engine
from tests.unit.test_discovery_schedule import schedule_document

_ROLE = "atl_agent_research"
_PROOF = "PASS daily schedule restart/latest-only/concurrency/rollback/pause/ACL"
_TABLES = tuple("agent_research." + table for table in (
    "discovery_schedules", "discovery_schedule_events", "discovery_occurrences", "runs",
    "run_events",
))


def test_schedule_actual_postgresql():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    prior_groups, prior_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*prior_groups, _ROLE, "atl_agent_quant_admission")
        harness.TABLES = (*prior_tables, *_TABLES)
        assert harness.host("tests.recovery.test_discovery_schedule_db") == _PROOF + "/restore"
    finally:
        ledger_support.GROUPS = prior_groups
        harness.TABLES = prior_tables


def _simulate_downtime(owner):
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.discovery_schedules
            SET active_since=clock_timestamp()-interval '5 days'
        """))


def guest():
    from alembic import command
    from alembic.config import Config

    payload = json.load(sys.stdin)
    owner = create_engine(URL.create(
        "postgresql+psycopg", username=payload["role"], password=payload["password"],
        host="postgres", port=5432, database=payload["database"], query={"connect_timeout": "5"},
    ), hide_parameters=True)
    worker = role_engine(owner, _ROLE)
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0045_discovery_schedules")
            now = connection.scalar(text("SELECT clock_timestamp()")).astimezone(UTC)
        local_time = (now - timedelta(hours=1)).strftime("%H:%M")
        document = schedule_document("UTC", local_time, "DISABLED")
        identifier = document["scheduleId"]
        admin, runtime = ScheduleStore(owner), ScheduleStore(worker)
        admin.create(DailySchedule.parse(document))
        assert runtime.read(identifier).state == "DISABLED"

        def enqueue(connection, schedule, occurrence, *, epoch):
            return RunStore.enqueue_in_transaction(
                connection, actor="local-schedule", key=f"schedule:{occurrence.local_date}",
                kind=RunKind.SCHEDULED, input_ref=f"{schedule.schedule_id}:{epoch}",
                trace_id="b" * 32, agent=QUANT_RESEARCH_V1,
            )

        assert runtime.tick(identifier, enqueue=enqueue) is None
        active = DailySchedule.parse(document | {"version": 2, "state": "ACTIVE"})
        admin.replace(active, expected_version=1)
        _simulate_downtime(owner)
        expected = active.latest_due(now)

        def fail_after_enqueue(*args, **kwargs):
            enqueue(*args, **kwargs)
            raise RuntimeError("synthetic callback failure")

        with pytest.raises(RuntimeError, match="synthetic callback failure"):
            runtime.tick(identifier, enqueue=fail_after_enqueue)
        with worker.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM agent_research.runs")) == 0
            assert connection.scalar(text("SELECT count(*) FROM agent_research.run_events")) == 0
            assert connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_occurrences
            """)) == 0

        barrier = Barrier(2, timeout=10)

        def tick():
            barrier.wait()
            return ScheduleStore(worker).tick(identifier, enqueue=enqueue)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = (pool.submit(tick) for _ in range(2))
            occurrences = [first.result(timeout=20), second.result(timeout=20)]
        assert expected is not None and occurrences.count(expected) == 1
        assert occurrences.count(None) == 1
        restarted = ScheduleStore(worker)
        assert restarted.tick(identifier, enqueue=enqueue) is None
        current = restarted.read(identifier).to_document()
        assert current["lastExecutionAt"] == utc_string(expected.scheduled_at)
        assert current["nextExecutionAt"] == utc_string(active.next_after(now).scheduled_at)
        with worker.begin() as connection:
            restarted.require_active(connection, identifier, 2)
            occurred = connection.execute(text("""
                SELECT local_date,scheduled_at,schedule_version,schedule_epoch,resolution
                FROM agent_research.discovery_occurrences
            """)).one()
            assert tuple(occurred) == (
                expected.local_date, expected.scheduled_at, 2, 2, expected.resolution,
            )

        paused = DailySchedule.parse(document | {"version": 3, "state": "PAUSED"})
        admin.replace(paused, expected_version=2)
        assert restarted.tick(identifier, enqueue=enqueue) is None
        with worker.begin() as connection, pytest.raises(ScheduleDenied, match="SCHEDULE_STALE"):
            restarted.require_active(connection, identifier, 2)
        resumed = DailySchedule.parse(document | {"version": 4, "state": "ACTIVE"})
        admin.replace(resumed, expected_version=3)
        _simulate_downtime(owner)
        assert restarted.tick(identifier, enqueue=enqueue) is None
        with worker.begin() as connection:
            restarted.require_active(connection, identifier, 4)
        delta = -1 if local_time == "23:59" else 1
        changed_time = (expected.scheduled_at + timedelta(minutes=delta)).strftime("%H:%M")
        changed = DailySchedule.parse(document | {
            "version": 5, "state": "ACTIVE", "localTime": changed_time,
        })
        assert changed.latest_due(now).local_date == expected.local_date
        admin.replace(changed, expected_version=4)
        _simulate_downtime(owner)
        assert restarted.tick(identifier, enqueue=enqueue) is None
        with worker.begin() as connection, pytest.raises(DBAPIError) as error:
            connection.execute(text("UPDATE agent_research.discovery_schedules SET state='PAUSED'"))
        assert error.value.orig.sqlstate == "42501"
    finally:
        worker.dispose()
        owner.dispose()


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-5:])
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", "")
        print(f"FAIL {type(error).__name__} {sqlstate} {where}")
        raise SystemExit(1) from None
    print(_PROOF)
