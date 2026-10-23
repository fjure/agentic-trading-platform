"""Actual isolated-role attempts, synthetic provider waits and immediate KILL."""

from pathlib import Path

MARKER = "PASS: isolated Paper submit lifecycle"
_STAGE = "SETUP"


def _host(scenario):
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER, support_paths=[
        Path(__file__).with_name("test_paper_lifecycle.py"),
    ], guest_args=(scenario,)) == MARKER


def test_paper_submit_ack_postgres():
    _host("ack")


def test_paper_submit_kill_postgres():
    _host("kill-timeout")


def test_paper_submit_crash_postgres():
    _host("crash")


def test_paper_kill_during_final_preflight_postgres():
    _host("kill-preflight")


def test_paper_open_order_rotation_recovery_postgres():
    _host("rotation-open")


def test_paper_filled_order_rotation_recovery_postgres():
    _host("rotation-filled")


def test_paper_committed_attempt_expiry_postgres():
    _host("commit-expiry")


def guest(scenario):
    global _STAGE
    import json
    import time
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from decimal import Decimal
    from threading import Event
    from types import SimpleNamespace
    from unittest.mock import patch

    import httpx
    from sqlalchemy import URL, create_engine, text
    from test_paper_lifecycle import PaperFixture

    from packages.domain import models as m
    from packages.persistence.ledger import LedgerUnit, PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.execution.gates import SubmitDenied
    from services.execution.paper_reconciliation import reconcile_paper_account
    from services.execution.paper_submit import submit_paper_once
    from services.runtime.worker import WorkerRuntime

    class Crash(BaseException):
        pass

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    posted, release = Event(), Event()
    posts = []
    orders = {}
    positions = []
    activities = []
    provider_cash = Decimal(100000)
    final_request = False

    def routes(request):
        path = request.url.path
        if final_request and scenario == "kill-preflight" and path == "/v2/account":
            posted.set()
            assert release.wait(5), "synthetic final preflight release timeout"
        if request.method == "GET" and path == "/v2/account":
            return httpx.Response(200, json=dict(
                id=f.account, currency="USD", cash=m.decimal_string(provider_cash),
                equity="100000", portfolio_value="100000", status="ACTIVE",
                account_blocked=False, trading_blocked=False, trade_suspended_by_user=False,
            ))
        if request.method == "GET" and path == "/v2/positions":
            return httpx.Response(200, json=positions)
        if request.method == "GET" and path == "/v2/account/activities":
            return httpx.Response(200, json=activities)
        if request.method == "GET" and path == "/v2/orders":
            return httpx.Response(200, json=list(orders.values()))
        if request.method == "GET" and path == "/v2/clock":
            return httpx.Response(200, json=dict(timestamp=f.clock.now().isoformat(), is_open=True))
        if path == "/v2/calendar":
            return httpx.Response(200, json=[dict(date=request.url.params["start"],
                                                 open="09:30", close="16:00")])
        if path.startswith("/v2/assets/"):
            symbol = path.rsplit("/", 1)[1]
            asset = {"SPY": "22222222-2222-4222-8222-222222222222",
                     "QQQ": "33333333-3333-4333-8333-333333333333"}[symbol]
            return httpx.Response(200, json=dict(id=asset, symbol=symbol, status="active",
                                                tradable=True, fractionable=True,
                                                **{"class": "us_equity"}))
        if path == "/v2/orders:by_client_order_id":
            row = orders.get(request.url.params["client_order_id"])
            return httpx.Response(200, json=row) if row else httpx.Response(404)
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.content)
            posts.append(body)
            row = body | dict(id="44444444-4444-4444-8444-444444444444", status="new",
                              filled_qty="0", filled_avg_price=None,
                              created_at=f.clock.now().isoformat(),
                              updated_at=f.clock.now().isoformat())
            row["submitted_at"] = row["created_at"]
            orders[body["client_order_id"]] = row
            posted.set()
            if scenario == "crash":
                raise Crash()
            if scenario == "kill-timeout":
                assert release.wait(5), "synthetic provider release timeout"
                raise httpx.ReadTimeout("synthetic Paper timeout", request=request)
            return httpx.Response(200, json=row)
        return None

    f.route_handler = routes
    admin_engine = create_engine(URL.create("postgresql+psycopg", username="lab_migration",
                                            host="postgres", database=f.database))
    # Upgrade the fixture's admitted source rows through the actual dual-provider
    # binding port. Fresh GET proofs retain every provider/account/asset check.
    with (patch("packages.brokers.alpaca_paper._EASTERN", f.zone),
          patch("packages.brokers.alpaca_market_data._EASTERN", f.zone),
          patch("services.worker.paper_limit._EASTERN", f.zone)):
        for symbol in ("SPY", "QQQ"):
            f.binding.publish(symbol, f.trace)
        f.receipt = reconcile_paper_account(ledger=f.ledger, account_id="paper", broker=f.broker,
            binding=f.binding, owner_id=f.owner, fencing_token=f.fence)
        assert f.receipt.ready, f.receipt.reason_code
        reference = f.sql("execution", "SELECT paper_daily_reference()")
        body = reference | dict(observedAt=m.utc_string(f.clock.now()), currentEquity="100000",
                                externalFunding="0", observationHash="e"*64)
        assert f.sql("execution",
                     "SELECT paper_daily_append(:owner,:fence,:portfolio,CAST(:body AS jsonb))",
                     owner=f.owner, fence=f.fence, portfolio=f.receipt.portfolio_version,
                     body=json.dumps(body))
        # New immutable admitted market states follow the new portfolio/binding.
        # No existing state, evidence, proposal, or database guard is rewritten.
        for symbol in ("SPY", "QQQ"):
            with admin_engine.begin() as connection:
                unit = LedgerUnit(connection)
                old = unit.get(m.MarketState, symbol+"-state")
                snapshot = unit.get(m.PortfolioSnapshot, connection.execute(text(
                    "SELECT portfolio_snapshot_id FROM risk_account_context "
                    "WHERE account_id='paper'"
                )).scalar_one())
                context = connection.execute(text(
                    "SELECT version,capability_version FROM risk_instrument_context "
                    "WHERE account_id='paper' AND instrument_id=:symbol"
                ), {"symbol": symbol}).one()
                source = connection.execute(text(
                    "SELECT source_capability_version FROM broker_binding_proofs "
                    "WHERE account_id='paper' AND instrument_id=:symbol "
                    "AND context_version=:version"
                ), {"symbol": symbol, "version": context.version}).scalar_one()
                state = replace(old, id=symbol+"-zsubmit-state", portfolio=snapshot,
                                as_of=f.clock.now())
                unit.insert(state)
            stamp = m.utc_string(f.clock.now())
            quote = dict(sourceId="alpaca-market-data:iex", bid="14.99", ask="15",
                         observedAt=stamp, providerTime=stamp,
                         sourceCapabilityVersion=source)
            assert "quoteId" in f.sql("execution",
                "SELECT paper_quote_append(:state,:context,:owner,:fence,CAST(:quote AS jsonb))",
                state=state.id, context=context.version, owner=f.owner, fence=f.fence,
                quote=json.dumps(quote))
        assert f.sql("worker", "SELECT paper_worker_heartbeat('READY')")
        activation = f.start(ttl=8 if scenario == "commit-expiry" else 25)[3]
        assert "activationId" in activation
        config = SimpleNamespace(deployment="paper", account_id="paper", instruments=("SPY",),
                                 decision_provider="mock")
        worker = WorkerRuntime(config, PostgresLedger(engine=f.engines["worker"]))
        assert worker.tick() == "READY"
        proposal = f.sql("execution", "SELECT id FROM trade_proposals")
        _STAGE = "RESERVE"
        risk = reserve(ledger=f.ledger, account_id="paper", proposal_id=proposal,
                       limits=RiskLimits())
        assert risk.approved, risk.reason_codes
        intent_id = f.sql("execution", "SELECT id FROM order_intents")

        def attempt():
            global _STAGE
            _STAGE = "ATTEMPT"
            return submit_paper_once(ledger=f.ledger, account_id="paper", intent_id=intent_id,
                limits=RiskLimits(), owner_id=f.owner, fencing_token=f.fence,
                broker=f.broker, binding=f.binding)

        def kill():
            # A held financial lock must not hide behind a provider wait.
            with f.engines["api"].begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout='1500ms'"))
                result = connection.execute(text(
                    "SELECT paper_enqueue('KILL','{\"action\":\"KILL\"}'::jsonb,"
                    "'operator','kill-submit-lifecycle-0001',:trace)"
                ), {"trace": f.trace}).scalar_one()
            assert result["status"] == "ACCEPTED"
            assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"

        if scenario == "commit-expiry":
            from contextlib import contextmanager
            from datetime import datetime

            from services.execution.paper_submit import PaperAttemptExpired

            deadline = datetime.fromisoformat(activation["expiresAt"])
            original_transaction = f.ledger.transaction
            delayed = False

            @contextmanager
            def delayed_commit_return():
                nonlocal delayed
                with original_transaction() as tx:
                    yield tx
                # The real attempt/audit/outbox transaction has committed. Delay
                # only its return to the callback, with no transaction held and
                # no gate, SQL clock, or provider clock replaced.
                if not delayed and f.sql("execution", "SELECT count(*) FROM broker_orders") == 1:
                    delayed = True
                    duration = (deadline-f.clock.now()).total_seconds()+0.1
                    assert 0 < duration <= 10
                    time.sleep(duration)

            with patch.object(f.ledger, "transaction", delayed_commit_return):
                try:
                    attempt()
                except PaperAttemptExpired as error:
                    assert error.reason_code == "PAPER_ATTEMPT_EXPIRED"
                else:
                    raise AssertionError("expired committed attempt reached provider POST")
            assert delayed and posts == []
            assert f.sql("execution", "SELECT status FROM broker_orders") == "SUBMITTING"
            assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
            assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
            calls = len(f.http_calls)
            try:
                attempt()
            except SubmitDenied as error:
                assert error.reason_code == "RECONCILE_REQUIRED"
            else:
                raise AssertionError("expired durable attempt was replayed")
            assert len(f.http_calls) == calls and posts == []
            assert f.sql("execution", "SELECT count(*) FROM paper_order_accounting_bases") == 1
            print(MARKER)
            return
        if scenario == "kill-preflight":
            original_request = f.broker._request

            def request(method, path, **kwargs):
                nonlocal final_request
                if method == "POST":
                    final_request = True
                return original_request(method, path, **kwargs)

            f.broker._request = request
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                try:
                    assert posted.wait(5), "submit did not reach final account preflight"
                    kill()
                finally:
                    release.set()
                try:
                    future.result(5)
                except SubmitDenied:
                    pass
                else:
                    raise AssertionError("KILL during final preflight did not deny submit")
            assert posts == []
            assert f.sql("execution", "SELECT count(*) FROM broker_orders") == 0
            assert f.sql("execution", "SELECT count(*) FROM paper_order_accounting_bases") == 0
            assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
            print(MARKER)
            return
        if scenario == "kill-timeout":
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(attempt)
                try:
                    assert posted.wait(5), "submit did not reach synthetic POST"
                    kill()
                finally:
                    release.set()
                assert future.result(5).status is m.OrderStatus.UNKNOWN
            assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"
            assert f.sql("execution", "SELECT state FROM risk_reservations") == "HELD_UNKNOWN"
        elif scenario == "crash":
            try:
                attempt()
            except Crash:
                pass
            else:
                raise AssertionError("synthetic crash did not propagate")
            assert f.sql("execution", "SELECT status FROM broker_orders") == "SUBMITTING"
            assert f.sql("execution", "SELECT state FROM risk_reservations") == "ACTIVE"
            # A still-fresh earlier ready receipt is not authority to reopen an
            # account after an ambiguous committed attempt. No new activation or
            # TTL may be manufactured from that stale readiness proof.
            with admin_engine.connect() as connection:
                before_activations = connection.execute(text(
                    "SELECT count(*) FROM paper_activations"
                )).scalar_one()
            restarted_start = f.start(key="restart-after-crash-0002")[3]
            assert restarted_start == {"error": "RECONCILIATION_REQUIRED"}
            assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
            with admin_engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT count(*) FROM paper_activations"
                )).scalar_one() == before_activations
        else:
            assert attempt().status is m.OrderStatus.ACKNOWLEDGED
        if scenario == "ack":
            _STAGE = "RECEIPT_COVERAGE"
            old_operation, old_claim, _, old_result = f.start(key="start-old-ack-receipt-0002")
            assert old_result == {"error": "RECONCILIATION_REQUIRED"}
            assert f.execution.finish(old_operation["operationId"], f.command_owner,
                                      old_claim["fence"], error_code="RECONCILIATION_REQUIRED")
            # New readiness must be produced by actual exhaustive GET evidence,
            # not by altering the receipt, status, or account latch in a fixture.
            f.receipt = reconcile_paper_account(ledger=f.ledger, account_id="paper",
                broker=f.broker, binding=f.binding, owner_id=f.owner, fencing_token=f.fence)
            assert f.receipt.ready, f.receipt.reason_code
            fresh_start = f.start(key="start-fresh-ack-receipt-0003")[3]
            assert "activationId" in fresh_start and fresh_start["activationId"] != (
                activation["activationId"]
            )
            assert f.sql("execution", "SELECT status FROM broker_orders") == "ACKNOWLEDGED"
            assert len(posts) == 1
        if scenario in {"rotation-open", "rotation-filled"}:
            _STAGE = "ROTATION"
            frozen_baseline = f.sql("execution",
                "SELECT md5(row_to_json(b)::text) FROM paper_reconciliation_baselines b")
            assert f.sql("execution",
                "SELECT fee_authority_profile IS NULL FROM paper_reconciliation_baselines")
            if scenario == "rotation-filled":
                row = next(iter(orders.values()))
                quantity, price = Decimal(row["qty"]), Decimal(row["limit_price"])
                cost = quantity*price
                provider_cash -= cost
                stamp = m.utc_string(f.clock.now())
                row.update(status="filled", filled_qty=row["qty"],
                           filled_avg_price=row["limit_price"], updated_at=stamp,
                           filled_at=stamp)
                positions.append(dict(asset_id="22222222-2222-4222-8222-222222222222",
                    symbol="SPY", side="long", qty=row["qty"], qty_available=row["qty"],
                    avg_entry_price=row["limit_price"], market_value=m.decimal_string(cost),
                    cost_basis=m.decimal_string(cost)))
                activities.append(dict(id="synthetic::rotation-fill", activity_type="FILL",
                    transaction_time=stamp, created_at=stamp, order_id=row["id"], symbol="SPY",
                    side="buy", qty=row["qty"], price=row["limit_price"],
                    cum_qty=row["qty"], leaves_qty="0", type="fill"))
                receipt = reconcile_paper_account(ledger=f.ledger, account_id="paper",
                    broker=f.broker, binding=f.binding, owner_id=f.owner, fencing_token=f.fence)
                assert receipt.ready, receipt.reason_code
                assert f.sql("execution", "SELECT status FROM broker_orders") == "FILLED"
                assert f.sql("execution", "SELECT state FROM risk_reservations") == "CONSUMED"
                assert f.sql("execution", "SELECT cash FROM portfolio_snapshots "
                    "WHERE id=(SELECT portfolio_snapshot_id FROM risk_account_context)"
                ) == provider_cash
            assert f.api.rotation_begin("operator", "rotation-submit-0002", f.trace) == 2
            assert f.api.rotation_complete(2)
            assert f.sql("execution", "SELECT paper_fee_authority()") is None
            retained = f.sql("execution", "SELECT paper_recovery_fee_authority()")
            assert retained["credentialGeneration"] == retained["profileVersion"] == 1
            receipt = reconcile_paper_account(ledger=f.ledger, account_id="paper",
                broker=f.broker, binding=f.binding, owner_id=f.owner, fencing_token=f.fence)
            assert receipt.ready, receipt.reason_code
            assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
            assert f.sql("execution",
                         "SELECT count(*) FROM broker_orders WHERE status='UNKNOWN'") == 0
            operation = f.api.enqueue("VERIFY_CONNECTION", {"credentialGeneration": 2},
                                      "operator", "verify-submit-0002", f.trace)
            claim = f.execution.claim(f.command_owner, lease_seconds=120)
            assert claim["operationId"] == operation["operationId"]
            n = f.clock.now()
            assert f.execution.finish(operation["operationId"], f.command_owner, claim["fence"],
                public=dict(credentialGeneration=2, observedAt=n, verifiedAt=n,
                    accountLabel="paper-11111111", currency="USD", cash=provider_cash,
                    marketDataFeed="IEX", providerVerification="CONFIRMED", blockers=[]),
                private_account_id=f.account)
            assert f.api.read_operation(operation["operationId"], f.trace)["status"] == "SUCCEEDED"
            assert f.sql("execution", "SELECT paper_fee_authority()") is None
            assert f.sql("execution", "SELECT paper_recovery_fee_authority()") == (
                retained | {"credentialGeneration": 2}
            )
            receipt = reconcile_paper_account(ledger=f.ledger, account_id="paper",
                broker=f.broker, binding=f.binding, owner_id=f.owner, fencing_token=f.fence)
            assert receipt.ready, receipt.reason_code
            preparation = f.sql("execution", "SELECT paper_activation_read()")
            assert preparation["credentialGeneration"] == 2
            assert preparation["accountGeneration"] == 1
            assert preparation["pinnedAccountId"] == f.account
            assert preparation["feeAuthority"] is None and preparation["activation"] is None
            assert f.sql("execution", "SELECT md5(row_to_json(b)::text) "
                         "FROM paper_reconciliation_baselines b") == frozen_baseline
            assert f.sql("execution", "SELECT mode FROM risk_account_context") == "PAPER"
            assert f.sql("execution", "SELECT safety_state FROM risk_account_context"
                         ) == "PAUSE_NEW"
            assert not f.api.read_status(f.trace)["entryGateOpen"]
        assert len(posts) == 1
        calls = len(f.http_calls)
        try:
            attempt()
        except SubmitDenied as error:
            assert error.reason_code == "RECONCILE_REQUIRED"
        else:
            raise AssertionError("durable attempted intent was replayed")
        assert len(posts) == 1 and len(f.http_calls) == calls
        if scenario == "crash":
            from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
            from services.execution.broker_binding import AlpacaExecutionBinding
            from services.execution.broker_recovery import restore_alpaca_attempts

            restarted = AlpacaPaperBrokerAdapter(
                f.clock, f.ids, api_key="synthetic-key", api_secret="synthetic-secret",
                paper_enabled=True, expected_account_id=f.account,
                reconciliation_fee_profile="alpaca-paper-us-retail-equity-zero-v1",
                transport=httpx.MockTransport(f.handler), proxy_deployment="paper",
            )
            binding = AlpacaExecutionBinding(f.ledger, "paper", restarted, f.market)
            assert restore_alpaca_attempts(
                ledger=f.ledger, account_id="paper", broker=restarted, binding=binding,
                owner_id=f.owner, fencing_token=f.fence,
            ) == 1
            assert binding.restored
            with f.ledger.transaction() as tx:
                durable_intent = tx.get(m.OrderIntent, intent_id)
            before_lookup = len(f.http_calls)
            assert restarted.submit(durable_intent).status is m.OrderStatus.ACKNOWLEDGED
            assert f.http_calls[before_lookup:] == [
                ("GET", "/v2/account"), ("GET", "/v2/orders:by_client_order_id"),
            ]
            assert len(posts) == 1
        assert f.sql("execution", "SELECT count(*) FROM paper_order_accounting_bases") == 1
        assert f.sql("execution", "SELECT count(*) FROM broker_orders") == 1
    print(MARKER)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        guest(sys.argv[2])
    except Exception as error:
        root = getattr(error, "orig", error)
        frame = traceback.extract_tb(error.__traceback__)[-1]
        fixed = ""
        message = getattr(getattr(root, "diag", None), "message_primary", None)
        for table in ("risk_decisions", "risk_reservations", "order_intents", "broker_orders",
                      "execution_reports", "paper_order_accounting_bases", "risk_account_context",
                      "audit_events", "outbox_events", "accounts", "positions"):
            if message == "permission denied for table " + table:
                fixed = ":TABLE_" + table.upper()
        print(f"APW02_FAIL:{_STAGE}:{type(root).__name__}{fixed}:"
              f"{Path(frame.filename).name}:{frame.lineno}")
        raise SystemExit(1) from None
