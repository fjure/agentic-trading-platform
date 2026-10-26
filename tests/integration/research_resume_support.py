"""Real PostgreSQL operator-resume, source-gate and idempotency checks."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.domain.models import utc_string
from packages.research.jobs import ResearchJobError, ResearchJobs
from packages.research.ports import SourceErrorCode


def verify(engine, execution, control, instrument, start):
    requests = ResearchJobs(control)
    jobs = ResearchJobs(execution)
    end = start + timedelta(hours=1)

    def payload(mode="BACKFILL"):
        return dict(
            instrumentId=str(instrument.instrument_id),
            start=utc_string(start),
            end=utc_string(end),
            interval="15Min",
            feed="iex",
            adjustment="raw",
            mode=mode,
        )

    def enqueue(key):
        return requests.enqueue(payload(), "local-operator", key, "c" * 32)

    original = enqueue("research-resume-create-01")
    blocked_keys = [
        ("research-gate-disabled-01", "DISABLED", "CONFIRMED", "UNAVAILABLE"),
        ("research-gate-license-01", "IMPLEMENTED", "UNKNOWN", "BLOCKED_LICENSE"),
        ("research-gate-credentials-1", "BLOCKED", "CONFIRMED", "BLOCKED_CREDENTIALS"),
    ]
    # Exercise all three admission decisions through the actual control role.
    gated = {}
    try:
        for key, state, license_status, expected in blocked_keys:
            with execution.begin() as connection:
                connection.execute(
                    text("UPDATE research.source_status SET state=:state,license_status=:license"),
                    dict(state=state, license=license_status),
                )
            result = enqueue(key)
            assert result["status"] == "BLOCKED" and result["errorCode"] == expected
            gated[key] = result["jobId"]
    finally:
        with execution.begin() as connection:
            connection.execute(text("""UPDATE research.source_status
              SET state='IMPLEMENTED',license_status='CONFIRMED',
                  policy_version='offline-pg-fixture-v1'
              WHERE singleton"""))

    resume_key = "research-resume-action-01"
    try:
        requests.resume(
            UUID(original["jobId"]),
            "local-operator",
            "research-resume-create-01",
            "d" * 32,
        )
    except ResearchJobError as error:
        assert (error.status, error.code) == (409, "IDEMPOTENCY_CONFLICT")
    else:
        raise AssertionError("create key was accepted for resume")

    # Simulate durable partial progress, including a private provider cursor.
    with engine.begin() as connection:
        connection.execute(
            text("""UPDATE research.ingestion_jobs
              SET status='PARTIAL',error_code='BUDGET_EXHAUSTED',
              pages=7,bars=91,run_pages=1000,private_cursor='private-provider-cursor',partition=3
              WHERE job_id=:id"""),
            dict(id=UUID(original["jobId"])),
        )
    resumed = requests.resume(
        UUID(original["jobId"]), "local-operator", resume_key, "e" * 32
    )
    replay = requests.resume(
        UUID(original["jobId"]), "local-operator", resume_key, "f" * 32
    )
    assert replay["jobId"] == resumed["jobId"] == original["jobId"]
    assert resumed["status"] == "ACCEPTED" and resumed["pages"] == 7 and resumed["bars"] == 91
    assert resumed["attempts"] == 0 and "private_cursor" not in resumed
    with engine.connect() as connection:
        row = connection.execute(
            text("""SELECT private_cursor,partition,run_pages FROM research.ingestion_jobs
              WHERE job_id=:id"""),
            dict(id=UUID(original["jobId"])),
        ).one()
        assert row == ("private-provider-cursor", 3, 0)

    # One Idempotency-Key namespace covers create and resume operations in both directions.
    try:
        requests.enqueue(payload("UPDATE"), "local-operator", resume_key, "1" * 32)
    except ResearchJobError as error:
        assert (error.status, error.code) == (409, "IDEMPOTENCY_CONFLICT")
    else:
        raise AssertionError("resume key was accepted for create")

    try:
        requests.resume(
            UUID(original["jobId"]),
            "local-operator",
            "research-resume-other-01",
            "2" * 32,
        )
    except ResearchJobError as error:
        assert (error.status, error.code) == (409, "INVALID_REQUEST")
    else:
        raise AssertionError("duplicate resume of an accepted job was accepted")

    with control.connect() as connection:
        try:
            connection.execute(text("SELECT * FROM research.resume_requests")).all()
        except DBAPIError:
            pass
        else:
            raise AssertionError("control API can inspect private resume receipt table")
    with execution.connect() as connection:
        assert connection.execute(text("""SELECT has_function_privilege(
          'atl_control_api','research.resume_job(uuid,text,text,text)','EXECUTE')""")).scalar_one()
        assert not connection.execute(text("""SELECT has_function_privilege(
          'atl_execution','research.resume_job(uuid,text,text,text)','EXECUTE')""")).scalar_one()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status,error_code FROM research.ingestion_jobs WHERE job_id=:id"),
            dict(id=UUID(gated[blocked_keys[0][0]])),
        ).one() == ("BLOCKED", "UNAVAILABLE")

    lease = jobs.claim(UUID("00000000-0000-4000-8000-000000000001"))
    # Earlier integration sections intentionally leave admitted requests in the
    # shared guest. Settle them through the real fenced port before this job.
    for _ in range(20):
        if lease is None or lease.job_id == UUID(original["jobId"]):
            break
        jobs.block(lease, SourceErrorCode.UNAVAILABLE)
        lease = jobs.claim(UUID("00000000-0000-4000-8000-000000000001"))
    assert lease and lease.job_id == UUID(original["jobId"])
    for attempt in range(4):
        result = jobs.retry(lease, SourceErrorCode.RATE_LIMITED)
        if attempt < 3:
            assert result["status"] == "RETRY_WAIT"
            with engine.begin() as connection:
                connection.execute(
                    text("""UPDATE research.ingestion_jobs SET retry_at=clock_timestamp()
                      -interval '1 second' WHERE job_id=:id"""),
                    dict(id=lease.job_id),
                )
            lease = jobs.claim(UUID("00000000-0000-4000-8000-000000000002"))
            assert lease and lease.job_id == UUID(original["jobId"])
        else:
            assert result["status"] == "PARTIAL" and result["attempts"] == 3
