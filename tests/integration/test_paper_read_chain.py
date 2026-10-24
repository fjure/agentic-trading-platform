"""Authenticated Paper reads through both HTTP boundaries and real fixed-role SQL.

All history belongs to an isolated fixture with synthetic GET-only evidence.
Reading these views must neither call its provider nor alter financial records.
"""

from pathlib import Path

MARKER = "PASS: authenticated isolated Paper reads and immutable finances"
STAGE = "READ_START"


def test_paper_read_http_postgres():
    from tests.integration.test_paper_provision import host

    assert host(
        Path(__file__), MARKER,
        support_paths=tuple(Path(__file__).with_name(name) for name in (
            "test_paper_lifecycle.py", "test_paper_cancel_sql.py", "test_paper_cancel_chain.py",
        )),
    ) == MARKER


def guest():
    import json

    import psycopg
    from test_paper_cancel_chain import PublicPaper
    from test_paper_cancel_sql import cancel_fixture

    global STAGE
    STAGE = "READ_FIXTURE"
    f = cancel_fixture()
    public = PublicPaper(f.api)

    def finances():
        tables = (
            "accounts", "positions", "portfolio_snapshots", "trade_proposals",
            "risk_decisions", "risk_reservations", "order_intents", "broker_orders",
            "execution_reports",
        )
        with psycopg.connect(dbname=f.database, **f.common) as connection:
            return tuple(connection.execute(
                "SELECT md5(COALESCE(jsonb_agg(to_jsonb(r) "
                "ORDER BY to_jsonb(r)::text)::text,'[]')) "
                f"FROM public.{table} r"
            ).fetchone()[0] for table in tables)

    def read(path):
        response = public.request("GET", "/api/v1/paper/" + path)
        assert response.status_code == 200, response.status_code
        assert response.headers["Cache-Control"] == "no-store"
        return response.json()

    try:
        STAGE = "READ_FINANCIAL_PROJECTION"
        before, calls = finances(), len(f.http_calls)
        snapshot = read("snapshot")
        assert snapshot["namespace"] == "alpaca-paper"
        assert snapshot["status"]["environment"] == "PAPER"
        assert snapshot["profile"]["configured"] is True
        assert snapshot["portfolio"]["cash"] == "100000"
        assert snapshot["positions"] == []
        orders = read("orders")
        order = next(row for row in orders["items"] if row["orderId"] == f.cancel_order_id)
        assert order["proposalSource"] == "MODEL_DECISION"
        assert order["decisionId"] and order["humanRequestId"] is None
        assert order["cancel"] is None
        assert read("fills")["items"] == []
        trace = read("traces/" + f.trace)
        assert trace["traceId"] == f.trace and len(trace["items"]) <= 32
        assert finances() == before and len(f.http_calls) == calls

        STAGE = "READ_PENDING_RELOAD"
        operation = public.cancel(f.cancel_order_id)
        before, calls = finances(), len(f.http_calls)
        # A new authenticated browser session rediscovers the durable operation.
        reloaded = PublicPaper(f.api)
        try:
            response = reloaded.request("GET", "/api/v1/paper/operations?limit=50")
            assert response.status_code == 200
            assert any(row["operationId"] == operation["operationId"]
                       for row in response.json()["items"])
        finally:
            reloaded.close()
        order = next(row for row in read("orders")["items"]
                     if row["orderId"] == f.cancel_order_id)
        assert order["cancel"] is not None
        assert order["cancel"]["deleteAttempts"] == 0

        STAGE = "READ_CURSOR_AND_BOUNDARIES"
        first = read("operations?limit=1")
        assert len(first["items"]) == 1 and first["nextCursor"]
        second = read("operations?limit=1&cursor=" + first["nextCursor"])
        assert first["items"][0]["operationId"] != second["items"][0]["operationId"]
        for path in (
            "orders?limit=0", "orders?limit=51", "orders?limit=true",
            "orders?limit=1&limit=2", "orders?cursor=x&cursor=y",
            "orders?accountId=fake", "snapshot?accountId=fake",
            "traces/not-a-trace", "orders?cursor=invalid",
        ):
            response = public.request("GET", "/api/v1/paper/" + path)
            assert response.status_code == 400
            assert response.json()["code"] == "INVALID_REQUEST"
        response = public.request("GET", "/api/v1/paper/orders?cursor=" + first["nextCursor"])
        assert response.status_code == 409 and response.json()["code"] == "VERSION_CONFLICT"
        assert finances() == before and len(f.http_calls) == calls
        wire = json.dumps(public.values)
        assert f.account not in wire and f.cancel_provider_order_id not in wire
        assert all(value not in wire for value in (
            "apiSecret", "apiKey", "rawPayload", "activity_id", "brokerOrderId",
        ))
        assert all(method == "GET" or path == "/commands" for method, path in public.relayed)
        print(MARKER)
    finally:
        public.close()
        f.broker.close()
        for engine in f.engines.values():
            engine.dispose()


if __name__ == "__main__":
    import traceback

    try:
        guest()
    except Exception as error:
        location = traceback.extract_tb(error.__traceback__)[-1]
        root = getattr(error, "orig", error)
        print(f"APW02_FAIL:{STAGE}:{type(root).__name__}:"
              f"{Path(location.filename).name}:{location.lineno}")
        raise SystemExit(1) from None
