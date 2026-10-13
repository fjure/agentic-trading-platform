"""PTV-04 integration checks on a UUID-isolated PostgreSQL 18 database."""

from pathlib import Path

import pytest

from services.control_api.migrations.ledger_schema_v1 import TABLE_DDL, TABLE_NAMES
from tests.integration.ledger_support import host


def test_first_revision_is_frozen() -> None:
    revision = Path("services/control_api/migrations/versions/0001_ledger.py").read_text(
        encoding="utf-8"
    )
    assert "packages.persistence.schema" not in revision
    assert sum("CREATE TABLE" in statement for statement in TABLE_DDL) == len(TABLE_NAMES)


def test_ledger_migration_runtime_and_restore() -> None:
    try:
        result = host()
    except RuntimeError as error:
        pytest.fail(str(error), pytrace=False)
    assert "backup/restore: PASS" in result
