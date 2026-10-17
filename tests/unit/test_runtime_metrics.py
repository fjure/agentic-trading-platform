"""Read-only runtime observation never becomes admission or exception logging."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from packages.observability.runtime import RuntimeMetrics
from packages.observability.telemetry import Component, Metric, Status

NOW = datetime.now(UTC)
CANARY = "synthetic-secret-observer-canary"


def facts(**overrides):
    return {
        "now_at": NOW, "account_count": 1, "account_exists": True,
        "market_at": NOW-timedelta(seconds=15), "owner_at": NOW-timedelta(seconds=3),
        "job_at": NOW-timedelta(seconds=8), "leased_count": 1, "missing_beat": 0,
        "pending_at": NOW-timedelta(seconds=4), "pending_count": 1,
        "unknown_count": 2, "reserved_risk": Decimal("101.123456789012345678"),
        "reconciliation": "CURRENT", "reconciliation_current": True,
        "context_until": NOW+timedelta(seconds=60), **overrides,
    }


class Connection:
    def __init__(self, row):
        self.row, self.statements, self.params, self.rolled_back = row, [], None, False

    def exec_driver_sql(self, sql):
        self.statements.append(sql)

    def execute(self, statement, params):
        self.statements.append(str(statement))
        self.params = params
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: self.row))

    def rollback(self):
        self.rolled_back = True


class Engine:
    def __init__(self, row=None, fail=False):
        self.connection, self.fail = Connection(row), fail

    @contextmanager
    def connect(self):
        if self.fail:
            raise RuntimeError(CANARY)
        yield self.connection


class Sink:
    def __init__(self):
        self.points = {}

    def observe(self, metric, value, component, status=Status.OK):
        self.points[(metric, component)] = (value, status)


def observer(row=None, fail=False):
    engine, sink = Engine(facts() if row is None else row, fail), Sink()
    return RuntimeMetrics(SimpleNamespace(engine=engine), sink, "paper"), engine, sink


def test_sampler_observes_actual_ledger_values_with_one_bounded_readonly_statement():
    sample, engine, sink = observer()
    assert sample.sample() is True
    assert sink.points[(Metric.DATA_AGE_SECONDS, Component.MARKET)] == (15, Status.OK)
    assert sink.points[(Metric.LEASE_AGE_SECONDS, Component.EXECUTION)] == (3, Status.OK)
    assert sink.points[(Metric.LEASE_AGE_SECONDS, Component.DECISION)] == (8, Status.OK)
    assert sink.points[(Metric.OUTBOX_LAG_SECONDS, Component.CONTROL_API)] == (4, Status.OK)
    assert sink.points[(Metric.UNKNOWN_COUNT, Component.EXECUTION)] == (2, Status.DEGRADED)
    assert sink.points[(Metric.RESERVED_RISK, Component.EXECUTION)] == (
        Decimal("101.123456789012345678"), Status.OK,
    )
    assert sink.points[(Metric.RECONCILIATION_MISMATCH_COUNT, Component.BROKER)] == (0, Status.OK)
    statements = engine.connection.statements
    assert statements[:3] == ["SET TRANSACTION READ ONLY",
                              "SET LOCAL statement_timeout = '1000ms'",
                              "SET LOCAL lock_timeout = '100ms'"]
    assert len(statements) == 4
    assert "payload" not in statements[3]
    assert engine.connection.params == {"account": "paper"}
    assert engine.connection.rolled_back is True


def test_missing_evidence_and_pending_reconciliation_never_look_like_healthy_zero():
    sample, _, sink = observer(facts(
        market_at=None, owner_at=None, job_at=None, missing_beat=1,
        pending_at=None, reconciliation="PENDING", reconciliation_current=False,
    ))
    assert sample.sample() is True
    for key in (
        (Metric.DATA_AGE_SECONDS, Component.MARKET),
        (Metric.LEASE_AGE_SECONDS, Component.EXECUTION),
        (Metric.LEASE_AGE_SECONDS, Component.DECISION),
        (Metric.OUTBOX_LAG_SECONDS, Component.CONTROL_API),
        (Metric.RECONCILIATION_MISMATCH_COUNT, Component.BROKER),
    ):
        assert sink.points[key] == (0, Status.UNKNOWN)


def test_healthy_empty_queues_are_real_zero_and_mismatch_is_observed():
    sample, _, sink = observer(facts(
        job_at=None, leased_count=0, pending_at=None, pending_count=0,
        reconciliation="MISMATCH", reconciliation_current=False,
    ))
    assert sample.sample() is True
    assert sink.points[(Metric.LEASE_AGE_SECONDS, Component.DECISION)] == (0, Status.OK)
    assert sink.points[(Metric.OUTBOX_LAG_SECONDS, Component.CONTROL_API)] == (0, Status.OK)
    assert sink.points[(Metric.RECONCILIATION_MISMATCH_COUNT, Component.BROKER)] == (
        1, Status.DEGRADED,
    )


def test_stale_market_owner_and_expired_context_never_signal_health():
    sample, _, sink = observer(facts(
        market_at=NOW-timedelta(seconds=121), owner_at=NOW-timedelta(seconds=31),
        context_until=NOW,
    ))
    assert sample.sample() is True
    assert sink.points[(Metric.DATA_AGE_SECONDS, Component.MARKET)] == (121, Status.DEGRADED)
    assert sink.points[(Metric.LEASE_AGE_SECONDS, Component.EXECUTION)] == (31, Status.DEGRADED)
    assert sink.points[(Metric.RECONCILIATION_MISMATCH_COUNT, Component.BROKER)] == (
        0, Status.UNKNOWN,
    )


def test_database_failure_and_unknown_account_never_escape_or_disclose_exception(capsys):
    for row, fail in ((facts(), True), (facts(account_exists=False), False),
                      (facts(account_count=2), False)):
        sample, _, sink = observer(row, fail)
        assert sample.sample() is False
        assert all(status == Status.UNKNOWN for _, status in sink.points.values())
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


def test_observer_failure_cannot_prevent_safety_work_or_propagate_canaries(capsys):
    class BrokenSink:
        def observe(self, *_):
            raise RuntimeError(CANARY)

    sample, _, _ = observer()
    sample.telemetry = BrokenSink()
    assert sample.sample() is True
    sample.delivered(datetime.now(UTC))
    assert CANARY not in repr(capsys.readouterr())


def test_delivery_age_is_observed_only_on_real_delivery_callback_not_db_sampling():
    sample, _, sink = observer()
    sample.sample()
    assert (Metric.SSE_LAG_SECONDS, Component.CONTROL_API) not in sink.points
    sample.delivered(datetime.now(UTC)-timedelta(seconds=5))
    value, status = sink.points[(Metric.SSE_LAG_SECONDS, Component.CONTROL_API)]
    assert 5 <= value < 7 and status == Status.OK
    sample.delivered(datetime.now(UTC)+timedelta(seconds=10))
    assert sink.points[(Metric.SSE_LAG_SECONDS, Component.CONTROL_API)] == (0, Status.UNKNOWN)


def test_synthetic_exception_in_delivery_input_never_gets_stringified(capsys):
    sample, _, sink = observer()
    sample.delivered(RuntimeError(CANARY))
    assert sink.points[(Metric.SSE_LAG_SECONDS, Component.CONTROL_API)] == (0, Status.UNKNOWN)
    assert CANARY not in repr(capsys.readouterr())
