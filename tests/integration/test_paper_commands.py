"""Historical migration15 command model, including pre-isolation Fake obligations.

Current fixed-identity/head behavior is exercised by test_paper_lifecycle,
test_paper_submit_lifecycle and the real fixed-role HTTP chain. This UUID-DB
fixture cannot represent the strict fixed database/login boundary introduced
in16 and must not be used as its acceptance evidence.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_durable_paper_commands_postgres():
    run_isolated_guest(Path(__file__), "PASS durable paper command invariants")


def run_isolated_guest(guest_path, expected_marker, *, scenario="guest"):
    if scenario not in {"guest", "obligations"}:
        raise ValueError("unsupported isolated scenario")
    guest_path = guest_path.resolve()
    if not guest_path.is_relative_to(ROOT / "tests") or guest_path.suffix != ".py":
        raise ValueError("isolated guest must be a Python test source")
    import json
    import secrets
    import subprocess
    import uuid

    from tests.integration.ledger_support import GROUPS, _admin

    identity = "atl_cmd_" + uuid.uuid4().hex[:20]
    password = secrets.token_urlsafe(32)
    groups = (*GROUPS, "atl_paper_control_api", "atl_paper_execution")
    # Reuse the suite's fixed NOLOGIN groups; a fresh isolated runner may not have them yet.
    for group in groups:
        _admin(
            f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{group}') "
            f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS; END IF; END $$"
        )
    # Fixture owns only its UUID login/database; existing group identities remain unchanged.
    _admin(f"CREATE ROLE {identity} LOGIN PASSWORD '{password}' NOINHERIT")
    created = False
    try:
        _admin(";".join(f"GRANT {group} TO {identity}" for group in groups))
        _admin(f"CREATE DATABASE {identity} OWNER {identity}")
        created = True
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--network",
            "agentic-trading-lab-dev_local",
            "--workdir",
            "/opt/lab",
            "--entrypoint",
            "python",
        ]
        migration = ROOT / "services/control_api/migrations/versions/0013_paper_commands.py"
        if not migration.is_file():
            migration = ROOT / ".tooling/output/0013_paper_commands.py"
        for source, target in [
            (ROOT / "packages", "/opt/lab/packages"),
            (ROOT / "services", "/opt/lab/services"),
            (ROOT / "alembic.ini", "/opt/lab/alembic.ini"),
            (guest_path, "/opt/lab/test_guest.py"),
            (migration, "/opt/lab/command_migration.py"),
        ]:
            command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        if scenario == "obligations":
            for source, target in [
                (ROOT / "tests/integration/risk_support.py", "/opt/lab/risk_support.py"),
                (ROOT / "config/agents/jev.yaml", "/opt/lab/config/agents/jev.yaml"),
            ]:
                command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        command += ["agentic-trading-lab-dev-build-smoke:latest", "test_guest.py", scenario]
        result = subprocess.run(
            command,
            input=json.dumps({"database": identity, "role": identity, "password": password}),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            detail = [
                line
                for line in result.stderr.replace(password, "[redacted]").splitlines()
                if "psycopg.errors" in line
                or "syntax error" in line
                or "LINE " in line
                or "AssertionError" in line
                or 'File "/opt/lab/test_guest.py"' in line
                or "Error:" in line
            ][-6:]
            raise AssertionError("isolated command test failed: " + " | ".join(detail))
        assert result.stdout.strip().endswith(expected_marker)
    finally:
        if created:
            _admin(f"DROP DATABASE {identity} WITH (FORCE)")
        _admin(f"DROP ROLE {identity}")


def guest():
    import importlib.util
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from services.control_api.paper_commands import PaperCommandError, PaperCommands

    config = json.loads(sys.stdin.read())
    url = URL.create(
        "postgresql+psycopg",
        username=config["role"],
        password=config["password"],
        host="postgres",
        database=config["database"],
    )
    engine = create_engine(url, hide_parameters=True)
    alembic = Config("alembic.ini")
    with engine.begin() as connection:
        alembic.attributes["connection"] = connection
        command.upgrade(alembic, "0015_paper_limit_policy")
        if not connection.execute(text("SELECT to_regclass('public.paper_commands')")).scalar_one():
            spec = importlib.util.spec_from_file_location(
                "command_migration", "/opt/lab/command_migration.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.op = SimpleNamespace(execute=lambda sql: connection.execute(text(sql)))
            module.upgrade()
        assert connection.execute(text("SELECT count(*) FROM public.accounts")).scalar_one() == 0
        connection.execute(text("REVOKE USAGE ON SCHEMA public FROM PUBLIC"))

    def port(role):
        role_engine = create_engine(url, hide_parameters=True)

        @event.listens_for(role_engine, "connect")
        def assign(connection, _):
            connection.execute("SET ROLE " + role)
            connection.commit()

        return PaperCommands(role_engine)

    api = port("atl_paper_control_api")
    execution = port("atl_paper_execution")
    trace = "a" * 32
    assert api.read_status(trace)["credentialGeneration"] == 0
    generation = api.rotation_begin("local-operator", "rotation-key-0001", trace)
    assert generation == api.rotation_begin("local-operator", "rotation-key-0001", trace) == 1
    assert api.rotation_complete(generation)
    assert not api.rotation_complete(generation + 1)

    def enqueue(_):
        return api.enqueue(
            "VERIFY_CONNECTION",
            {"credentialGeneration": generation},
            "local-operator",
            "verify-key-00001",
            trace,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(enqueue, range(12)))
    assert len({receipt["operationId"] for receipt in receipts}) == 1
    receipt = receipts[0]
    assert receipt["status"] == "ACCEPTED"
    try:
        api.enqueue(
            "VERIFY_CONNECTION",
            {"credentialGeneration": generation + 1},
            "local-operator",
            "verify-key-00001",
            trace,
        )
        raise AssertionError("idempotency conflict accepted")
    except PaperCommandError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
    owners = [str(uuid4()), str(uuid4())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        competing_claims = list(pool.map(execution.claim, owners))
    claimed = [value for value in competing_claims if value is not None]
    assert len(claimed) == 1
    claim = claimed[0]
    owner = claim["owner"]
    assert owner in owners
    assert claim["traceId"] == trace and claim["actorId"] == "local-operator"
    assert execution.claim(str(uuid4())) is None
    with engine.connect() as c:
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM public.paper_command_events "
                    "WHERE event_type='ATTEMPT_STARTED'"
                )
            ).scalar_one()
            == 1
        )
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    public = dict(
        credentialGeneration=generation,
        observedAt=now,
        accountLabel="paper-12345678",
        currency="USD",
        cash="100000",
        verifiedAt=now,
        marketDataFeed="IEX",
        providerVerification="CONFIRMED",
        blockers=[],
    )
    private = str(uuid4())
    assert not execution.finish(
        claim["operationId"],
        str(uuid4()),
        claim["fence"],
        public=public,
        private_account_id=private,
    )
    for params in [
        {"owner": None, "fence": claim["fence"]},
        {"owner": owner, "fence": None},
    ]:
        with execution.engine.begin() as c:
            result = c.execute(
                text(
                    "SELECT public.paper_finish(CAST(:id AS uuid),"
                    "CAST(:owner AS uuid),:fence,'{}'::jsonb,NULL,NULL)"
                ),
                {"id": claim["operationId"], **params},
            ).scalar_one()
            assert result is False
    try:
        with execution.engine.begin() as c:
            c.execute(
                text("SELECT public.paper_claim(CAST(:owner AS uuid),NULL)"), {"owner": owner}
            )
        raise AssertionError("null lease duration accepted")
    except DBAPIError:
        pass
    with engine.begin() as c:
        c.execute(
            text("UPDATE public.paper_commands SET lease_until=NULL WHERE id=CAST(:id AS uuid)"),
            {"id": claim["operationId"]},
        )
    assert not execution.finish(
        claim["operationId"], owner, claim["fence"], public=public, private_account_id=private
    )
    previous = claim
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE public.paper_commands "
                "SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=CAST(:id AS uuid)"
            ),
            {"id": claim["operationId"]},
        )
    claim = execution.claim(owner)
    assert claim["fence"] == previous["fence"] + 1
    assert not execution.finish(
        previous["operationId"], owner, previous["fence"], public=public, private_account_id=private
    )
    assert execution.finish(
        claim["operationId"], owner, claim["fence"], public=public, private_account_id=private
    )
    state = api.read_status(trace)
    assert state["setupState"] == "VERIFIED" and state["entryGateOpen"] is False
    assert private not in json.dumps(state) and private not in json.dumps(
        api.read_operation(receipt["operationId"])
    )
    assert api.read_operation(receipt["operationId"])["status"] == "SUCCEEDED"

    refused = api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": 1},
        "local-operator",
        "verify-refused-key",
        trace,
    )
    refused_claim = execution.claim(owner)
    assert execution.finish(
        refused["operationId"], owner, refused_claim["fence"], error_code="CREDENTIALS_INVALID"
    )
    refused_state = api.read_status(trace)
    assert refused_state["setupState"] == "SAVED"
    assert refused_state["verifiedAt"] is None and "cash" not in refused_state
    assert refused_state["providerVerification"] == "UNVERIFIED"

    unhandled = api.enqueue(
        "PREPARE_LEDGER",
        {
            "credentialGeneration": 1,
            "accountGeneration": 1,
            "verificationOperationId": receipt["operationId"],
        },
        "local-operator",
        "prepare-key-0001",
        trace,
    )
    unhandled_claim = execution.claim(owner)
    assert execution.finish(unhandled["operationId"], owner, unhandled_claim["fence"])
    assert api.read_operation(unhandled["operationId"])["status"] == "BLOCKED"
    assert api.read_operation(unhandled["operationId"])["errorCode"] == "LEDGER_NOT_PREPARED"

    generation2 = api.rotation_begin("local-operator", "rotation-key-0002", trace)
    assert generation2 == 2 and api.read_status(trace)["verifiedAt"] is None
    gap = api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": 1},
        "local-operator",
        "write-gap-key-001",
        trace,
    )
    assert execution.claim(owner) is None
    assert api.read_operation(gap["operationId"])["errorCode"] == "VERSION_CONFLICT"
    assert api.rotation_complete(generation2)
    # Rotation write gap cannot re-verify the older saved generation and erase pending keys.
    # A third generation is tested below while an actual lease is running.
    invalids = [
        ("missing", None),
        ("cash", None),
        ("cash", True),
        ("cash", "1e3"),
        ("cash", "0.1234567890123456789"),
        ("cash", "-1"),
        ("verifiedAt", "2026-10-21T01:00:00+00:00"),
        ("observedAt", "not-a-timestamp"),
    ]
    for index, (field, value) in enumerate(invalids):
        bad = dict(public, credentialGeneration=generation2)
        if field == "missing":
            del bad["cash"]
        else:
            bad[field] = value
        invalid = api.enqueue(
            "VERIFY_CONNECTION",
            {"credentialGeneration": generation2},
            "local-operator",
            f"invalid-proof-key-{index}",
            trace,
        )
        invalid_claim = execution.claim(owner)
        assert execution.finish(
            invalid["operationId"],
            owner,
            invalid_claim["fence"],
            public=bad,
            private_account_id=private,
        )
        assert api.read_operation(invalid["operationId"])["errorCode"] == "CONNECTION_NOT_VERIFIED"
        with engine.connect() as c:
            assert tuple(
                c.execute(
                    text(
                        "SELECT active_generation,pending_generation "
                        "FROM public.paper_workflow_state"
                    )
                ).one()
            ) == (1, 2)
    second = api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": generation2},
        "local-operator",
        "verify-key-00002",
        trace,
    )
    claim = execution.claim(owner)
    public["credentialGeneration"] = generation2
    assert execution.finish(
        second["operationId"], owner, claim["fence"], public=public, private_account_id=str(uuid4())
    )
    assert api.read_operation(second["operationId"])["errorCode"] == "ACCOUNT_IDENTITY_MISMATCH"
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT active_generation FROM public.paper_workflow_state")
            ).scalar_one()
            == 1
        )

    stale = api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": generation2},
        "local-operator",
        "verify-stale-key-1",
        trace,
    )
    stale_claim = execution.claim(owner)
    generation3 = api.rotation_begin("local-operator", "rotation-key-0003", trace)
    assert generation3 == 3
    assert execution.finish(
        stale["operationId"], owner, stale_claim["fence"], public=public, private_account_id=private
    )
    assert api.read_operation(stale["operationId"])["errorCode"] == "VERSION_CONFLICT"
    assert api.rotation_complete(generation3)
    public["credentialGeneration"] = generation3

    third = api.enqueue(
        "VERIFY_CONNECTION",
        {"credentialGeneration": generation3},
        "local-operator",
        "verify-key-00003",
        trace,
    )
    claim = execution.claim(owner)
    kill = api.enqueue("KILL", {"action": "KILL"}, "local-operator", "kill-key-0000001", trace)
    assert kill["status"] == "ACCEPTED"
    assert api.read_status(trace)["operatingState"] == "HALTED"
    assert execution.finish(
        third["operationId"], owner, claim["fence"], public=public, private_account_id=private
    )
    assert api.read_operation(third["operationId"])["errorCode"] == "KILL_SWITCH_ACTIVE"
    kill_claim = execution.claim(owner)
    assert execution.finish(kill["operationId"], owner, kill_claim["fence"])
    assert api.read_operation(kill["operationId"])["status"] == "SUCCEEDED"

    # API can read only projections; direct state/private proof/lease/event writes are refused.
    for sql in [
        "SELECT pinned_account_id FROM public.paper_workflow_state",
        "UPDATE public.paper_workflow_state SET epoch=1",
        "SELECT * FROM public.paper_commands",
        "SELECT public.paper_claim(gen_random_uuid(),30)",
    ]:
        try:
            with api.engine.begin() as c:
                c.execute(text(sql))
            raise AssertionError("API obtained private authority")
        except DBAPIError:
            pass
    try:
        with engine.begin() as c:
            c.execute(text("UPDATE public.paper_commands SET payload='{}'::jsonb"))
        raise AssertionError("immutable payload replaced")
    except DBAPIError:
        pass
    try:
        with engine.begin() as c:
            c.execute(text("DELETE FROM public.paper_command_events"))
        raise AssertionError("immutable audit deleted")
    except DBAPIError:
        pass
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE public.paper_workflow_state SET credential_counter=9007199254740991,"
                "epoch=9007199254740991"
            )
        )
    for action in [
        lambda: api.rotation_begin("local-operator", "max-rotation-key1", trace),
        lambda: api.enqueue(
            "PAUSE", {"action": "PAUSE"}, "local-operator", "max-pause-key-001", trace
        ),
    ]:
        try:
            action()
            raise AssertionError("unbounded public generation accepted")
        except PaperCommandError as exc:
            assert exc.code == "VERSION_CONFLICT"
    with engine.connect() as c:
        assert tuple(
            c.execute(
                text("SELECT credential_counter,epoch FROM public.paper_workflow_state")
            ).one()
        ) == (9007199254740991, 9007199254740991)
    print("PASS durable paper command invariants")


def test_paper_rotation_obligations_postgres():
    run_isolated_guest(Path(__file__), "PASS paper rotation obligations", scenario="obligations")


def obligations_guest():
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    import risk_support
    from sqlalchemy import create_engine, event, text

    from packages.domain import models as m
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.control_api.paper_commands import PaperCommands

    def check(ledger, execution_ledger, engine, now):
        trace = "a" * 32
        pin = "12345678-1234-4234-8234-123456789012"
        owner = str(uuid4())

        def port(role):
            role_engine = create_engine(engine.url, hide_parameters=True)

            @event.listens_for(role_engine, "connect")
            def assign(connection, _):
                connection.execute("SET ROLE " + role)
                connection.commit()

            return PaperCommands(role_engine)

        api = port("atl_paper_control_api")
        execution = port("atl_paper_execution")

        def verify(generation, key, private=pin):
            receipt = api.enqueue(
                "VERIFY_CONNECTION",
                {"credentialGeneration": generation},
                "local-operator",
                key,
                trace,
            )
            claimed = execution.claim(owner)
            assert claimed is not None and claimed["operationId"] == receipt["operationId"]
            stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            proof = dict(
                credentialGeneration=generation,
                observedAt=stamp,
                accountLabel="paper-12345678",
                currency="USD",
                cash="2000",
                verifiedAt=stamp,
                marketDataFeed="IEX",
                providerVerification="CONFIRMED",
                blockers=[],
            )
            assert execution.finish(
                receipt["operationId"],
                owner,
                claimed["fence"],
                public=proof,
                private_account_id=private,
            )
            return api.read_operation(receipt["operationId"])

        def rotate(generation):
            assert (
                api.rotation_begin("local-operator", f"obligation-rotation-{generation}", trace)
                == generation
            )
            assert api.rotation_complete(generation)

        def identity():
            with engine.connect() as c:
                return tuple(
                    c.execute(
                        text(
                            "SELECT active_generation,pending_generation,"
                            "pinned_account_id::text FROM public.paper_workflow_state"
                        )
                    ).one()
                )

        rotate(1)
        assert verify(1, "obligation-verify-1")["status"] == "SUCCEEDED"
        assert identity() == (1, None, pin)

        # Reuse the existing fully linked financial fixture and the real deterministic risk path.
        # No broker method or network submit is invoked to seed these local ledger obligations.
        decision = reserve(
            ledger=execution_ledger, account_id="paper", proposal_id="p-0", limits=RiskLimits()
        )
        assert decision.approved and decision.reservation_id
        with engine.connect() as c:
            intent = (
                c.execute(
                    text(
                        "SELECT id,client_order_id,quantity FROM public.order_intents "
                        "WHERE risk_decision_id=:decision"
                    ),
                    {"decision": decision.id},
                )
                .mappings()
                .one()
            )
        order_id = "rotation-fixture-order"
        with ledger.transaction() as tx:
            tx.insert(
                m.AuditEvent(
                    str(uuid4()), now, "fixture", "fixture.order", ("FIXTURE",), order_id, 1, trace
                )
            )
            tx.insert(
                m.BrokerOrder(
                    order_id,
                    intent["id"],
                    intent["client_order_id"],
                    None,
                    m.OrderStatus.ACKNOWLEDGED,
                    Decimal(intent["quantity"]),
                    Decimal("0"),
                    1,
                    now,
                    trace,
                ),
                adapter="fake",
            )

        # A known open order on the same pinned account permits checked key rotation.
        rotate(2)
        assert verify(2, "obligation-verify-2")["status"] == "SUCCEEDED"
        assert identity() == (2, None, pin)
        rotate(3)

        def mutate_fixture(order_status, reservation_state):
            with ledger.transaction() as tx:
                versions = [
                    (
                        order_id,
                        tx.connection.execute(
                            text(
                                "SELECT version+1 FROM public.broker_orders WHERE id=:id FOR UPDATE"
                            ),
                            {"id": order_id},
                        ).scalar_one(),
                    ),
                    (
                        decision.reservation_id,
                        tx.connection.execute(
                            text(
                                "SELECT version+1 FROM public.risk_reservations "
                                "WHERE id=:id FOR UPDATE"
                            ),
                            {"id": decision.reservation_id},
                        ).scalar_one(),
                    ),
                ]
                for entity, next_version in versions:
                    tx.insert(
                        m.AuditEvent(
                            str(uuid4()),
                            datetime.now(UTC),
                            "fixture",
                            "fixture.obligation",
                            ("FIXTURE",),
                            entity,
                            next_version,
                            trace,
                        )
                    )
                tx.connection.execute(
                    text(
                        "UPDATE public.broker_orders SET status=:status,"
                        "version=version+1 WHERE id=:id"
                    ),
                    {"status": order_status, "id": order_id},
                )
                tx.connection.execute(
                    text(
                        "UPDATE public.risk_reservations SET state=:state,"
                        "version=version+1 WHERE id=:id"
                    ),
                    {"state": reservation_state, "id": decision.reservation_id},
                )

        for index, (order_status, reservation_state) in enumerate(
            [("UNKNOWN", "ACTIVE"), ("SUBMITTING", "ACTIVE"), ("ACKNOWLEDGED", "HELD_UNKNOWN")]
        ):
            mutate_fixture(order_status, reservation_state)
            result = verify(3, f"obligation-block-{index}")
            assert result["status"] == "BLOCKED" and result["errorCode"] == "BROKER_STATUS_UNKNOWN"
            assert identity() == (2, 3, pin)
            with engine.connect() as c:
                held = c.execute(
                    text(
                        "SELECT state,reserved_notional,released_at FROM "
                        "public.risk_reservations WHERE id=:id"
                    ),
                    {"id": decision.reservation_id},
                ).one()
                assert held.state == reservation_state and held.reserved_notional > 0
                assert held.released_at is None

        mutate_fixture("ACKNOWLEDGED", "ACTIVE")
        assert verify(3, "obligation-recovered")["status"] == "SUCCEEDED"
        assert identity() == (3, None, pin)
        # A different identity is forbidden even with a known, otherwise recoverable open order.
        rotate(4)
        wrong = verify(4, "obligation-wrong-id", private="12345678-1234-4234-8234-123456789013")
        assert wrong["errorCode"] == "ACCOUNT_IDENTITY_MISMATCH"
        assert identity() == (3, 4, pin)
        api.engine.dispose()
        execution.engine.dispose()
        print("PASS paper rotation obligations")

    # Preserve every legacy Fake-obligation assertion against its historical
    # command schema. The current Paper deployment never adopts Fake finances.
    from unittest.mock import patch

    from alembic import command

    upgrade = command.upgrade

    def migration15(configuration, target):
        assert target == "head"
        return upgrade(configuration, "0015_paper_limit_policy")

    with patch.object(command, "upgrade", migration15):
        risk_support.guest(run_checks=check)


if __name__ == "__main__":
    import sys

    if sys.argv[-1] == "obligations":
        obligations_guest()
    else:
        guest()
