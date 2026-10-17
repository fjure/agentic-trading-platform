"""Real PostgreSQL Alpaca binding checks; all provider I/O is MockTransport."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

ACCOUNT = "10000000-0000-4000-8000-000000000001"
ASSET = "20000000-0000-4000-8000-000000000001"
ORDER = "30000000-0000-4000-8000-000000000001"
TRACE = "a" * 32
INSTRUMENT = "BTC/USD"


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace: str, seed: str | None = None) -> str:
        return f"{namespace}:{seed}"


class Crash(BaseException):
    pass


class Server:
    def __init__(self, engine, *, role: str) -> None:
        self.engine = engine
        self.role = role
        self.calls: list[tuple[str, str]] = []
        self.account_id = ACCOUNT
        self.asset_id = ASSET
        self.orders: dict[str, dict[str, object]] = {}
        self.timeout_after_accept = False
        self.hide_lookup = False
        self.assert_durable = False

    def count(self, method: str, path: str) -> int:
        return self.calls.count((method, path))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        from sqlalchemy import text

        assert request.url.scheme == "https"
        assert request.url.host in (
            {"data.alpaca.markets", "paper-api.alpaca.markets"}
            if self.role == "market"
            else {"paper-api.alpaca.markets"}
        )
        assert request.headers["APCA-API-KEY-ID"] == "synthetic-key"
        assert request.headers["APCA-API-SECRET-KEY"] == "synthetic-secret"
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        now = datetime.now(UTC)
        stamp = now.isoformat().replace("+00:00", "Z")
        if path == "/v2/account" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": self.account_id,
                    "status": "ACTIVE",
                    "currency": "USD",
                    "account_blocked": False,
                    "trading_blocked": False,
                    "trade_suspended_by_user": False,
                    "crypto_status": "ACTIVE",
                    "cash": "2000.00",
                },
            )
        if path == "/v2/assets" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": self.asset_id,
                        "symbol": INSTRUMENT,
                        "class": "crypto",
                        "status": "active",
                        "tradable": True,
                        "min_trade_increment": "0.00000001",
                        "price_increment": "0.01",
                        "min_order_size": "0.00001",
                    }
                ],
            )
        if path == "/v2/clock" and method == "GET":
            return httpx.Response(200, json={"timestamp": stamp, "is_open": False})
        if self.role == "market" and path == "/v1beta3/crypto/us/bars" and method == "GET":
            floor = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)

            def bar(start: datetime) -> dict[str, str]:
                return {
                    "t": start.isoformat().replace("+00:00", "Z"),
                    "o": "100",
                    "h": "101",
                    "l": "99",
                    "c": "100",
                    "v": "1",
                }

            return httpx.Response(
                200,
                json={
                    "bars": {
                        INSTRUMENT: [
                            bar(floor - timedelta(minutes=30)),
                            bar(floor - timedelta(minutes=15)),
                        ]
                    },
                    "next_page_token": None,
                },
            )
        if self.role == "trading" and path == "/v2/orders:by_client_order_id" and method == "GET":
            found = (
                None if self.hide_lookup else self.orders.get(request.url.params["client_order_id"])
            )
            return httpx.Response(200, json=found) if found else httpx.Response(404)
        if self.role == "trading" and path == "/v2/orders" and method == "POST":
            if self.assert_durable:
                body = json.loads(request.content)
                with self.engine.connect() as observer:
                    row = observer.execute(
                        text("""
                        SELECT b.status,b.adapter,i.client_order_id
                        FROM public.broker_orders b JOIN public.order_intents i ON i.id=b.intent_id
                        WHERE i.client_order_id=:client
                    """),
                        {"client": body["client_order_id"]},
                    ).one()
                    assert (row.status, row.adapter, row.client_order_id) == (
                        "SUBMITTING",
                        "alpaca-paper",
                        body["client_order_id"],
                    )
            body = json.loads(request.content)
            order = {
                "id": ORDER,
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": body["side"],
                "type": body["type"],
                "time_in_force": "gtc",
                "order_class": "simple",
                "qty": body["qty"],
                "limit_price": body.get("limit_price"),
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
                "created_at": stamp,
                "updated_at": stamp,
            }
            self.orders[body["client_order_id"]] = order
            if self.timeout_after_accept:
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(200, json=order)
        raise AssertionError(f"unexpected provider route: {method} {path}")


def _crypto_fixture(ledger, now: datetime) -> None:
    from sqlalchemy import text

    from packages.domain import models as m

    with ledger.transaction() as tx:
        original_bar = tx.get(m.MarketBar, "bar")
        original_evidence = tx.get(m.EvidenceBundle, "ev")
        original_state = tx.get(m.MarketState, "ms")
        original_decision = tx.get(m.DecisionResult, "dec")
        original_position = tx.get(m.Position, "pos")
        original_proposal = tx.get(m.TradeProposal, "p-0")
        instrument = m.Instrument(
            INSTRUMENT,
            m.Market.CRYPTO,
            INSTRUMENT,
            "CRYPTO",
            "USD",
            1,
            "24/7",
            Decimal("0.00000001"),
            Decimal("0.01"),
        )
        bar = replace(original_bar, id="btc-bar", instrument_id=INSTRUMENT)
        evidence = replace(
            original_evidence, id="btc-evidence", instrument_id=INSTRUMENT, sources=(bar.id,)
        )
        position = replace(original_position, id="btc-position", instrument_id=INSTRUMENT)
        state = replace(
            original_state,
            id="btc-state",
            instrument_id=INSTRUMENT,
            market=m.Market.CRYPTO,
            bar=bar,
            evidence=evidence,
            position=position,
        )
        decision = replace(original_decision, id="btc-decision", market_state_id=state.id)
        proposal = replace(
            original_proposal,
            id="btc-proposal",
            decision_id=decision.id,
            market_state_id=state.id,
            instrument_id=INSTRUMENT,
            created_at=now,
            valid_until=now + timedelta(seconds=30),
            limit_price=Decimal("100.01"),
        )
        for item in (instrument, bar, evidence):
            tx.insert(item)
        for item in (position, decision, proposal):
            tx.insert(
                m.AuditEvent(
                    "audit-" + item.id, now, "fixture", "fixture", ("FIXTURE",), item.id, 1, TRACE
                )
            )
        tx.connection.execute(
            text("""
            INSERT INTO public.positions
              (id,instrument_id,currency,quantity,average_price,realized_pnl,ledger_version,account_id)
            VALUES ('btc-position','BTC/USD','USD',0,NULL,0,1,'paper')
        """)
        )
        tx.insert(state)
        tx.insert(decision)
        tx.insert(proposal)


def checks(ledger, execution_ledger, engine, now):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.domain import models as m
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.execution.broker_binding import AlpacaExecutionBinding
    from services.execution.broker_recovery import restore_alpaca_attempts
    from services.execution.gates import SubmitDenied
    from services.execution.intents import ExecutionProcess, submit_once

    scenario = sys.argv[2]
    _crypto_fixture(ledger, now)
    market_server = Server(engine, role="market")
    trading_server = Server(engine, role="trading")
    market = AlpacaMarketDataProvider(
        Clock(),
        Ids(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        enabled=True,
        enable_crypto=True,
        transport=httpx.MockTransport(market_server),
    )
    broker = AlpacaPaperBrokerAdapter(
        Clock(),
        Ids(),
        api_key="synthetic-key",
        api_secret="synthetic-secret",
        paper_enabled=True,
        expected_account_id=ACCOUNT,
        enable_crypto=True,
        transport=httpx.MockTransport(trading_server),
    )
    binding = AlpacaExecutionBinding(execution_ledger, "paper", broker, market)

    def attempted() -> int:
        return restore_alpaca_attempts(
            ledger=execution_ledger,
            account_id="paper",
            broker=broker,
            binding=binding,
            owner_id="fixture",
            fencing_token=1,
        )

    def publish():
        return binding.publish(INSTRUMENT, TRACE)

    def reserve_btc():
        risk = reserve(
            ledger=execution_ledger,
            account_id="paper",
            proposal_id="btc-proposal",
            limits=RiskLimits(),
        )
        assert risk.approved, risk.reason_codes
        with engine.connect() as observer:
            intent_id = observer.execute(
                text("""
                SELECT id FROM public.order_intents WHERE proposal_id='btc-proposal'
            """)
            ).scalar_one()
        return risk, intent_id

    def submit(intent_id: str, *, owner="fixture", fence=1):
        return submit_once(
            ledger=execution_ledger,
            account_id="paper",
            intent_id=intent_id,
            limits=RiskLimits(),
            broker=broker,
            binding=binding,
            paper_smoke_enabled=True,
            owner_id=owner,
            fencing_token=fence,
        )

    if scenario == "proofs":
        try:
            publish()
        except SubmitDenied:
            assert trading_server.count("POST", "/v2/orders") == 0
            raise
        with engine.connect() as observer:
            proof = observer.execute(
                text("""
                SELECT p.trading_capability_version,p.source_capability_version,
                       c.capability_version,c.continuous_session_confirmed,
                       p.provider_account_id,p.provider_asset_id
                FROM public.broker_binding_proofs p
                JOIN public.risk_instrument_context c
                  ON c.account_id=p.account_id AND c.instrument_id=p.instrument_id
                 AND c.version=p.context_version
                WHERE p.account_id='paper' AND p.instrument_id='BTC/USD'
            """)
            ).one()
            assert proof.trading_capability_version == proof.capability_version
            assert proof.source_capability_version > 0
            assert proof.continuous_session_confirmed
            assert str(proof.provider_account_id) == ACCOUNT
            assert str(proof.provider_asset_id) == ASSET
        for table in ("broker_account_bindings", "broker_asset_bindings", "broker_binding_proofs"):
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"DELETE FROM public.{table}"))
            except DBAPIError:
                pass
            else:
                raise AssertionError(f"{table} was mutable")
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE atl_worker"))
                connection.execute(text("SELECT count(*) FROM public.broker_binding_proofs"))
        except DBAPIError:
            pass
        else:
            raise AssertionError("worker role read private provider identities")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""
                    UPDATE public.risk_instrument_context
                    SET version=version+1 WHERE account_id='paper' AND instrument_id='BTC/USD'
                """)
                )
        except DBAPIError:
            pass
        else:
            raise AssertionError("risk context changed without same-transaction audit")
        assert not any(path.startswith("/v2/orders") for _, path in market_server.calls)
        print("PASS proofs: dual-source provenance, SQL immutability and separate transport")
        return

    if scenario == "mismatch":
        for attribute, changed in (
            ("account_id", "10000000-0000-4000-8000-000000000002"),
            ("asset_id", "20000000-0000-4000-8000-000000000002"),
        ):
            original = getattr(market_server, attribute)
            setattr(market_server, attribute, changed)
            try:
                publish()
            except SubmitDenied as exc:
                assert exc.reason_code == "BINDING_PROOF_MISMATCH"
            else:
                raise AssertionError(f"cross-provider {attribute} mismatch published")
            finally:
                setattr(market_server, attribute, original)
        with engine.connect() as observer:
            assert (
                observer.execute(
                    text("SELECT count(*) FROM public.broker_binding_proofs")
                ).scalar_one()
                == 0
            )
        assert trading_server.count("POST", "/v2/orders") == 0
        print(
            "PASS mismatch: provider account and asset mismatches denied before SQL proof or order"
        )
        return

    assert attempted() == 0
    assert trading_server.count("POST", "/v2/orders") == 0
    publish()
    with engine.connect() as observer:
        state = observer.execute(
            text("""
            SELECT safety_state,reconciliation_current
            FROM public.risk_account_context WHERE account_id='paper'
        """)
        ).one()
        assert state.safety_state == "PAUSE_NEW" and not state.reconciliation_current
        assert (
            observer.execute(
                text("""
            SELECT count(*) FROM public.outbox_events WHERE event_type='safety.changed.v1'
        """)
            ).scalar_one()
            > 0
        )
    if scenario != "activation":
        # Explicit synthetic fixture authority for testing the low-level submit
        # substrate. Production recovery remains blocked until PTV-25; this is
        # not a claim that provider metadata establishes inventory reconciliation.
        with ledger.transaction() as tx:
            version = (
                tx.connection.execute(
                    text("""
                SELECT version FROM public.risk_account_context WHERE account_id='paper'
                FOR UPDATE
            """)
                ).scalar_one()
                + 1
            )
            tx.insert(
                m.AuditEvent(
                    id="synthetic-alpaca-runtime",
                    occurred_at=datetime.now(UTC),
                    actor_id="fixture",
                    action="fixture.synthetic_runtime",
                    reason_codes=("TEST",),
                    entity_id="paper",
                    entity_version=version,
                    trace_id=TRACE,
                )
            )
            tx.connection.execute(
                text("""
                UPDATE public.risk_account_context
                SET version=:v,safety_state='RUNNING',reconciliation_current=true,
                    actor_id='fixture',trace_id=:trace WHERE account_id='paper'
            """),
                {"v": version, "trace": TRACE},
            )
    else:
        process = ExecutionProcess(
            ledger=execution_ledger,
            account_id="paper",
            limits=RiskLimits(),
            broker=broker,
            binding=binding,
            paper_smoke_enabled=True,
        )
        try:
            process.submit("not-reserved")
        except SubmitDenied:
            pass
        else:
            raise AssertionError("restart allowed ordinary Alpaca submit")
        try:
            process.recover("synthetic-request")
        except SubmitDenied as exc:
            assert exc.reason_code == "ALPACA_RECOVERY_PENDING"
        else:
            raise AssertionError("PTV-25 recovery gate opened")
        assert trading_server.count("POST", "/v2/orders") == 0
        print("PASS activation: first binding and restart remain paused pending PTV-25")
        return
    risk, intent_id = reserve_btc()
    with engine.connect() as observer:
        stored = observer.execute(
            text("""
            SELECT i.capability_version AS intent_capability_version,
                   c.capability_version AS context_capability_version
            FROM public.order_intents i JOIN public.risk_instrument_context c
              ON c.account_id='paper' AND c.instrument_id=i.instrument_id
            WHERE i.id=:id
        """),
            {"id": intent_id},
        ).one()
        assert stored.intent_capability_version == stored.context_capability_version

    if scenario == "stale-fence":
        for owner, fence in (("other", 1), ("fixture", 2)):
            try:
                submit(intent_id, owner=owner, fence=fence)
            except SubmitDenied:
                pass
            else:
                raise AssertionError("stale owner or fence submitted")
        with engine.connect() as observer:
            assert (
                observer.execute(text("SELECT count(*) FROM public.broker_orders")).scalar_one()
                == 0
            )
            assert (
                observer.execute(
                    text("""
                SELECT state FROM public.risk_reservations WHERE id=:id
            """),
                    {"id": risk.reservation_id},
                ).scalar_one()
                == "ACTIVE"
            )
        assert trading_server.count("POST", "/v2/orders") == 0
        print("PASS stale-fence: denied before I/O, reservation retained")
        return

    trading_server.assert_durable = True
    if scenario == "timeout":
        trading_server.timeout_after_accept = True
    if scenario == "commit-failure":
        # This deferred SQL trigger rejects only the post-I/O ACK transaction.
        # The earlier SUBMITTING transaction remains committed and observable.
        with engine.begin() as connection:
            connection.execute(
                text("""
                CREATE FUNCTION public.synthetic_reject_ack() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  RAISE EXCEPTION 'synthetic deferred commit failure';
                END $$
            """)
            )
            connection.execute(
                text("""
                CREATE CONSTRAINT TRIGGER synthetic_reject_ack
                AFTER UPDATE ON public.broker_orders
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                WHEN (NEW.status = 'ACKNOWLEDGED')
                EXECUTE FUNCTION public.synthetic_reject_ack()
            """)
            )
    if scenario in {"crash-before", "crash-after"}:
        original = broker.submit

        def interrupted(intent):
            if scenario == "crash-before":
                raise Crash()
            original(intent)
            raise Crash()

        broker.submit = interrupted
        try:
            submit(intent_id)
        except Crash:
            pass
        else:
            raise AssertionError("synthetic crash did not cross process boundary")
    elif scenario == "commit-failure":
        try:
            submit(intent_id)
        except DBAPIError:
            pass
        else:
            raise AssertionError("post-I/O deferred SQL failure did not abort commit")
    else:
        order = submit(intent_id)
        assert order.status is (
            m.OrderStatus.UNKNOWN if scenario == "timeout" else m.OrderStatus.ACKNOWLEDGED
        )
    expected_posts = 0 if scenario == "crash-before" else 1
    assert trading_server.count("POST", "/v2/orders") == expected_posts
    with engine.connect() as observer:
        row = observer.execute(
            text("""
            SELECT b.status,b.adapter,r.state
            FROM public.broker_orders b JOIN public.order_intents i ON i.id=b.intent_id
            JOIN public.risk_reservations r ON r.risk_decision_id=i.risk_decision_id
            WHERE i.id=:id
        """),
            {"id": intent_id},
        ).one()
        if scenario.startswith("crash") or scenario == "commit-failure":
            assert row.status == "SUBMITTING"
        elif scenario == "timeout":
            assert row.status == "UNKNOWN" and row.state == "HELD_UNKNOWN"
        else:
            assert row.status == "ACKNOWLEDGED" and row.adapter == "alpaca-paper"
            report = observer.execute(
                text("""
                SELECT r.reconciliation,r.fee FROM public.execution_reports r
                JOIN public.broker_orders b ON b.id=r.order_id
                WHERE b.intent_id=:id ORDER BY r.received_at DESC LIMIT 1
            """),
                {"id": intent_id},
            ).one()
            assert report.reconciliation == "PENDING" and report.fee is None
    if scenario != "success":
        fresh_broker = AlpacaPaperBrokerAdapter(
            Clock(),
            Ids(),
            api_key="synthetic-key",
            api_secret="synthetic-secret",
            paper_enabled=True,
            expected_account_id=ACCOUNT,
            enable_crypto=True,
            transport=httpx.MockTransport(trading_server),
        )
        fresh_binding = AlpacaExecutionBinding(execution_ledger, "paper", fresh_broker, market)
        count = restore_alpaca_attempts(
            ledger=execution_ledger,
            account_id="paper",
            broker=fresh_broker,
            binding=fresh_binding,
            owner_id="fixture",
            fencing_token=1,
        )
        assert count == 1
        trading_server.hide_lookup = True
        with engine.connect() as observer:
            client_id = observer.execute(
                text("""
                SELECT client_order_id FROM public.order_intents WHERE id=:id
            """),
                {"id": intent_id},
            ).scalar_one()
        recovered = fresh_broker.reconcile(client_id)
        assert recovered.status is m.OrderStatus.UNKNOWN
        assert trading_server.count("POST", "/v2/orders") == expected_posts
        with engine.connect() as observer:
            assert observer.execute(
                text("""
                SELECT state FROM public.risk_reservations WHERE id=:id
            """),
                {"id": risk.reservation_id},
            ).scalar_one() in ("ACTIVE", "HELD_UNKNOWN")
    print(f"PASS {scenario}: durable Alpaca attempt and no second POST")


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
