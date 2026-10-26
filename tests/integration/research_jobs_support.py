"""Real PostgreSQL job fencing, rollback and redaction checks for the isolated guest."""

from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.domain.models import utc_string
from packages.research.jobs import ResearchJobError, ResearchJobs
from packages.research.ports import SourceErrorCode


def verify(engine, execution, control, instrument, start):
    from datetime import timedelta

    requests = ResearchJobs(control)
    jobs = ResearchJobs(execution)
    payload = dict(
        instrumentId=str(instrument.instrument_id),
        start=utc_string(start),
        end=utc_string(start + timedelta(hours=1)),
        interval="15Min",
        feed="iex",
        adjustment="raw",
        mode="BACKFILL",
    )
    first = requests.enqueue(payload, "local-operator", "research-test-key-0001", "a" * 32)
    repeated = requests.enqueue(payload, "local-operator", "research-test-key-0001", "b" * 32)
    assert first == repeated
    try:
        requests.enqueue(
            {**payload, "mode": "UPDATE"}, "local-operator", "research-test-key-0001", "a" * 32
        )
    except ResearchJobError as error:
        assert (error.status, error.code) == (409, "IDEMPOTENCY_CONFLICT")
    else:
        raise AssertionError("changed payload accepted under old key")
    lease = jobs.claim(uuid4())
    assert lease and str(lease.job_id) == first["jobId"]
    assert jobs.claim(uuid4()) is None
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.ingestion_jobs SET lease_until=clock_timestamp()-"
                "interval '1 second' WHERE job_id=:id"
            ),
            dict(id=lease.job_id),
        )
    newer = jobs.claim(uuid4())
    assert newer and newer.fence > lease.fence
    called = []
    empty = sha256(b"").hexdigest()
    try:
        jobs.commit_page(lease, "a" * 64, empty, None, lambda conn: called.append(True))
    except ResearchJobError as error:
        assert error.code == "LEASE_LOST"
    else:
        raise AssertionError("old lease accepted")
    assert not called

    def crash(connection):
        connection.execute(
            text(
                "INSERT INTO research.page_receipts"
                "(job_id,partition,cursor_hash,content_hash) VALUES(:id,0,:hash,:hash)"
            ),
            dict(id=newer.job_id, hash="b" * 64),
        )
        raise RuntimeError("synthetic-private-error-canary")

    try:
        jobs.commit_page(newer, "a" * 64, empty, "synthetic-cursor-1", crash)
    except ResearchJobError as error:
        assert error.code == "UNAVAILABLE" and error.__context__ is None
    else:
        raise AssertionError("crashing page accepted")
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.page_receipts WHERE job_id=:id"),
                dict(id=newer.job_id),
            ).scalar_one()
            == 0
        )
    result = jobs.retry(newer, SourceErrorCode.RATE_LIMITED, retry_after=3)
    assert result["status"] == "RETRY_WAIT" and result["retryAt"] and result["attempts"] == 1
    assert "private_cursor" not in result and "synthetic-private" not in str(result)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research.ingestion_jobs SET retry_at=clock_timestamp()-interval"
                " '1 second' WHERE job_id=:id"
            ),
            dict(id=newer.job_id),
        )
    retry = jobs.claim(uuid4())
    assert retry and retry.fence > newer.fence
    committed = jobs.commit_page(retry, "c" * 64, empty, None, lambda connection: 0)
    assert committed["status"] == "SUCCEEDED" and committed["pages"] == 1
    # Control/Worker cannot inspect private tokens or change progress.
    for role in ["atl_control_api", "atl_worker", "atl_watchdog"]:
        for statement in [
            "SELECT private_cursor FROM research.ingestion_jobs",
            "UPDATE research.ingestion_jobs SET status='SUCCEEDED'",
            "INSERT INTO research.page_receipts SELECT * FROM research.page_receipts",
        ]:
            try:
                with engine.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE " + role))
                    connection.execute(text(statement))
            except DBAPIError:
                pass
            else:
                raise AssertionError("private job rights widened")
    assert requests.read_job(UUID(first["jobId"]))["status"] == "SUCCEEDED"
    assert len(requests.list_jobs().items) == 1
