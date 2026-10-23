"""Guarded synthetic lifecycle fixture and actual fixed-role Paper SQL checks."""

from pathlib import Path

MARKER = "PASS: fixed Paper profile, attestation, activation, pause and expiry"


def test_paper_lifecycle_postgres():
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER) == MARKER


def test_paper_quiet_portfolio_refresh_postgres():
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER, guest_args=("quiet",)) == MARKER


def test_paper_counter_exhaustion_postgres():
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER, guest_args=("counter",)) == MARKER


def test_paper_start_budget_and_daily_guards_postgres():
    from tests.integration.test_paper_provision import host

    for scenario in ("budget", "daily-loss", "other-holding"):
        assert host(Path(__file__), MARKER, guest_args=(scenario,)) == MARKER


class PaperFixture:
    """Disposable cluster only; real SQL authority, synthetic GET-only provider."""

    def __init__(self):
        import tempfile
        import uuid

        import psycopg
        from sqlalchemy import URL, create_engine, event

        from packages.persistence.ledger import PostgresLedger
        from services.control_api.paper_commands import PaperCommands
        from services.runtime.common import Clock, Ids
        from services.runtime.provision_paper import DATABASE, provision_paper

        self.common = dict(host="postgres", user="lab_migration", password="synthetic-unused")
        self.database = DATABASE
        self.clock, self.ids = Clock(), Ids()
        self.command_owner = str(uuid.uuid4())
        self.owner, self.trace = "synthetic-execution-owner", "a" * 32
        self.account = "11111111-1111-4111-8111-111111111111"
        self.http_calls = []
        self.route_handler = None
        self.initial_holding_value = None
        self.current_holding_value = None
        self.market_prices = {}
        with psycopg.connect(dbname="lab", autocommit=True, **self.common) as c:
            for name in ("lab_runtime", "lab_demo"):
                c.execute(f"CREATE DATABASE {name}")
                c.execute(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC")
        with tempfile.TemporaryDirectory() as directory:
            provision_paper(self.common, Path(directory))
        self.engines = {}
        for role in ("api", "execution", "worker", "watchdog"):
            group = {"api": "atl_paper_control_api"}.get(role, "atl_paper_" + role)
            engine = create_engine(
                URL.create(
                    "postgresql+psycopg",
                    username=group + "_login",
                    host="postgres",
                    database=DATABASE,
                )
            )

            def initialize(connection, _record, group=group):
                with connection.cursor() as cursor:
                    cursor.execute("SET ROLE " + group)
                connection.commit()

            event.listen(engine, "connect", initialize)
            self.engines[role] = engine
        self.api = PaperCommands(self.engines["api"])
        self.execution = PaperCommands(self.engines["execution"])
        self.ledger = PostgresLedger(engine=self.engines["execution"])

    def sql(self, role, statement, **values):
        from sqlalchemy import text

        with self.engines[role].begin() as c:
            return c.execute(text(statement), values).scalar_one_or_none()

    def prepare(self):
        from datetime import timedelta
        from decimal import Decimal

        from packages.risk.paper_daily import (
            DailyRiskObservation,
            DailyRiskReference,
            EquityInterval,
        )
        from services.execution.paper_daily_evidence import PreparedPaperEvidence
        from services.execution.paper_initial_evidence import (
            InitialPaperEvidence,
            InitialPaperPosition,
        )
        from services.execution.paper_initialization import complete_preparation
        from services.execution.recovery_health import claim_execution_owner

        assert self.api.rotation_begin("operator", "rotation-lifecycle-0001", self.trace) == 1
        assert self.api.rotation_complete(1)
        verified = self.api.enqueue(
            "VERIFY_CONNECTION",
            {"credentialGeneration": 1},
            "operator",
            "verify-lifecycle-0001",
            self.trace,
        )
        claim = self.execution.claim(self.command_owner, lease_seconds=120)
        n = self.clock.now()
        assert self.execution.finish(
            verified["operationId"],
            self.command_owner,
            claim["fence"],
            public=dict(
                credentialGeneration=1,
                observedAt=n,
                verifiedAt=n,
                accountLabel="paper-11111111",
                currency="USD",
                cash=Decimal(100000),
                marketDataFeed="IEX",
                providerVerification="CONFIRMED",
                blockers=[],
            ),
            private_account_id=self.account,
        )
        self.api.enqueue(
            "PREPARE_LEDGER",
            dict(
                credentialGeneration=1,
                accountGeneration=1,
                verificationOperationId=verified["operationId"],
            ),
            "operator",
            "prepare-lifecycle-0001",
            self.trace,
        )
        claim = self.execution.claim(self.command_owner, lease_seconds=120)
        n = self.clock.now()
        midnight = n.replace(hour=0, minute=0, second=0, microsecond=0)
        equity = Decimal(100000) + (self.initial_holding_value or Decimal(0))
        reference = DailyRiskReference(
            "UTC_DAY",
            midnight,
            midnight + timedelta(days=1),
            EquityInterval(equity - Decimal(".01"), equity + Decimal(".01")),
            equity - Decimal(".01"),
            "b" * 64,
        )
        positions = (
            ()
            if self.initial_holding_value is None
            else (
                InitialPaperPosition(
                    "22222222-2222-4222-8222-222222222222",
                    "SPY",
                    Decimal(60),
                    Decimal(770),
                    Decimal("12.83"),
                    self.initial_holding_value,
                ),
            )
        )
        initial = InitialPaperEvidence(
            1,
            self.account,
            Decimal(100000),
            equity,
            None,
            positions,
            (),
            (),
            n,
            n,
            n,
            "a" * 64,
            "c" * 64,
        )
        observed = DailyRiskObservation(
            reference.evidence_hash,
            n,
            EquityInterval(equity, equity),
            EquityInterval(Decimal(0), Decimal(0)),
            True,
            initial.evidence_hash,
        )
        complete_preparation(
            self.execution,
            claim,
            self.command_owner,
            PreparedPaperEvidence(initial, reference, observed),
        )
        self.fence = claim_execution_owner(
            ledger=self.ledger, account_id="paper", owner_id=self.owner, trace_id=self.trace
        )
        assert self.sql(
            "execution",
            "SELECT paper_restart_close(:owner,:fence)",
            owner=self.owner,
            fence=self.fence,
        )

    def profile(self, *, version=0, budget="1000", key="profile-lifecycle-0001", changes=None):
        import json

        body = dict(
            riskBudget=budget,
            currency="USD",
            strategy="neutral-baseline",
            credentialGeneration=1,
            accountGeneration=1,
            expectedProfileVersion=version,
            confirmation="SAVE_PAPER_PROFILE",
            feeAttestation=dict(
                scope="alpaca-paper-direct-retail-us-equity-zero-v1", confirmed=True
            ),
        )
        if changes is not None:
            body.update(changes)
        return self.sql(
            "api",
            "SELECT paper_trading_profile_put('operator',:key,:trace,CAST(:body AS jsonb))",
            key=key,
            trace=self.trace,
            body=json.dumps(body),
        )

    def handler(self, request):
        import httpx

        self.http_calls.append((request.method, request.url.path))
        if self.route_handler is not None:
            result = self.route_handler(request)
            if result is not None:
                return result
        assert request.method == "GET"
        if request.url.path == "/v2/clock":
            value = dict(timestamp=self.clock.now().isoformat())
        elif request.url.path == "/v2/account":
            from decimal import Decimal

            equity = str(Decimal(100000) + (self.current_holding_value or Decimal(0)))
            value = dict(
                id=self.account,
                currency="USD",
                cash="100000",
                equity=equity,
                portfolio_value=equity,
                status="ACTIVE",
                account_blocked=False,
                trading_blocked=False,
                trade_suspended_by_user=False,
            )
        elif request.url.path in ("/v2/positions", "/v2/orders", "/v2/account/activities"):
            value = []
            if request.url.path == "/v2/positions" and self.current_holding_value is not None:
                value = [
                    dict(
                        asset_id="22222222-2222-4222-8222-222222222222",
                        symbol="SPY",
                        side="long",
                        qty="60",
                        avg_entry_price="12.83",
                        cost_basis="770",
                        market_value=str(self.current_holding_value),
                    )
                ]
        else:
            raise AssertionError("unexpected synthetic provider route")
        return httpx.Response(200, json=value)

    def refresh(self, *, publish=True):
        import json

        import httpx

        from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
        from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
        from services.execution.broker_binding import AlpacaExecutionBinding
        from services.execution.broker_recovery import restore_alpaca_attempts
        from services.execution.paper_reconciliation import reconcile_paper_account
        from services.execution.recovery_health import heartbeat_execution_owner

        heartbeat_execution_owner(
            ledger=self.ledger, account_id="paper", owner_id=self.owner, fencing_token=self.fence
        )
        self.broker = AlpacaPaperBrokerAdapter(
            self.clock,
            self.ids,
            api_key="synthetic-key",
            api_secret="synthetic-secret",
            paper_enabled=True,
            expected_account_id=self.account,
            reconciliation_fee_profile="alpaca-paper-us-retail-equity-zero-v1",
            transport=httpx.MockTransport(self.handler),
            proxy_deployment="paper",
        )
        self.market = AlpacaMarketDataProvider(
            self.clock,
            self.ids,
            api_key="synthetic-key",
            api_secret="synthetic-secret",
            enabled=True,
            transport=httpx.MockTransport(self.handler),
            proxy_deployment="paper",
        )
        self.binding = AlpacaExecutionBinding(self.ledger, "paper", self.broker, self.market)
        restore_alpaca_attempts(
            ledger=self.ledger,
            account_id="paper",
            broker=self.broker,
            binding=self.binding,
            owner_id=self.owner,
            fencing_token=self.fence,
        )
        self.receipt = reconcile_paper_account(
            ledger=self.ledger,
            account_id="paper",
            broker=self.broker,
            binding=self.binding,
            owner_id=self.owner,
            fencing_token=self.fence,
        )
        assert self.receipt.ready, self.receipt.reason_code
        reference = self.sql("execution", "SELECT paper_daily_reference()")
        equity = self.sql(
            "execution",
            "SELECT trim_scale(p.equity)::text FROM portfolio_snapshots p "
            "JOIN risk_account_context c ON c.portfolio_snapshot_id=p.id WHERE "
            "c.account_id='paper'",
        )
        body = reference | dict(
            observedAt=self.clock.now().isoformat().replace("+00:00", "Z"),
            currentEquity=equity,
            externalFunding="0",
            observationHash="d" * 64,
        )
        assert self.sql(
            "execution",
            "SELECT paper_daily_append(:owner,:fence,:portfolio,CAST(:body AS jsonb))",
            owner=self.owner,
            fence=self.fence,
            portfolio=self.receipt.portfolio_version,
            body=json.dumps(body),
        )
        if publish:
            self.publish_sources()
        assert self.sql("worker", "SELECT paper_worker_heartbeat('READY')")

    def publish_sources(self):
        """Audited admitted market proofs; provider binding certification is separate."""
        import json
        from datetime import timedelta, timezone
        from decimal import Decimal

        import psycopg

        with psycopg.connect(dbname=self.database, **self.common) as c:
            now = c.execute("SELECT transaction_timestamp()").fetchone()[0]
            self.zone = timezone(
                timedelta(hours=12)
                - timedelta(
                    hours=now.hour,
                    minutes=now.minute,
                    seconds=now.second,
                    microseconds=now.microsecond,
                )
            )
            context = c.execute(
                "SELECT portfolio_snapshot_id,portfolio_version FROM risk_account_context "
                "WHERE account_id='paper'"
            ).fetchone()
            self.snapshot, self.portfolio_version = context
            for symbol, asset in (
                ("SPY", "22222222-2222-4222-8222-222222222222"),
                ("QQQ", "33333333-3333-4333-8333-333333333333"),
            ):
                price = self.market_prices.get(symbol, Decimal(15))
                for entity in (
                    f"paper:{symbol}:provider-asset",
                    f"paper:{symbol}:proof",
                    f"paper:{symbol}",
                ):
                    c.execute(
                        "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                        "entity_id,entity_version,trace_id) VALUES(gen_random_uuid()::text,"
                        "transaction_timestamp(),'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]',%s,1,%s)",
                        (entity, self.trace),
                    )
                c.execute(
                    "INSERT INTO broker_asset_bindings VALUES('paper',%s,%s,'fixture',%s,"
                    "transaction_timestamp()) ON CONFLICT(account_id,instrument_id) DO NOTHING",
                    (symbol, asset, self.trace),
                )
                c.execute(
                    "INSERT INTO broker_binding_proofs VALUES('paper',%s,1,1,1,%s,%s,%s,%s,%s,%s,"
                    "%s,%s,%s,'fixture',%s)",
                    (
                        symbol,
                        self.account,
                        asset,
                        now - timedelta(hours=2, minutes=30),
                        now + timedelta(hours=4),
                        now,
                        now,
                        now,
                        now,
                        now + timedelta(seconds=120),
                        self.trace,
                    ),
                )
                c.execute(
                    "INSERT INTO risk_instrument_context(account_id,instrument_id,version,"
                    "capability_version,broker_id,paper_confirmed,regular_session_confirmed,"
                    "market_order_confirmed,limit_order_confirmed,fractional_confirmed,quantity_step,"
                    "price_tick,session_open,session_version,session_valid_until,valid_until,actor_id,trace_id)"
                    " VALUES('paper',%s,1,1,'alpaca-paper',true,true,true,true,true,"
                    ".000000001,.01,true,"
                    "'synthetic-session',%s,%s,'fixture',%s)",
                    (symbol, now + timedelta(hours=4), now + timedelta(seconds=120), self.trace),
                )
                c.execute(
                    "INSERT INTO evidence_bundles VALUES(%s,%s,'[\"alpaca-market-data:iex\"]',"
                    "%s,'[]','VALID',true)",
                    (symbol + "-evidence", symbol, now),
                )
                c.execute(
                    "INSERT INTO market_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,100,%s,%s,%s,"
                    "'VALID','PT15M',true,1)",
                    (symbol + "-bar", symbol, now - timedelta(minutes=15), now,
                     price, price, price, price, now, now, now),
                )
                c.execute(
                    "INSERT INTO market_states VALUES(%s,%s,'US_EQUITY',%s,%s,%s,%s,"
                    "%s,"
                    ".01,%s,%s,%s,%s,'VALID',%s,1)",
                    (
                        symbol + "-state",
                        symbol,
                        symbol + "-bar",
                        symbol + "-evidence",
                        self.snapshot,
                        "paper-position-" + symbol,
                        json.dumps(dict(previous_close=str(price - 1), last_close=str(price),
                                        window_volume="100")),
                        now,
                        now,
                        now,
                        now,
                        self.trace,
                    ),
                )
        for symbol in ("SPY", "QQQ"):
            price = self.market_prices.get(symbol, Decimal(15))
            stamp = self.clock.now().isoformat().replace("+00:00", "Z")
            quote = dict(
                sourceId="alpaca-market-data:iex",
                bid=str(price - Decimal(".01")),
                ask=str(price),
                providerTime=stamp,
                observedAt=stamp,
                sourceCapabilityVersion=1,
            )
            result = self.sql(
                "execution",
                "SELECT paper_quote_append(:state,1,:owner,:fence,CAST(:q AS jsonb))",
                state=symbol + "-state",
                owner=self.owner,
                fence=self.fence,
                q=json.dumps(quote),
            )
            assert set(result) == {"quoteId", "quoteHash"}, result

    def start(self, key="start-lifecycle-0001", ttl=25, symbols=("SPY", "QQQ")):
        import json
        from datetime import timedelta

        profile = self.sql("api", "SELECT paper_trading_profile_get(:trace)", trace=self.trace)
        body = {
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
                "strategy",
            )
        }
        body |= dict(
            symbols=list(symbols),
            confirmation="START_PAPER",
            expiresAt=(self.clock.now() + timedelta(seconds=ttl))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        operation = self.api.enqueue("START", body, "operator", key, self.trace)
        claim = self.execution.claim(self.command_owner, lease_seconds=120)
        proof = dict(
            executionOwnerId=self.owner,
            executionFencingToken=self.fence,
            reconciliationReceiptId=self.receipt.receipt_id,
        )
        result = self.sql(
            "execution",
            "SELECT paper_activation_begin(CAST(:id AS uuid),CAST(:owner AS uuid),"
            ":fence,CAST(:proof AS jsonb))",
            id=operation["operationId"],
            owner=self.command_owner,
            fence=claim["fence"],
            proof=json.dumps(proof),
        )
        return operation, claim, proof, result


def guest():
    import json
    import time
    from types import SimpleNamespace
    from unittest.mock import patch

    from services.control_api.paper_dto import Profile, Status
    from services.runtime.worker import WorkerRuntime

    f = PaperFixture()
    empty = f.sql("api", "SELECT paper_trading_profile_get(:trace)", trace=f.trace)
    assert not empty["configured"] and empty["riskBudget"] is None and empty["profileVersion"] == 0
    Profile.model_validate(empty)
    Status.model_validate(f.api.read_status(f.trace))
    assert f.sql("execution", "SELECT paper_activation_read()") is None
    f.prepare()
    unchanged_epoch = f.sql("execution", "SELECT paper_activation_read()")["epoch"]
    for changes in (
        {"apiKey": "synthetic-canary"},
        {"riskBudget": "1000001"},
        {"credentialGeneration": True},
        {"confirmation": "START_PAPER"},
        {"feeAttestation": {"scope": "unknown", "confirmed": True}},
    ):
        invalid = f.profile(key="profile-invalid-0001", changes=changes)
        assert invalid == {"error": "INVALID_REQUEST"}
    assert f.profile(version=1, key="profile-conflict-0001") == {"error": "VERSION_CONFLICT"}
    assert f.sql("execution", "SELECT paper_activation_read()")["epoch"] == unchanged_epoch
    profile = f.profile()
    assert profile["configured"] and profile["profileVersion"] == 1
    Profile.model_validate(profile)
    assert f.profile() == profile
    assert f.sql("execution", "SELECT paper_fee_authority()")["profileVersion"] == 1
    f.refresh()
    operation, claim, proof, result = f.start()
    assert set(result) == {"activationId", "expiresAt"}, result
    guard = f.sql("execution", "SELECT paper_activation_guard('paper',NULL)")
    assert guard and guard["activationId"] == result["activationId"] and not guard["proposalLinked"]
    assert f.sql("worker", "SELECT paper_planning_active()")
    assert f.api.read_status(f.trace)["entryGateOpen"] is True
    active_status = Status.model_validate(f.api.read_status(f.trace))
    assert active_status.reconciledAt is not None and active_status.riskBudget == "1000"
    assert f.sql("watchdog", "SELECT paper_watchdog_poll(:trace)", trace=f.trace)
    assert f.sql("worker", "SELECT paper_planning_active()")
    frozen = f.sql(
        "execution",
        "SELECT paper_activation_begin(CAST(:id AS uuid),CAST(:owner AS uuid),"
        ":fence,CAST(:proof AS jsonb))",
        id=operation["operationId"],
        owner=f.command_owner,
        fence=claim["fence"],
        proof=json.dumps(proof),
    )
    assert frozen == result
    runtime = WorkerRuntime(
        SimpleNamespace(
            deployment="paper", account_id="paper", instruments=("SPY",), decision_provider="mock"
        ),
        f.ledger,
    )
    # The worker must use its own real fixed role, not Execution's financial role.
    from packages.persistence.ledger import PostgresLedger

    runtime = WorkerRuntime(runtime.config, PostgresLedger(engine=f.engines["worker"]))
    with patch("services.worker.paper_limit._EASTERN", f.zone):
        assert runtime.tick() == "READY"
    assert f.sql("execution", "SELECT count(*) FROM trade_proposals") == 1
    paused = f.api.enqueue(
        "PAUSE", {"action": "PAUSE"}, "operator", "pause-lifecycle-0001", f.trace
    )
    assert paused["status"] == "ACCEPTED"
    assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
    assert not f.sql("worker", "SELECT paper_planning_active()")
    assert (
        f.sql("execution", "SELECT safety_state FROM risk_account_context WHERE account_id='paper'")
        == "PAUSE_NEW"
    )
    assert f.api.read_status(f.trace)["entryGateOpen"] is False
    # Idempotent control cannot increment epoch or recreate authority.
    after_pause = f.sql("execution", "SELECT paper_activation_read()")["epoch"]
    assert (
        f.api.enqueue("PAUSE", {"action": "PAUSE"}, "operator", "pause-lifecycle-0001", f.trace)
        == paused
    )
    assert f.sql("execution", "SELECT paper_activation_read()")["epoch"] == after_pause
    control = f.execution.claim(f.command_owner, lease_seconds=120)
    assert f.execution.finish(control["operationId"], f.command_owner, control["fence"])
    f.refresh(publish=False)
    second_op, second_claim, second_proof, second = f.start("start-lifecycle-0002", ttl=2)
    assert "activationId" in second
    time.sleep(2.1)
    assert f.sql("execution", "SELECT paper_activation_guard('paper',NULL)") is None
    expired_status = f.api.read_status(f.trace)
    Status.model_validate(expired_status)
    assert expired_status["operatingState"] == "PAUSED"
    assert expired_status["blockers"] == ["APPROVAL_EXPIRED"]
    assert f.sql("execution", "SELECT (paper_activation_read()->>'activation') IS NULL")
    import psycopg

    with psycopg.connect(dbname=f.database, **f.common) as c:
        assert c.execute("SELECT operating_state FROM paper_workflow_state").fetchone() == (
            "RUNNING",
        )
    assert f.sql("watchdog", "SELECT paper_watchdog_poll(:trace)", trace=f.trace)
    assert (
        f.sql("execution", "SELECT safety_state FROM risk_account_context WHERE account_id='paper'")
        == "PAUSE_NEW"
    )
    retry = f.sql(
        "execution",
        "SELECT paper_activation_begin(CAST(:id AS uuid),CAST(:owner AS uuid),"
        ":fence,CAST(:proof AS jsonb))",
        id=second_op["operationId"],
        owner=f.command_owner,
        fence=second_claim["fence"],
        proof=json.dumps(second_proof),
    )
    assert retry == second and not f.sql("worker", "SELECT paper_planning_active()")
    # Rotation preserves retained-generation recovery authority but revokes entry.
    assert f.api.rotation_begin("operator", "rotation-lifecycle-0002", f.trace) == 2
    assert f.sql("execution", "SELECT paper_fee_authority()") is None
    recovery = f.sql("execution", "SELECT paper_recovery_fee_authority()")
    assert recovery["credentialGeneration"] == recovery["attestedCredentialGeneration"] == 1
    assert f.api.rotation_complete(2)
    verify = f.api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": 2},
        "operator",
        "verify-lifecycle-0002",
        f.trace,
    )
    verify_claim = f.execution.claim(f.command_owner, lease_seconds=120)
    now = f.clock.now()
    from decimal import Decimal

    assert f.execution.finish(
        verify["operationId"],
        f.command_owner,
        verify_claim["fence"],
        public=dict(
            credentialGeneration=2,
            observedAt=now,
            verifiedAt=now,
            accountLabel="paper-11111111",
            currency="USD",
            cash=Decimal(100000),
            marketDataFeed="IEX",
            providerVerification="CONFIRMED",
            blockers=[],
        ),
        private_account_id=f.account,
    )
    assert f.sql("execution", "SELECT paper_fee_authority()") is None
    recovered = f.sql("execution", "SELECT paper_recovery_fee_authority()")
    assert recovered["credentialGeneration"] == 2 and recovered["attestedCredentialGeneration"] == 1
    assert (
        f.sql("execution", "SELECT fee_authority_profile FROM paper_reconciliation_baselines")
        is None
    )
    assert not f.sql("worker", "SELECT paper_planning_active()")
    f.api.enqueue("KILL", {"action": "KILL"}, "operator", "kill-lifecycle-0001", f.trace)
    assert (
        f.sql("execution", "SELECT safety_state FROM risk_account_context WHERE account_id='paper'")
        == "HALTED"
    )
    f.api.enqueue("STOP", {"action": "STOP"}, "operator", "stop-lifecycle-0001", f.trace)
    assert f.api.read_status(f.trace)["operatingState"] == "HALTED"
    # A saturated generation must never prevent an explicit financial halt.
    import psycopg

    with psycopg.connect(dbname=f.database, **f.common) as c:
        c.execute("UPDATE paper_workflow_state SET epoch=9007199254740991")
    maximum = f.api.enqueue(
        "KILL", {"action": "KILL"}, "operator", "kill-maximum-epoch-0001", f.trace
    )
    assert maximum["acceptedGeneration"] == 9007199254740991
    assert (
        f.api.enqueue("KILL", {"action": "KILL"}, "operator", "kill-maximum-epoch-0001", f.trace)
        == maximum
    )
    assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "HALTED"
    print(MARKER)


def quiet_guest():
    import psycopg

    f = PaperFixture()
    f.prepare()
    f.profile()
    with psycopg.connect(dbname=f.database, **f.common) as c:
        baseline = c.execute("SELECT to_jsonb(b) FROM paper_reconciliation_baselines b").fetchone()[
            0
        ]
        previous = c.execute("SELECT id,cash,equity,exposure FROM portfolio_snapshots").fetchone()
        c.execute(
            "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,"
            "entity_version,trace_id) SELECT "
            "'quiet-audit-'||entity,clock_timestamp(),'fixture','fixture',"
            "'[\"SYNTHETIC_FIXTURE\"]',entity,2,%s FROM unnest(ARRAY['paper','quiet-old']) entity",
            (f.trace,),
        )
        c.execute(
            "INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,"
            "entity_version,trace_id) SELECT 'quiet-context',clock_timestamp(),'fixture','fixture',"
            "'[\"SYNTHETIC_FIXTURE\"]','paper',version+1,%s FROM risk_account_context",
            (f.trace,),
        )
        c.execute(
            "INSERT INTO portfolio_snapshots SELECT 'quiet-old',currency,equity,cash,exposure,"
            "clock_timestamp()-interval '121 seconds',2,quality,reconciliation FROM "
            "portfolio_snapshots "
            "WHERE id=%s",
            (previous[0],),
        )
        c.execute("UPDATE accounts SET ledger_version=2 WHERE id='paper'")
        c.execute(
            "UPDATE risk_account_context SET version=version+1,portfolio_snapshot_id='quiet-old',"
            "portfolio_version=2,actor_id='fixture',trace_id=%s WHERE account_id='paper'",
            (f.trace,),
        )
    f.refresh()
    assert f.receipt.ready and f.receipt.portfolio_version == 3
    _, _, _, result = f.start()
    assert "activationId" in result
    with psycopg.connect(dbname=f.database, **f.common) as c:
        assert (
            c.execute("SELECT to_jsonb(b) FROM paper_reconciliation_baselines b").fetchone()[0]
            == baseline
        )
        assert (
            c.execute(
                "SELECT cash,equity,exposure FROM portfolio_snapshots WHERE id='quiet-old'"
            ).fetchone()
            == previous[1:]
        )
        latest = c.execute(
            "SELECT cash,equity,exposure,as_of>clock_timestamp()-interval '30 seconds' "
            "FROM portfolio_snapshots WHERE ledger_version=3"
        ).fetchone()
        assert latest == (*previous[1:], True)
        assert c.execute("SELECT count(*) FROM broker_orders").fetchone() == (0,)
    print(MARKER)


def counter_guest():
    import json

    import psycopg

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    with psycopg.connect(dbname=f.database, **f.common) as c:
        c.execute("UPDATE paper_workflow_state SET epoch=9007199254740991")
    op, claim, proof, failed = f.start()
    assert failed == {"error": "VERSION_CONFLICT"}
    paused = f.api.enqueue(
        "PAUSE", {"action": "PAUSE"}, "operator", "pause-counter-edge-0001", f.trace
    )
    assert paused["acceptedGeneration"] == 9007199254740991
    retry = f.sql(
        "execution",
        "SELECT paper_activation_begin(CAST(:id AS uuid),CAST(:owner AS uuid),"
        ":fence,CAST(:proof AS jsonb))",
        id=op["operationId"],
        owner=f.command_owner,
        fence=claim["fence"],
        proof=json.dumps(proof),
    )
    assert retry == {"error": "VERSION_CONFLICT"}
    assert not f.sql("worker", "SELECT paper_planning_active()")
    assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
    print(MARKER)


def global_risk_guest(scenario):
    from decimal import Decimal

    f = PaperFixture()
    f.initial_holding_value = Decimal(
        30000 if scenario == "other-holding" else 900 if scenario == "budget" else 3000
    )
    f.current_holding_value = (
        Decimal(30000) if scenario == "other-holding" else Decimal(900)
    )
    if scenario == "other-holding":
        f.market_prices["SPY"] = Decimal(500)
    f.prepare()
    f.profile(budget=(
        "50000" if scenario == "other-holding" else "100" if scenario == "budget" else "10000"
    ))
    f.refresh()
    before = f.sql("execution", "SELECT version FROM risk_account_context")
    _, _, _, result = f.start(symbols=("QQQ",) if scenario == "other-holding" else ("SPY", "QQQ"))
    assert result == {
        "error": (
            "POLICY_MISMATCH" if scenario == "other-holding"
            else "BUDGET_EXCEEDED" if scenario == "budget" else "RECONCILIATION_REQUIRED"
        )
    }
    assert f.sql("execution", "SELECT version FROM risk_account_context") == before
    assert f.sql("execution", "SELECT safety_state FROM risk_account_context") == "PAUSE_NEW"
    assert f.sql("execution", "SELECT (paper_activation_read()->>'activation') IS NULL")
    assert f.api.read_status(f.trace)["entryGateOpen"] is False
    print(MARKER)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        scenario = sys.argv[2] if len(sys.argv) > 2 else ""
        if scenario in ("budget", "daily-loss", "other-holding"):
            global_risk_guest(scenario)
        else:
            {"quiet": quiet_guest, "counter": counter_guest}.get(scenario, guest)()
    except Exception as error:
        root = getattr(error, "orig", error)
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"APW02_FAIL:LIFECYCLE:{type(root).__name__}:{Path(frame.filename).name}:{frame.lineno}"
        )
        raise SystemExit(1) from None
