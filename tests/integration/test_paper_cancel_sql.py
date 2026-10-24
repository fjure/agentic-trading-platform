"""Guarded historical-own-order admission and fixed-role cancellation SQL tests.

The admitted ACK is test history, not a claim that Saturday START or Risk
approved a new order. Every FK/audit/immutability guard remains enabled. No
entry activation, clock change or provider POST is used to prepare cancellation.
"""

from pathlib import Path

MARKER = "PASS: independent fenced cancellation and retained obligations"
STAGE = "CANCEL_SQL"


def test_paper_cancel_sql_postgres():
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
        )
        == MARKER
    )


def test_paper_cancel_retry_fences_and_generation_postgres():
    from tests.integration.test_paper_provision import host

    for scenario in ("retry", "fences", "promotion", "fairness"):
        assert (
            host(
                Path(__file__),
                MARKER,
                support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
                guest_args=(scenario,),
            )
            == MARKER
        )


class CancelSql:
    def __init__(self, f):
        import uuid

        self.f, self.worker = f, str(uuid.uuid4())
        self.operation = f.api.enqueue(
            "CANCEL_ORDER",
            dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
            "operator",
            "cancel-scenario-command-0001",
            f.trace,
            order_id=f.cancel_order_id,
        )

    def claim(self, seconds=30):
        claim = self.f.sql(
            "execution",
            "SELECT paper_cancel_claim(:owner,:fence,CAST(:worker AS uuid),:seconds)",
            owner=self.f.owner,
            fence=self.f.fence,
            worker=self.worker,
            seconds=seconds,
        )
        assert claim is not None
        self.claimed = claim
        self.auth = dict(
            cancel=claim["cancelId"],
            worker=self.worker,
            lease=claim["leaseFence"],
            owner=self.f.owner,
            fence=self.f.fence,
        )
        return claim

    def call(self, name, suffix="", **values):
        return self.f.sql(
            "execution",
            "SELECT paper_cancel_" + name + "(CAST(:cancel AS uuid),"
            "CAST(:worker AS uuid),:lease,:owner,:fence" + suffix + ")",
            **(self.auth | values),
        )

    def record(self, attempt, kind="OBSERVED", *, seconds=None, **values):
        import json

        from packages.domain import models as m

        body = dict(
            kind=kind,
            observedAt=m.utc_string(self.f.clock.now()),
            retryAfterSeconds=seconds,
            deleteAttempted=kind != "OBSERVED",
            order=None,
        )
        if kind == "OBSERVED":
            body["order"] = dict(
                clientOrderId=self.f.cancel_client_order_id,
                brokerOrderId=self.f.cancel_provider_order_id,
                status="ACKNOWLEDGED",
                requestedQuantity="1",
                filledQuantity="0",
            )
        return self.f.sql(
            "execution",
            "SELECT paper_cancel_record(CAST(:attempt AS uuid),"
            "CAST(:worker AS uuid),:lease,:owner,:fence,CAST(:body AS jsonb))",
            **(self.auth | values),
            attempt=attempt,
            body=json.dumps(body),
        )

    def lookup(self):
        assert self.call("finish") is False
        lookup = self.call("begin_lookup")
        assert "attemptId" in lookup
        assert self.record(lookup["attemptId"])
        return lookup


def fairness_guest():
    import time
    import uuid
    from dataclasses import replace

    from sqlalchemy import URL, create_engine, text

    from packages.domain import models as m
    from packages.persistence.ledger import LedgerUnit
    from services.execution.paper_reconciliation import ensure_paper_order_basis

    f = cancel_fixture()
    first = CancelSql(f)
    old = f.cancel_history
    now = f.clock.now()
    decision = replace(old[0], id=str(uuid.uuid4()))
    proposal = replace(old[1], id=str(uuid.uuid4()), decision_id=decision.id)
    risk_id, reservation_id = str(uuid.uuid4()), str(uuid.uuid4())
    risk = replace(old[2], id=risk_id, proposal_id=proposal.id, reservation_id=reservation_id)
    reservation = replace(
        old[3], id=reservation_id, proposal_id=proposal.id, risk_decision_id=risk.id
    )
    client_id, provider_id = "paper-cancel-" + str(uuid.uuid4()), str(uuid.uuid4())
    intent = replace(
        old[4],
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        risk_decision_id=risk.id,
        client_order_id=client_id,
    )
    order = replace(
        old[5],
        id=str(uuid.uuid4()),
        intent_id=intent.id,
        client_order_id=client_id,
        broker_order_id=provider_id,
    )
    report = replace(old[6], id=str(uuid.uuid4()), order_id=order.id)
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="lab_migration",
            host="postgres",
            database=f.database,
        )
    )
    # Admit a second historical obligation through the same guarded ledger path.
    # No entry authority or provider effect is manufactured by this fixture.
    with engine.begin() as connection:
        tx = LedgerUnit(connection)
        for value in (decision, proposal, risk, reservation, intent, order, report):
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    now,
                    "fixture",
                    "fixture.historical_admission",
                    ("ADMITTED_HISTORICAL_ORDER",),
                    value.id,
                    1,
                    f.trace,
                )
            )
            tx.insert(
                value,
                adapter="alpaca-paper"
                if type(value)
                in (
                    m.BrokerOrder,
                    m.ExecutionReport,
                )
                else None,
            )
        connection.execute(
            text("UPDATE risk_reservations SET account_id='paper' WHERE id=:id"),
            {"id": reservation.id},
        )
        connection.execute(
            text("UPDATE order_intents SET account_id='paper' WHERE id=:id"), {"id": intent.id}
        )
    engine.dispose()
    with f.ledger.transaction() as tx:
        ensure_paper_order_basis(tx, "paper", order, intent, reservation, now, f.trace)
    f.api.enqueue(
        "CANCEL_ORDER",
        dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
        "operator",
        "cancel-fairness-second-0001",
        f.trace,
        order_id=order.id,
    )
    assert first.claim()["orderId"] == f.cancel_order_id
    first.lookup()
    assert first.call("finish") is False
    time.sleep(1.1)
    # Both are due: an unresolved older lookup must yield to the older due time.
    assert first.claim()["orderId"] == order.id
    assert f.sql("execution", "SELECT count(*) FROM risk_reservations WHERE state='ACTIVE'") == 2
    assert (
        f.sql(
            "execution",
            "SELECT count(*) FROM execution_reports WHERE id LIKE 'paper-cancel-report-%'",
        )
        == 0
    )
    print(MARKER)


def retry_guest():
    import time

    f = cancel_fixture()
    lane = CancelSql(f)
    for count in range(1, 4):
        lane.claim()
        lookup = lane.lookup()
        if count > 1:
            assert "PROVIDER_RATE_LIMITED" not in f.api.read_status(f.trace)["blockers"]
        attempt = lane.call("begin_delete", ",CAST(:lookup AS uuid)", lookup=lookup["attemptId"])
        assert "attemptId" in attempt
        assert lane.record(attempt["attemptId"], "THROTTLED", seconds=1)
        assert "PROVIDER_RATE_LIMITED" in f.api.read_status(f.trace)["blockers"]
        assert not lane.call("finish")
        read = f.sql(
            "execution", "SELECT paper_cancel_read(CAST(:id AS uuid))", id=lane.claimed["cancelId"]
        )
        assert read["deleteAttempts"] == count and read["lookupOnly"] == (count == 3)
        assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
        time.sleep(1.1)
    # New worker/process and new human/KILL links cannot reset the lifetime cap.
    f.api.enqueue(
        "CANCEL_ORDER",
        dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
        "operator",
        "cancel-retry-new-command-0001",
        f.trace,
        order_id=f.cancel_order_id,
    )
    f.api.enqueue("KILL", {"action": "KILL"}, "operator", "cancel-retry-kill-0001", f.trace)
    restarted = CancelSql.__new__(CancelSql)
    import uuid

    restarted.f, restarted.worker = f, str(uuid.uuid4())
    restarted.claim()
    lookup = restarted.lookup()
    assert restarted.call("begin_delete", ",CAST(:lookup AS uuid)", lookup=lookup["attemptId"]) == {
        "error": "BROKER_STATUS_UNKNOWN"
    }
    assert (
        f.sql(
            "execution",
            "SELECT count(*) FROM execution_reports WHERE id LIKE 'paper-cancel-report-%'",
        )
        == 3
    )
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
    print(MARKER)


def fences_guest():
    import time
    import uuid

    from services.execution.recovery_health import claim_execution_owner

    f = cancel_fixture()
    lane = CancelSql(f)
    lane.claim(seconds=1)
    lookup = lane.call("begin_lookup")
    assert "attemptId" in lookup
    assert lane.call("begin_lookup", owner="wrong-owner") == {"error": "VERSION_CONFLICT"}
    assert lane.call("begin_lookup", fence=f.fence + 1) == {"error": "VERSION_CONFLICT"}
    time.sleep(1.1)
    assert not lane.record(lookup["attemptId"])
    assert lane.call("begin_delete", ",CAST(:lookup AS uuid)", lookup=lookup["attemptId"]) == {
        "error": "VERSION_CONFLICT"
    }
    assert not lane.call("finish")
    previous = dict(lane.auth)
    lane.worker = str(uuid.uuid4())
    lane.claim(seconds=1)
    assert lane.auth["lease"] == previous["lease"] + 1
    assert lane.call("begin_lookup", **previous) == {"error": "VERSION_CONFLICT"}
    assert not lane.record(lookup["attemptId"], **previous)
    f.owner = "cancel-replacement-owner"
    f.fence = claim_execution_owner(
        ledger=f.ledger, account_id="paper", owner_id=f.owner, trace_id=f.trace
    )
    assert lane.call("begin_lookup") == {"error": "VERSION_CONFLICT"}
    assert not lane.call("finish")
    assert (
        f.sql(
            "execution",
            "SELECT count(*) FROM execution_reports WHERE id LIKE 'paper-cancel-report-%'",
        )
        == 0
    )
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
    print(MARKER)


def promotion_guest():
    from decimal import Decimal

    f = cancel_fixture()
    lane = CancelSql(f)
    lane.claim()
    lookup = lane.lookup()
    assert lookup["credentialGeneration"] == 1
    assert f.api.rotation_begin("operator", "cancel-promote-rotation-0001", f.trace) == 2
    assert f.api.rotation_complete(2)
    operation = f.api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": 2},
        "operator",
        "cancel-promote-verify-0001",
        f.trace,
    )
    claim = f.execution.claim(f.command_owner, lease_seconds=120)
    assert claim["operationId"] == operation["operationId"]
    n = f.clock.now()
    assert f.execution.finish(
        operation["operationId"],
        f.command_owner,
        claim["fence"],
        public=dict(
            credentialGeneration=2,
            observedAt=n,
            verifiedAt=n,
            accountLabel="paper-11111111",
            currency="USD",
            cash=Decimal(100000),
            marketDataFeed="IEX",
            providerVerification="CONFIRMED",
            blockers=[],
        ),
        private_account_id=f.account,
    )
    assert lane.call("begin_delete", ",CAST(:lookup AS uuid)", lookup=lookup["attemptId"]) == {
        "error": "BROKER_STATUS_UNKNOWN"
    }
    assert (
        f.sql(
            "execution",
            "SELECT count(*) FROM execution_reports WHERE id LIKE 'paper-cancel-report-%'",
        )
        == 0
    )
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
    print(MARKER)


def cancel_fixture():
    global STAGE
    import uuid
    from datetime import timedelta
    from decimal import Decimal

    import httpx
    from sqlalchemy import URL, create_engine, text
    from test_paper_lifecycle import PaperFixture

    from packages.decisions.mock import MockDecisionEngine
    from packages.domain import models as m
    from packages.persistence.ledger import LedgerUnit
    from services.execution.paper_reconciliation import ensure_paper_order_basis

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    now = f.clock.now()
    with f.ledger.transaction() as tx:
        state = tx.get(m.MarketState, "SPY-state")
    decision = MockDecisionEngine(clock=f.clock, ids=f.ids).decide(state, "admitted-history.v1")
    proposal = m.TradeProposal(
        str(uuid.uuid4()),
        decision.id,
        state.id,
        "SPY",
        m.Target.LONG,
        Decimal(1),
        now,
        now + timedelta(seconds=30),
        f.trace,
        ("ADMITTED_HISTORICAL_ORDER",),
        Decimal(15),
    )
    risk_id, reservation_id, intent_id, order_id = (str(uuid.uuid4()) for _ in range(4))
    risk = m.RiskDecision(
        risk_id,
        proposal.id,
        True,
        "APPROVED",
        ("APPROVED",),
        1,
        m.ExposureSnapshot("USD", Decimal(100000), Decimal(0), Decimal(0)),
        now,
        now + timedelta(seconds=30),
        f.trace,
        reservation_id,
    )
    reservation = m.RiskReservation(
        reservation_id,
        proposal.id,
        risk.id,
        "USD",
        Decimal(15),
        m.ReservationState.ACTIVE,
        1,
        now,
        f.trace,
    )
    client_id = "paper-cancel-" + str(uuid.uuid4())
    intent = m.OrderIntent(
        intent_id,
        proposal.id,
        risk.id,
        client_id,
        "SPY",
        "BUY",
        "LIMIT",
        Decimal(1),
        1,
        now,
        f.trace,
        Decimal(15),
    )
    provider_id = str(uuid.uuid4())
    order = m.BrokerOrder(
        order_id,
        intent.id,
        client_id,
        provider_id,
        m.OrderStatus.ACKNOWLEDGED,
        Decimal(1),
        Decimal(0),
        1,
        now,
        f.trace,
    )
    report = m.ExecutionReport(
        str(uuid.uuid4()),
        order.id,
        order.status,
        Decimal(1),
        Decimal(0),
        m.Reconciliation.CURRENT,
        now,
        now,
        f.trace,
        reason_code="ADMITTED_HISTORICAL_ORDER",
    )
    f.cancel_history = (decision, proposal, risk, reservation, intent, order, report)
    engine = create_engine(
        URL.create(
            "postgresql+psycopg", username="lab_migration", host="postgres", database=f.database
        )
    )
    with engine.begin() as c:
        tx = LedgerUnit(c)
        for value in (decision, proposal, risk, reservation, intent, order, report):
            tx.insert(
                m.AuditEvent(
                    str(uuid.uuid4()),
                    now,
                    "fixture",
                    "fixture.historical_admission",
                    ("ADMITTED_HISTORICAL_ORDER",),
                    value.id,
                    1,
                    f.trace,
                )
            )
            tx.insert(
                value,
                adapter="alpaca-paper"
                if type(value) in (m.BrokerOrder, m.ExecutionReport)
                else None,
            )
        c.execute(
            text("UPDATE risk_reservations SET account_id='paper' WHERE id=:id"),
            {"id": reservation.id},
        )
        c.execute(
            text("UPDATE order_intents SET account_id='paper' WHERE id=:id"), {"id": intent.id}
        )
    engine.dispose()
    with f.ledger.transaction() as tx:
        ensure_paper_order_basis(tx, "paper", order, intent, reservation, now, f.trace)
    f.cancel_order_id, f.cancel_intent_id = order.id, intent.id
    f.cancel_client_order_id, f.cancel_provider_order_id = client_id, provider_id
    f.cancel_quantity = Decimal(1)
    stamp = m.utc_string(now)
    f.provider_orders = {
        client_id: dict(
            id=provider_id,
            client_order_id=client_id,
            symbol="SPY",
            side="buy",
            type="limit",
            time_in_force="day",
            order_class="simple",
            qty="1",
            limit_price="15",
            filled_qty="0",
            filled_avg_price=None,
            status="new",
            created_at=stamp,
            updated_at=stamp,
            submitted_at=stamp,
        )
    }
    f.cancel_requests = []

    def routes(request):
        f.cancel_requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v2/clock":
            return httpx.Response(
                200, json=dict(timestamp=m.utc_string(f.clock.now()), is_open=False)
            )
        if path == "/v2/orders:by_client_order_id":
            found = f.provider_orders.get(request.url.params["client_order_id"])
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=list(f.provider_orders.values()))
        if path.startswith("/v2/orders/"):
            found = next(
                (row for row in f.provider_orders.values() if row["id"] == path.rsplit("/", 1)[1]),
                None,
            )
            if found is None:
                return httpx.Response(404)
            if request.method == "DELETE":
                found.update(status="canceled", updated_at=m.utc_string(f.clock.now()))
                return httpx.Response(204)
            return httpx.Response(200, json=found)
        return None

    f.route_handler = routes
    f.cancel_handler = f.handler
    try:
        f.refresh(publish=False)
    except AssertionError:
        STAGE = "CANCEL_SQL_" + f.receipt.reason_code
        raise
    assert f.receipt.ready
    assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
    return f


def guest():
    import json
    import time
    import uuid
    from datetime import timedelta

    from packages.domain import models as m

    f = cancel_fixture()
    operation = f.api.enqueue(
        "CANCEL_ORDER",
        dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
        "operator",
        "cancel-sql-command-0001",
        f.trace,
        order_id=f.cancel_order_id,
    )
    assert operation["status"] == "ACCEPTED"
    # An older CANCEL cannot enter or block the main command lane. A live
    # human lease then cannot starve the independent cancellation claim.
    profile = f.api.read_profile(f.trace)
    payload = {
        key: profile[key]
        for key in (
            "accountGeneration",
            "credentialGeneration",
            "profileVersion",
            "policyVersion",
            "limitPolicyVersion",
            "budgetVersion",
            "riskBudget",
            "currency",
        )
    }
    payload |= dict(
        symbol="QQQ",
        side="BUY",
        quantity="1",
        limitPrice="15",
        orderType="LIMIT",
        timeInForce="DAY",
        session="REGULAR",
        confirmation="PLACE_PAPER_LIMIT",
        expiresAt=m.utc_string(f.clock.now() + timedelta(seconds=30)),
    )
    human = f.api.enqueue(
        "PLACE_LIMIT_ORDER", payload, "operator", "cancel-unrelated-human-0001", f.trace
    )
    main = f.execution.claim(str(uuid.uuid4()), lease_seconds=120)
    assert main["operationId"] == human["operationId"]
    alias = f.api.enqueue(
        "CANCEL_ORDER",
        dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
        "operator",
        "cancel-sql-command-0002",
        f.trace,
        order_id=f.cancel_order_id,
    )
    for key in ("cancel-kill-control-0001", "cancel-kill-control-0002"):
        f.api.enqueue("KILL", {"action": "KILL"}, "operator", key, f.trace)
    assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"
    worker = str(uuid.uuid4())
    claim = f.sql(
        "execution",
        "SELECT paper_cancel_claim(:owner,:fence,CAST(:worker AS uuid),30)",
        owner=f.owner,
        fence=f.fence,
        worker=worker,
    )
    assert claim and claim["orderId"] == f.cancel_order_id
    assert (
        f.sql(
            "execution",
            "SELECT paper_cancel_claim(:owner,:fence,CAST(:worker AS uuid),30)",
            owner=f.owner,
            fence=f.fence,
            worker=worker,
        )
        is None
    )
    for key in (operation["operationId"], alias["operationId"]):
        assert not f.execution.finish(key, str(uuid.uuid4()), 1)
    arguments = dict(
        cancel=claim["cancelId"],
        worker=worker,
        lease=claim["leaseFence"],
        owner=f.owner,
        fence=f.fence,
    )
    sql_args = "CAST(:cancel AS uuid),CAST(:worker AS uuid),:lease,:owner,:fence"
    assert not f.sql("execution", "SELECT paper_cancel_finish(" + sql_args + ")", **arguments)
    lookup = f.sql("execution", "SELECT paper_cancel_begin_lookup(" + sql_args + ")", **arguments)
    assert "attemptId" in lookup, lookup
    assert f.sql(
        "execution",
        "SELECT paper_cancel_begin_delete(" + sql_args + ",CAST(:lookup AS uuid))",
        **(arguments | dict(lease=arguments["lease"] + 1)),
        lookup=lookup["attemptId"],
    ) == {"error": "VERSION_CONFLICT"}
    outcome = dict(
        kind="OBSERVED",
        observedAt=m.utc_string(f.clock.now()),
        retryAfterSeconds=None,
        deleteAttempted=False,
        order=dict(
            clientOrderId=f.cancel_client_order_id,
            brokerOrderId=f.cancel_provider_order_id,
            status="ACKNOWLEDGED",
            requestedQuantity="1",
            filledQuantity="0",
        ),
    )
    record = (
        "SELECT paper_cancel_record(CAST(:attempt AS uuid),CAST(:worker AS uuid),"
        ":lease,:owner,:fence,CAST(:body AS jsonb))"
    )
    malformed = outcome | dict(order=outcome["order"] | dict(status=None))
    assert not f.sql(
        "execution", record, **arguments, attempt=lookup["attemptId"], body=json.dumps(malformed)
    )
    assert f.sql(
        "execution", record, **arguments, attempt=lookup["attemptId"], body=json.dumps(outcome)
    )
    # Rotation retains the authenticated ACTIVE generation; pending new keys
    # cannot replace the just-observed identity or authorize a new entry.
    assert f.api.rotation_begin("operator", "cancel-rotation-active-0001", f.trace) == 2
    assert f.api.rotation_complete(2)
    assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
    attempt = f.sql(
        "execution",
        "SELECT paper_cancel_begin_delete(" + sql_args + ",CAST(:lookup AS uuid))",
        **arguments,
        lookup=lookup["attemptId"],
    )
    assert "attemptId" in attempt, attempt
    assert (
        f.sql("execution", "SELECT status FROM broker_orders WHERE id=:id", id=f.cancel_order_id)
        == "CANCEL_PENDING"
    )
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
    from decimal import Decimal

    from packages.observability.outbox import Outbox
    from services.control_api.events import _payload

    report_id = "paper-cancel-report-" + attempt["attemptId"]
    body = f.sql(
        "execution", "SELECT payload FROM outbox_events WHERE aggregate_id=:id", id=report_id
    )
    assert body["requested_quantity"] == "1" and body["filled_quantity"] == "0"
    assert body["reason_code"] is None
    assert _payload("execution.reported.v1", body)["status"] == "CANCEL_PENDING"
    published = []
    publisher = Outbox(f.engines["execution"])
    for _ in range(100):
        if publisher.publish_one(published.append) is None:
            break
    else:
        raise AssertionError("bounded outbox drain did not exhaust")
    report = next(event.payload for event in published if event.aggregate_id == report_id)
    assert type(report) is m.ExecutionReport and report.requested_quantity == Decimal(1)
    assert report.filled_quantity == Decimal(0) and report.reason_code is None
    accepted = dict(
        kind="ACCEPTED",
        observedAt=m.utc_string(f.clock.now()),
        retryAfterSeconds=None,
        deleteAttempted=True,
        order=None,
    )
    assert f.sql(
        "execution", record, **arguments, attempt=attempt["attemptId"], body=json.dumps(accepted)
    )
    assert not f.sql("execution", "SELECT paper_cancel_finish(" + sql_args + ")", **arguments)
    assert f.api.read_operation(operation["operationId"])["status"] == "RUNNING"
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
    assert (
        f.sql("execution", "SELECT paper_cancel_read(CAST(:id AS uuid))", id=claim["cancelId"])[
            "deleteAttempts"
        ]
        == 1
    )
    # A 204 alone did not release funds. Only genuine full account evidence
    # now proves the provider's terminal cancellation and unchanged cash.
    f.provider_orders[f.cancel_client_order_id].update(
        status="canceled", updated_at=m.utc_string(f.clock.now())
    )
    f.refresh(publish=False)
    assert f.receipt.ready
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "RELEASED"
    time.sleep(1.1)
    final = f.sql(
        "execution",
        "SELECT paper_cancel_claim(:owner,:fence,CAST(:worker AS uuid),30)",
        owner=f.owner,
        fence=f.fence,
        worker=worker,
    )
    assert final and final["lookupOnly"]
    arguments["lease"] = final["leaseFence"]
    assert f.sql("execution", "SELECT paper_cancel_finish(" + sql_args + ")", **arguments)
    for op in (operation, alias):
        assert f.api.read_operation(op["operationId"])["status"] == "SUCCEEDED"
    from services.control_api.paper_commands import PaperCommandError

    try:
        f.api.enqueue(
            "CANCEL_ORDER",
            dict(accountGeneration=1, confirmation="CANCEL_PAPER_ORDER"),
            "operator",
            "cancel-sql-command-0003",
            f.trace,
            order_id=f.cancel_order_id,
        )
    except PaperCommandError as error:
        assert error.code == "ORDER_NOT_FOUND"
    else:
        raise AssertionError("terminal cancel created an orphan command")
    for role in ("api", "worker"):
        try:
            f.sql(role, "SELECT paper_cancel_read(CAST(:id AS uuid))", id=claim["cancelId"])
        except Exception as error:
            assert getattr(getattr(error, "orig", error), "sqlstate", None) == "42501"
        else:
            raise AssertionError("private cancel proof exposed to another role")
    print(MARKER)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        {
            "retry": retry_guest,
            "fences": fences_guest,
            "promotion": promotion_guest,
            "fairness": fairness_guest,
        }.get(sys.argv[2] if len(sys.argv) > 2 else "", guest)()
    except Exception as error:
        root = getattr(error, "orig", error)
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"APW02_FAIL:{STAGE}:{type(root).__name__}:{Path(frame.filename).name}:{frame.lineno}"
        )
        raise SystemExit(1) from None
