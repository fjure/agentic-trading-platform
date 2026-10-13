"""One hundred real PostgreSQL contenders for one paper account budget."""

from tests.integration.risk_support import host


def test_parallel_risk_reservations_postgres() -> None:
    assert "PASS" in host()
