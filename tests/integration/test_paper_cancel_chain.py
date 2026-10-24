"""Authenticated cancel transport and real fixed-role SQL, offline provider only.

Established orders use the SQL suite's explicitly documented historical fixture.
Cancellation runs with genuine UTC time outside entry sessions as well: no START,
calendar, financial gate, reconciliation or SQL clock is replaced by this suite.
"""

from pathlib import Path

MARKER = "PASS: durable public Paper cancellation and financial recovery"
STAGE = "CANCEL_START"


def test_paper_cancel_pending_and_kill_dedup():
    run_host("pending")


def test_paper_cancel_timeout_restart_lookup_only():
    run_host("timeout")


def test_paper_cancel_crash_restart_genuine_lease():
    run_host("crash")


def test_paper_cancel_persistent_429_backoff():
    run_host("backoff")


def test_paper_cancel_partial_fill_financial_proof():
    run_host("partial")


def test_paper_cancel_full_fill_wins():
    run_host("filled")


def run_host(scenario):
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            MARKER,
            support_paths=(
                Path(__file__).with_name("test_paper_lifecycle.py"),
                Path(__file__).with_name("test_paper_cancel_sql.py"),
            ),
            guest_args=(scenario,),
        )
        == MARKER
    )


class PublicPaper:
    """Real normal HTTP app relayed to the actual private command HTTP app."""

    def __init__(self, commands):
        import httpx
        from fastapi.testclient import TestClient

        from services.control_api.app import create_app
        from services.control_api.auth import LocalAuth
        from services.control_api.paper_client import PaperClient
        from services.runtime.paper_api import create_private_paper_app

        self.origin = "http://127.0.0.1:8000"
        self.values, self.relayed = [], []
        private = create_private_paper_app(commands)

        def relay(request):
            assert request.url.host == "paper-api"
            self.relayed.append((request.method, request.url.path))
            with TestClient(
                private, base_url="http://paper-api:8012", client=("127.0.0.1", 46001)
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

        auth = LocalAuth()
        self.client = TestClient(
            create_app(auth=auth, paper=PaperClient(transport=httpx.MockTransport(relay))),
            base_url=self.origin,
            client=("127.0.0.1", 46000),
            raise_server_exceptions=False,
        )
        response = self.request(
            "POST", "/api/v1/session", {"activationCode": auth.issue_activation_code()}
        )
        assert response.status_code == 204

    def request(self, method, path, body=None, key="cancel-chain-session-0001"):
        response = self.client.request(
            method, path, json=body, headers={"Origin": self.origin, "Idempotency-Key": key}
        )
        self.values.append(response.text)
        return response

    def cancel(self, order_id, key="cancel-chain-request-0001", generation=1):
        response = self.request(
            "POST",
            f"/api/v1/paper/orders/{order_id}/cancel",
            {"accountGeneration": generation, "confirmation": "CANCEL_PAPER_ORDER"},
            key,
        )
        assert response.status_code == 202, response.status_code
        return response.json()

    def operation(self, operation_id):
        response = self.request("GET", "/api/v1/paper/operations/" + operation_id)
        assert response.status_code == 200
        return response.json()

    def close(self):
        self.client.close()


def guest(scenario):
    import os
    import tempfile
    import time
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from decimal import Decimal

    import httpx
    import psycopg
    from test_paper_cancel_sql import cancel_fixture

    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.domain import models as m
    from services.control_api.control import ControlDenied
    from services.execution.paper_cancel import PaperCancelService
    from services.execution.paper_credentials import PaperGenerationReader
    from services.execution.paper_reconciliation import (
        collect_paper_reconciliation,
        reconcile_paper_account,
    )
    from services.execution.recovery_health import (
        claim_execution_owner,
        heartbeat_execution_owner,
    )
    from services.runtime.config import RuntimeConfig
    from services.runtime.paper_coordinator import PaperOwner, PaperRuntimeCoordinator
    from services.setup.store import ProviderStore

    global STAGE
    STAGE = "CANCEL_FIXTURE"
    f = cancel_fixture()
    row = f.provider_orders[f.cancel_client_order_id]
    state = dict(cash=Decimal(100000), positions=[], activities=[], deletes=0, cancel_io=True)
    calls, credential_reads = [], []
    sql_failures = []
    public = PublicPaper(f.api)
    temporary = tempfile.TemporaryDirectory()
    private_root = Path(temporary.name)
    (private_root / "alpaca").mkdir(mode=0o700)
    ProviderStore(private_root, owner_uid=os.getuid()).save_generation(
        "alpaca",
        1,
        dict(
            apiKey="cancel-synthetic-key-canary",
            apiSecret="cancel-synthetic-secret-canary",
            expectedAccountId=f.account,
            paperOnly=True,
        ),
    )

    class Reader(PaperGenerationReader):
        def __init__(self):
            super().__init__(private_root, owner_uid=os.getuid())

        def load(self, generation):
            credential_reads.append(generation)
            assert generation == 1
            return super().load(generation)

    class CrashAfterDelete(BaseException):
        pass

    def record_fill(quantity, status):
        price = Decimal(row["limit_price"])
        cost = quantity * price
        stamp = m.utc_string(f.clock.now())
        row.update(
            status=status,
            filled_qty=m.decimal_string(quantity),
            filled_avg_price=m.decimal_string(price),
            updated_at=stamp,
        )
        if status == "filled":
            row["filled_at"] = stamp
        state["cash"] = Decimal(100000) - cost
        state["positions"] = [
            dict(
                asset_id="22222222-2222-4222-8222-222222222222",
                symbol="SPY",
                side="long",
                qty=m.decimal_string(quantity),
                qty_available=m.decimal_string(quantity),
                avg_entry_price=m.decimal_string(price),
                market_value=m.decimal_string(cost),
                cost_basis=m.decimal_string(cost),
            )
        ]
        state["activities"] = [
            dict(
                id="synthetic::cancel-fill",
                activity_type="FILL",
                transaction_time=stamp,
                created_at=stamp,
                order_id=f.cancel_provider_order_id,
                symbol="SPY",
                side="buy",
                qty=m.decimal_string(quantity),
                price=m.decimal_string(price),
                cum_qty=m.decimal_string(quantity),
                leaves_qty=m.decimal_string(Decimal(row["qty"]) - quantity),
                type="fill" if quantity == Decimal(row["qty"]) else "partial_fill",
            )
        ]
        if scenario == "partial":
            first_time = f.clock.now() - timedelta(microseconds=2)
            unit = quantity / 2
            activity = state["activities"][0]
            # Real partial fills arrive out of order; cumulative quantities and
            # timestamps still identify their correct accounting sequence.
            state["activities"] = [
                activity
                | dict(
                    id="synthetic::cancel-fill-02",
                    qty=m.decimal_string(unit),
                    transaction_time=m.utc_string(first_time + timedelta(microseconds=1)),
                    created_at=m.utc_string(first_time + timedelta(microseconds=1)),
                ),
                activity
                | dict(
                    id="synthetic::cancel-fill-01",
                    qty=m.decimal_string(unit),
                    cum_qty=m.decimal_string(unit),
                    leaves_qty=m.decimal_string(Decimal(row["qty"]) - unit),
                    transaction_time=m.utc_string(first_time),
                    created_at=m.utc_string(first_time),
                ),
            ]

    def provider(request):
        assert request.url.host == "paper-api.alpaca.markets"
        path = request.url.path
        calls.append((request.method, path))
        # Every network boundary obtains the financial lock independently.
        # Provider I/O must happen after the authorizing transaction commits.
        with psycopg.connect(dbname=f.database, **f.common) as c:
            assert c.execute(
                "SELECT id FROM accounts WHERE id='paper' FOR UPDATE NOWAIT"
            ).fetchone() == ("paper",)
            if state["cancel_io"]:
                attempt = c.execute(
                    "SELECT a.id,a.kind FROM paper_cancel_attempts a "
                    "JOIN paper_cancels q ON q.id=a.cancel_id "
                    "JOIN audit_events e ON e.entity_id=a.id::text AND e.entity_version=1 "
                    "AND e.actor_id=a.actor_id AND e.trace_id=a.trace_id "
                    "WHERE q.order_id=%s AND a.worker_id=q.worker_id "
                    "AND a.lease_fence=q.lease_fence AND e.action=%s "
                    "ORDER BY a.started_at DESC LIMIT 1",
                    (
                        f.cancel_order_id,
                        "paper.cancel_delete"
                        if request.method == "DELETE"
                        else "paper.cancel_lookup",
                    ),
                ).fetchone()
                assert attempt is not None, (
                    "Every cancellation I/O requires committed attempt audit"
                )
                assert attempt[1] == ("DELETE" if request.method == "DELETE" else "LOOKUP")
                if request.method == "DELETE":
                    assert (
                        c.execute(
                            "SELECT count(*) FROM execution_reports r JOIN outbox_events o "
                            "ON o.aggregate_id=r.id WHERE r.id=%s "
                            "AND r.reason_code IS NULL AND r.status='CANCEL_PENDING'",
                            ("paper-cancel-report-" + str(attempt[0]),),
                        ).fetchone()[0]
                        == 1
                    )
        assert request.method in {"GET", "DELETE"}, "Cancel must never submit or liquidate"
        if request.method == "DELETE":
            assert path == "/v2/orders/" + f.cancel_provider_order_id
            state["deletes"] += 1
            if scenario == "backoff" and state["deletes"] == 1:
                return httpx.Response(429, json={"message": "synthetic rate limit"})
            if scenario == "timeout":
                raise httpx.ReadTimeout("synthetic lost cancellation response", request=request)
            if scenario == "crash":
                killed = public.request(
                    "POST",
                    "/api/v1/paper/control",
                    {"action": "KILL"},
                    "cancel-crash-live-kill-0001",
                )
                assert killed.status_code == 202
                assert (
                    f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"
                )
                raise CrashAfterDelete()
            if scenario == "partial":
                record_fill(Decimal(row["qty"]) / 2, "pending_cancel")
            elif scenario == "filled":
                record_fill(Decimal(row["qty"]), "filled")
            return httpx.Response(204)
        if path == "/v2/clock":
            value = dict(timestamp=m.utc_string(f.clock.now()), is_open=False)
        elif path == "/v2/account":
            value = dict(
                id=f.account,
                currency="USD",
                cash=m.decimal_string(state["cash"]),
                equity="100000",
                portfolio_value="100000",
                status="ACTIVE",
                account_blocked=False,
                trading_blocked=True,
                trade_suspended_by_user=True,
            )
        elif path == "/v2/orders:by_client_order_id":
            assert request.url.params["client_order_id"] == f.cancel_client_order_id
            value = row
        elif path == "/v2/orders/" + f.cancel_provider_order_id:
            value = row
        elif path == "/v2/orders":
            value = list(f.provider_orders.values())
        elif path == "/v2/positions":
            value = state["positions"]
        elif path == "/v2/account/activities":
            value = state["activities"]
        else:
            raise AssertionError("Cancel/recovery must not depend on market data or entry session")
        return httpx.Response(200, json=value)

    f.route_handler = provider
    transport = httpx.MockTransport(provider)

    def make_coordinator():
        coordinator = PaperRuntimeCoordinator(
            RuntimeConfig(deployment="paper", broker_provider="alpaca-paper"),
            f.ledger,
            reader_factory=Reader,
            broker_factory=lambda *a, **k: AlpacaPaperBrokerAdapter(*a, **k, transport=transport),
        )
        # This is the helper's genuine fenced Execution owner, not a fake lease.
        coordinator.process = PaperOwner(f.owner, f.fence)
        service = PaperCancelService(
            f.ledger,
            reader_factory=Reader,
            broker_factory=lambda *a, **k: AlpacaPaperBrokerAdapter(*a, **k, transport=transport),
        )
        original_call = service._call

        def observed_call(statement, values):
            try:
                return original_call(statement, values)
            except Exception as error:
                sql_failures.append(error)
                raise

        # Forward real ports unchanged; preserve SQL errors the bounded service
        # deliberately redacts into UNAVAILABLE, for the guest's safe reporter.
        service._call = observed_call
        coordinator._cancel_service = service
        return coordinator

    coordinator = make_coordinator()

    def reservation():
        return f.sql(
            "execution",
            "SELECT r.state FROM risk_reservations r "
            "JOIN risk_decisions d ON d.reservation_id=r.id "
            "JOIN order_intents i ON i.risk_decision_id=d.id "
            "WHERE i.id=:intent",
            intent=f.cancel_intent_id,
        )

    def cancel_tick():
        coordinator.heartbeat_tick()
        result = coordinator.cancel_tick()
        if sql_failures:
            raise sql_failures.pop()
        return result

    def reconcile(*, ready=True):
        global STAGE
        coordinator.heartbeat_tick()
        state["cancel_io"] = False
        try:
            collected = collect_paper_reconciliation(
                ledger=f.ledger, account_id="paper", broker=f.broker, binding=f.binding
            )
            receipt = reconcile_paper_account(
                ledger=f.ledger,
                account_id="paper",
                broker=f.broker,
                binding=f.binding,
                owner_id=f.owner,
                fencing_token=f.fence,
                collected=collected,
            )
        finally:
            state["cancel_io"] = True
        if receipt.ready is not ready:
            safe_reasons = {
                "PAPER_EVIDENCE_UNAVAILABLE",
                "FILL_PROOF_MISSING",
                "STATUS_FILL_MISMATCH",
                "POSITION_ACCOUNT_MISMATCH",
                "CASH_ACCOUNT_MISMATCH",
                "TERMINAL_STATUS_CORRECTION",
                "PAPER_FEE_PROFILE_CHANGED",
                "POSITION_COST_BASIS_MISMATCH",
            }
            if receipt.reason_code in safe_reasons:
                STAGE = "CANCEL_RECON_" + receipt.reason_code
            raise AssertionError("Unexpected financial reconciliation readiness")
        return receipt

    def queue():
        with psycopg.connect(dbname=f.database, **f.common) as c:
            return c.execute(
                "SELECT id,state,lookup_only,delete_attempts,not_before,lease_until "
                "FROM paper_cancels "
                "WHERE order_id=%s",
                (f.cancel_order_id,),
            ).fetchone()

    def financial_fingerprint():
        with psycopg.connect(dbname=f.database, **f.common) as c:
            hashes = tuple(
                c.execute(
                    "SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text,'[]')) "
                    f"FROM {table} t"
                ).fetchone()[0]
                for table in ("accounts", "positions", "paper_activity_evidence")
            )
            current = c.execute(
                "SELECT p.cash,p.equity,p.exposure,p.ledger_version FROM portfolio_snapshots p "
                "JOIN risk_account_context ac ON ac.portfolio_snapshot_id=p.id "
                "WHERE ac.account_id='paper'"
            ).fetchone()
            return hashes, current

    def wait_due():
        current = queue()
        deadline = max(value for value in current[4:] if value is not None)
        duration = (deadline - f.clock.now()).total_seconds()
        assert duration <= (31 if scenario == "crash" else 6), (
            "Provider retry or crashed lease must remain bounded and persistent"
        )
        while f.clock.now() < deadline:
            coordinator.heartbeat_tick()
            time.sleep(min(1, max(0.01, (deadline - f.clock.now()).total_seconds())))
        coordinator.heartbeat_tick()

    try:
        STAGE = "CANCEL_ACCEPT"
        unknown = public.request(
            "POST",
            "/api/v1/paper/orders/99999999-9999-4999-8999-999999999999/cancel",
            {"accountGeneration": 1, "confirmation": "CANCEL_PAPER_ORDER"},
            "cancel-unknown-order-0001",
        )
        assert unknown.status_code == 404 and unknown.json()["code"] == "ORDER_NOT_FOUND"
        malformed = public.request(
            "POST",
            f"/api/v1/paper/orders/{f.cancel_order_id}/cancel",
            {"accountGeneration": 1, "confirmation": "CANCEL_PAPER_ORDER", "apiKey": "extra"},
            "cancel-invalid-extra-0001",
        )
        assert malformed.status_code == 400
        profile = f.api.read_profile(f.trace)
        manual_body = {
            key: profile[key]
            for key in (
                "credentialGeneration",
                "accountGeneration",
                "policyVersion",
                "profileVersion",
                "limitPolicyVersion",
                "budgetVersion",
                "riskBudget",
                "currency",
            )
        } | dict(
            symbol="SPY",
            side="BUY",
            quantity="1",
            limitPrice="15",
            orderType="LIMIT",
            timeInForce="DAY",
            session="REGULAR",
            confirmation="PLACE_PAPER_LIMIT",
            expiresAt=m.utc_string(f.clock.now() + timedelta(seconds=20)),
        )
        manual = public.request(
            "POST", "/api/v1/paper/order-requests", manual_body, "cancel-blocked-manual-0001"
        )
        assert manual.status_code == 202
        manual_id = manual.json()["operationId"]
        manual_claim = f.execution.claim(str(uuid.uuid4()), lease_seconds=120)
        assert manual_claim["operationId"] == manual_id
        assert public.operation(manual_id)["status"] == "RUNNING"
        accepted = public.cancel(f.cancel_order_id)
        assert public.cancel(f.cancel_order_id) == accepted
        assert accepted["status"] == "ACCEPTED" and not calls and not credential_reads
        paused = public.request(
            "POST", "/api/v1/paper/control", {"action": "PAUSE"}, "cancel-chain-pause-0001"
        )
        assert paused.status_code == 202
        assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
        # A real global RUNNING manual command must not starve this lane.
        # The helper exposes its fully admitted historical order; no new order
        # is fabricated through public transport in this cancellation suite.
        STAGE = "CANCEL_TICK"
        coordinator._faulted = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            with coordinator._cycle:
                future = pool.submit(cancel_tick)
                if scenario == "crash":
                    try:
                        future.result(timeout=5)
                    except CrashAfterDelete:
                        pass
                    else:
                        raise AssertionError("Crash after DELETE authorization did not escape")
                else:
                    assert future.result(timeout=5) == "PENDING"
        assert state["deletes"] == 1
        assert public.operation(manual_id)["status"] == "RUNNING"
        assert public.operation(accepted["operationId"])["status"] == "RUNNING"
        assert reservation() in {"ACTIVE", "HELD_UNKNOWN"}
        if scenario == "pending":
            for index in range(3):
                killed = public.request(
                    "POST",
                    "/api/v1/paper/control",
                    {"action": "KILL"},
                    f"cancel-chain-kill-{index:04d}",
                )
                assert killed.status_code == 202
            assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"
            cancel_tick()
            cancel_tick()
            assert state["deletes"] == 1
            with psycopg.connect(dbname=f.database, **f.common) as c:
                assert c.execute("SELECT count(*) FROM paper_cancels").fetchone()[0] == 1
                assert (
                    c.execute(
                        "SELECT count(*) FROM paper_cancel_links WHERE origin='KILL_DRAIN'"
                    ).fetchone()[0]
                    == 3
                )
        if scenario in {"timeout", "crash", "backoff"}:
            durable = queue()
            assert durable[1:4] == (
                ("BACKOFF", False, 1) if scenario == "backoff" else ("LOOKUP_ONLY", True, 1)
            )
            deadline = max(value for value in durable[4:] if value is not None)
            assert (
                0 < (deadline - f.clock.now()).total_seconds() <= (30 if scenario == "crash" else 6)
            )
            coordinator.close()
            if scenario == "crash":
                STAGE = "CANCEL_OWNER_RECLAIM"
                old_owner, old_fence = f.owner, f.fence
                old_worker = coordinator._cancel_service.worker_id
                metadata = f.sql(
                    "execution", "SELECT paper_cancel_read(CAST(:id AS uuid))", id=durable[0]
                )
                f.owner = str(uuid.uuid4())
                f.fence = claim_execution_owner(
                    ledger=f.ledger, account_id="paper", owner_id=f.owner, trace_id=uuid.uuid4().hex
                )
                assert f.fence > old_fence
                assert (
                    f.sql(
                        "execution",
                        "SELECT paper_restart_close(:owner,:fence)",
                        owner=f.owner,
                        fence=f.fence,
                    )
                    is True
                )
                try:
                    heartbeat_execution_owner(
                        ledger=f.ledger,
                        account_id="paper",
                        owner_id=old_owner,
                        fencing_token=old_fence,
                    )
                except ControlDenied as error:
                    assert error.reason_code == "OWNER_FENCED_OR_STALE"
                else:
                    raise AssertionError("Old Execution owner renewed its heartbeat")
                denied = f.sql(
                    "execution",
                    "SELECT paper_cancel_begin_delete(CAST(:id AS uuid),CAST(:worker AS uuid),"
                    ":lease,:owner,:fence,CAST(:lookup AS uuid))",
                    id=durable[0],
                    worker=old_worker,
                    lease=metadata["leaseFence"],
                    owner=old_owner,
                    fence=old_fence,
                    lookup=str(uuid.uuid4()),
                )
                assert denied == {"error": "VERSION_CONFLICT"}
                assert queue() == durable
            coordinator = make_coordinator()
            before = (len(calls), len(credential_reads))
            assert cancel_tick() == "READY"
            assert (len(calls), len(credential_reads)) == before
            assert queue() == durable
            wait_due()
            assert cancel_tick() == "PENDING"
            assert state["deletes"] == (2 if scenario == "backoff" else 1)
            assert len(calls) > before[0]  # Actual lookup after process-local state was discarded.
        STAGE = "CANCEL_TERMINAL"
        row["status"] = "filled" if scenario == "filled" else "canceled"
        row["updated_at"] = m.utc_string(f.clock.now())
        if scenario == "partial":
            # Terminal order status alone is insufficient financial evidence.
            activities = state["activities"]
            state["activities"] = []
            refused = reconcile(ready=False)
            assert not refused.ready
            assert reservation() in {"ACTIVE", "HELD_UNKNOWN"}
            assert public.operation(accepted["operationId"])["status"] == "RUNNING"
            state["activities"] = activities
        reconcile()
        if scenario == "partial":
            booked = financial_fingerprint()
            activities = state["activities"]
            state["activities"] = activities + [dict(activities[0])]
            # A duplicate within one provider page is conflicting evidence,
            # and must not change money already justified by the earlier proof.
            reconcile(ready=False)
            assert financial_fingerprint() == booked
            state["activities"] = list(reversed(activities))
            reconcile()
            assert financial_fingerprint() == booked
            assert f.sql("execution", "SELECT count(*) FROM paper_activity_evidence") == 2
        wait_due()
        before = (len(calls), len(credential_reads))
        cancel_tick()
        assert public.operation(accepted["operationId"])["status"] == "SUCCEEDED"
        assert reservation() == ("CONSUMED" if scenario in {"partial", "filled"} else "RELEASED")
        with psycopg.connect(dbname=f.database, **f.common) as c:
            assert c.execute(
                "SELECT reserved_notional,released_at IS NOT NULL FROM risk_reservations r "
                "JOIN risk_decisions d ON d.reservation_id=r.id "
                "JOIN order_intents i ON i.risk_decision_id=d.id WHERE i.id=%s",
                (f.cancel_intent_id,),
            ).fetchone() == (Decimal(0), True)
        if scenario in {"partial", "filled"}:
            assert f.sql(
                "execution", "SELECT quantity FROM positions WHERE instrument_id='SPY'"
            ) == (Decimal(row["qty"]) / 2 if scenario == "partial" else Decimal(row["qty"]))
            assert (
                f.sql(
                    "execution",
                    "SELECT p.cash FROM portfolio_snapshots p JOIN risk_account_context c "
                    "ON c.portfolio_snapshot_id=p.id WHERE c.account_id='paper'",
                )
                == state["cash"]
            )
        assert state["deletes"] == (2 if scenario == "backoff" else 1)
        assert (len(calls), len(credential_reads)) == before
        assert f.sql(
            "execution", "SELECT status FROM broker_orders WHERE id=:id", id=f.cancel_order_id
        ) == ("FILLED" if scenario == "filled" else "CANCELED")
        assert any(path == "/commands" for _, path in public.relayed)
        audit = f.sql("execution", "SELECT string_agg(row_to_json(a)::text,'') FROM audit_events a")
        assert all(
            canary not in " ".join(public.values) + audit
            for canary in (
                "cancel-synthetic-key-canary",
                "cancel-synthetic-secret-canary",
                f.account,
            )
        )
    finally:
        coordinator.close()
        public.close()
        f.broker.close()
        for engine in f.engines.values():
            engine.dispose()
        temporary.cleanup()
    print(MARKER)


if __name__ == "__main__":
    import re
    import sys
    import traceback

    try:
        guest(sys.argv[2])
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        location = frames[-1]
        root = getattr(error, "orig", error)
        denied = re.fullmatch(
            r"permission denied for (?:table|function|schema|sequence) ([a-z_]+)",
            getattr(getattr(root, "diag", None), "message_primary", "") or "",
        )
        code = ":" + denied[1].upper() if denied else ""
        print(
            f"APW02_FAIL:{STAGE}:{type(root).__name__}{code}:"
            f"{Path(location.filename).name}:{location.lineno}"
        )
        sys.exit(1)
