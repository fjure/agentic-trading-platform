"""RDR-04: tatsächlicher PG-Crashfortschritt, Fristlücken und harte Tagesbudgets."""

import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4


def test_collection_crash_generation_recovery_window_and_daily_budget():
    from tests.integration.test_readiness_storage import host

    assert host("tests.recovery.test_research_collection_recovery").startswith(
        "PASS collection recovery"
    )


def guest():
    from sqlalchemy import text

    from packages.research.collection import CollectionScheduler, CollectionStore, Dispatch
    from packages.research.jobs import ResearchJobs, enqueue_collection_job
    from packages.research.ports import SourceErrorCode
    from packages.research.registry import definitions, register
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.integration.test_backtest_jobs import role_engine

    engine = guest_engine()
    execution = role_engine(engine, "atl_execution")
    instrument = definitions()[0][0].instrument_id
    with engine.begin() as connection:
        register(connection)
        now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
        connection.execute(
            text(
                "UPDATE research.source_status SET state='IMPLEMENTED',license_status='CONFIRMED',"
                "policy_version='synthetic-v1' WHERE singleton"
            )
        )
        connection.execute(
            text("""
          INSERT INTO research.collection_plans
            (instrument_id,enabled,version,generation,status,reason,updated_at,scheduled_through_at)
          VALUES(:id,true,1,1,'ACTIVE','OK',:at,:at)
        """),
            dict(id=instrument, at=now - timedelta(hours=72)),
        )
    scheduler = CollectionScheduler(execution, enabled=True)
    assert scheduler.tick() >= 2
    with engine.connect() as connection:
        gap = (
            connection.execute(
                text("SELECT * FROM research.due_jobs WHERE reason='RECOVERY_WINDOW_EXCEEDED'")
            )
            .mappings()
            .one()
        )
        assert abs((gap["end_at"] - (now - timedelta(hours=24))).total_seconds()) < 10
        assert gap["start_at"] == now - timedelta(hours=72)
        assert gap["state"] == "BLOCKED" and gap["job_id"] is None
        calendar = (
            connection.execute(
                text(
                    "SELECT * FROM research.due_jobs WHERE purpose='CALENDAR' "
                    "ORDER BY due_at LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
    store = CollectionStore(execution)
    dispatch = store.admit(None, 1, due_id=calendar["due_id"], global_enabled=True)
    assert isinstance(dispatch, Dispatch)
    # Crash nach committed STARTED: kein Transport wird in diesem Test ausgeführt.
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.due_jobs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE due_id=:id"
            ),
            dict(id=dispatch.due_id),
        )
    scheduler.tick()
    assert store.admit(None, 1, due_id=dispatch.due_id, global_enabled=True) is False
    newer = store.admit(None, 2, due_id=dispatch.due_id, global_enabled=True)
    assert isinstance(newer, Dispatch) and newer.receipt_id != dispatch.receipt_id
    store.finish(newer, state="COMPLETED")
    # Abgeschlossen, aber diese höhere Fencenummer wurde noch nie gestartet.
    assert store.admit(None, 3, due_id=dispatch.due_id, global_enabled=True) is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM research.observation_receipts WHERE dispatch_id=:id "
                    "AND state='UNKNOWN'"
                ),
                dict(id=dispatch.receipt_id),
            ).scalar_one()
            == 1
        )
        count = connection.execute(text("SELECT count(*) FROM research.due_jobs")).scalar_one()
    # Wiederholung erstellt für erledigte Fälligkeiten keinen zweiten logischen Job.
    scheduler.tick()
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM research.due_jobs")).scalar_one() == count
        )
    # Ein alter Jobinhaber kann VOR seinem ersten STARTED pausieren. Nach Ablauf
    # und neuer Claim reicht eine bisher unbenutzte alte Fencenummer nicht aus.
    linked_due = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.due_jobs SET state='BLOCKED',reason='SYNTHETIC_FENCE_SETUP' "
                "WHERE state='PENDING'"
            )
        )
        linked_job = enqueue_collection_job(
            connection, linked_due, instrument, now - timedelta(hours=1), now
        )
        connection.execute(
            text("""
          INSERT INTO research.due_jobs
            (due_id,instrument_id,generation,purpose,due_at,start_at,end_at,job_id,state)
          VALUES(:due,:instrument,1,'INITIAL_BAR',:at,:start,:end,:job,'PENDING')
        """),
            dict(
                due=linked_due,
                instrument=instrument,
                at=now,
                start=now - timedelta(hours=1),
                end=now,
                job=linked_job,
            ),
        )
        started_before = connection.execute(
            text("SELECT count(*) FROM research.observation_receipts WHERE state='STARTED'")
        ).scalar_one()
    jobs = ResearchJobs(execution)
    stale = jobs.claim(uuid4())
    assert stale is not None and stale.job_id == linked_job
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.ingestion_jobs "
                "SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE job_id=:id"
            ),
            dict(id=linked_job),
        )
    current = jobs.claim(uuid4())
    assert current is not None and current.fence > stale.fence
    assert store.admit(linked_job, stale.fence, owner=stale.owner, global_enabled=True) is False
    assert store.admit(linked_job, current.fence, owner=uuid4(), global_enabled=True) is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.observation_receipts WHERE state='STARTED'")
            ).scalar_one()
            == started_before
        )
    admitted = store.admit(linked_job, current.fence, owner=current.owner, global_enabled=True)
    assert isinstance(admitted, Dispatch)
    jobs.block(current, SourceErrorCode.INVALID_REQUEST)
    assert store.finish(admitted, state="COMPLETED")
    assert store.admit(linked_job, stale.fence, owner=stale.owner, global_enabled=True) is False
    # Schon verbrauchte Tagesfälligkeiten bleiben nach Neustart wirksam.
    with engine.begin() as connection:
        for index in range(100):
            at = now - timedelta(seconds=index + 1)
            connection.execute(
                text("""
              INSERT INTO research.due_jobs
                (due_id,instrument_id,generation,purpose,due_at,start_at,end_at,state)
              VALUES(:id,:instrument,1,'RECOVERY',:at,:start,:end,'COMPLETED')
            """),
                dict(
                    id=uuid4(),
                    instrument=instrument,
                    at=at,
                    start=at - timedelta(minutes=15),
                    end=at,
                ),
            )
        # Neue Generation löst eine neue Aktivierungsfälligkeit aus, nicht das Tagesbudget.
        connection.execute(
            text(
                "UPDATE research.collection_plans SET generation=2,version=2,"
                "updated_at=clock_timestamp(),scheduled_through_at=NULL WHERE instrument_id=:id"
            ),
            dict(id=instrument),
        )
    scheduler.tick()
    with engine.connect() as connection:
        plan = (
            connection.execute(
                text("SELECT * FROM research.collection_plans WHERE instrument_id=:id"),
                dict(id=instrument),
            )
            .mappings()
            .one()
        )
        assert plan["status"] == "BLOCKED" and plan["reason"] == "DAILY_BUDGET_EXHAUSTED"
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM research.due_jobs WHERE generation=2 AND state<>'BLOCKED'"
                )
            ).scalar_one()
            == 0
        )
    assert store.admit(None, 3, due_id=calendar["due_id"], global_enabled=True) is False
    assert store.admit(None, 3, due_id=calendar["due_id"], global_enabled=False) is False
    engine.dispose()
    execution.dispose()
    print(
        "PASS collection recovery crash/receipt/fresh-fence/stale-prestart-owner/"
        "completed-unused-fence/dedupe/24h-gap/100day/globaldisable"
    )


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        own = [
            frame
            for frame in traceback.extract_tb(error.__traceback__)
            if frame.filename.startswith("/opt/lab/")
        ]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own[-4:])
        diagnostic = getattr(getattr(error, "orig", None), "diag", None)
        message = getattr(diagnostic, "message_primary", "")
        print("FAIL " + type(error).__name__ + " " + where + " " + (message or "")[:160])
        sys.exit(1)
