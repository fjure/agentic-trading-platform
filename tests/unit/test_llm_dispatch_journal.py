"""Gateway claim durability and redacted fail-closed behavior."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from services.llm_gateway.dispatch_journal import DispatchJournal, DispatchOutcome, JournalDenied

_HASH = "a" * 64


def _deadline():
    return datetime.now(UTC) + timedelta(seconds=30)


def test_only_one_concurrent_gateway_claim_and_restart_stays_closed(tmp_path):
    path = tmp_path / "dispatch.sqlite"
    journal = DispatchJournal.provision(path)
    attempt = uuid4()
    start = Barrier(2)

    def claim():
        start.wait(timeout=5)
        try:
            journal.claim(attempt, _HASH, _deadline())
        except JournalDenied as error:
            return error.code
        return "CLAIMED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [
            "CLAIMED", "DISPATCH_ALREADY_CLAIMED",
        ]
    restarted = DispatchJournal(path)
    assert restarted.status(attempt, _HASH) == DispatchOutcome.OUTCOME_UNKNOWN
    with pytest.raises(JournalDenied, match="^DISPATCH_ALREADY_CLAIMED$"):
        restarted.claim(attempt, _HASH, _deadline())
    with pytest.raises(JournalDenied, match="^IDEMPOTENCY_CONFLICT$"):
        restarted.claim(attempt, "b" * 64, _deadline())
    with closing(sqlite3.connect(path)) as connection:
        assert [row[1] for row in connection.execute("PRAGMA table_info(dispatches)")] == [
            "attempt_id", "request_hash", "expires_at", "outcome",
        ]
        assert connection.execute("SELECT count(*) FROM dispatches").fetchone() == (1,)


@pytest.mark.parametrize("outcome", list(DispatchOutcome))
def test_terminal_status_is_durable_and_never_releases_claim(tmp_path, outcome):
    path = tmp_path / "dispatch.sqlite"
    journal = DispatchJournal.provision(path)
    attempt = uuid4()
    journal.claim(attempt, _HASH, _deadline())
    journal.finish(attempt, outcome)
    restarted = DispatchJournal(path)
    restarted.finish(attempt, outcome)
    assert restarted.status(attempt, _HASH) == outcome
    with pytest.raises(JournalDenied, match="^DISPATCH_ALREADY_CLAIMED$"):
        restarted.claim(attempt, _HASH, _deadline())
    different = next(value for value in DispatchOutcome if value != outcome)
    with pytest.raises(JournalDenied, match="^DISPATCH_BINDING_INVALID$"):
        restarted.finish(attempt, different)


def test_journal_capacity_never_evicts_consumed_identities(tmp_path):
    path = tmp_path / "dispatch.sqlite"
    journal = DispatchJournal.provision(path, max_entries=1)
    attempt = uuid4()
    journal.claim(attempt, _HASH, _deadline())
    journal.finish(attempt, DispatchOutcome.UNSENT)
    with pytest.raises(JournalDenied, match="^DISPATCH_JOURNAL_FULL$"):
        DispatchJournal(path, max_entries=1).claim(uuid4(), _HASH, _deadline())


def test_invalid_binding_never_creates_a_claim(tmp_path):
    journal = DispatchJournal.provision(tmp_path / "dispatch.sqlite")
    attempt = uuid4()
    for deadline in (
        datetime.now(UTC), datetime.now(UTC) + timedelta(hours=1), datetime(2030, 1, 1),
    ):
        with pytest.raises(JournalDenied, match="^DISPATCH_BINDING_INVALID$"):
            journal.claim(attempt, _HASH, deadline)
    with pytest.raises(JournalDenied, match="^DISPATCH_BINDING_INVALID$"):
        journal.claim(attempt, "untrusted-data", _deadline())
    assert journal.status(attempt, _HASH) is None


def test_missing_or_corrupt_journal_is_not_silently_created(tmp_path):
    path = tmp_path / "dispatch.sqlite"
    with pytest.raises(JournalDenied, match="^DISPATCH_JOURNAL_UNAVAILABLE$"):
        DispatchJournal(path)
    assert not path.exists()
    path.write_bytes(b"not a database")
    for open_journal in (DispatchJournal, DispatchJournal.provision):
        with pytest.raises(JournalDenied, match="^DISPATCH_JOURNAL_UNAVAILABLE$"):
            open_journal(path)
    assert path.read_bytes() == b"not a database"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX mode semantics")
def test_private_mode_is_required_before_opening_journal(tmp_path):
    path = tmp_path / "dispatch.sqlite"
    journal = DispatchJournal.provision(path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(JournalDenied, match="^DISPATCH_JOURNAL_UNAVAILABLE$"):
        journal.claim(uuid4(), _HASH, _deadline())
