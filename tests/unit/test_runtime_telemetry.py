"""Synthetic canaries exercise the entire telemetry boundary without credentials."""

import io
import json
from decimal import Decimal

import pytest

from packages.observability.telemetry import (
    Component,
    Event,
    Metric,
    ReasonCode,
    Role,
    Status,
    Telemetry,
)

CANARY = "synthetic-secret-never-emit-42"


@pytest.fixture
def telemetry():
    instance = Telemetry(Role.EXECUTION, io.StringIO())
    yield instance
    instance.close()


def test_sdk_metrics_and_traces_are_real_local_and_have_bounded_labels(telemetry):
    for name in Metric:
        telemetry.observe(name, Decimal("1.25"), Component.EXECUTION)
    with telemetry.span(Component.EXECUTION, Event.TICK, trace_id="a" * 32):
        pass
    snapshot = telemetry.snapshot()
    assert len(snapshot["metrics"]) == len(Metric)
    resource = snapshot["otelMetrics"]["resource_metrics"][0]
    assert resource["resource"]["attributes"] == {"service.name": "trading-lab-execution"}
    metrics = resource["scope_metrics"][0]["metrics"]
    assert {row["name"] for row in metrics} == {name.value for name in Metric}
    for row in metrics:
        for point in row["data"]["data_points"]:
            assert point["attributes"] == {
                "role": "execution", "component": "EXECUTION", "status": "OK",
            }
    assert len(snapshot["spans"]) == 1
    span = snapshot["spans"][0]
    assert span["event"] == "TICK"
    assert len(span["traceId"]) == 32
    assert span["attributes"]["correlation.trace_id"] == "a" * 32


def test_ambient_resource_configuration_is_never_collected(monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"secret={CANARY}")
    monkeypatch.setenv("OTEL_SERVICE_NAME", CANARY)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
    instance = Telemetry(Role.WATCHDOG, io.StringIO())
    try:
        instance.observe(Metric.LEASE_AGE_SECONDS, 3, Component.WATCHDOG)
        with instance.span(Component.WATCHDOG, Event.TICK):
            pass
        serialized = json.dumps(instance.snapshot())
        assert CANARY not in serialized
        assert "service.instance.id" not in serialized
        assert "host.name" not in serialized
    finally:
        instance.close()


@pytest.mark.parametrize("action", [
    lambda t: t.emit(CANARY, Event.FAILED, ReasonCode.INTERNAL_ERROR),
    lambda t: t.emit(Component.BROKER, CANARY, ReasonCode.INTERNAL_ERROR),
    lambda t: t.emit(Component.BROKER, Event.FAILED, RuntimeError(CANARY)),
    lambda t: t.emit(Component.BROKER, Event.FAILED, ReasonCode.INTERNAL_ERROR, CANARY),
    lambda t: t.observe(CANARY, 1, Component.BROKER),
    lambda t: t.observe(Metric.JEV_USAGE, RuntimeError(CANARY), Component.DECISION),
    lambda t: t.observe(Metric.JEV_USAGE, 1, CANARY),
    lambda t: t.observe(Metric.JEV_USAGE, 1, Component.DECISION, CANARY),
    lambda t: t.update_health(Component.BROKER, Status.UNAVAILABLE, CANARY),
])
def test_unknown_inputs_are_rejected_without_echo_or_output(telemetry, action):
    with pytest.raises(ValueError) as error:
        action(telemetry)
    assert CANARY not in str(error.value)
    assert telemetry.stream.getvalue() == ""
    assert telemetry.snapshot()["metrics"] == []
    assert telemetry.snapshot()["spans"] == []


def test_exception_message_and_stack_never_reach_sdk_or_logs(telemetry):
    with pytest.raises(RuntimeError):
        with telemetry.span(Component.BROKER, Event.FAILED):
            raise RuntimeError(CANARY)
    result = telemetry.snapshot()
    assert result["spans"][0]["status"] == "ERROR"
    assert CANARY not in json.dumps(result)
    assert CANARY not in telemetry.stream.getvalue()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, CANARY])
def test_invalid_measurements_do_not_mutate_state(telemetry, value):
    with pytest.raises(ValueError):
        telemetry.observe(Metric.RESERVED_RISK, value, Component.EXECUTION)
    assert telemetry.snapshot()["metrics"] == []


def test_health_log_utc_and_snapshot_are_safe_copies(telemetry):
    telemetry.update_health(
        Component.DECISION, Status.UNAVAILABLE, ReasonCode.DECISION_UNAVAILABLE,
        data_age_seconds=30,
    )
    record = json.loads(telemetry.stream.getvalue())
    assert record["reasonCode"] == "DECISION_UNAVAILABLE"
    assert record["utc"].endswith("+00:00")
    first = telemetry.health_snapshot()
    assert first[0]["dataAgeSeconds"] == 30
    first[0]["status"] = CANARY
    assert telemetry.health_snapshot()[0]["status"] == "UNAVAILABLE"
    with pytest.raises(TypeError):
        telemetry.emit(Component.DECISION, Event.FAILED, ReasonCode.INTERNAL_ERROR, payload=CANARY)


def test_trace_buffer_is_bounded(telemetry):
    for _ in range(260):
        with telemetry.span(Component.WATCHDOG, Event.TICK):
            pass
    assert len(telemetry.snapshot()["spans"]) == 256


def test_entity_identifier_is_validated_without_high_cardinality_metric_tags(telemetry):
    identifier = "20000000-0000-4000-8000-000000000001"
    telemetry.emit(Component.EXECUTION, Event.TICK, ReasonCode.OK, entity_id=identifier)
    assert json.loads(telemetry.stream.getvalue())["entityId"] == identifier
    with pytest.raises(ValueError) as error:
        telemetry.emit(Component.EXECUTION, Event.TICK, ReasonCode.OK, entity_id=CANARY)
    assert CANARY not in str(error.value)
    assert CANARY not in telemetry.stream.getvalue()


def test_snapshot_mutation_cannot_inject_span_attributes(telemetry):
    with telemetry.span(Component.EXECUTION, Event.TICK):
        pass
    telemetry.snapshot()["spans"][0]["attributes"]["secret"] = CANARY
    assert CANARY not in json.dumps(telemetry.snapshot())


def test_private_local_snapshot_and_export_failure_are_bounded(tmp_path, monkeypatch):
    import json

    import packages.observability.telemetry as module

    destination = tmp_path / "runtime-telemetry.json"
    monkeypatch.setattr(module, "LOCAL_SNAPSHOT", destination)
    telemetry = module.Telemetry("execution")
    telemetry.observe("unknown_count", 2, "EXECUTION", "DEGRADED")
    assert telemetry.export_local()
    snapshot = json.loads(destination.read_text("utf-8"))
    assert snapshot["role"] == "execution" and snapshot["checkedAt"]
    assert snapshot["metrics"][0]["value"] == 2
    monkeypatch.setattr(module, "LOCAL_SNAPSHOT", tmp_path / "absent" / "snapshot.json")
    assert telemetry.export_local() is False
    telemetry.close()
