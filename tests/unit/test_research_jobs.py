"""Pure request/retry/budget invariants; SQL atomicity and fencing require PG tests."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from packages.research.jobs import (
    Lease,
    ResearchJobError,
    ResearchJobs,
    budget_exhausted,
    canonical_request_hash,
    retry_plan,
    validated_request,
)
from packages.research.ports import SourceError, SourceErrorCode

NOW = datetime(2026, 10, 25, tzinfo=UTC)


def request(**changes):
    return {
        "instrumentId": "ce85d363-e845-4b4a-9b11-6de080bb43da",
        "start": "2026-10-23T13:30:00Z",
        "end": "2026-10-23T14:00:00Z",
        "interval": "15Min",
        "mode": "BACKFILL",
        "feed": "iex",
        "adjustment": "raw",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"apiKey": "synthetic-canary"},
        {"privateCursor": "canary"},
        {"url": "https://evil.invalid"},
        {"instrumentId": "SPY"},
        {"instrumentId": "CE85D363-E845-4B4A-9B11-6DE080BB43DA"},
        {"start": "2026-10-23T13:30:00+00:00"},
        {"start": "2026-10-23T13:30:00"},
        {"start": "2026-10-23T13:30:00.0000001Z"},
        {"start": "2026-02-30T00:00:00Z"},
        {"end": "2026-10-23T13:30:00Z"},
        {"end": "2028-10-23T13:30:00Z"},
        {"interval": "1Min"},
        {"interval": []},
        {"mode": "LIVE"},
        {"feed": "sip"},
        {"adjustment": "split"},
    ],
)
def test_public_request_rejects_transport_data_and_ambiguous_values(changes):
    with pytest.raises(ResearchJobError) as failure:
        validated_request(request(**changes))
    assert failure.value.status == 400 and str(failure.value) == "INVALID_REQUEST"
    assert failure.value.__context__ is None


def test_canonical_hash_binds_payload_and_normalizes_timestamp_precision():
    first = request()
    equivalent = request(start="2026-10-23T13:30:00.000000Z")
    assert canonical_request_hash(first) == canonical_request_hash(equivalent)
    assert canonical_request_hash(dict(reversed(list(first.items())))) == canonical_request_hash(
        first
    )
    for changes in (
        {"mode": "UPDATE"},
        {"interval": "1Day"},
        {"end": "2026-10-23T14:00:00.000001Z"},
    ):
        assert canonical_request_hash(request(**changes)) != canonical_request_hash(first)
    assert validated_request(equivalent)["start"] == first["start"]


def test_three_durable_retries_then_partial_without_implicit_restart():
    attempts = 0
    plans = []
    for _ in range(4):
        plan = retry_plan(attempts)
        plans.append(plan)
        attempts = plan.attempts
    assert [plan.delay_seconds for plan in plans] == [1, 2, 4, None]
    assert [plan.status for plan in plans] == ["RETRY_WAIT"] * 3 + ["PARTIAL"]
    assert [plan.attempts for plan in plans] == [1, 2, 3, 3]
    assert retry_plan(3).status == "PARTIAL"


def test_retry_after_can_delay_but_never_shorten_backoff_and_is_capped():
    assert retry_plan(2, 0).delay_seconds == 4
    assert retry_plan(0, 30).delay_seconds == 30
    assert retry_plan(0, 999999999).delay_seconds == 60
    for attempts, after in [(True, None), (4, None), (0, -1), (0, 1.5)]:
        with pytest.raises(ResearchJobError):
            retry_plan(attempts, after)


def test_page_and_time_budgets_are_inclusive_per_run():
    assert not budget_exhausted(999, NOW, NOW + timedelta(minutes=15, microseconds=-1))
    assert budget_exhausted(1000, NOW, NOW)
    assert budget_exhausted(0, NOW, NOW + timedelta(minutes=15))
    # An explicitly resumed run gets a fresh budget even if lifetime pages exceed 1000.
    assert not budget_exhausted(0, NOW + timedelta(hours=1), NOW + timedelta(hours=1))


def test_lease_has_immutable_request_and_hidden_private_resume_state():
    lease = Lease(uuid4(), uuid4(), 1, request(), 0, 2000, 0, 0, NOW, "private-page-canary")
    assert "private-page-canary" not in repr(lease)
    with pytest.raises(TypeError):
        lease.request["feed"] = "sip"
    with pytest.raises(FrozenInstanceError):
        lease.fence = 2
    with pytest.raises(ResearchJobError):
        replace(lease, private_cursor="page&secret=canary")


def test_bad_admission_and_reads_fail_before_database_access():
    jobs = ResearchJobs(None)
    for actor, key, trace in [
        ("remote", "valid-key-123456789", "a" * 32),
        ("local-operator", "short", "a" * 32),
        ("local-operator", "valid-key-123456789", "raw-trace"),
    ]:
        with pytest.raises(ResearchJobError):
            jobs.enqueue(request(), actor, key, trace)
    for limit in [0, 201, True]:
        with pytest.raises(ResearchJobError):
            jobs.list_jobs(limit)
    with pytest.raises(ResearchJobError):
        jobs.read_job("not-a-uuid")
    with pytest.raises(ResearchJobError):
        jobs.claim("not-a-uuid")
    with pytest.raises(ResearchJobError):
        jobs.resume(UUID(int=0), "remote", "valid-key-123456789", "a" * 32)


def test_database_failure_is_detached_from_public_error_chain():
    class ConnectionThatFails:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("synthetic-canary-driver-message")

    class EngineThatFails:
        @contextmanager
        def begin(self):
            yield ConnectionThatFails()

    with pytest.raises(ResearchJobError) as failure:
        ResearchJobs(EngineThatFails()).read_job(uuid4())
    assert failure.value.code == "UNAVAILABLE"
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None
    assert "synthetic-canary" not in repr(failure.value)


def test_invalid_normalized_response_is_not_hidden_as_generic_database_unavailable():
    class ConnectionWithInvalidResponse:
        def execute(self, *_args, **_kwargs):
            raise SourceError(SourceErrorCode.INVALID_RESPONSE)

    class EngineWithInvalidResponse:
        @contextmanager
        def begin(self):
            yield ConnectionWithInvalidResponse()

    with pytest.raises(ResearchJobError) as failure:
        ResearchJobs(EngineWithInvalidResponse()).read_job(uuid4())
    assert (failure.value.status, failure.value.code) == (503, "PROVIDER_RESPONSE_INVALID")
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None


def test_page_cursor_hash_must_match_leased_resume_cursor_before_sql():
    class EngineMustNotBeUsed:
        def begin(self):
            raise AssertionError("SQL should not run for a mismatched cursor hash")

    lease = Lease(uuid4(), uuid4(), 1, request(), 0, 0, 0, 0, NOW, "leased-cursor")
    with pytest.raises(ResearchJobError) as failure:
        ResearchJobs(EngineMustNotBeUsed()).commit_page(
            lease,
            "a" * 64,
            "b" * 64,
            None,
            lambda _connection: 0,
        )
    assert failure.value.status == 400
    assert failure.value.code == "INVALID_REQUEST"
    assert failure.value.__context__ is None
