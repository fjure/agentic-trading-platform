"""Offline operator-boundary tests: no credentials, sockets or provider clients."""

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.domain import models as m
from services.execution import paper_smoke as smoke

RUN_ID = "00000000-0000-4000-8000-000000000022"
ACCOUNT = "00000000-0000-4000-8000-000000000099"


def arguments():
    return [
        "--paper-only",
        "--operator-confirmed",
        "--run-id",
        RUN_ID,
        "--account-fingerprint",
        smoke.hashlib.sha256(ACCOUNT.encode()).hexdigest(),
        "--instrument",
        "SPY",
        "--limit-price",
        "100.00",
        "--max-notional",
        "101",
    ]


def options():
    return smoke.parse_options(arguments())


@pytest.mark.parametrize("flag", ["--paper-only", "--operator-confirmed"])
def test_missing_optin_stops_before_configuration_or_any_runtime(monkeypatch, capsys, flag):
    args = arguments()
    args.remove(flag)
    execute = Mock(side_effect=AssertionError("must never construct runtime"))
    terminal = Mock(side_effect=AssertionError("must not even ask for a terminal"))
    monkeypatch.setattr(smoke, "execute", execute)
    monkeypatch.setattr(smoke, "require_operator_terminal", terminal)
    assert smoke.main(args) == 1
    execute.assert_not_called()
    terminal.assert_not_called()
    assert capsys.readouterr().err == "PAPER_SMOKE_DENIED_OR_UNRESOLVED\n"


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--run-id", "not-a-uuid"),
        ("--account-fingerprint", "synthetic-private-canary"),
        ("--instrument", "BTC/USD"),
        ("--limit-price", "NaN"),
        ("--limit-price", "Infinity"),
        ("--limit-price", "-1"),
        ("--limit-price", "100.001"),
        ("--max-notional", "1000.01"),
        ("--max-notional", "100"),
        ("--max-notional", "0"),
    ],
)
def test_invalid_public_bounds_fail_without_runtime_or_payload_echo(
    monkeypatch,
    capsys,
    flag,
    value,
):
    args = arguments()
    args[args.index(flag) + 1] = value
    execute = Mock()
    monkeypatch.setattr(smoke, "execute", execute)
    assert smoke.main(args) == 1
    execute.assert_not_called()
    assert value not in capsys.readouterr().err


def test_noninteractive_terminal_stops_before_confirmation_or_runtime(monkeypatch):
    monkeypatch.setattr(smoke.sys, "platform", "linux")
    monkeypatch.setattr(smoke.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    execute = Mock()
    monkeypatch.setattr(smoke, "execute", execute)
    assert smoke.main(arguments()) == 1
    execute.assert_not_called()


def test_windows_terminal_is_always_denied(monkeypatch):
    monkeypatch.setattr(smoke.sys, "platform", "win32")
    with pytest.raises(smoke.SmokeDenied):
        smoke.require_operator_terminal()


def test_flags_and_tty_still_require_exact_human_challenge(monkeypatch):
    monkeypatch.setattr(smoke, "require_operator_terminal", Mock())
    monkeypatch.setattr(smoke, "operator_input", lambda prompt: "yes")
    execute = Mock()
    monkeypatch.setattr(smoke, "execute", execute)
    assert smoke.main(arguments()) == 1
    execute.assert_not_called()


def test_guarded_entry_catches_private_exception_without_output_payload(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "require_operator_terminal", Mock())
    monkeypatch.setattr(smoke, "operator_input", lambda prompt: "PAPER " + RUN_ID)
    monkeypatch.setattr(smoke, "execute", Mock(side_effect=ValueError("private-canary")))
    assert smoke.main(arguments()) == 1
    captured = capsys.readouterr()
    assert captured.err == "PAPER_SMOKE_DENIED_OR_UNRESOLVED\n"
    assert not captured.out


def test_account_fingerprint_checked_without_provider_contact():
    broker = SimpleNamespace(_account_id=ACCOUNT, submit=Mock(), capabilities=Mock())
    runtime = SimpleNamespace(process=SimpleNamespace(broker=broker))
    smoke._fingerprint(runtime, options())
    with pytest.raises(smoke.SmokeDenied):
        smoke._fingerprint(runtime, smoke.replace(options(), account_fingerprint="0" * 64))
    broker.submit.assert_not_called()
    broker.capabilities.assert_not_called()


def run_fixture(monkeypatch, submit_error=None):
    events = []
    process = SimpleNamespace(
        heartbeat=lambda: events.append("heartbeat"),
        reconcile=lambda: events.append("reconcile") or SimpleNamespace(ready=True),
        submit=Mock(),
    )
    process.submit.side_effect = submit_error
    runtime = SimpleNamespace(
        process=process,
        publish_market=lambda: events.append("market") or True,
        pause_if_running=lambda actor: events.append("pause"),
    )
    request = options().request("actual-state")
    intent = SimpleNamespace(id="one-intent")
    activation = Mock(side_effect=lambda *args: events.append("activate"))
    monkeypatch.setattr(smoke, "_activation", activation)
    monkeypatch.setattr(smoke, "authorize", Mock(return_value=request))
    monkeypatch.setattr(smoke, "validated_intent", Mock(return_value=intent))
    settlement = Mock(side_effect=lambda *args: events.append("settle") or "CANCELED")
    monkeypatch.setattr(smoke, "settle", settlement)
    answers = iter(["activation-request", request.proposal_id])

    def ask(*args, **kwargs):
        return next(answers)

    return runtime, events, request, activation, settlement, ask


def test_single_target_submit_then_pause_and_settlement_without_daemon(monkeypatch):
    runtime, events, request, activation, settle, ask = run_fixture(monkeypatch)
    started = datetime.now(UTC)
    smoke.run(runtime, options(), started, ask=ask, output=Mock())
    runtime.process.submit.assert_called_once_with("one-intent")
    activation.assert_called_once_with(runtime, "activation-request", started)
    assert events.index("activate") > events.index("reconcile")
    assert events.count("market") == 2
    assert events[-2:] == ["pause", "settle"]
    settle.assert_called_once()
    assert request.limit_price == Decimal("100")


@pytest.mark.parametrize("error", [RuntimeError("private-canary"), KeyboardInterrupt()])
def test_submit_failure_or_interrupt_pauses_and_reconciles_never_retries(monkeypatch, error):
    runtime, events, _, _, settle, ask = run_fixture(monkeypatch, error)
    with pytest.raises(type(error)):
        smoke.run(runtime, options(), datetime.now(UTC), ask=ask, output=Mock())
    runtime.process.submit.assert_called_once()
    settle.assert_called_once()
    assert events[-2:] == ["pause", "settle"]


def test_wrong_worker_proposal_cannot_reserve_or_submit(monkeypatch):
    runtime, events, _, _, settle, _ = run_fixture(monkeypatch)
    answers = iter(["activation-request", "foreign-proposal"])
    with pytest.raises(smoke.SmokeDenied):
        smoke.run(
            runtime,
            options(),
            datetime.now(UTC),
            ask=lambda *args, **kwargs: next(answers),
            output=Mock(),
        )
    smoke.validated_intent.assert_not_called()
    runtime.process.submit.assert_not_called()
    settle.assert_not_called()
    assert events[-1] == "pause"


def test_operator_cancellation_before_activation_pauses_without_submit(monkeypatch):
    runtime, events, _, _, settle, _ = run_fixture(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        smoke.run(
            runtime,
            options(),
            datetime.now(UTC),
            ask=Mock(side_effect=KeyboardInterrupt),
            output=Mock(),
        )
    runtime.process.submit.assert_not_called()
    settle.assert_not_called()
    assert events[-1] == "pause"


@contextmanager
def empty_transaction():
    yield SimpleNamespace()


def settle_fixture(monkeypatch, states, *, ready=True):
    orders = [SimpleNamespace(status=state) for state in states]
    last = orders[-1]
    sequence = iter(orders)
    monkeypatch.setattr(smoke, "_order", lambda *args: next(sequence, last))
    cancel = Mock()
    unknown = Mock(return_value=SimpleNamespace(status=m.OrderStatus.UNKNOWN))
    audit = Mock()
    monkeypatch.setattr(smoke, "cancel_once", cancel)
    monkeypatch.setattr(smoke, "_unknown", unknown)
    monkeypatch.setattr(smoke, "_audit", audit)
    runtime = SimpleNamespace(
        process=SimpleNamespace(
            heartbeat=Mock(),
            reconcile=Mock(
                return_value=SimpleNamespace(
                    ready=ready,
                )
            ),
            submit=Mock(),
        ),
        ledger=SimpleNamespace(transaction=empty_transaction),
    )
    return runtime, cancel, unknown, audit


@pytest.mark.parametrize("terminal", list(smoke._TERMINAL))
def test_terminal_status_needs_authoritative_reconciliation_but_no_cancel(monkeypatch, terminal):
    runtime, cancel, unknown, audit = settle_fixture(monkeypatch, [terminal, terminal])
    result = smoke.settle(runtime, options().request("actual-state"), SimpleNamespace(id="intent"))
    assert result == terminal.value
    cancel.assert_not_called()
    unknown.assert_not_called()
    runtime.process.submit.assert_not_called()
    assert audit.call_args.args[4] == terminal.value


def test_cancel_fill_race_preserves_fill_and_issues_at_most_one_cancel(monkeypatch):
    runtime, cancel, unknown, _ = settle_fixture(
        monkeypatch,
        [
            m.OrderStatus.ACKNOWLEDGED,
            m.OrderStatus.PARTIALLY_FILLED,
            m.OrderStatus.CANCEL_PENDING,
            m.OrderStatus.FILLED,
        ],
    )
    result = smoke.settle(
        runtime, options().request("actual-state"), SimpleNamespace(id="intent"), wait=Mock()
    )
    assert result == "FILLED"
    cancel.assert_called_once()
    unknown.assert_not_called()
    assert runtime.process.reconcile.call_count == 3
    runtime.process.submit.assert_not_called()


def test_unknown_submit_does_not_cancel_or_resubmit(monkeypatch):
    runtime, cancel, unknown, _ = settle_fixture(monkeypatch, [m.OrderStatus.UNKNOWN])
    assert (
        smoke.settle(
            runtime, options().request("actual-state"), SimpleNamespace(id="intent"), wait=Mock()
        )
        == "UNKNOWN"
    )
    cancel.assert_not_called()
    unknown.assert_called_once()
    runtime.process.submit.assert_not_called()


def test_unfinished_cancel_is_bounded_then_unknown_with_reservation_hold(monkeypatch):
    runtime, cancel, unknown, _ = settle_fixture(monkeypatch, [m.OrderStatus.ACKNOWLEDGED])
    wait = Mock()
    assert (
        smoke.settle(
            runtime,
            options().request("actual-state"),
            SimpleNamespace(id="intent"),
            wait=wait,
            polls=3,
        )
        == "UNKNOWN"
    )
    cancel.assert_called_once()
    unknown.assert_called_once()
    assert runtime.process.reconcile.call_count == 3
    assert wait.call_count == 2
    runtime.process.submit.assert_not_called()


def test_reconciliation_read_failure_becomes_incident_without_retry_or_cancel(monkeypatch):
    runtime, cancel, unknown, _ = settle_fixture(monkeypatch, [m.OrderStatus.ACKNOWLEDGED])
    runtime.process.reconcile.side_effect = RuntimeError("private-provider-canary")
    assert (
        smoke.settle(
            runtime, options().request("actual-state"), SimpleNamespace(id="intent"), wait=Mock()
        )
        == "UNKNOWN"
    )
    cancel.assert_not_called()
    assert runtime.process.reconcile.call_count == 1
    unknown.assert_called()
    runtime.process.submit.assert_not_called()


def test_terminal_report_without_fee_authority_is_not_success(monkeypatch):
    runtime, cancel, _, _ = settle_fixture(monkeypatch, [m.OrderStatus.FILLED], ready=False)
    assert (
        smoke.settle(
            runtime, options().request("actual-state"), SimpleNamespace(id="intent"), wait=Mock()
        )
        == "UNKNOWN"
    )
    cancel.assert_not_called()
    runtime.process.submit.assert_not_called()


def test_operator_wait_heartbeats_without_reading_credentials(monkeypatch):
    import io

    monkeypatch.setattr(smoke.sys, "stdin", io.StringIO("request-id\n"))
    ready = iter([([], [], []), ([smoke.sys.stdin], [], [])])
    monkeypatch.setattr(smoke.select, "select", lambda *args: next(ready))
    process = SimpleNamespace(heartbeat=Mock())
    assert smoke.operator_input("Synthetic prompt", process) == "request-id"
    assert process.heartbeat.call_count == 2


def test_operator_eof_does_not_proceed(monkeypatch):
    import io

    monkeypatch.setattr(smoke.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(smoke.select, "select", lambda *args: ([smoke.sys.stdin], [], []))
    with pytest.raises(smoke.SmokeDenied):
        smoke.operator_input("Synthetic prompt")


@pytest.mark.parametrize("malformed", [False, True])
def test_cancel_is_durable_before_io_and_is_not_sent_twice(monkeypatch, malformed):
    from packages.observability import outbox
    from services.execution import intents

    request = options().request("actual-state")
    now = datetime.now(UTC)
    order = m.BrokerOrder(
        "order",
        "intent",
        "client",
        ACCOUNT,
        m.OrderStatus.PARTIALLY_FILLED,
        Decimal(1),
        Decimal("0.5"),
        1,
        now,
        "a" * 32,
    )
    state = {"order": order}
    events = []

    def replace_order(value, **kwargs):
        state["order"] = value
        events.append("persist-pending")
        return True

    tx = SimpleNamespace(
        lock_account=Mock(),
        connection=SimpleNamespace(
            execute=lambda *args: SimpleNamespace(scalar_one=lambda: "order")
        ),
        get=lambda *args: state["order"],
        replace=replace_order,
        insert=lambda entity, **kwargs: events.append(type(entity).__name__),
    )

    @contextmanager
    def transaction():
        yield tx
        events.append("commit")

    def cancel(client_id):
        assert state["order"].status is m.OrderStatus.CANCEL_PENDING
        assert "commit" in events
        assert "AuditEvent" in events and "ExecutionReport" in events
        events.append("DELETE")
        return SimpleNamespace(
            status=(
                SimpleNamespace(value="private-canary")
                if malformed
                else m.OrderStatus.CANCEL_PENDING
            )
        )

    runtime = SimpleNamespace(
        ledger=SimpleNamespace(transaction=transaction),
        process=SimpleNamespace(
            owner_id="owner",
            fencing_token=1,
            broker=SimpleNamespace(cancel=Mock(side_effect=cancel)),
        ),
    )
    monkeypatch.setattr(intents, "_assert_owner", Mock())
    monkeypatch.setattr(smoke, "_now", lambda tx: now)
    audit = Mock()
    monkeypatch.setattr(smoke, "_audit", audit)
    monkeypatch.setattr(outbox, "persist", lambda *args: events.append("outbox"))
    intent = SimpleNamespace(id="intent", quantity=Decimal(1), client_order_id="client")
    smoke.cancel_once(runtime, request, intent)
    smoke.cancel_once(runtime, request, intent)
    runtime.process.broker.cancel.assert_called_once_with("client")
    assert events.index("persist-pending") < events.index("commit") < events.index("DELETE")
    expected = "UNKNOWN" if malformed else "CANCEL_PENDING"
    assert audit.call_args.args[3] == (expected,)
    assert audit.call_args_list[0].kwargs["trace_id"] == order.trace_id
    assert "private-canary" not in repr(audit.call_args)


def test_public_outbox_publisher_is_bounded_without_execution_work(monkeypatch):
    from packages.observability import outbox

    publish = Mock(return_value=1)
    monkeypatch.setattr(outbox, "Outbox", lambda engine: SimpleNamespace(publish_one=publish))
    tx = SimpleNamespace(
        connection=SimpleNamespace(
            execute=lambda *args: SimpleNamespace(
                scalar_one=lambda: True,
            )
        )
    )

    @contextmanager
    def transaction():
        yield tx

    ledger = SimpleNamespace(engine=object(), transaction=transaction)
    with pytest.raises(smoke.SmokeDenied, match="PAPER_SMOKE_OUTBOX_UNRESOLVED"):
        smoke.publish_outbox(ledger, limit=3)
    assert publish.call_count == 3


def test_outbox_stops_when_empty(monkeypatch):
    from packages.observability import outbox

    publish = Mock(side_effect=[1, None])
    monkeypatch.setattr(outbox, "Outbox", lambda engine: SimpleNamespace(publish_one=publish))
    smoke.publish_outbox(SimpleNamespace(engine=object()), limit=1000)
    assert publish.call_count == 2


def test_backlog_reason_remains_fixed_and_public(monkeypatch, capsys):
    monkeypatch.setattr(smoke, "require_operator_terminal", Mock())
    monkeypatch.setattr(smoke, "operator_input", lambda prompt: "PAPER " + RUN_ID)
    monkeypatch.setattr(
        smoke,
        "execute",
        Mock(
            side_effect=smoke.SmokeDenied(
                "PAPER_SMOKE_OUTBOX_UNRESOLVED",
            )
        ),
    )
    assert smoke.main(arguments()) == 1
    assert capsys.readouterr().err == "PAPER_SMOKE_OUTBOX_UNRESOLVED\n"


def safe_runtime_config():
    return SimpleNamespace(
        mode="DISABLED",
        broker_provider="alpaca-paper",
        paper_enabled=True,
        decision_provider="mock",
        instruments=("SPY", "QQQ"),
        reconciliation_fee_profile="alpaca-paper-us-retail-equity-zero-v1",
    )


@pytest.mark.parametrize("failure", [RuntimeError("private-provider-canary"), KeyboardInterrupt()])
def test_execute_failure_pauses_publishes_and_closes_all_resources_in_order(
    monkeypatch,
    capsys,
    failure,
):
    from packages.persistence import ledger as ledger_module
    from services.control_api import control
    from services.runtime import config, execution

    events = []
    engine = SimpleNamespace(dispose=lambda: events.append("engine-dispose"))
    ledger = SimpleNamespace(engine=engine)
    runtime = SimpleNamespace(
        process=SimpleNamespace(
            broker=SimpleNamespace(
                close=lambda: events.append("broker-close"),
            )
        ),
        market_provider=SimpleNamespace(close=lambda: events.append("market-close")),
    )
    load = Mock(return_value=safe_runtime_config())
    database = Mock(return_value=engine)
    factory = Mock(return_value=ledger)
    construct = Mock(side_effect=lambda *args: events.append("construct") or runtime)
    monkeypatch.setattr(config.RuntimeConfig, "load", load)
    monkeypatch.setattr(config, "database_engine", database)
    monkeypatch.setattr(ledger_module, "PostgresLedger", factory)
    monkeypatch.setattr(execution, "ExecutionRuntime", construct)

    @contextmanager
    def run_lock(actual):
        assert actual is ledger
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    started = datetime.now(UTC)

    def begin(actual, opts):
        assert actual is ledger and opts.run_id == RUN_ID
        events.append("begin")
        return started

    def fail_run(actual, opts, actual_started):
        assert actual is runtime and actual_started is started
        events.append("run")
        raise failure

    pause = Mock(side_effect=lambda **kwargs: events.append("pause"))
    publish = Mock(side_effect=lambda actual: events.append("publish"))
    monkeypatch.setattr(smoke, "_run_lock", run_lock)
    monkeypatch.setattr(smoke, "begin_run", begin)
    monkeypatch.setattr(smoke, "_fingerprint", lambda *args: events.append("fingerprint"))
    monkeypatch.setattr(smoke, "run", fail_run)
    monkeypatch.setattr(control, "pause", pause)
    monkeypatch.setattr(smoke, "publish_outbox", publish)
    with pytest.raises(type(failure)):
        smoke.execute(options())
    assert events == [
        "lock",
        "begin",
        "construct",
        "fingerprint",
        "run",
        "pause",
        "publish",
        "unlock",
        "broker-close",
        "market-close",
        "engine-dispose",
    ]
    database.assert_called_once_with("execution")
    factory.assert_called_once_with(engine=engine)
    pause.assert_called_once_with(
        ledger=ledger,
        account_id="paper",
        actor_id="paper-smoke-execution",
        trace_id=smoke.uuid.UUID(RUN_ID).hex,
        reason_code="PAUSED_NEW",
    )
    publish.assert_called_once_with(ledger)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "PAPER"),
        ("mode", "LIVE"),
        ("broker_provider", "fake"),
        ("paper_enabled", False),
        ("decision_provider", "jev"),
        ("reconciliation_fee_profile", None),
        ("instruments", ("QQQ",)),
    ],
)
def test_execute_unsafe_config_rejects_before_database_or_provider_construction(
    monkeypatch,
    field,
    value,
):
    from packages.persistence import ledger as ledger_module
    from services.runtime import config, execution

    unsafe = safe_runtime_config()
    setattr(unsafe, field, value)
    database = Mock()
    provider = Mock()
    ledger = Mock()
    monkeypatch.setattr(config.RuntimeConfig, "load", Mock(return_value=unsafe))
    monkeypatch.setattr(config, "database_engine", database)
    monkeypatch.setattr(execution, "ExecutionRuntime", provider)
    monkeypatch.setattr(ledger_module, "PostgresLedger", ledger)
    with pytest.raises(smoke.SmokeDenied):
        smoke.execute(options())
    database.assert_not_called()
    provider.assert_not_called()
    ledger.assert_not_called()
