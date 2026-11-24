"""Actual temporary Paper SQL and revision-bound evidence, with synthetic provider fixtures."""

from pathlib import Path

MARKER = "PASS: isolated Paper portfolio revision and reconciliation read"
QUERY = "SELECT public.paper_investment_portfolio(:trace)"


def test_investment_portfolio_paper_postgres():
    from tests.integration.test_paper_provision import host

    assert host(
        Path(__file__), MARKER,
        support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
    ) == MARKER


def guest():
    from decimal import Decimal

    import psycopg
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from test_paper_lifecycle import PaperFixture

    from packages.domain.models import utc_string
    from services.control_api.investment_portfolio_paper import PaperPortfolioProjection

    fixture = PaperFixture()
    trace = fixture.trace

    def read():
        raw = fixture.api.read_investment_portfolio(trace)
        return PaperPortfolioProjection.model_validate(raw).model_dump()

    initial = read()
    assert initial["quality"] == "UNAVAILABLE"
    assert initial["blockers"] == ["LEDGER_NOT_PREPARED"]
    assert initial["positions"] is None and initial["cash"] is None
    for role in ("execution", "worker", "watchdog"):
        try:
            fixture.sql(role, QUERY, trace=trace)
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501"
        else:
            raise AssertionError("A different login gained access to the Paper portfolio reader")

    with psycopg.connect(dbname=fixture.database, **fixture.common) as connection:
        assert connection.execute(
            "SELECT paper_investment_portfolio(%s)", (trace,)
        ).fetchone()[0] is None
    assert fixture.sql("api", QUERY, trace="invalid") == {"error": "INVALID_REQUEST"}

    fixture.initial_holding_value = fixture.current_holding_value = Decimal(900)
    fixture.prepare()
    fixture.profile()
    prepared = read()
    assert prepared["generation"] == 1 and prepared["revision"] == 1
    assert prepared["cash"] == "100000" and prepared["equity"] == "100900"
    assert prepared["reconciliation"] == "REQUIRED" and prepared["reconciliation_at"] is None
    assert prepared["positions"] == [{
        "instrument": "SPY", "revision": 1, "quantity": "60", "average_price": "12.83",
        "cost_basis": "770", "realized_pnl": None,
    }]

    fixture.refresh(publish=False)
    before = read()
    assert before["reconciliation"] == "CURRENT" and before["reconciliation_at"]
    assert before["revision"] == fixture.receipt.portfolio_version
    assert before["reconciliation_at"] == utc_string(fixture.receipt.observed_at)

    with fixture.engines["api"].connect().execution_options(
        isolation_level="REPEATABLE READ"
    ) as connection, connection.begin():
        first = connection.execute(text(QUERY), {"trace": trace}).scalar_one()
        fixture.current_holding_value = Decimal(925)
        fixture.refresh(publish=False)
        second = connection.execute(text(QUERY), {"trace": trace}).scalar_one()
        stable_fields = ("snapshot_id", "revision", "as_of", "cash", "equity", "positions",
                         "reconciliation_at")
        assert all(first[key] == second[key] for key in stable_fields)

    calls = list(fixture.http_calls)
    later = read()
    assert later["snapshot_id"] != before["snapshot_id"]
    assert later["revision"] == before["revision"] + 1
    assert later["equity"] == "100925" and later["positions"] == before["positions"]
    assert later["reconciliation"] == "CURRENT"
    assert later["reconciliation_at"] == utc_string(fixture.receipt.observed_at)
    assert later["reconciliation_at"] > before["reconciliation_at"]
    assert read()["snapshot_id"] == later["snapshot_id"]
    assert fixture.http_calls == calls
    print(MARKER)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            location = traceback.extract_tb(error.__traceback__)[-1]
            print(f"APW02_FAIL:PORTFOLIO:{type(error).__name__}:"
                  f"{Path(location.filename).name}:{location.lineno}")
            raise SystemExit(1) from None
