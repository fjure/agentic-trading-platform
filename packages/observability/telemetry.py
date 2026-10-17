"""Manual, bounded telemetry: no ambient resources, payloads or network exporters.

All public input is validated before any SDK or stream call. SDK objects never
escape the boundary, so callers cannot attach exception text or arbitrary tags.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import TextIO

from opentelemetry.context import Context
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Status as SpanStatus
from opentelemetry.trace import StatusCode

LOCAL_SNAPSHOT = Path("/tmp/runtime-telemetry.json")


class Role(StrEnum):
    CONTROL_API = "api"
    EXECUTION = "execution"
    WATCHDOG = "watchdog"
    WORKER = "worker"
    MARKET_DATA = "market_data"
    DECISION = "decision"


class Component(StrEnum):
    DATABASE = "DATABASE"
    MARKET = "MARKET"
    BROKER = "BROKER"
    DECISION = "DECISION"
    EXECUTION = "EXECUTION"
    WATCHDOG = "WATCHDOG"
    CONTROL_API = "CONTROL_API"


class Status(StrEnum):
    UNKNOWN = "UNKNOWN"
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ReasonCode(StrEnum):
    STARTING = "STARTING"
    OK = "OK"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    DECISION_UNAVAILABLE = "DECISION_UNAVAILABLE"
    EXECUTION_HEARTBEAT_STALE = "EXECUTION_HEARTBEAT_STALE"
    LEASE_LOST = "LEASE_LOST"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Event(StrEnum):
    STARTED = "STARTED"
    STOPPED = "STOPPED"
    HEALTH_CHANGED = "HEALTH_CHANGED"
    TICK = "TICK"
    PAUSED = "PAUSED"
    RECONCILED = "RECONCILED"
    DECISION_COMPLETED = "DECISION_COMPLETED"
    FAILED = "FAILED"


class Metric(StrEnum):
    DATA_AGE_SECONDS = "data_age_seconds"
    LEASE_AGE_SECONDS = "lease_age_seconds"
    OUTBOX_LAG_SECONDS = "outbox_lag_seconds"
    RECONCILIATION_MISMATCH_COUNT = "reconciliation_mismatch_count"
    UNKNOWN_COUNT = "unknown_count"
    RESERVED_RISK = "reserved_risk"
    JEV_LATENCY_SECONDS = "jev_latency_seconds"
    JEV_ERROR_COUNT = "jev_error_count"
    JEV_USAGE = "jev_usage"
    SSE_LAG_SECONDS = "sse_lag_seconds"


def _enum(kind, value):
    # Do not stringify unknown objects (including exceptions) in validation errors.
    if not isinstance(value, (str, kind)):
        raise ValueError("unsupported telemetry code")
    for member in kind:
        if value == member.value:
            return member
    raise ValueError("unsupported telemetry code")


def _number(value) -> float:
    if type(value) not in (int, float, Decimal):
        raise ValueError("invalid telemetry measurement")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("invalid telemetry measurement") from None
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid telemetry measurement")
    return result


def _trace(value: str | None) -> str | None:
    if value is not None and (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
        or int(value, 16) == 0
    ):
        raise ValueError("invalid telemetry trace identifier")
    return value


def _entity(value: str | None) -> str | None:
    if value is not None and (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", value) is None
    ):
        raise ValueError("invalid telemetry entity identifier")
    return value


class _SafeSpanExporter(SpanExporter):
    def __init__(self):
        self.records = deque(maxlen=256)

    def export(self, spans):
        for span in spans:
            self.records.append({
                "event": span.name,
                "traceId": f"{span.context.trace_id:032x}",
                "status": span.status.status_code.name,
                "attributes": dict(span.attributes),
            })
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


@dataclass
class Telemetry:
    role: Role | str
    stream: TextIO | None = None
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self):
        self.role = _enum(Role, self.role)
        # Resource.create() runs environment detectors; raw Resource does not.
        resource = Resource({"service.name": f"trading-lab-{self.role.value.lower()}"})
        self._reader = InMemoryMetricReader()
        self._meters = MeterProvider(
            resource=resource, metric_readers=[self._reader], shutdown_on_exit=False,
        )
        meter = self._meters.get_meter("trading-lab.runtime")
        self._instruments = {
            name: meter.create_gauge(name.value) for name in Metric
            if name != Metric.JEV_LATENCY_SECONDS
        }
        self._instruments[Metric.JEV_LATENCY_SECONDS] = meter.create_histogram(
            Metric.JEV_LATENCY_SECONDS.value, unit="s",
        )
        self._exporter = _SafeSpanExporter()
        self._traces = TracerProvider(
            resource=resource, sampler=ALWAYS_ON, shutdown_on_exit=False,
        )
        self._traces.add_span_processor(SimpleSpanProcessor(self._exporter))
        self._tracer = self._traces.get_tracer("trading-lab.runtime")
        self._metrics = {}
        self._health = {}

    def emit(
        self, component: Component | str, event: Event | str, reason_code: ReasonCode | str,
        trace_id: str | None = None, *, entity_id: str | None = None,
    ) -> None:
        component = _enum(Component, component)
        event = _enum(Event, event)
        reason_code = _enum(ReasonCode, reason_code)
        trace_id = _trace(trace_id)
        entity_id = _entity(entity_id)
        record = {
            "utc": datetime.now(UTC).isoformat(), "role": self.role.value,
            "component": component.value, "event": event.value,
            "reasonCode": reason_code.value, "traceId": trace_id, "entityId": entity_id,
        }
        if self.stream is not None:
            with self._lock:
                self.stream.write(json.dumps(record, separators=(",", ":")) + "\n")
                self.stream.flush()

    def observe(
        self, name: Metric | str, value: int | float | Decimal, component: Component | str,
        status: Status | str = Status.OK,
    ) -> None:
        name = _enum(Metric, name)
        value = _number(value)
        component = _enum(Component, component)
        status = _enum(Status, status)
        attributes = {
            "role": self.role.value, "component": component.value, "status": status.value,
        }
        with self._lock:
            instrument = self._instruments[name]
            if name == Metric.JEV_LATENCY_SECONDS:
                instrument.record(value, attributes)
            else:
                instrument.set(value, attributes)
            self._metrics[(name.value, component.value, status.value)] = value

    def update_health(
        self, component: Component | str, status: Status | str, reason_code: ReasonCode | str,
        *, data_age_seconds: int | float | Decimal | None = None,
    ) -> None:
        component = _enum(Component, component)
        status = _enum(Status, status)
        reason_code = _enum(ReasonCode, reason_code)
        age = None if data_age_seconds is None else _number(data_age_seconds)
        with self._lock:
            self._health[component.value] = {
                "component": component.value, "status": status.value,
                "reasonCode": reason_code.value, "utc": datetime.now(UTC).isoformat(),
                "dataAgeSeconds": age,
            }
            if age is not None:
                self.observe(Metric.DATA_AGE_SECONDS, age, component, status)
            self.emit(component, Event.HEALTH_CHANGED, reason_code)

    def health_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(value) for _, value in sorted(self._health.items())]

    @contextmanager
    def span(
        self, component: Component | str, event: Event | str,
        reason_code: ReasonCode | str = ReasonCode.OK, trace_id: str | None = None,
    ) -> Iterator[None]:
        component = _enum(Component, component)
        event = _enum(Event, event)
        reason_code = _enum(ReasonCode, reason_code)
        trace_id = _trace(trace_id)
        attributes = {
            "role": self.role.value, "component": component.value,
            "reason": reason_code.value,
        }
        if trace_id is not None:
            attributes["correlation.trace_id"] = trace_id
        # Explicit empty context excludes ambient baggage and parent attributes.
        span = self._tracer.start_span(
            event.value, context=Context(), attributes=attributes,
            record_exception=False, set_status_on_exception=False,
        )
        try:
            yield None
        except BaseException:
            span.set_status(SpanStatus(StatusCode.ERROR))
            raise
        finally:
            with self._lock:
                span.end()

    def snapshot(self) -> dict:
        with self._lock:
            data = self._reader.get_metrics_data()
            return {
                "role": self.role.value,
                "health": self.health_snapshot(),
                "metrics": [
                    {"name": name, "component": component, "status": status, "value": value}
                    for (name, component, status), value in sorted(self._metrics.items())
                ],
                "otelMetrics": None if data is None else json.loads(data.to_json()),
                "spans": deepcopy(list(self._exporter.records)),
            }

    def export_local(self) -> bool:
        """Bounded private tmpfs evidence; a failed write never gates safety work."""
        try:
            value = self.snapshot()
            value["checkedAt"] = datetime.now(UTC).isoformat()
            encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
            temporary = LOCAL_SNAPSHOT.with_suffix(".tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(encoded)
            os.replace(temporary, LOCAL_SNAPSHOT)
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._meters.shutdown()
        self._traces.shutdown()
