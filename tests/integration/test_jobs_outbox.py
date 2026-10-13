"""Real PostgreSQL proof for PTV-05 through the isolated Docker guest."""

from tests.integration.jobs_support import host


def test_jobs_outbox_postgres() -> None:
    assert "PASS" in host()
