"""Actual fixed Paper DB/login in a disposable cluster; synthetic proofs only."""

from pathlib import Path

MARKER = "PASS: atomic Paper initial basis, private role, fencing and immutable provenance"


def test_paper_initialization_postgres():
    from tests.integration.test_paper_provision import host

    assert host(Path(__file__), MARKER) == MARKER


def guest():
    import json
    import os
    import tempfile
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal
    from types import SimpleNamespace

    import httpx
    import psycopg
    from psycopg import sql
    from sqlalchemy import URL, create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.domain import models as m
    from packages.domain.models import Position
    from packages.persistence.ledger import PostgresLedger
    from packages.risk.budget import budget_reasons, read_cap
    from packages.risk.paper_daily import DailyRiskObservation, DailyRiskReference, EquityInterval
    from packages.risk.paper_daily_sql import is_isolated_paper, read_daily
    from services.control_api.paper_commands import PaperCommandError, PaperCommands
    from services.execution.broker_binding import AlpacaExecutionBinding
    from services.execution.broker_recovery import restore_alpaca_attempts
    from services.execution.paper_cost_basis import costed_fill
    from services.execution.paper_credentials import PaperGenerationReader
    from services.execution.paper_daily_evidence import (
        PaperPreparationCollector,
        PreparedPaperEvidence,
    )
    from services.execution.paper_initial_evidence import InitialPaperEvidence, InitialPaperPosition
    from services.execution.paper_initialization import (
        complete_preparation,
        initial_payload,
        read_initial_history,
    )
    from services.execution.paper_reconciliation import reconcile_paper_account
    from services.execution.paper_verification import PaperAccountVerifier
    from services.execution.recovery_health import claim_execution_owner
    from services.runtime.paper_verification import PaperVerificationRuntime
    from services.runtime.provision_paper import DATABASE, FILES, PAPER_ROLES, provision_paper
    from services.setup.store import ProviderStore, generation_filename

    common = dict(host="postgres", user="lab_migration", password="synthetic-unused")
    with psycopg.connect(dbname="lab", autocommit=True, **common) as admin:
        for name in ("lab_runtime", "lab_demo"):
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
            )
    account, asset = str(uuid.uuid4()), str(uuid.uuid4())
    owner, trace = str(uuid.uuid4()), "a" * 32

    def proof(positions=True):
        now = datetime.now(UTC)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        equity = Decimal("100900" if positions else "100000")
        initial = InitialPaperEvidence(
            1,
            account,
            Decimal("100000"),
            equity,
            None,
            (
                InitialPaperPosition(
                    asset, "SPY", Decimal("60"), Decimal("770"), Decimal("12.83"), Decimal("900")
                ),
            )
            if positions
            else (),
            (),
            (),
            now,
            now,
            now,
            "a" * 64,
            "b" * 64,
        )
        ref = DailyRiskReference(
            "UTC_DAY" if positions else "FIRST_DAY",
            midnight if positions else now,
            midnight + timedelta(days=1),
            EquityInterval(Decimal("100899.99"), Decimal("100900.01"))
            if positions
            else EquityInterval(equity, equity),
            Decimal("100899.99") if positions else equity,
            "c" * 64,
            None if positions else midnight + timedelta(microseconds=1),
        )
        obs = DailyRiskObservation(
            ref.evidence_hash,
            now,
            EquityInterval(equity, equity),
            EquityInterval(Decimal(0), Decimal(0)),
            True,
            initial.evidence_hash,
        )
        return PreparedPaperEvidence(initial, ref, obs)

    class RecordingCommands(PaperCommands):
        def __init__(self, engine):
            super().__init__(engine)
            self.claims = []

        def claim(self, *args, **kwargs):
            result = super().claim(*args, **kwargs)
            if result is not None:
                self.claims.append(deepcopy(result))
            return result

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for imported in (True, False):
            provision_paper(common, directory)
            engines = []

            def port(role, engines=engines):
                path = directory / FILES[role]
                os.chown(path, 0, 0)
                try:
                    credentials = json.loads(path.read_bytes())
                finally:
                    os.chown(path, 10001, 10001)
                engine = create_engine(
                    URL.create(
                        "postgresql+psycopg",
                        host="postgres",
                        database=DATABASE,
                        username=credentials["username"],
                        password=credentials["password"],
                    ),
                    hide_parameters=True,
                )
                engines.append(engine)

                @event.listens_for(engine, "connect")
                def assign(connection, _):
                    connection.execute("SET ROLE " + PAPER_ROLES[role])
                    connection.commit()

                return RecordingCommands(engine) if role == "execution" else PaperCommands(engine)

            api, execution = port("api"), port("execution")
            generation = api.rotation_begin("operator", "rotation-test-0001", trace)
            assert generation == 1 and api.rotation_complete(1)
            verify = api.enqueue(
                "VERIFY_CONNECTION",
                {"credentialGeneration": 1},
                "operator",
                "verification-test-0001",
                trace,
            )
            if imported:
                claim = execution.claim(owner, lease_seconds=120)
                now = datetime.now(UTC)
                assert execution.finish(
                    verify["operationId"],
                    owner,
                    claim["fence"],
                    public={
                        "credentialGeneration": 1,
                        "observedAt": now,
                        "accountLabel": "paper-12345678",
                        "currency": "USD",
                        "cash": Decimal("100000"),
                        "verifiedAt": now,
                        "marketDataFeed": "IEX",
                        "providerVerification": "CONFIRMED",
                        "blockers": [],
                    },
                    private_account_id=account,
                )
            else:
                # Fresh-account proof travels through actual POSIX storage, immutable
                # generation reader, verifier, preparation collector and fixed-role SQL.
                provider_root = directory / "synthetic-provider"
                provider_root.mkdir(mode=0o700)
                for provider in ("alpaca", "jev"):
                    (provider_root / provider).mkdir(mode=0o700)
                key, secret = "synthetic-first-day-canary-key", "synthetic-first-day-canary-secret"
                storage = ProviderStore(provider_root, owner_uid=os.getuid())
                assert storage.save_generation(
                    "alpaca",
                    1,
                    {
                        "apiKey": key,
                        "apiSecret": secret,
                        "expectedAccountId": account,
                        "paperOnly": True,
                    },
                ) == {"status": "CONFIGURED", "credentialGeneration": 1}
                stored = (provider_root / "alpaca" / generation_filename(1)).stat()
                assert stored.st_uid == os.getuid() and stored.st_mode & 0o777 == 0o600
                assert stored.st_nlink == 1
                provider_calls, collected = [], []
                created = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=1)
                unfilled_order = {
                    "id": str(uuid.uuid4()),
                    "status": "canceled",
                    "symbol": "SPY",
                    "side": "buy",
                    "qty": "1",
                    "filled_qty": "0",
                    "filled_avg_price": None,
                    "created_at": created.isoformat(),
                    "submitted_at": created.isoformat(),
                    "updated_at": created.isoformat(),
                }

                def provider_http(
                    request,
                    key=key,
                    secret=secret,
                    provider_calls=provider_calls,
                    created=created,
                    unfilled_order=unfilled_order,
                ):
                    assert request.method == "GET"
                    assert request.url.host in {"paper-api.alpaca.markets", "data.alpaca.markets"}
                    assert request.headers["APCA-API-KEY-ID"] == key
                    assert request.headers["APCA-API-SECRET-KEY"] == secret
                    provider_calls.append(
                        (request.url.host, request.url.path, dict(request.url.params))
                    )
                    stamp = datetime.now(UTC).isoformat()
                    if request.url.path == "/v2/account":
                        value = {
                            "id": account,
                            "currency": "USD",
                            "status": "ACTIVE",
                            "cash": "100000",
                            "equity": "100000",
                            "portfolio_value": "100000",
                            "short_market_value": "0",
                            "last_equity": "100000",
                            "account_blocked": False,
                            "trading_blocked": False,
                            "trade_suspended_by_user": False,
                            "created_at": created.isoformat(),
                            "untrusted_extra": secret,
                        }
                    elif request.url.path == "/v2/stocks/quotes/latest":
                        assert request.url.host == "data.alpaca.markets"
                        assert request.url.params == httpx.QueryParams(
                            {"symbols": "SPY,QQQ", "feed": "iex"}
                        )
                        quote = {"bp": "100", "ap": "100.01", "t": stamp}
                        value = {"quotes": {"SPY": quote, "QQQ": quote}}
                    elif request.url.path == "/v2/clock":
                        value = {"timestamp": stamp}
                    else:
                        assert request.url.path in {
                            "/v2/positions",
                            "/v2/orders",
                            "/v2/account/activities",
                        }
                        assert request.url.host == "paper-api.alpaca.markets"
                        value = (
                            [unfilled_order]
                            if (
                                request.url.path == "/v2/orders"
                                and request.url.params.get("status") == "all"
                            )
                            else []
                        )
                    return httpx.Response(200, json=value)

                class RecordingCollector(PaperPreparationCollector):
                    def collect(self, *args, _captured=collected, **kwargs):
                        result = super().collect(*args, **kwargs)
                        _captured.append(result)
                        return result

                runtime = PaperVerificationRuntime(
                    commands=execution,
                    owner=owner,
                    reader_factory=lambda provider_root=provider_root: PaperGenerationReader(
                        provider_root, owner_uid=os.getuid()
                    ),
                    verifier_factory=lambda **kwargs: PaperAccountVerifier(
                        transport=httpx.MockTransport(provider_http), **kwargs
                    ),
                    preparation_factory=lambda **kwargs: RecordingCollector(
                        transport=httpx.MockTransport(provider_http), **kwargs
                    ),
                )
                assert provider_calls == []
                assert runtime.tick() == "READY"
                assert api.read_operation(verify["operationId"])["status"] == "SUCCEEDED"
                assert len(provider_calls) == 2 and collected == []
                assert execution.claims[-1]["operationId"] == verify["operationId"]
            prepare = api.enqueue(
                "PREPARE_LEDGER",
                {
                    "credentialGeneration": 1,
                    "accountGeneration": 1,
                    "verificationOperationId": verify["operationId"],
                },
                "operator",
                "preparation-test-0001",
                trace,
            )
            if imported:
                claim = execution.claim(owner, lease_seconds=120)
                evidence = proof(True)
            else:
                assert runtime.tick() == "READY"
                assert len(collected) == 1
                evidence = collected[0]
                claim = execution.claims[-1]
                assert claim["operationId"] == prepare["operationId"]
                assert claim["operationType"] == "PREPARE_LEDGER"
                assert evidence.daily_reference.kind == "FIRST_DAY"
                assert evidence.daily_reference.account_created_at == created
                assert evidence.initial.positions == ()
                assert evidence.initial.activity_anchors == ()
                assert len(evidence.initial.order_anchors) == 1
                assert evidence.initial.order_anchors[0].provider_order_id == unfilled_order["id"]
                assert evidence.initial.order_anchors[0].untraded is True
                assert sum(path == "/v2/clock" for _, path, _ in provider_calls) == 2
                assert sum(path == "/v2/account/activities" for _, path, _ in provider_calls) == 2
                assert (
                    sum(
                        path == "/v2/orders" and params["status"] == "all"
                        for _, path, params in provider_calls
                    )
                    == 2
                )
                assert sum(path == "/v2/stocks/quotes/latest" for _, path, _ in provider_calls) == 2
                assert claim["owner"] == owner and claim["fence"] > 0
                assert not any(path.endswith("/history") for _, path, _ in provider_calls)
                assert api.read_operation(prepare["operationId"])["status"] == "SUCCEEDED"
            payload = initial_payload(evidence)
            if not imported:
                assert key not in json.dumps(payload) and secret not in json.dumps(payload)
            with psycopg.connect(dbname=DATABASE, **common) as admin:
                valid = admin.execute(
                    "SELECT public.paper_initial_valid(%s::jsonb)", (json.dumps(payload),)
                ).fetchone()[0]
                assert valid

            if imported:

                def call(
                    body=payload,
                    caller=owner,
                    fence=claim["fence"],
                    execution=execution,
                    operation_id=prepare["operationId"],
                ):
                    return execution._call(
                        "SELECT public.paper_prepare(CAST(:id AS uuid),CAST(:owner AS uuid),:fence,"
                        "CAST(:body AS jsonb))",
                        {
                            "id": operation_id,
                            "owner": caller,
                            "fence": fence,
                            "body": json.dumps(body),
                        },
                    )

                def empty():
                    with psycopg.connect(dbname=DATABASE, **common) as admin:
                        assert admin.execute("SELECT count(*) FROM public.accounts").fetchone() == (
                            0,
                        )
                        assert admin.execute(
                            "SELECT count(*) FROM public.paper_initial_bases"
                        ).fetchone() == (0,)

                for caller, fence in (
                    (str(uuid.uuid4()), claim["fence"]),
                    (owner, 0),
                    (owner, None),
                ):
                    try:
                        call(caller=caller, fence=fence)
                    except PaperCommandError as error:
                        assert error.code == "VERSION_CONFLICT"
                    else:
                        raise AssertionError("invalid owner/fence accepted")
                    empty()
                bad = []
                value = deepcopy(payload)
                value["apiSecret"] = "synthetic-canary"
                bad.append(value)
                value = deepcopy(payload)
                value["cash"] = "10000"
                bad.append(value)
                value = deepcopy(payload)
                value["daily"]["externalFunding"] = None
                bad.append(value)
                value = deepcopy(payload)
                value["daily"]["expiresAt"] = "2000-01-01T00:00:00Z"
                bad.append(value)
                value = deepcopy(payload)
                value["providerAccountId"] = str(uuid.uuid4())
                bad.append(value)
                if imported:
                    value = deepcopy(payload)
                    del value["positions"][0]["costBasis"]
                    bad.append(value)
                    value = deepcopy(payload)
                    value["positions"][0]["symbol"] = "AAPL"
                    bad.append(value)
                anchor = {"id": str(uuid.uuid4()), "hash": "e" * 64, "untraded": True}
                anchor_payload = deepcopy(payload)
                anchor_payload["orderAnchors"] = [anchor]
                first_day_payload = initial_payload(proof(False))
                first_day_payload["orderAnchors"] = [deepcopy(anchor)]
                with psycopg.connect(dbname=DATABASE, **common) as admin:
                    for otherwise_valid in (anchor_payload, first_day_payload):
                        assert admin.execute(
                            "SELECT public.paper_initial_valid(%s::jsonb)",
                            (json.dumps(otherwise_valid),),
                        ).fetchone() == (True,)
                    malformed = []
                    for untraded in (None, "true", 1, [], {}):
                        value = deepcopy(anchor_payload)
                        value["orderAnchors"][0]["untraded"] = untraded
                        malformed.append(value)
                    value = deepcopy(anchor_payload)
                    del value["orderAnchors"][0]["untraded"]
                    malformed.append(value)
                    value = deepcopy(first_day_payload)
                    value["orderAnchors"][0]["untraded"] = False
                    malformed.append(value)
                    for value in malformed:
                        assert admin.execute(
                            "SELECT public.paper_initial_valid(%s::jsonb)", (json.dumps(value),)
                        ).fetchone() == (False,)
                    bad.extend(malformed)
                for value in bad:
                    try:
                        call(value)
                    except PaperCommandError:
                        pass
                    else:
                        raise AssertionError("invalid financial proof accepted")
                    empty()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    assert list(pool.map(lambda _: call(), range(2))) == [{"prepared": True}] * 2
            assert api.read_operation(prepare["operationId"])["status"] == "SUCCEEDED"
            status = api.read_status(trace)
            assert status["setupState"] == "PREPARED" and not status["entryGateOpen"]
            assert account not in json.dumps(status)
            public_operation = api.read_operation(prepare["operationId"])
            assert account not in json.dumps(public_operation)
            if not imported:
                assert key not in json.dumps(status) and secret not in json.dumps(status)
                assert key not in json.dumps(public_operation)
                assert secret not in json.dumps(public_operation)
            complete_preparation(execution, claim, owner, evidence)
            with api.engine.connect() as connection:
                assert not connection.execute(
                    text(
                        "SELECT has_function_privilege(current_user,"
                        "'public.paper_initial_history(text)','EXECUTE')"
                    )
                ).scalar_one()
                try:
                    read_initial_history(connection, "paper")
                except DBAPIError as error:
                    assert error.orig.sqlstate == "42501"
                else:
                    raise AssertionError("public API role read private import history")
            with execution.engine.connect() as connection:
                assert is_isolated_paper(connection)
                history = read_initial_history(connection, "paper")
                assert history.provider_account_id == account
                assert history.baseline_at == evidence.initial.confirmed_at
                assert history.orders == tuple(
                    (anchor.provider_order_id, anchor.evidence_hash)
                    for anchor in evidence.initial.order_anchors
                )
                assert history.activities == tuple(
                    (anchor.activity_id, anchor.evidence_hash)
                    for anchor in evidence.initial.activity_anchors
                )
                assert (
                    connection.execute(
                        text("SELECT public.paper_initial_history('paper')")
                    ).scalar_one()["orders"]
                    == payload["orderAnchors"]
                )
                ref, observation = read_daily(connection, "paper", 1)
                assert ref == evidence.daily_reference
                assert observation == evidence.daily_observation
                assert read_daily(connection, "paper", 2) == (None, None)
                assert read_daily(connection, "other", 1) == (None, None)
                assert connection.execute(
                    text("SELECT public.paper_cost_basis_read('paper-position-SPY',1)")
                ).scalar_one() == Decimal("770" if imported else "0")
                assert (
                    connection.execute(
                        text("SELECT public.paper_cost_basis_read('paper-position-SPY',2)")
                    ).scalar_one_or_none()
                    is None
                )
                assert not connection.execute(
                    text(
                        "SELECT public.paper_cost_basis_append("
                        "'paper-position-SPY',2,59,1,:hash,:trace)"
                    ),
                    {"hash": "d" * 64, "trace": trace},
                ).scalar_one()
            with psycopg.connect(dbname=DATABASE, **common) as admin:
                assert admin.execute("SELECT currency,ledger_version FROM accounts").fetchall() == [
                    ("USD", 1)
                ]
                assert admin.execute("SELECT cash,equity FROM portfolio_snapshots").fetchone() == (
                    evidence.initial.cash,
                    evidence.initial.equity,
                )
                assert admin.execute("SELECT count(*) FROM paper_initial_bases").fetchone() == (1,)
                basis = admin.execute(
                    "SELECT cost_basis FROM paper_position_cost_bases b "
                    "JOIN positions p ON p.id=b.position_id "
                    "WHERE p.instrument_id='SPY'"
                ).fetchone()[0]
                assert basis == Decimal("770" if imported else "0")
                assert admin.execute(
                    "SELECT mode,safety_state,reconciliation_current FROM risk_account_context"
                ).fetchone() == ("DISABLED", "PAUSE_NEW", False)
                assert admin.execute(
                    "SELECT reference_kind FROM paper_daily_risk_evidence"
                ).fetchone() == ("UTC_DAY" if imported else "FIRST_DAY",)
                assert admin.execute("SELECT count(*) FROM order_intents").fetchone() == (0,)
                try:
                    admin.execute("UPDATE paper_initial_bases SET evidence_hash=repeat('d',64)")
                except psycopg.Error:
                    admin.rollback()
                else:
                    raise AssertionError("immutable basis changed")
            # Saved risk authority is independent of the provider's funded cash.
            # Only this isolated fixture owner calls the existing control function;
            # fixed Paper runtime grants remain exactly as provisioned.
            budget_engine = create_engine(
                URL.create(
                    "postgresql+psycopg",
                    host="postgres",
                    database=DATABASE,
                    username=common["user"],
                    password=common["password"],
                ),
                hide_parameters=True,
            )
            try:
                owner_ledger = PostgresLedger(engine=budget_engine)
                with owner_ledger.transaction() as tx:
                    snapshot_id = tx.connection.execute(
                        text("SELECT id FROM portfolio_snapshots")
                    ).scalar_one()
                    position_id = tx.connection.execute(
                        text("SELECT id FROM positions WHERE instrument_id='SPY'")
                    ).scalar_one()
                    snapshot = tx.get(m.PortfolioSnapshot, snapshot_id)
                    held = tx.get(m.Position, position_id)
                    assert snapshot.cash == Decimal("100000")
                    assert snapshot.equity == Decimal("100900" if imported else "100000")
                    # A current real mark makes the imported rejection an exposure
                    # limit rejection rather than a missing-market-evidence fallback.
                    now = datetime.now(UTC)
                    bar = m.MarketBar(
                        "budget-bar",
                        "SPY",
                        now - timedelta(minutes=15),
                        now,
                        Decimal("15"),
                        Decimal("15"),
                        Decimal("15"),
                        Decimal("15"),
                        Decimal("1"),
                        now,
                        now,
                        now,
                        m.Quality.VALID,
                    )
                    bundle = m.EvidenceBundle(
                        "budget-evidence", "SPY", (bar.id,), now, (), m.Quality.VALID
                    )
                    state = m.MarketState(
                        "budget-market",
                        "SPY",
                        m.Market.US_EQUITY,
                        bar,
                        bundle,
                        snapshot,
                        held,
                        {},
                        Decimal("0"),
                        now,
                        now,
                        now,
                        now,
                        m.Quality.VALID,
                        trace,
                    )
                    for value in (bar, bundle, state):
                        tx.insert(value)
                with budget_engine.begin() as connection:
                    unchanged = {
                        table: connection.execute(
                            text(f"SELECT jsonb_agg(to_jsonb(t)) FROM public.{table} t")
                        ).scalar_one()
                        for table in (
                            "accounts",
                            "portfolio_snapshots",
                            "positions",
                            "risk_account_context",
                            "paper_initial_bases",
                        )
                    }
                    assert read_cap(connection, "paper") is None
                    result = connection.execute(
                        text(
                            "SELECT public.control_update_trading_profile("
                            "'paper','operator',:key,:trace,100,'mock-neutral')"
                        ),
                        {"key": "initial-budget-" + uuid.uuid4().hex, "trace": trace},
                    ).scalar_one()
                    if imported:
                        assert held.quantity * bar.close == Decimal("900")
                        assert result == {"error": "RISK_LIMIT_EXCEEDED"}
                        assert read_cap(connection, "paper") is None
                    else:
                        assert result == {
                            "budget": "100",
                            "currency": "USD",
                            "strategy": "mock-neutral",
                        }
                        cap = read_cap(connection, "paper")
                        assert cap == Decimal("100")
                        # Project the authoritative portfolio into the arithmetic
                        # gate; candidate amounts never alter its funded cash/equity.
                        facts = SimpleNamespace(
                            cash=snapshot.cash,
                            equity=snapshot.equity,
                            gross_notional=held.quantity * bar.close,
                            reserved_notional=Decimal("0"),
                            market_price=bar.close,
                            spread=Decimal("0"),
                        )
                        before_facts = deepcopy(vars(facts))
                        candidate = m.TradeProposal(
                            "budget-candidate",
                            "budget-decision",
                            state.id,
                            "SPY",
                            m.Target.LONG,
                            Decimal("1"),
                            now,
                            now + timedelta(seconds=30),
                            trace,
                            limit_price=Decimal("100"),
                        )
                        assert budget_reasons(candidate, facts, cap) == ("CASH_LIMIT",)
                        from dataclasses import replace

                        assert (
                            budget_reasons(
                                replace(candidate, limit_price=Decimal("99")), facts, cap
                            )
                            == ()
                        )
                        assert Decimal("100") * Decimal("1.01") == Decimal("101")
                        assert Decimal("99") * Decimal("1.01") == Decimal("99.99")
                        assert vars(facts) == before_facts
                    for table, value in unchanged.items():
                        assert (
                            connection.execute(
                                text(f"SELECT jsonb_agg(to_jsonb(t)) FROM public.{table} t")
                            ).scalar_one()
                            == value
                        )
                    assert connection.execute(
                        text("SELECT cash,equity FROM portfolio_snapshots")
                    ).one() == (Decimal("100000"), snapshot.equity)
                # Observe committed authority, rather than a transaction-local save.
                with budget_engine.connect() as connection:
                    assert read_cap(connection, "paper") == (None if imported else Decimal("100"))
                    assert connection.execute(
                        text("SELECT cash FROM portfolio_snapshots")
                    ).scalar_one() == Decimal("100000")
            finally:
                budget_engine.dispose()
            if imported:
                # Run the real reconciliation route under the fixed Paper login,
                # with no grants, binding publication, or synthetic local orders.
                with psycopg.connect(dbname=DATABASE, **common) as admin:
                    immutable_basis = admin.execute(
                        "SELECT to_jsonb(b) FROM public.paper_initial_bases b"
                    ).fetchone()[0]

                class ReconcileClock:
                    def now(self):
                        return datetime.now(UTC)

                class ReconcileIds:
                    def new_id(self, namespace, seed=None):
                        return str(uuid.uuid4())

                reconciliation_calls = []
                provider_basis = {"cost_basis": "770"}
                reconcile_key = "synthetic-reconcile-key-canary"
                reconcile_secret = "synthetic-reconcile-secret-canary"

                def reconcile_http(
                    request,
                    reconcile_key=reconcile_key,
                    reconcile_secret=reconcile_secret,
                    reconciliation_calls=reconciliation_calls,
                    provider_basis=provider_basis,
                ):
                    assert request.method == "GET"
                    assert request.url.host == "paper-api.alpaca.markets"
                    assert request.headers["APCA-API-KEY-ID"] == reconcile_key
                    assert request.headers["APCA-API-SECRET-KEY"] == reconcile_secret
                    reconciliation_calls.append(request.url.path)
                    if request.url.path == "/v2/clock":
                        value = {"timestamp": datetime.now(UTC).isoformat()}
                    elif request.url.path == "/v2/account":
                        value = {
                            "id": account,
                            "currency": "USD",
                            "cash": "100000",
                            "equity": "100900",
                            "portfolio_value": "100900",
                        }
                    elif request.url.path == "/v2/positions":
                        value = [
                            {
                                "asset_id": asset,
                                "symbol": "SPY",
                                "side": "long",
                                "qty": "60",
                                "avg_entry_price": "12.83",
                                "cost_basis": provider_basis["cost_basis"],
                                "market_value": "900",
                            }
                        ]
                    else:
                        assert request.url.path in {"/v2/orders", "/v2/account/activities"}
                        value = []
                    return httpx.Response(200, json=value)

                def unused_market_http(request):
                    raise AssertionError("reconciliation requested market publication or quotes")

                clock, ids = ReconcileClock(), ReconcileIds()
                ledger = PostgresLedger(engine=execution.engine)
                broker = AlpacaPaperBrokerAdapter(
                    clock,
                    ids,
                    api_key=reconcile_key,
                    api_secret=reconcile_secret,
                    paper_enabled=True,
                    expected_account_id=account,
                    reconciliation_fee_profile=None,
                    transport=httpx.MockTransport(reconcile_http),
                )
                market = AlpacaMarketDataProvider(
                    clock,
                    ids,
                    api_key=reconcile_key,
                    api_secret=reconcile_secret,
                    enabled=True,
                    transport=httpx.MockTransport(unused_market_http),
                )
                try:
                    binding = AlpacaExecutionBinding(ledger, "paper", broker, market)
                    runtime_owner = str(uuid.uuid4())
                    token = claim_execution_owner(
                        ledger=ledger,
                        account_id="paper",
                        owner_id=runtime_owner,
                        trace_id=trace,
                    )
                    assert not binding.restored and reconciliation_calls == []
                    assert (
                        restore_alpaca_attempts(
                            ledger=ledger,
                            account_id="paper",
                            broker=broker,
                            binding=binding,
                            owner_id=runtime_owner,
                            fencing_token=token,
                        )
                        == 0
                    )
                    assert binding.restored and reconciliation_calls == []
                    receipt = reconcile_paper_account(
                        ledger=ledger,
                        account_id="paper",
                        broker=broker,
                        binding=binding,
                        owner_id=runtime_owner,
                        fencing_token=token,
                    )
                    assert not receipt.ready
                    assert receipt.reason_code == "PAPER_FEE_AUTHORITY_UNKNOWN"
                    provider_basis["cost_basis"] = "771"
                    mismatch = reconcile_paper_account(
                        ledger=ledger,
                        account_id="paper",
                        broker=broker,
                        binding=binding,
                        owner_id=runtime_owner,
                        fencing_token=token,
                    )
                    assert not mismatch.ready
                    assert mismatch.reason_code == "POSITION_COST_BASIS_MISMATCH"
                    assert receipt.receipt_id != mismatch.receipt_id
                finally:
                    broker.close()
                    market.close()
                assert reconciliation_calls.count("/v2/clock") == 2
                with psycopg.connect(dbname=DATABASE, **common) as admin:
                    assert admin.execute(
                        "SELECT id,ready,reason_code FROM public.paper_reconciliation_receipts "
                        "ORDER BY observed_at,id"
                    ).fetchall() == [
                        (receipt.receipt_id, False, "PAPER_FEE_AUTHORITY_UNKNOWN"),
                        (mismatch.receipt_id, False, "POSITION_COST_BASIS_MISMATCH"),
                    ]
                    assert (
                        admin.execute(
                            "SELECT to_jsonb(b) FROM public.paper_initial_bases b"
                        ).fetchone()[0]
                        == immutable_basis
                    )
                    assert admin.execute(
                        "SELECT mode,safety_state,reconciliation_current "
                        "FROM public.risk_account_context"
                    ).fetchone() == ("DISABLED", "PAUSE_NEW", False)
                    for table in ("order_intents", "broker_orders", "execution_reports"):
                        assert admin.execute(
                            sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
                        ).fetchone() == (0,)
                    assert admin.execute(
                        "SELECT p.instrument_id,b.quantity,b.cost_basis "
                        "FROM public.paper_position_cost_bases b "
                        "JOIN public.positions p ON p.id=b.position_id ORDER BY p.instrument_id"
                    ).fetchall() == [
                        ("QQQ", Decimal("0"), Decimal("0")),
                        ("SPY", Decimal("60"), Decimal("770")),
                    ]
                public = json.dumps(api.read_status(trace))
                assert account not in public and reconcile_key not in public
                assert reconcile_secret not in public
                # Fixture owner creates a constrained version/audit transaction;
                # the protected append itself runs as the real fixed Paper login.
                current = Position(
                    "paper-position-SPY", "SPY", "USD", Decimal(60), Decimal("12.83"), Decimal(0), 1
                )
                filled, residual = costed_fill(
                    current, Decimal("770"), "SELL", Decimal(1), Decimal("20")
                )
                with psycopg.connect(dbname=DATABASE, **common) as admin:
                    for identity, version in ((current.id, 2), (current.id + ":cost:2", 1)):
                        admin.execute(
                            "INSERT INTO audit_events(id,occurred_at,actor_id,action,"
                            "reason_codes,entity_id,entity_version,trace_id) "
                            "VALUES(%s,clock_timestamp(),'execution-reconciliation','fixture',"
                            "'[\"SYNTHETIC_FIXTURE\"]',%s,%s,%s)",
                            (str(uuid.uuid4()), identity, version, trace),
                        )
                    admin.execute(
                        "UPDATE positions SET quantity=59,ledger_version=2,"
                        "realized_pnl=%s WHERE id=%s",
                        (filled.realized_pnl, current.id),
                    )
                    admin.execute("SET SESSION AUTHORIZATION atl_paper_execution_login")
                    admin.execute("SET ROLE atl_paper_execution")
                    assert admin.execute(
                        "SELECT public.paper_cost_basis_append(%s,2,59,%s,%s,%s)",
                        (current.id, residual, "d" * 64, trace),
                    ).fetchone() == (True,)
                    admin.execute("SET SESSION AUTHORIZATION DEFAULT")
                    admin.execute("RESET ROLE")
                    assert admin.execute(
                        "SELECT cost_basis FROM paper_position_cost_bases "
                        "WHERE position_id=%s AND ledger_version=2",
                        (current.id,),
                    ).fetchone() == (residual,)
                    admin.rollback()  # Leave the original immutable initialization fixture intact.
            # Fresh command cannot rebase an existing ledger, even with another valid proof.
            api.enqueue(
                "PREPARE_LEDGER",
                {
                    "credentialGeneration": 1,
                    "accountGeneration": 1,
                    "verificationOperationId": verify["operationId"],
                },
                "operator",
                "preparation-test-0002",
                trace,
            )
            second = execution.claim(owner, lease_seconds=120)
            try:
                complete_preparation(execution, second, owner, evidence)
            except PaperCommandError as error:
                assert error.code == "LEDGER_ALREADY_BOUND"
            else:
                raise AssertionError("rebase accepted")
            for engine in engines:
                engine.dispose()
            with psycopg.connect(dbname="lab", autocommit=True, **common) as admin:
                admin.execute("DROP DATABASE lab_paper_runtime WITH (FORCE)")
    print(MARKER)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import re
            import traceback

            location = traceback.extract_tb(error.__traceback__)[-1]
            code = getattr(error, "code", "")
            code = ":" + code if re.fullmatch(r"[A-Z_]{1,60}", code) else ""
            print(
                f"APW02_FAIL:INITIAL_BASIS:{type(error).__name__}{code}:guest.py:{location.lineno}"
            )
            raise SystemExit(1) from None
    else:
        test_paper_initialization_postgres()
        print(MARKER)
