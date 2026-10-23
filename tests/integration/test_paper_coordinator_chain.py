"""Real fixed-role coordinator chain, offline provider, genuine SQL wall clock.

The positive equity-session case runs weekdays in the first 45 seconds of a
UTC quarter. A bounded host wait preserves the production 15-minute bars and
120-second freshness rules. Only the synthetic venue timezone/configuration
differs; no SQL clock, readiness receipt, financial gate or collector is mocked.
"""

from pathlib import Path

MARKER = "PASS: real Paper coordinator START Worker Risk Submit recovery chain"
READ_MARKER = "PASS: Paper execution observation read privilege"
PUBLISH_MARKER = "PASS: fixed Paper publication privileges and actual delivery"


def test_paper_execution_actual_publication_privilege():
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            PUBLISH_MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
            guest_args=("publication",),
        )
        == PUBLISH_MARKER
    )


def test_paper_execution_observation_read_privilege():
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            READ_MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
            guest_args=("permissions",),
        )
        == READ_MARKER
    )


def test_paper_coordinator_chain_postgres():
    import time
    from datetime import UTC, datetime

    import pytest

    from tests.integration.test_paper_provision import host

    now = datetime.now(UTC)
    if now.weekday() >= 5:
        pytest.skip("Positive real-clock equity chain requires a weekday")
    elapsed = (now.minute % 15) * 60 + now.second
    if elapsed >= 45:
        print("Waiting for genuine next UTC quarter +5s market freshness window")
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
            MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
        )
        == MARKER
    )


def guest():
    import json
    import os
    import tempfile
    import uuid
    from datetime import UTC, date, datetime, time, timedelta, timezone
    from decimal import Decimal
    from unittest.mock import patch

    import httpx
    import psycopg
    from test_paper_lifecycle import PaperFixture

    from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.domain import models as m
    from packages.persistence.ledger import PostgresLedger
    from services.execution.paper_credentials import PaperGenerationReader
    from services.execution.paper_daily_refresh import PaperDailyRefreshCollector
    from services.execution.paper_verification import PaperAccountVerifier
    from services.runtime.config import RuntimeConfig
    from services.runtime.paper_coordinator import PaperRuntimeCoordinator
    from services.runtime.worker import WorkerRuntime
    from services.setup.store import ProviderStore
    from services.worker.market_state import PaperMarketConfig

    f = PaperFixture()
    import sys

    if sys.argv[-1:] == ["permissions"]:
        # The real publication query reads this same receipt relation. Diagnose
        # its fixed-role read privilege without inventing fresh market evidence.
        f.sql(
            "execution",
            "SELECT account_id,ready,portfolio_snapshot_id,portfolio_version,"
            "owner_id,fencing_token,observed_at FROM public.paper_reconciliation_receipts LIMIT 1",
        )
        from sqlalchemy.exc import DBAPIError

        for role, column in (("execution", "id"), ("api", "account_id"), ("worker", "account_id")):
            try:
                f.sql(
                    role, "SELECT " + column + " FROM public.paper_reconciliation_receipts LIMIT 1"
                )
            except DBAPIError as denied:
                assert denied.orig.sqlstate == "42501"
            else:
                raise AssertionError("Receipt projection exceeded fixed column boundary")
        print(READ_MARKER)
        return
    f.prepare()
    profile = f.profile()
    if sys.argv[-1:] == ["publication"]:
        from sqlalchemy.exc import DBAPIError

        from packages.observability.outbox import Outbox

        f.api.enqueue("KILL", {"action": "KILL"}, "operator", "kill-publication-0001", f.trace)
        outbox, delivered, cursors = Outbox(f.engines["execution"]), [], []
        for _ in range(30):
            cursor = outbox.publish_one(lambda event: delivered.append(event.event_id))
            if cursor is None:
                break
            cursors.append(cursor)
        assert delivered and cursors == sorted(set(cursors))
        assert f.sql("execution", "SELECT count(*) FROM publish_log") == len(delivered)
        assert (
            f.sql("execution", "SELECT count(*) FROM outbox_events WHERE published_at IS NULL") == 0
        )
        for role, statement in (
            ("api", "SELECT cursor FROM publish_log"),
            ("worker", "SELECT cursor FROM publish_log"),
            ("api", "UPDATE publish_cursor SET next_cursor=next_cursor+1"),
            ("worker", "UPDATE outbox_events SET published_at=clock_timestamp()"),
            ("execution", "UPDATE publish_log SET cursor=cursor+1"),
        ):
            try:
                f.sql(role, statement)
            except DBAPIError as denied:
                assert denied.orig.sqlstate == "42501"
            else:
                raise AssertionError("Publication exceeded fixed column boundary")
        print(PUBLISH_MARKER)
        return
    # Owner-only fixture inserts an audited immutable old observation. No
    # UPDATE of snapshots, trigger disabling or permission relaxation occurs.
    with psycopg.connect(dbname=f.database, **f.common) as c:
        baseline = c.execute("SELECT to_jsonb(b) FROM paper_reconciliation_baselines b").fetchone()[
            0
        ]
        previous = c.execute("SELECT id,cash,equity,exposure FROM portfolio_snapshots").fetchone()
        c.execute(
            "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
            "entity_id,entity_version,trace_id) SELECT 'quiet-audit-'||entity,"
            "clock_timestamp(),'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]',entity,2,%s "
            "FROM unnest(ARRAY['paper','quiet-old']) entity",
            (f.trace,),
        )
        c.execute(
            "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
            "entity_id,entity_version,trace_id) SELECT 'quiet-context',clock_timestamp(),"
            "'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]','paper',version+1,%s "
            "FROM risk_account_context",
            (f.trace,),
        )
        c.execute(
            "INSERT INTO portfolio_snapshots SELECT 'quiet-old',currency,equity,cash,exposure,"
            "clock_timestamp()-interval '121 seconds',2,quality,reconciliation "
            "FROM portfolio_snapshots WHERE id=%s",
            (previous[0],),
        )
        c.execute("UPDATE accounts SET ledger_version=2 WHERE id='paper'")
        c.execute(
            "UPDATE risk_account_context SET version=version+1,portfolio_snapshot_id='quiet-old',"
            "portfolio_version=2,actor_id='fixture',trace_id=%s WHERE account_id='paper'",
            (f.trace,),
        )
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
            orders[client] = value
        else:
            assert request.method == "GET"
            if path == "/v2/clock":
                value = dict(timestamp=stamp, is_open=True)
            elif path == "/v2/account":
                value = dict(
                    id=f.account,
                    currency="USD",
                    cash="100000",
                    equity="100000",
                    portfolio_value="100000",
                    status="ACTIVE",
                    account_blocked=False,
                    trading_blocked=False,
                    trade_suspended_by_user=False,
                )
            elif path in ("/v2/positions", "/v2/account/activities"):
                value = []
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
                    fractionable=True,
                )
            elif path == "/v2/calendar":
                value = [
                    dict(date=n.astimezone(zone).date().isoformat(), open="09:30", close="16:00")
                ]
            elif path == "/v2/stocks/quotes/latest":
                value = dict(
                    quotes={
                        symbol: dict(bp=14.99, ap=15, t=stamp)
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
    worker = WorkerRuntime(config, PostgresLedger(engine=f.engines["worker"]))
    coordinator.verifier.verifier_factory = lambda **k: PaperAccountVerifier(
        **k, transport=transport
    )
    start_errors = []
    real_start = coordinator.verifier.start_handler

    def observed_start(*args):
        try:
            return real_start(*args)
        except Exception as error:
            start_errors.append(error)
            raise

    coordinator.verifier.start_handler = observed_start
    try:
        with (
            patch("packages.brokers.alpaca_paper._EASTERN", zone),
            patch("packages.brokers.alpaca_market_data._EASTERN", zone),
            patch("services.worker.paper_limit._EASTERN", zone),
            patch.object(PaperMarketConfig, "load", return_value=market_config),
        ):
            # Restart fencing precedes acceptance and must not load credentials.
            coordinator.command_tick()
            assert not credential_reads and not f.http_calls
            assert f.sql("worker", "SELECT paper_worker_heartbeat('READY')")
            body = {
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
                    "strategy",
                )
            }
            body |= dict(
                symbols=list(config.instruments),
                confirmation="START_PAPER",
                expiresAt=(f.clock.now() + timedelta(seconds=25))
                .isoformat()
                .replace("+00:00", "Z"),
            )
            accepted = f.api.enqueue("START", body, "operator", "start-coordinator-0001", f.trace)
            coordinator.command_tick()
            if start_errors:
                raise start_errors[0]
            operation = f.api.read_operation(accepted["operationId"])
            assert operation["status"] == "SUCCEEDED", operation
            assert f.api.read_status(f.trace)["entryGateOpen"]
            with psycopg.connect(dbname=f.database, **f.common) as c:
                assert (
                    c.execute(
                        "SELECT to_jsonb(b) FROM paper_reconciliation_baselines b"
                    ).fetchone()[0]
                    == baseline
                )
                assert (
                    c.execute(
                        "SELECT cash,equity,exposure FROM portfolio_snapshots WHERE id='quiet-old'"
                    ).fetchone()
                    == previous[1:]
                )
                assert c.execute(
                    "SELECT ledger_version,as_of>clock_timestamp()-interval '30 seconds' "
                    "FROM portfolio_snapshots ORDER BY ledger_version DESC LIMIT 1"
                ).fetchone() == (3, True)
            assert worker.tick() == "READY"
            assert f.sql("execution", "SELECT count(*) FROM trade_proposals") >= 1
            # Next actual Execution cycle must consume the existing immutable
            # Worker proof before refreshing any binding/quote version.
            assert coordinator.recovery_tick() == "READY"
            assert f.sql("execution", "SELECT count(*) FROM order_intents") >= 1
            assert f.sql("execution", "SELECT count(*) FROM broker_orders") >= 1
            assert (
                f.sql("execution", "SELECT count(*) FROM broker_orders WHERE status='ACKNOWLEDGED'")
                >= 1
            )
            assert submitted and len({b["client_order_id"] for b in submitted}) == len(submitted)
            assert f.sql(
                "execution",
                "SELECT cash FROM portfolio_snapshots ORDER BY ledger_version DESC LIMIT 1",
            ) == Decimal(100000)
            submitted_count = len(submitted)
            assert f.api.rotation_begin("operator", "rotation-coordinator-0002", f.trace) == 2
            assert not f.api.read_status(f.trace)["entryGateOpen"]
            # A pending credential generation cannot remove the retained
            # attestation needed to recover already acknowledged orders.
            assert coordinator.recovery_tick() == "READY"
            assert (
                f.sql("execution", "SELECT count(*) FROM broker_orders WHERE status='UNKNOWN'") == 0
            )
            store.save_generation("alpaca", 2, synthetic_credentials)
            assert f.api.rotation_complete(2)
            verify = f.api.enqueue(
                "VERIFY_CONNECTION",
                {"credentialGeneration": 2},
                "operator",
                "verify-coordinator-0002",
                f.trace,
            )
            coordinator.command_tick()
            verification = f.api.read_operation(verify["operationId"])
            assert verification["status"] == "SUCCEEDED", verification
            assert coordinator.recovery_tick() == "READY"
            assert (
                f.sql("execution", "SELECT count(*) FROM broker_orders WHERE status='UNKNOWN'") == 0
            )
            assert not f.api.read_status(f.trace)["entryGateOpen"]
            assert len(submitted) == submitted_count and 2 in credential_reads
            public = json.dumps([f.api.read_status(f.trace), profile], default=str)
            audit = f.sql(
                "execution", "SELECT string_agg(row_to_json(a)::text,'') FROM audit_events a"
            )
            assert all(
                canary not in public + audit
                for canary in ("synthetic-key-canary", "synthetic-secret-canary", f.account)
            )
    finally:
        coordinator.close()
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
        public_error = (
            error.args[0].get("errorCode") if error.args and type(error.args[0]) is dict else None
        )
        code = ":" + public_error if public_error in ERROR_CODES else ""
        denied = re.fullmatch(
            r"permission denied for (?:table|function|schema|sequence) ([a-z_]+)",
            getattr(getattr(root, "diag", None), "message_primary", "") or "",
        )
        if denied:
            code = ":" + denied[1].upper()
        print(
            f"APW02_FAIL:COORDINATOR:{type(root).__name__}{code}:"
            f"{Path(location.filename).name}:{location.lineno}"
        )
        sys.exit(1)
