"""Actual fixed-role, stored-only Paper reads on disposable synthetic PostgreSQL."""

from pathlib import Path

MARKER = "PASS: bounded stored Paper public projections"
STAGE = "READ_SQL"


def test_paper_public_read_sql_postgres():
    from tests.integration.test_paper_provision import host

    for scenario in ("reads", "basis", "human"):
        assert (
            host(
                Path(__file__),
                MARKER,
                support_paths=(
                    Path(__file__).with_name("test_paper_lifecycle.py"),
                    Path(__file__).with_name("test_paper_cancel_sql.py"),
                    Path(__file__).with_name("test_paper_human_sql.py"),
                ),
                guest_args=(scenario,),
            )
            == MARKER
        )


def read(f, resource, limit=None, cursor=None, trace=None):
    return f.sql(
        "api",
        "SELECT paper_" + resource + "(:limit,:cursor,:trace)",
        limit=limit,
        cursor=cursor,
        trace=trace or f.trace,
    )


def reads_guest():
    global STAGE
    import base64
    import json

    import httpx
    from sqlalchemy import URL, create_engine, text
    from test_paper_cancel_sql import cancel_fixture

    from packages.domain import models as m

    f = cancel_fixture()
    flat = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    assert flat["positions"] == [] and flat["verificationOperationId"] is not None
    pending = f.api.enqueue(
        "CANCEL_ORDER",
        dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
        "operator",
        "read-old-pending-cancel-0001",
        f.trace,
        order_id=f.cancel_order_id,
    )
    row = f.provider_orders[f.cancel_client_order_id]
    stamp = m.utc_string(f.clock.now())
    row.update(
        status="filled", filled_qty="1", filled_avg_price="15", updated_at=stamp, filled_at=stamp
    )
    old_routes = f.route_handler

    def filled_routes(request):
        if request.url.path == "/v2/account":
            return httpx.Response(
                200,
                json=dict(
                    id=f.account,
                    currency="USD",
                    cash="99985",
                    equity="100000",
                    portfolio_value="100000",
                    status="ACTIVE",
                    account_blocked=False,
                    trading_blocked=False,
                    trade_suspended_by_user=False,
                ),
            )
        if request.url.path == "/v2/positions":
            return httpx.Response(
                200,
                json=[
                    dict(
                        asset_id="22222222-2222-4222-8222-222222222222",
                        symbol="SPY",
                        side="long",
                        qty="1",
                        qty_available="1",
                        avg_entry_price="15",
                        market_value="15",
                        cost_basis="15",
                    )
                ],
            )
        if request.url.path == "/v2/account/activities":
            return httpx.Response(
                200,
                json=[
                    dict(
                        id="PRIVATE::ACTIVITY_SENTINEL",
                        activity_type="FILL",
                        transaction_time=stamp,
                        created_at=stamp,
                        order_id=f.cancel_provider_order_id,
                        symbol="SPY",
                        side="buy",
                        qty="1",
                        price="15",
                        cum_qty="1",
                        leaves_qty="0",
                        type="fill",
                    )
                ],
            )
        return old_routes(request)

    f.route_handler = filled_routes
    try:
        f.refresh(publish=False)
    except AssertionError:
        STAGE = "READ_SQL_" + f.receipt.reason_code
        raise RuntimeError(f.receipt.reason_code) from None
    assert f.receipt.ready
    for number in range(28):
        operation = f.api.enqueue(
            "PAUSE",
            {"action": "PAUSE"},
            "PRIVATE_ACTOR_SENTINEL",
            f"public-read-pause-{number:04d}",
            f.trace,
        )
        claim = f.execution.claim(f.command_owner, lease_seconds=120)
        assert claim["operationId"] == operation["operationId"]
        assert f.execution.finish(operation["operationId"], f.command_owner, claim["fence"])
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="lab_migration",
            host="postgres",
            database=f.database,
        )
    )

    def fingerprint():
        with engine.connect() as connection:
            tables = (
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "ORDER BY tablename"
                    )
                )
                .scalars()
                .all()
            )
            return tuple(
                (
                    table,
                    connection.execute(
                        text(
                            "SELECT md5(coalesce(string_agg(v,'' ORDER BY v),'')) FROM "
                            '(SELECT row_to_json(t)::text v FROM public."' + table + '" t) s'
                        )
                    ).scalar_one(),
                )
                for table in tables
            )

    before, calls = fingerprint(), len(f.cancel_requests)
    snapshot = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    assert snapshot["portfolio"]["cash"] == "99985"
    assert snapshot["portfolio"]["equity"] == "100000"
    position = next(p for p in snapshot["positions"] if p["symbol"] == "SPY")
    assert position["quantity"] == "1" and position["costBasis"] == "15"
    assert snapshot["status"]["reconciledAt"] is not None
    assert len(snapshot["markets"]) == 2
    for market in snapshot["markets"]:
        assert market["capability"] is not None and market["session"] is not None
        assert market["session"]["startsAt"] < market["session"]["endsAt"]
    orders = read(f, "orders")["items"]
    assert len(orders) == 1
    order = orders[0]
    assert order["orderId"] == f.cancel_order_id and order["status"] == "FILLED"
    assert order["clientOrderId"] == f.cancel_client_order_id
    assert order["proposalSource"] == "MODEL_DECISION" and order["decisionId"]
    assert order["humanRequestId"] is None and order["cancel"]["state"] == "READY"
    # Historical intent had no persisted LIMIT-plan policy: do not invent its TIF/session.
    assert order["timeInForce"] is None and order["session"] is None
    assert order["quantity"] == "1" and order["limitPrice"] == "15"
    fills = read(f, "fills")["items"]
    assert len(fills) == 1 and fills[0]["quantity"] == "1" and fills[0]["price"] == "15"
    assert fills[0]["fee"] is None and fills[0]["orderId"] == f.cancel_order_id
    assert fills[0]["fillId"] != "PRIVATE::ACTIVITY_SENTINEL"
    first = read(f, "operations")
    assert len(first["items"]) == 25 and first["nextCursor"]
    assert first["items"][0]["operationId"] == pending["operationId"]
    assert first["items"][0]["status"] == "ACCEPTED"
    assert all(value["status"] == "SUCCEEDED" for value in first["items"][1:])
    second = read(f, "operations", cursor=first["nextCursor"])
    ids = [v["operationId"] for v in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) and second["nextCursor"] is None
    assert read(f, "orders", cursor=first["nextCursor"]) == {"error": "VERSION_CONFLICT"}
    for cursor in ("!", "a" * 1025, "bnVsbA", "e30", "W10"):
        assert read(f, "operations", cursor=cursor) == {"error": "INVALID_REQUEST"}
    decoded = json.loads(base64.urlsafe_b64decode(first["nextCursor"] + "===\n"))
    decoded["accountGeneration"] += 1
    stale = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip("=")
    assert read(f, "operations", cursor=stale) == {"error": "VERSION_CONFLICT"}
    for limit in (0, 51, -1):
        assert read(f, "fills", limit=limit) == {"error": "INVALID_REQUEST"}
    trace = f.sql("api", "SELECT paper_trace(:trace)", trace=f.trace)
    assert len(trace["items"]) == 32 and trace["complete"] is False
    serialized = json.dumps([snapshot, orders, fills, first, second, trace])
    for forbidden in (
        f.account,
        f.cancel_provider_order_id,
        "PRIVATE::ACTIVITY_SENTINEL",
        "PRIVATE_ACTOR_SENTINEL",
        "provider_account_id",
    ):
        assert forbidden not in serialized
    assert len(serialized.encode()) < 65536
    for role in ("execution", "worker", "watchdog"):
        try:
            f.sql(role, "SELECT paper_snapshot(:trace)", trace=f.trace)
        except Exception as error:
            assert getattr(error.orig, "sqlstate", None) == "42501"
        else:
            raise AssertionError("non-API projection privilege")
    for statement in (
        "SELECT cash FROM portfolio_snapshots",
        "SELECT * FROM paper_activity_evidence",
        "SELECT * FROM broker_binding_proofs",
        "SELECT paper_read_order(:id)",
    ):
        try:
            f.sql("api", statement, id=f.cancel_order_id)
        except Exception as error:
            assert getattr(error.orig, "sqlstate", None) == "42501"
        else:
            raise AssertionError("direct private read privilege")
    assert fingerprint() == before and len(f.cancel_requests) == calls
    engine.dispose()
    print(MARKER)


def basis_guest():
    from decimal import Decimal

    from test_paper_lifecycle import PaperFixture

    f = PaperFixture()
    dormant = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    assert dormant["portfolio"] is None and dormant["positions"] is None
    assert all(market["quote"] is None for market in dormant["markets"])
    f.initial_holding_value = f.current_holding_value = Decimal(900)
    f.prepare()
    f.profile()
    f.refresh()
    snapshot = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    holding = next(p for p in snapshot["positions"] if p["symbol"] == "SPY")
    assert holding["quantity"] == "60" and holding["averagePrice"] == "12.83"
    assert holding["costBasis"] == "770"  # exact raw basis, not 60 * 12.83
    assert snapshot["portfolio"]["equity"] == "100900"
    assert snapshot["verificationOperationId"] is not None
    f.api.rotation_begin("operator", "read-basis-rotation-0002", f.trace)
    rotated = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    assert rotated["verificationOperationId"] is None
    assert rotated["positions"] == snapshot["positions"]
    print(MARKER)


def human_guest():
    import uuid
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy import URL, create_engine, text
    from test_paper_human_sql import create_human
    from test_paper_lifecycle import PaperFixture

    from packages.domain import models as m
    from packages.persistence.ledger import LedgerUnit

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    assert "activationId" in f.start()[3]
    operation, _, _, proposal = create_human(f, lifetime=10)
    # Read-side historical admission only. Saturday Risk stays SESSION_CLOSED;
    # this fixture does not assert a new entry was approved or sent by runtime.
    now = f.clock.now()
    reservation_id = str(uuid.uuid4())
    risk = m.RiskDecision(
        str(uuid.uuid4()),
        proposal["id"],
        True,
        "APPROVED",
        ("APPROVED",),
        1,
        m.ExposureSnapshot("USD", Decimal(100000), Decimal(0), Decimal(0)),
        now,
        now + timedelta(seconds=30),
        "b" * 32,
        reservation_id,
    )
    reservation = m.RiskReservation(
        reservation_id,
        proposal["id"],
        risk.id,
        "USD",
        Decimal(15),
        m.ReservationState.ACTIVE,
        1,
        now,
        "b" * 32,
    )
    intent = m.OrderIntent(
        str(uuid.uuid4()),
        proposal["id"],
        risk.id,
        "historical-human-" + str(uuid.uuid4()),
        "SPY",
        "BUY",
        "LIMIT",
        Decimal(1),
        1,
        now,
        "b" * 32,
        Decimal(15),
    )
    order = m.BrokerOrder(
        str(uuid.uuid4()),
        intent.id,
        intent.client_order_id,
        str(uuid.uuid4()),
        m.OrderStatus.ACKNOWLEDGED,
        Decimal(1),
        Decimal(0),
        1,
        f.clock.now(),
        intent.trace_id,
    )
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="lab_migration",
            host="postgres",
            database=f.database,
        )
    )
    with engine.begin() as connection:
        tx = LedgerUnit(connection)
        for value in (risk, reservation, intent, order):
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    now,
                    "fixture",
                    "fixture.historical_admission",
                    ("ADMITTED_HISTORICAL_ORDER",),
                    value.id,
                    1,
                    value.trace_id,
                )
            )
            tx.insert(value, adapter="alpaca-paper" if type(value) is m.BrokerOrder else None)
        connection.execute(
            text("UPDATE risk_reservations SET account_id='paper' WHERE id=:id"),
            {"id": reservation.id},
        )
        connection.execute(
            text("UPDATE order_intents SET account_id='paper' WHERE id=:id"), {"id": intent.id}
        )
        # Immutable newer combined proof deliberately retains an older broker observation.
        for entity in ("paper:SPY:proof", "paper:SPY"):
            connection.execute(
                text(
                    "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                    "entity_id,entity_version,trace_id) VALUES(gen_random_uuid()::text,"
                    "statement_timestamp(),'fixture','fixture','[]',:entity,2,:trace)"
                ),
                dict(entity=entity, trace=f.trace),
            )
        connection.execute(
            text(
                "INSERT INTO broker_binding_proofs SELECT account_id,instrument_id,2,"
                "trading_capability_version,source_capability_version,provider_account_id,"
                "provider_asset_id,session_start,session_end,statement_timestamp(),"
                "statement_timestamp()-interval '60 seconds',market_provider_time,"
                "trade_provider_time,valid_until,actor_id,trace_id FROM broker_binding_proofs "
                "WHERE account_id='paper' AND instrument_id='SPY' AND context_version=1"
            )
        )
        connection.execute(
            text(
                "UPDATE risk_instrument_context SET version=2 WHERE account_id='paper' "
                "AND instrument_id='SPY'"
            )
        )
    projected = read(f, "orders")["items"][0]
    assert projected["proposalSource"] == "HUMAN_REQUEST" and projected["decisionId"] is None
    assert projected["humanRequestId"] == operation["operationId"]
    assert projected["timeInForce"] == "DAY" and projected["session"] == "REGULAR"
    snapshot = f.sql("api", "SELECT paper_snapshot(:trace)", trace=f.trace)
    capability = next(x for x in snapshot["markets"] if x["symbol"] == "SPY")["capability"]
    with engine.connect() as connection:
        expected = connection.execute(
            text(
                "SELECT paper_read_time(least(market_observed_at,trade_observed_at)) "
                "FROM broker_binding_proofs WHERE account_id='paper' AND instrument_id='SPY' "
                "AND context_version=2"
            )
        ).scalar_one()
    assert capability["observedAt"] == expected
    engine.dispose()
    print(MARKER)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        {"reads": reads_guest, "basis": basis_guest, "human": human_guest}[sys.argv[2]]()
    except Exception as error:
        root = getattr(error, "orig", error)
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"APW02_FAIL:{STAGE}:{type(root).__name__}:{Path(frame.filename).name}:{frame.lineno}"
        )
        raise SystemExit(1) from None
