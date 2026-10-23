"""Real authenticated HTTP/manual-command/Risk/Execution chain; offline broker.

Positive equity-session checks retain genuine SQL UTC time, 15-minute bars and
120-second freshness. The bounded host wait admits weekday quarter windows only;
synthetic venue timezone is the sole calendar configuration seam. No financial
readiness, collector, source proof, risk or submit gate is replaced.
"""

from pathlib import Path

MARKER = "PASS: public human Paper LIMIT provenance and shared financial chain"
RECLAIM_MARKER = "PASS: human ACK crash pause rotation and genuine lease recovery"


def test_paper_human_order_http_sql_chain():
    run_host(MARKER)


def test_paper_human_ack_crash_reclaim():
    run_host(RECLAIM_MARKER, ("reclaim",))


def run_host(marker, arguments=()):
    import time
    from datetime import UTC, datetime

    import pytest

    from tests.integration.test_paper_provision import host

    now = datetime.now(UTC)
    if now.weekday() >= 5:
        pytest.skip("Positive real-clock equity chain requires a weekday")
    if (now.minute % 15) * 60 + now.second >= 45:
        print("Waiting for genuine next UTC quarter +5s human-order freshness window")
        deadline = time.monotonic() + 905
        while time.monotonic() < deadline:
            now = datetime.now(UTC)
            if now.minute % 15 == 0 and 5 <= now.second < 45:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("Quarter window wait exceeded bound")
    assert (
        host(
            Path(__file__),
            marker,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
            guest_args=arguments,
        )
        == marker
    )


def guest():
    import json
    import os
    import sys
    import tempfile
    import time as elapsed_time
    import uuid
    from datetime import UTC, date, datetime, time, timedelta, timezone
    from decimal import Decimal
    from unittest.mock import patch

    import httpx
    import psycopg
    from fastapi.testclient import TestClient
    from test_paper_lifecycle import PaperFixture

    from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.domain import models as m
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.paper_client import PaperClient
    from services.control_api.paper_commands import PaperCommandError
    from services.execution.paper_credentials import PaperGenerationReader
    from services.execution.paper_daily_refresh import PaperDailyRefreshCollector
    from services.execution.paper_verification import PaperAccountVerifier
    from services.runtime.config import RuntimeConfig
    from services.runtime.paper_api import create_private_paper_app
    from services.runtime.paper_coordinator import PaperRuntimeCoordinator
    from services.setup.store import ProviderStore
    from services.worker.market_state import PaperMarketConfig

    f = PaperFixture()
    f.prepare()
    profile = f.profile()
    now = datetime.now(UTC)
    quarter = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    zone = timezone(timedelta(hours=12) - timedelta(hours=quarter.hour, minutes=quarter.minute))
    assert now.weekday() < 5
    config = RuntimeConfig(deployment="paper", broker_provider="alpaca-paper")
    market_config = PaperMarketConfig(
        "synthetic-quarter-v1",
        "a" * 64,
        "alpaca-market-data:iex",
        "PT15M",
        120,
        Decimal(".01"),
        2,
        {
            s: m.Instrument(s, m.Market.US_EQUITY, s, "ETF", "USD", 1, "XNYS")
            for s in config.instruments
        },
        date(2000, 1, 1),
        date(2100, 1, 1),
        frozenset(),
        {},
        zone,
        time(9, 30),
        time(16),
    )
    orders, submitted, credential_reads = {}, [], []
    provider_state = dict(
        cash=Decimal(100000),
        positions=[],
        activities=[],
        timeout_once=False,
        fractionable=True,
        stale_quote=False,
        reject_once=False,
    )
    private_directory = tempfile.TemporaryDirectory()
    private_root = Path(private_directory.name)
    (private_root / "alpaca").mkdir(mode=0o700)
    store = ProviderStore(private_root, owner_uid=os.getuid())
    synthetic_credentials = dict(
        apiKey="synthetic-key-canary",
        apiSecret="synthetic-secret-canary",
        expectedAccountId=f.account,
        paperOnly=True,
    )
    store.save_generation("alpaca", 1, synthetic_credentials)
    assets = dict(
        SPY="22222222-2222-4222-8222-222222222222", QQQ="33333333-3333-4333-8333-333333333333"
    )

    class Reader(PaperGenerationReader):
        def __init__(self):
            super().__init__(private_root, owner_uid=os.getuid())

        def load(self, generation):
            credential_reads.append(generation)
            assert generation in (1, 2)
            return super().load(generation)

    def provider(request):
        assert request.url.host in {"paper-api.alpaca.markets", "data.alpaca.markets"}
        # A separate real connection can take the financial row lock at every
        # HTTP boundary; a provider wait must never retain that account lock.
        with psycopg.connect(dbname=f.database, **f.common) as probe:
            assert probe.execute(
                "SELECT id FROM accounts WHERE id='paper' FOR UPDATE NOWAIT"
            ).fetchone() == ("paper",)
            if request.method == "POST":
                assert (
                    probe.execute(
                        "SELECT count(*) FROM broker_orders WHERE status='SUBMITTING'"
                    ).fetchone()[0]
                    >= 1
                )
        path = request.url.path
        f.http_calls.append((request.method, path))
        n = f.clock.now()
        stamp = n.isoformat().replace("+00:00", "Z")
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.content)
            client = body["client_order_id"]
            assert client not in orders and body["type"] == "limit"
            assert body["time_in_force"] == "day" and not body.get("extended_hours", False)
            submitted.append(body)
            value = body | dict(
                id=str(uuid.uuid4()),
                status="new",
                filled_qty="0",
                filled_avg_price=None,
                submitted_at=stamp,
                updated_at=stamp,
                created_at=stamp,
                asset_id=assets[body["symbol"]],
                asset_class="us_equity",
            )
            if provider_state["reject_once"]:
                provider_state["reject_once"] = False
                value["status"] = "rejected"
            orders[client] = value
            if provider_state["timeout_once"]:
                provider_state["timeout_once"] = False
                raise httpx.ReadTimeout("synthetic reply lost after provider acceptance")
        else:
            assert request.method == "GET"
            if path == "/v2/clock":
                value = dict(timestamp=stamp, is_open=True)
            elif path == "/v2/account":
                value = dict(
                    id=f.account,
                    currency="USD",
                    cash=str(provider_state["cash"]),
                    equity="100000",
                    portfolio_value="100000",
                    status="ACTIVE",
                    account_blocked=False,
                    trading_blocked=False,
                    trade_suspended_by_user=False,
                )
            elif path == "/v2/positions":
                value = provider_state["positions"]
            elif path == "/v2/account/activities":
                value = provider_state["activities"]
            elif path == "/v2/positions/SPY":
                value = next((p for p in provider_state["positions"] if p["symbol"] == "SPY"), None)
                if value is None:
                    return httpx.Response(404, json={"code": 40410000})
            elif path == "/v2/orders":
                value = list(orders.values())
            elif path == "/v2/orders:by_client_order_id":
                value = orders.get(request.url.params["client_order_id"])
                if value is None:
                    return httpx.Response(404, json={"code": 40410000})
            elif path.startswith("/v2/orders/"):
                value = next(
                    order for order in orders.values() if order["id"] == path.rsplit("/", 1)[1]
                )
            elif path.startswith("/v2/assets/"):
                symbol = path.rsplit("/", 1)[1]
                value = dict(
                    id=assets[symbol],
                    symbol=symbol,
                    **{"class": "us_equity"},
                    status="active",
                    tradable=True,
                    fractionable=provider_state["fractionable"],
                )
            elif path == "/v2/calendar":
                value = [
                    dict(date=n.astimezone(zone).date().isoformat(), open="09:30", close="16:00")
                ]
            elif path == "/v2/stocks/quotes/latest":
                quote_stamp = (
                    (n - timedelta(seconds=121)).isoformat()
                    if provider_state["stale_quote"]
                    else stamp
                )
                value = dict(
                    quotes={
                        symbol: dict(bp=14.99, ap=15, t=quote_stamp)
                        for symbol in request.url.params["symbols"].split(",")
                    }
                )
            elif path == "/v2/stocks/bars":
                symbol = request.url.params["symbols"]
                value = dict(
                    bars={
                        symbol: [
                            dict(
                                t=(quarter - timedelta(minutes=minutes)).isoformat(),
                                o=14,
                                h=16,
                                l=13,
                                c=close,
                                v=100,
                            )
                            for minutes, close in ((30, 14), (15, 15))
                        ]
                    },
                    next_page_token=None,
                )
            else:
                raise AssertionError("Unexpected synthetic provider route")
        return httpx.Response(200, json=value)

    transport = httpx.MockTransport(provider)
    coordinator = PaperRuntimeCoordinator(
        config,
        f.ledger,
        reader_factory=Reader,
        broker_factory=lambda *a, **k: AlpacaPaperBrokerAdapter(*a, **k, transport=transport),
        market_factory=lambda *a, **k: AlpacaMarketDataProvider(*a, **k, transport=transport),
        daily_factory=lambda **k: PaperDailyRefreshCollector(**k, transport=transport),
    )
    coordinator.verifier.verifier_factory = lambda **k: PaperAccountVerifier(
        **k, transport=transport
    )
    command_errors, observed_source_errors = [], []
    for attribute in ("start_handler", "manual_handler"):
        original = getattr(coordinator.verifier, attribute)
        assert callable(original)

        def observe(claim, owner, original=original):
            try:
                return original(claim, owner)
            except PaperCommandError:
                raise
            except Exception as error:
                if provider_state["stale_quote"] and type(error).__name__ == "MarketDataError":
                    observed_source_errors.append(error.reason)
                else:
                    command_errors.append(error)
                raise

        setattr(coordinator.verifier, attribute, observe)

    def tick():
        result = coordinator.command_tick()
        if command_errors:
            raise command_errors.pop()
        return result

    private_api = create_private_paper_app(f.api)
    relayed, public_values = [], []

    def relay(request):
        assert request.url.host == "paper-api"
        relayed.append((request.method, request.url.path))
        with TestClient(
            private_api, base_url="http://paper-api:8012", client=("127.0.0.1", 46001)
        ) as client:
            response = client.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=dict(request.headers),
            )
        return httpx.Response(
            response.status_code, headers=dict(response.headers), content=response.content
        )

    origin = "http://127.0.0.1:8000"
    auth = LocalAuth()
    paper = PaperClient(transport=httpx.MockTransport(relay))
    public = TestClient(
        create_app(auth=auth, paper=paper),
        base_url=origin,
        client=("127.0.0.1", 46000),
        raise_server_exceptions=False,
    )

    def request(method, path, body=None, key="human-default-key-0001"):
        response = public.request(
            method, path, json=body, headers={"Origin": origin, "Idempotency-Key": key}
        )
        public_values.append(response.text)
        return response

    session = request(
        "POST",
        "/api/v1/session",
        {"activationCode": auth.issue_activation_code()},
        "human-session-key-0001",
    )
    assert session.status_code == 204
    terms = {
        k: profile[k]
        for k in (
            "credentialGeneration",
            "accountGeneration",
            "policyVersion",
            "profileVersion",
            "limitPolicyVersion",
            "budgetVersion",
            "riskBudget",
            "currency",
        )
    }

    def operation(identity):
        response = request("GET", "/api/v1/paper/operations/" + identity)
        assert response.status_code == 200
        return response.json()

    def place(body, key):
        response = request("POST", "/api/v1/paper/order-requests", body, key)
        assert response.status_code == 202
        return response.json()

    def limit(**changes):
        return (
            terms
            | dict(
                symbol="SPY",
                side="BUY",
                quantity="1",
                limitPrice="15",
                orderType="LIMIT",
                timeInForce="DAY",
                session="REGULAR",
                confirmation="PLACE_PAPER_LIMIT",
                expiresAt=(f.clock.now() + timedelta(seconds=20))
                .isoformat()
                .replace("+00:00", "Z"),
            )
            | changes
        )

    def provenance(accepted, result, body):
        # Owner inspection is read-only; effects must originate through HTTP.
        with psycopg.connect(dbname=f.database, **f.common) as c:
            row = c.execute(
                "SELECT p.id,p.decision_id,p.human_request_id,p.trace_id,r.terms,"
                "r.actor_id,ms.trace_id,rd.proposal_id,oi.trace_id,bo.trace_id,bo.id "
                "FROM paper_human_requests r JOIN trade_proposals p ON p.human_request_id=r.id "
                "JOIN market_states ms ON ms.id=p.market_state_id "
                "JOIN risk_decisions rd ON rd.proposal_id=p.id "
                "JOIN order_intents oi ON oi.proposal_id=p.id "
                "JOIN broker_orders bo ON bo.intent_id=oi.id WHERE r.operation_id=%s",
                (accepted["operationId"],),
            ).fetchone()
            assert row is not None
            (
                proposal,
                decision,
                human,
                trace,
                bound,
                actor,
                market_trace,
                risk_proposal,
                intent_trace,
                broker_trace,
                order,
            ) = row
            assert str(uuid.UUID(proposal)) == proposal and decision is None
            assert human == accepted["operationId"] and trace == accepted["traceId"]
            assert (
                trace == bound["trace_id"] == intent_trace == broker_trace and trace != market_trace
            )
            assert (
                actor == "local-operator"
                and risk_proposal == proposal
                and order == result["orderId"]
            )
            assert bound["operation_id"] == human and bound["symbol"] == body["symbol"]
            for internal, public_name in (
                ("account_generation", "accountGeneration"),
                ("credential_generation", "credentialGeneration"),
                ("policy_version", "policyVersion"),
                ("profile_version", "profileVersion"),
                ("limit_policy_version", "limitPolicyVersion"),
                ("budget_version", "budgetVersion"),
            ):
                assert bound[internal] == body[public_name]
            assert Decimal(bound["risk_budget"]) == Decimal(body["riskBudget"])
            assert datetime.fromisoformat(bound["created_at"]) == datetime.fromisoformat(
                accepted["acceptedAt"]
            )
            assert bound["side"] == body["side"]
            assert Decimal(bound["quantity"]) == Decimal(body["quantity"])
            assert Decimal(bound["limit_price"]) == Decimal(body["limitPrice"])
            assert datetime.fromisoformat(bound["valid_until"]) == datetime.fromisoformat(
                body["expiresAt"]
            )
            assert (
                c.execute(
                    "SELECT count(*) FROM audit_events WHERE trace_id=%s AND entity_id IN (%s,%s)",
                    (trace, human, proposal),
                ).fetchone()[0]
                >= 2
            )

    def fill_buy(client):
        order = orders[client]
        stamp = m.utc_string(f.clock.now())
        quantity, price = Decimal(order["qty"]), Decimal(order["limit_price"])
        cost = quantity * price
        order.update(
            status="filled",
            filled_qty=order["qty"],
            filled_avg_price=order["limit_price"],
            updated_at=stamp,
            filled_at=stamp,
        )
        provider_state["cash"] -= cost
        provider_state["positions"] = [
            dict(
                asset_id=assets["SPY"],
                symbol="SPY",
                side="long",
                qty=order["qty"],
                qty_available=order["qty"],
                avg_entry_price=order["limit_price"],
                market_value=str(cost),
                cost_basis=str(cost),
            )
        ]
        provider_state["activities"].append(
            dict(
                id="synthetic::human-buy-fill",
                activity_type="FILL",
                transaction_time=stamp,
                created_at=stamp,
                order_id=order["id"],
                symbol="SPY",
                side="buy",
                qty=order["qty"],
                price=order["limit_price"],
                cum_qty=order["qty"],
                leaves_qty="0",
                type="fill",
            )
        )

    def reclaim_after_ack_crash():
        nonlocal coordinator

        class ProcessCrash(BaseException):
            pass

        body = limit()
        accepted = place(body, "human-crash-ack-key-0001")
        real_call = coordinator.commands._call

        def crash_before_completion(statement, parameters):
            if "public.paper_human_finish(" in statement:
                raise ProcessCrash()
            return real_call(statement, parameters)

        with patch.object(coordinator.commands, "_call", crash_before_completion):
            try:
                tick()
            except ProcessCrash:
                pass
            else:
                raise AssertionError("Process crash did not follow the durable ACK")
        pending = operation(accepted["operationId"])
        assert pending["status"] == "RUNNING", pending
        assert len(submitted) == 1 and pending["orderId"]
        metadata = f.sql(
            "execution",
            "SELECT paper_human_operation_read(CAST(:id AS uuid))",
            id=accepted["operationId"],
        )
        original = f.sql(
            "execution", "SELECT paper_human_request_read(:id)", id=accepted["operationId"]
        )
        assert metadata["orderStatus"] == "ACKNOWLEDGED"
        pause = request(
            "POST", "/api/v1/paper/control", {"action": "PAUSE"}, "human-crash-pause-key-0001"
        )
        assert pause.status_code == 202
        assert f.api.rotation_begin("operator", "human-crash-rotation-0002", f.trace) == 2
        coordinator.close()
        # Let the real 90-second SQL command lease expire. Do not change SQL
        # clocks/lease timestamps or require fresh entry evidence after expiry.
        with psycopg.connect(dbname=f.database, **f.common) as c:
            old_owner, old_fence, deadline = c.execute(
                "SELECT owner,fence,lease_until FROM paper_commands WHERE id=%s",
                (accepted["operationId"],),
            ).fetchone()
        bounded = elapsed_time.monotonic() + 92
        while f.clock.now() <= deadline + timedelta(milliseconds=50):
            assert elapsed_time.monotonic() < bounded
            elapsed_time.sleep(0.5)
        reads, calls = len(credential_reads), len(f.http_calls)
        coordinator = PaperRuntimeCoordinator(
            config,
            f.ledger,
            reader_factory=Reader,
            broker_factory=lambda *a, **k: AlpacaPaperBrokerAdapter(*a, **k, transport=transport),
            market_factory=lambda *a, **k: AlpacaMarketDataProvider(*a, **k, transport=transport),
            daily_factory=lambda **k: PaperDailyRefreshCollector(**k, transport=transport),
        )
        tick()
        completed = operation(accepted["operationId"])
        assert completed["status"] == "SUCCEEDED", completed
        assert completed["orderId"] == pending["orderId"]
        assert completed["acceptedAt"] == accepted["acceptedAt"]
        assert completed["traceId"] == accepted["traceId"]
        assert (
            f.sql("execution", "SELECT paper_human_request_read(:id)", id=accepted["operationId"])
            == original
        )
        assert len(submitted) == 1 and len(credential_reads) == reads and len(f.http_calls) == calls
        with psycopg.connect(dbname=f.database, **f.common) as c:
            owner, fence = c.execute(
                "SELECT owner,fence FROM paper_commands WHERE id=%s", (accepted["operationId"],)
            ).fetchone()
            assert owner != old_owner and fence > old_fence
        assert not f.api.read_status(f.trace)["entryGateOpen"]
        provenance(accepted, completed, body)

    try:
        with (
            patch("packages.brokers.alpaca_paper._EASTERN", zone),
            patch("packages.brokers.alpaca_market_data._EASTERN", zone),
            patch("services.worker.paper_limit._EASTERN", zone),
            patch("services.worker.paper_human_limit._EASTERN", zone),
            patch.object(PaperMarketConfig, "load", return_value=market_config),
        ):
            tick()
            assert not credential_reads and not f.http_calls
            assert f.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            start = request(
                "POST",
                "/api/v1/paper/start",
                terms
                | dict(
                    strategy=profile["strategy"],
                    symbols=list(config.instruments),
                    confirmation="START_PAPER",
                    expiresAt=(f.clock.now() + timedelta(seconds=25))
                    .isoformat()
                    .replace("+00:00", "Z"),
                ),
                "human-start-key-0001",
            )
            assert start.status_code == 202
            tick()
            started = operation(start.json()["operationId"])
            assert started["status"] == "SUCCEEDED", started
            if sys.argv[-1:] == ["reclaim"]:
                reclaim_after_ack_crash()
                print(RECLAIM_MARKER)
                return
            for index, (changes, error) in enumerate(
                (
                    ({"limitPrice": "15.001"}, "INVALID_REQUEST"),
                    ({"limitPrice": "20"}, "POLICY_MISMATCH"),
                    ({"quantity": "66.5"}, "BUDGET_EXCEEDED"),
                    (
                        {"side": "SELL", "quantity": "2", "limitPrice": "14.99"},
                        "INSUFFICIENT_POSITION",
                    ),
                    ({"credentialGeneration": 2}, "VERSION_CONFLICT"),
                    ({"budgetVersion": terms["budgetVersion"] + 1}, "VERSION_CONFLICT"),
                    (
                        {
                            "expiresAt": (f.clock.now() + timedelta(seconds=40))
                            .isoformat()
                            .replace("+00:00", "Z")
                        },
                        "APPROVAL_EXPIRED",
                    ),
                )
            ):
                bad = place(limit(**changes), f"human-invalid-key-{index:04d}")
                tick()
                refused = operation(bad["operationId"])
                assert refused["status"] == "BLOCKED" and refused["errorCode"] == error, refused
                assert not submitted
            malformed = request(
                "POST",
                "/api/v1/paper/order-requests",
                limit(quantity="0.0000000001"),
                "human-precision-key-0001",
            )
            assert malformed.status_code == 400 and malformed.json()["code"] == "INVALID_REQUEST"
            body = limit()
            accepted = place(body, "human-buy-key-000001")
            assert place(body, "human-buy-key-000001") == accepted
            changed = request(
                "POST",
                "/api/v1/paper/order-requests",
                body | {"quantity": "2"},
                "human-buy-key-000001",
            )
            assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_CONFLICT"
            assert f.sql("execution", "SELECT count(*) FROM decisions") == 0
            tick()
            result = operation(accepted["operationId"])
            assert result["status"] == "SUCCEEDED", result
            assert result["orderId"] and len(submitted) == 1
            assert submitted[0]["qty"] == "1" and submitted[0]["limit_price"] == "15"
            provenance(accepted, result, body)
            tick()
            assert len(submitted) == 1 and operation(accepted["operationId"]) == result
            # The actual fill collector/reconciliation creates holdings for SELL.
            fill_buy(submitted[0]["client_order_id"])
            assert coordinator.recovery_tick() == "READY"
            assert f.sql(
                "execution", "SELECT quantity FROM positions WHERE instrument_id='SPY'"
            ) == Decimal(1)
            changed_book_status = request("GET", "/api/v1/paper/status")
            assert changed_book_status.status_code == 200
            assert changed_book_status.json()["entryGateOpen"] is False
            # Explicit operator approval uses the now-current financial book;
            # an earlier portfolio-bound approval is never silently rebased.
            post_fill = request(
                "POST",
                "/api/v1/paper/start",
                terms
                | dict(
                    strategy=profile["strategy"],
                    symbols=list(config.instruments),
                    confirmation="START_PAPER",
                    expiresAt=(f.clock.now() + timedelta(seconds=25))
                    .isoformat()
                    .replace("+00:00", "Z"),
                ),
                "human-post-fill-start-0001",
            )
            assert post_fill.status_code == 202
            tick()
            post_fill_result = operation(post_fill.json()["operationId"])
            assert post_fill_result["status"] == "SUCCEEDED", post_fill_result
            sell_body = limit(side="SELL", limitPrice="14.99")
            sell = place(sell_body, "human-sell-key-00001")
            tick()
            sold = operation(sell["operationId"])
            assert sold["status"] == "SUCCEEDED", sold
            provenance(sell, sold, sell_body)
            assert submitted[-1]["side"] == "sell" and submitted[-1]["qty"] == "1"
            provider_state["timeout_once"] = True
            unknown_body = limit(symbol="QQQ")
            unknown = place(unknown_body, "human-timeout-key-0001")
            tick()
            pending = operation(unknown["operationId"])
            assert pending["status"] == "RUNNING", pending
            posts = len(submitted)
            assert (
                f.sql(
                    "execution", "SELECT count(*) FROM risk_reservations WHERE state='HELD_UNKNOWN'"
                )
                == 1
            )
            assert coordinator.recovery_tick() == "READY"
            recovered = operation(unknown["operationId"])
            assert recovered["status"] == "SUCCEEDED", recovered
            assert len(submitted) == posts
            provenance(unknown, recovered, unknown_body)
            assert f.sql("execution", "SELECT count(*) FROM decisions") == 0
            old_context = f.sql(
                "execution",
                "SELECT version FROM risk_instrument_context "
                "WHERE account_id='paper' AND instrument_id='QQQ'",
            )
            provider_state["fractionable"] = False
            capped = place(limit(symbol="QQQ", quantity="0.5"), "human-capability-key-0001")
            tick()
            capability_result = operation(capped["operationId"])
            assert capability_result["status"] == "BLOCKED", capability_result
            assert capability_result["errorCode"] == "RECONCILIATION_REQUIRED", capability_result
            assert len(submitted) == posts
            assert (
                f.sql(
                    "execution",
                    "SELECT version FROM risk_instrument_context "
                    "WHERE account_id='paper' AND instrument_id='QQQ'",
                )
                > old_context
            )
            assert (
                f.sql(
                    "execution",
                    "SELECT fractional_confirmed FROM risk_instrument_context "
                    "WHERE account_id='paper' AND instrument_id='QQQ'",
                )
                is False
            )
            assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
            provider_state["fractionable"] = True
            provider_state["stale_quote"] = True
            stale = place(limit(symbol="QQQ"), "human-stale-quote-key-0001")
            tick()
            stale_result = operation(stale["operationId"])
            assert stale_result["status"] == "BLOCKED", stale_result
            assert stale_result["errorCode"] == "RECONCILIATION_REQUIRED", stale_result
            assert len(submitted) == posts
            assert observed_source_errors == ["STALE_QUOTE"]
            assert any(path == "/commands" for _, path in relayed)
            observed = " ".join(public_values)
            audit = f.sql(
                "execution", "SELECT string_agg(row_to_json(a)::text,'') FROM audit_events a"
            )
            assert all(
                canary not in observed + audit
                for canary in ("synthetic-key-canary", "synthetic-secret-canary", f.account)
            )
    finally:
        coordinator.close()
        public.close()
        for engine in f.engines.values():
            engine.dispose()
        private_directory.cleanup()
    print(MARKER)


if __name__ == "__main__":
    import re
    import sys
    import traceback

    try:
        guest()
    except Exception as error:
        from services.control_api.paper_commands import ERROR_CODES

        frames = traceback.extract_tb(error.__traceback__)
        location = next(
            (
                frame
                for frame in reversed(frames)
                if "/services/" in frame.filename or "/packages/" in frame.filename
            ),
            frames[-1],
        )
        root = getattr(error, "orig", error)
        value = error.args[0] if error.args and type(error.args[0]) is dict else {}
        code = ":" + value["errorCode"] if value.get("errorCode") in ERROR_CODES else ""
        denied = re.fullmatch(
            r"permission denied for (?:table|function|schema|sequence) ([a-z_]+)",
            getattr(getattr(root, "diag", None), "message_primary", "") or "",
        )
        if denied:
            code = ":" + denied[1].upper()
        print(
            f"APW02_FAIL:HUMAN_CHAIN:{type(root).__name__}{code}:"
            f"{Path(location.filename).name}:{location.lineno}"
        )
        sys.exit(1)
