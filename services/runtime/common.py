"""Secret-free process status and cancellable scheduling."""

import hashlib
import json
import os
import signal
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock

_STATUS_LOCK = Lock()


class Clock:
    def now(self):
        return datetime.now(UTC)


class Ids:
    def new_id(self, namespace, seed=None):
        return (
            hashlib.sha256(f"{namespace}:{seed}".encode()).hexdigest() if seed else uuid.uuid4().hex
        )


def write_status(role: str, code: str):
    # Fixed symbolic status only; no exception strings, config or SDK payloads.
    if role not in {"execution", "worker", "watchdog", "api"} or code not in {
        "STARTING",
        "READY",
        "UNAVAILABLE",
        "DATA_UNAVAILABLE",
        "DECISION_UNAVAILABLE",
    }:
        raise ValueError("INVALID_STATUS")
    path = Path("/tmp/runtime-status.json")
    temporary = path.with_suffix(".tmp")
    with _STATUS_LOCK:
        temporary.write_text(
            json.dumps({"role": role, "code": code, "checked_at": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
        os.replace(temporary, path)


def run_loop(role, tick, *, interval=5.0, ledger=None, telemetry=None, sampler=None):
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    while not stop.is_set():
        try:
            if ledger is not None:
                from sqlalchemy import text

                try:
                    with ledger.transaction() as tx:
                        tx.connection.execute(text("SELECT 1")).scalar_one()
                except Exception:
                    if telemetry:
                        telemetry.update_health("DATABASE", "UNAVAILABLE", "DATABASE_UNAVAILABLE")
                    raise
                if telemetry:
                    telemetry.update_health("DATABASE", "OK", "OK")
            component = {"execution": "EXECUTION", "worker": "DECISION", "watchdog": "WATCHDOG"}[
                role
            ]
            with telemetry.span(component, "TICK") if telemetry else nullcontext():
                code = tick() or "READY"
            if sampler:
                sampler.sample()
            write_status(role, code)
        except Exception:
            write_status(role, "UNAVAILABLE")
        if telemetry:
            telemetry.export_local()
        stop.wait(interval)
