"""Actual PostgreSQL guest; fixed synthetic credentials and HTTP MockTransport only.

The fixture shifts the two adapters' timezone to place real UTC database time
inside a synthetic regular session. Calendar hours, freshness, capability gates,
activation, fencing and recovery remain active. This certifies no real venue
calendar, DST rule, credentials or external provider response.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import httpx

ACCOUNT = "10000000-0000-4000-8000-000000000001"
ASSET = "20000000-0000-4000-8000-000000000001"
ORDER = "30000000-0000-4000-8000-000000000001"
TRACE = "a" * 32
PROFILE = "alpaca-paper-us-retail-equity-zero-v1"


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace, seed=None):
        return f"{namespace}:{seed}"


class Crash(BaseException):
    pass


class Server:
    def __init__(self, zone):
        self.zone = zone
        self.orders = {}
        self.activities = []
        self.quantity = Decimal(0)
        self.average_price = Decimal("100")
        self.cash = Decimal(2000)
        self.account_id = ACCOUNT
        self.calls = []
        self.timeout = False
        self.crash_after = False
        self.missing_page = False
        self.session_open = True
        self.ack_partial = False

    def __call__(self, request):
        assert request.url.scheme == "https"
        assert request.url.host in {"paper-api.alpaca.markets", "data.alpaca.markets"}
        assert request.headers["APCA-API-KEY-ID"] == "synthetic-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "synthetic-secret"
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        now = datetime.now(UTC)
        stamp = now.isoformat()
        if self.timeout and path == "/v2/account/activities":
            raise httpx.ReadTimeout("synthetic unavailable", request=request)
        if path == "/v2/account":
            equity = self.cash + self.quantity * 100
            return httpx.Response(
                200,
                json={
                    "id": self.account_id,
                    "status": "ACTIVE",
                    "currency": "USD",
                    "account_blocked": False,
                    "trading_blocked": False,
                    "trade_suspended_by_user": False,
                    "cash": str(self.cash),
                    "equity": str(equity),
                    "portfolio_value": str(equity),
                },
            )
        if path in {"/v2/assets/SPY", "/v2/assets/QQQ"}:
            symbol = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": ASSET if symbol == "SPY" else "20000000-0000-4000-8000-000000000002",
                    "symbol": symbol,
                    "class": "us_equity",
                    "status": "active",
                    "tradable": True,
                    "fractionable": False,
                    "min_trade_increment": "1",
                    "price_increment": "0.01",
                },
            )
        if path == "/v2/clock":
            return httpx.Response(200, json={"timestamp": stamp, "is_open": self.session_open})
        if path == "/v2/calendar":
            return httpx.Response(
                200,
                json=[
                    {
                        "date": now.astimezone(self.zone).date().isoformat(),
                        "open": "09:30",
                        "close": "16:00",
                    }
                ],
            )
        if path == "/v2/stocks/bars":
            symbol = request.url.params["symbols"]
            floor = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
            return httpx.Response(
                200,
                json={
                    "bars": {
                        symbol: [
                            {
                                "t": (floor - timedelta(minutes=minutes)).isoformat(),
                                "o": "100",
                                "h": "101",
                                "l": "99",
                                "c": "100",
                                "v": "1",
                            }
                            for minutes in (30, 15)
                        ]
                    },
                    "next_page_token": None,
                },
            )
        if path == "/v2/positions":
            return httpx.Response(
                200,
                json=[]
                if not self.quantity
                else [
                    {
                        "asset_id": ASSET,
                        "symbol": "SPY",
                        "side": "long",
                        "qty": str(self.quantity),
                        "avg_entry_price": str(self.average_price),
                        "market_value": str(self.quantity * 100),
                    }
                ],
            )
        if path == "/v2/account/activities":
            if self.missing_page and "page_token" in request.url.params:
                return httpx.Response(503)
            return httpx.Response(200, json=self.activities)
        if path == "/v2/orders" and method == "GET":
            return httpx.Response(200, json=list(self.orders.values()))
        if path == "/v2/orders:by_client_order_id":
            order = self.orders.get(request.url.params["client_order_id"])
            return httpx.Response(200, json=order) if order else httpx.Response(404)
        if path == "/v2/orders" and method == "POST":
            body = json.loads(request.content)
            order = {
                "id": ORDER,
                "client_order_id": body["client_order_id"],
                "symbol": "SPY",
                "side": body["side"],
                "type": body["type"],
                "time_in_force": "day",
                "order_class": "simple",
                "qty": body["qty"],
                "limit_price": body.get("limit_price"),
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
                "created_at": stamp,
                "updated_at": stamp,
                "submitted_at": stamp,
            }
            self.orders[body["client_order_id"]] = order
            if self.ack_partial:
                self.fill(quantity="0.4", terminal=False)
            if self.crash_after:
                raise Crash()
            return httpx.Response(200, json=order)
        raise AssertionError(f"unexpected mock route {method} {path}")

    def fill(self, *, quantity="1", terminal=True, price="100"):
        order = next(iter(self.orders.values()))
        prior = Decimal(order["filled_qty"])
        total = prior + Decimal(quantity)
        stamp = datetime.now(UTC).isoformat()
        order.update(
            status="filled" if terminal else "partially_filled",
            filled_qty=str(total),
            filled_avg_price=price,
            updated_at=stamp,
        )
        self.quantity = total
        self.average_price = Decimal(price)
        self.cash = Decimal(2000) - total * Decimal(price)
        self.activities.append(
            {
                "id": f"synthetic::{len(self.activities) + 1}",
                "activity_type": "FILL",
                "transaction_time": stamp,
                "order_id": ORDER,
                "symbol": "SPY",
                "side": "buy",
                "qty": quantity,
                "price": price,
                "cum_qty": str(total),
                "leaves_qty": str(Decimal(order["qty"]) - total),
                "type": "fill" if terminal else "partial_fill",
            }
        )


def fixture(admin, now):
    from packages.domain import models as m

    with admin.transaction() as tx:
        bar = replace(tx.get(m.MarketBar, "bar"), id="paper-bar", instrument_id="SPY")
        evidence = replace(
            tx.get(m.EvidenceBundle, "ev"),
            id="paper-evidence",
            instrument_id="SPY",
            sources=(bar.id,),
        )
        position = tx.get(m.Position, "pos")
        state = replace(
            tx.get(m.MarketState, "ms"),
            id="paper-state",
            instrument_id="SPY",
            bar=bar,
            evidence=evidence,
            position=position,
        )
        decision = replace(
            tx.get(m.DecisionResult, "dec"), id="paper-decision", market_state_id=state.id
        )
        proposal = replace(
            tx.get(m.TradeProposal, "p-0"),
            id="paper-proposal",
            decision_id=decision.id,
            market_state_id=state.id,
            instrument_id="SPY",
            created_at=now,
            valid_until=now + timedelta(seconds=30),
            limit_price=Decimal("100.01"),
        )
        for item in (bar, evidence):
            tx.insert(item)
        for item in (decision, proposal):
            tx.insert(
                m.AuditEvent(
                    "audit-" + item.id, now, "fixture", "fixture", ("FIXTURE",), item.id, 1, TRACE
                )
            )
        tx.insert(state)
        tx.insert(decision)
        tx.insert(proposal)


def checks(admin, execution, engine, now):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.brokers import alpaca_market_data, alpaca_paper
    from packages.persistence import PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.control_api.control import request_paper_activation, request_resume
    from services.execution.broker_binding import AlpacaExecutionBinding
    from services.execution.intents import ExecutionProcess
    from services.execution.paper_reconciliation import reconcile_paper_account

    scenario = sys.argv[2]
    # Real database/UTC timestamps; only the mock venue's timezone is synthetic.
    zone = timezone(timedelta(hours=12 - datetime.now(UTC).hour))
    alpaca_market_data._EASTERN = zone
    alpaca_paper._EASTERN = zone
    fixture(admin, now)
    if scenario == "activation":
        from packages.domain import models as m

        with admin.transaction() as tx:
            version = tx.connection.execute(
                text("SELECT version+1 FROM risk_account_context WHERE account_id='paper'")
            ).scalar_one()
            tx.insert(
                m.AuditEvent(
                    "fixture-disabled",
                    now,
                    "fixture",
                    "fixture",
                    ("FIXTURE",),
                    "paper",
                    version,
                    TRACE,
                )
            )
            tx.connection.execute(
                text("""
                UPDATE risk_account_context SET version=:v,mode='DISABLED',
                       actor_id='fixture',trace_id=:trace WHERE account_id='paper'
            """),
                {"v": version, "trace": TRACE},
            )
    server = Server(zone)
    market = alpaca_market_data.AlpacaMarketDataProvider(
        Clock(),
        Ids(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        transport=httpx.MockTransport(server),
    )

    def make_process(profile=PROFILE):
        broker = alpaca_paper.AlpacaPaperBrokerAdapter(
            Clock(),
            Ids(),
            api_key="synthetic-key",
            api_secret="synthetic-secret",
            paper_enabled=True,
            expected_account_id=ACCOUNT,
            reconciliation_fee_profile=profile,
            transport=httpx.MockTransport(server),
        )
        binding = AlpacaExecutionBinding(execution, "paper", broker, market)
        binding.publish("SPY", TRACE)
        binding.publish("QQQ", TRACE)
        return ExecutionProcess(
            ledger=execution,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            binding=binding,
            paper_smoke_enabled=True,
        )

    role_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(role_engine, "connect")
    def assign(connection, _record):
        connection.execute("SET ROLE atl_control_api")
        connection.commit()

    control = PostgresLedger(engine=role_engine)

    def request():
        with engine.connect() as c:
            mode = c.execute(text("SELECT mode FROM risk_account_context")).scalar_one()
        operation = request_paper_activation if mode == "DISABLED" else request_resume
        return operation(
            ledger=control,
            account_id="paper",
            actor_id="operator",
            trace_id=TRACE,
            expected_policy_version=1,
        )

    def receipt(process):
        return reconcile_paper_account(
            ledger=execution,
            account_id="paper",
            broker=process.broker,
            binding=process.binding,
            owner_id=process.owner_id,
            fencing_token=process.fencing_token,
        )

    process = make_process(profile=None if scenario == "unknown-fees" else PROFILE)
    if scenario == "unknown-fees":
        from services.control_api.control import ControlDenied

        result = receipt(process)
        assert not result.ready and result.reason_code == "PAPER_FEE_AUTHORITY_UNKNOWN"
        try:
            process.recover(request())
        except ControlDenied:
            pass
        else:
            raise AssertionError("unknown fee authority activated Paper")
        assert server.calls.count(("POST", "/v2/orders")) == 0
        role_engine.dispose()
        print("PASS unknown-fees: absent fee authority denies actual recovery")
        return
    process.recover(request())
    with engine.connect() as c:
        assert (
            c.execute(text("SELECT safety_state FROM risk_account_context")).scalar_one()
            == "RUNNING"
        )
    # MarketState must reference the actual recovered portfolio revision.
    from packages.domain import models as m

    with admin.transaction() as tx:
        snapshot_id = tx.connection.execute(
            text("SELECT portfolio_snapshot_id FROM risk_account_context WHERE account_id='paper'")
        ).scalar_one()
        state = replace(
            tx.get(m.MarketState, "paper-state"),
            id="fresh-state",
            portfolio=tx.get(m.PortfolioSnapshot, snapshot_id),
        )
        decision = replace(
            tx.get(m.DecisionResult, "paper-decision"),
            id="fresh-decision",
            market_state_id=state.id,
        )
        proposal = replace(
            tx.get(m.TradeProposal, "paper-proposal"),
            id="fresh-proposal",
            decision_id=decision.id,
            market_state_id=state.id,
            created_at=datetime.now(UTC),
            valid_until=datetime.now(UTC) + timedelta(seconds=29),
        )
        tx.insert(state)
        for item in (decision, proposal):
            tx.insert(
                m.AuditEvent(
                    "audit-" + item.id, now, "fixture", "fixture", ("FIXTURE",), item.id, 1, TRACE
                )
            )
            tx.insert(item)
    risk = reserve(
        ledger=execution, account_id="paper", proposal_id="fresh-proposal", limits=RiskLimits()
    )
    assert risk.approved, risk.reason_codes
    with engine.connect() as c:
        intent = c.execute(
            text("SELECT id FROM order_intents WHERE proposal_id='fresh-proposal'")
        ).scalar_one()
    if scenario == "crash-after":
        server.crash_after = True
        try:
            process.submit(intent)
        except Crash:
            pass
        else:
            raise AssertionError("synthetic process crash did not propagate")
        server.crash_after = False
        process = make_process()
    else:
        server.ack_partial = scenario == "ack-partial"
        process.submit(intent)
    if scenario == "restart-idempotent":
        server.fill(quantity="0.4", terminal=False)
        first = receipt(process)
        assert first.ready, first.reason_code
        server.fill(quantity="0.6", terminal=True)
        server.activities.reverse()
    else:
        server.fill(
            quantity="0.6" if scenario == "ack-partial" else "1",
            terminal=True,
            price="101" if scenario == "limit-violation" else "100",
        )

    if scenario == "resume-denied-after-fill":
        from services.control_api.control import ControlDenied, pause

        pause(
            ledger=control,
            account_id="paper",
            actor_id="operator",
            trace_id=TRACE,
            reason_code="PAUSED_NEW",
        )
        server.session_open = False
        try:
            process.recover(request())
        except ControlDenied:
            pass
        else:
            raise AssertionError("closed session allowed resume")
        with engine.connect() as c:
            assert (
                c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one() == 1
            )
            assert c.execute(
                text("""
                SELECT p.cash FROM portfolio_snapshots p JOIN risk_account_context r
                ON p.id=r.portfolio_snapshot_id WHERE r.account_id='paper'
            """)
            ).scalar_one() == Decimal("1900")
            assert (
                c.execute(text("SELECT safety_state FROM risk_account_context")).scalar_one()
                == "PAUSE_NEW"
            )
            assert c.execute(text("SELECT count(*) FROM paper_activity_evidence")).scalar_one() == 1
        assert server.calls.count(("POST", "/v2/orders")) == 1
        role_engine.dispose()
        print("PASS resume-denied-after-fill: actual resume denied; confirmed fill committed")
        return

    if scenario == "owner-expiry-after-book":
        from services.execution import paper_reconciliation
        from services.execution.gates import SubmitDenied

        original = paper_reconciliation._book

        def expire_after_book(tx, *args, **kwargs):
            original(tx, *args, **kwargs)
            tx.connection.execute(
                text("""
                UPDATE public.execution_owners
                SET heartbeat_at=clock_timestamp()-interval '31 seconds'
                WHERE account_id='paper'
            """)
            )

        paper_reconciliation._book = expire_after_book
        try:
            receipt(process)
        except SubmitDenied as exc:
            assert exc.reason_code == "EXECUTION_HEARTBEAT_STALE"
        else:
            raise AssertionError("expired owner committed money after book")
        finally:
            paper_reconciliation._book = original
        with engine.connect() as c:
            assert (
                c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one() == 0
            )
            assert c.execute(text("SELECT count(*) FROM paper_activity_evidence")).scalar_one() == 0
            assert (
                c.execute(
                    text("SELECT state FROM risk_reservations WHERE id=:id"),
                    {"id": risk.reservation_id},
                ).scalar_one()
                == "ACTIVE"
            )
        role_engine.dispose()
        print("PASS owner-expiry-after-book: final owner gate rolled back all money")
        return

    if scenario in {"partial-cancel", "cancel-fill-race", "terminal-regression"}:
        server.activities[0].update(qty="0.4", cum_qty="0.4", leaves_qty="0.6", type="partial_fill")
        server.quantity, server.cash = Decimal("0.4"), Decimal("1960")
        next(iter(server.orders.values())).update(status="canceled", filled_qty="0.4")
        if scenario == "cancel-fill-race":
            assert receipt(process).ready
            server.fill(quantity="0.6", terminal=True)
        elif scenario == "terminal-regression":
            assert receipt(process).ready
            server.cash -= Decimal("1")
            assert not receipt(process).ready
            server.cash += Decimal("1")
            next(iter(server.orders.values())).update(status="partially_filled")
            # Remove adapter-local terminal memory: only durable SQL history
            # may prevent this regression after a real process replacement.
            process = make_process()
    elif scenario == "profile-change":
        process = make_process(profile=None)
    elif scenario in {"daily-fee", "delayed-fee"}:
        if scenario == "delayed-fee":
            assert receipt(process).ready
            process.recover(request())
        server.activities.append(
            {
                "id": "synthetic::fee",
                "activity_type": "FEE",
                "date": datetime.now(UTC).date().isoformat(),
                "net_amount": "-0.1",
            }
        )
        server.cash -= Decimal("0.1")
    elif scenario == "missing-page":
        original = server.activities[0]
        server.activities = [
            dict(
                original,
                id=f"synthetic::{i}",
                qty="0.01",
                cum_qty=str(Decimal(i) / 100),
                leaves_qty=str(1 - Decimal(i) / 100),
                type="fill" if i == 100 else "partial_fill",
            )
            for i in range(1, 101)
        ]
        server.missing_page = True
    elif scenario == "missing-fill":
        server.activities.clear()
    elif scenario == "foreign-order":
        foreign = dict(
            next(iter(server.orders.values())),
            id="30000000-0000-4000-8000-000000000002",
            client_order_id="foreign",
        )
        server.orders["foreign"] = foreign
    elif scenario == "position-mismatch":
        server.quantity = Decimal("2")
    elif scenario == "cash-mismatch":
        server.cash -= Decimal("1")
    elif scenario == "account-mismatch":
        server.account_id = "10000000-0000-4000-8000-000000000002"
    elif scenario == "timeout":
        server.timeout = True

    result = receipt(process)
    happy = scenario in {
        "recovery",
        "activation",
        "partial-cancel",
        "restart-idempotent",
        "private-audit",
        "crash-after",
        "ack-partial",
        "cancel-fill-race",
    }
    assert result.ready == happy, result.reason_code
    if happy:
        expected = Decimal("0.4") if scenario == "partial-cancel" else Decimal("1")
        with engine.connect() as c:
            assert (
                c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one()
                == expected
            )
            assert (
                c.execute(
                    text("SELECT cash FROM portfolio_snapshots WHERE id=:id"),
                    {"id": result.portfolio_snapshot_id},
                ).scalar_one()
                == 2000 - expected * 100
            )
        repeat = receipt(process)
        assert repeat.ready, repeat.reason_code
        if scenario == "restart-idempotent":
            server.activities.reverse()
            process = make_process()
            assert receipt(process).ready
        with engine.connect() as c:
            assert (
                c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one()
                == expected
            )
            assert (
                c.execute(
                    text("""
                SELECT p.cash FROM portfolio_snapshots p JOIN risk_account_context r
                ON p.id=r.portfolio_snapshot_id WHERE r.account_id='paper'
            """)
                ).scalar_one()
                == 2000 - expected * 100
            )
            assert c.execute(
                text("SELECT count(*) FROM paper_activity_evidence WHERE activity_kind='FILL'")
            ).scalar_one() == (
                2 if scenario in {"restart-idempotent", "cancel-fill-race", "ack-partial"} else 1
            )
            rows = (
                c.execute(
                    text("""
                SELECT p.evidence,p.evidence_hash,p.order_id,p.trace_id,
                       b.id AS local_order_id,i.trace_id AS intent_trace
                FROM paper_activity_evidence p JOIN broker_orders b ON b.id=p.order_id
                JOIN order_intents i ON i.id=b.intent_id WHERE p.activity_kind='FILL'
            """)
                )
                .mappings()
                .all()
            )
            original_fills = {a["id"]: a for a in server.activities}
            assert len(rows) == len(original_fills)
            for row in rows:
                payload = row["evidence"]
                source = original_fills[payload["id"]]
                assert row["order_id"] == row["local_order_id"]
                assert row["trace_id"] == row["intent_trace"] == TRACE
                assert payload["order_id"] == ORDER
                for key, raw in (
                    ("quantity", "qty"),
                    ("price", "price"),
                    ("cumulative_quantity", "cum_qty"),
                    ("leaves_quantity", "leaves_qty"),
                ):
                    assert Decimal(payload[key]) == Decimal(source[raw])
                assert (
                    row["evidence_hash"]
                    == hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                )
        process.recover(request())
    else:
        with engine.connect() as c:
            state = c.execute(
                text("SELECT safety_state,reconciliation_current FROM risk_account_context")
            ).one()
            assert state.safety_state == "PAUSE_NEW" and not state.reconciliation_current
            reservation_state = c.execute(
                text("SELECT state FROM risk_reservations WHERE id=:id"),
                {"id": risk.reservation_id},
            ).scalar_one()
            if scenario == "terminal-regression":
                # A prior definitive partial cancellation already settled this
                # budget. Conflicting status must never reopen it as ACTIVE.
                assert result.reason_code == "TERMINAL_STATUS_CORRECTION", result.reason_code
                assert reservation_state == "CONSUMED"
                assert c.execute(
                    text("SELECT reserved_notional FROM risk_reservations WHERE id=:id"),
                    {"id": risk.reservation_id},
                ).scalar_one() == 0
                assert c.execute(
                    text("SELECT quantity FROM positions WHERE id='pos'")
                ).scalar_one() == Decimal("0.4")
            else:
                assert reservation_state in {"ACTIVE", "HELD_UNKNOWN"}
            assert (
                c.execute(
                    text("SELECT fee FROM execution_reports ORDER BY received_at DESC LIMIT 1")
                ).scalar_one()
                is None
            )
            if scenario in {"daily-fee", "delayed-fee"}:
                assert (
                    c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one()
                    == 1
                )
                assert c.execute(
                    text("SELECT cash FROM portfolio_snapshots WHERE id=:id"),
                    {"id": result.portfolio_snapshot_id},
                ).scalar_one() == Decimal("1899.9")

            if scenario == "limit-violation":
                assert result.reason_code == "FILL_LIMIT_PRICE_MISMATCH"
                assert (
                    c.execute(text("SELECT quantity FROM positions WHERE id='pos'")).scalar_one()
                    == 1
                )
                assert c.execute(
                    text("SELECT cash FROM portfolio_snapshots WHERE id=:id"),
                    {"id": result.portfolio_snapshot_id},
                ).scalar_one() == Decimal("1899")

    if scenario == "private-audit":
        tables = (
            "paper_reconciliation_baselines",
            "paper_activity_evidence",
            "paper_reconciliation_receipts",
            "paper_order_accounting_bases",
        )
        for table in tables:
            for statement in (
                f"UPDATE public.{table} SET trace_id=trace_id",
                f"DELETE FROM public.{table}",
                f"TRUNCATE public.{table}",
            ):
                try:
                    with engine.begin() as c:
                        c.execute(text(statement))
                except DBAPIError:
                    pass
                else:
                    raise AssertionError(f"immutable table accepted {statement}")
            for role in ("atl_worker", "atl_control_api", "atl_watchdog"):
                try:
                    with engine.begin() as c:
                        c.execute(text(f"SET LOCAL ROLE {role}"))
                        c.execute(text(f"SELECT * FROM public.{table}"))
                except DBAPIError:
                    pass
                else:
                    raise AssertionError(f"{role} read private evidence")
            with engine.connect() as c:
                assert (
                    c.execute(
                        text(f"""
                    SELECT count(*) FROM public.{table} p WHERE NOT EXISTS (
                        SELECT 1 FROM public.audit_events a
                        WHERE a.entity_id=p.id AND a.entity_version=1
                          AND a.actor_id=p.actor_id AND a.trace_id=p.trace_id
                    )
                """)
                    ).scalar_one()
                    == 0
                )
        try:
            with engine.begin() as c:
                c.execute(
                    text("""
                    INSERT INTO public.paper_reconciliation_receipts
                      (id,account_id,owner_id,fencing_token,portfolio_snapshot_id,
                       portfolio_version,context_version,ready,reason_code,observed_at,
                       actor_id,trace_id)
                    SELECT 'unaudited-receipt',account_id,owner_id,fencing_token,
                           portfolio_snapshot_id,portfolio_version,context_version,
                           ready,reason_code,observed_at,actor_id,trace_id
                    FROM public.paper_reconciliation_receipts LIMIT 1
                """)
                )
        except DBAPIError as exc:
            assert "same-transaction audit" in str(exc.orig)
        else:
            raise AssertionError("receipt without atomic audit committed")
    assert server.calls.count(("POST", "/v2/orders")) == 1
    role_engine.dispose()
    print(f"PASS {scenario}: real PostgreSQL Paper evidence and one mock POST")


if __name__ == "__main__":
    from risk_support import guest

    guest(checks, uppercase_instruments=True)
