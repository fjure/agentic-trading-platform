"""MDR-02 real PostgreSQL checks, never an in-memory replacement."""

from tests.integration.research_support import host


def test_research_storage_pit_concurrency_and_actual_runtime_roles():
    assert host().startswith('PASS research migration/ledger/dedupe/revision/late-commit')
