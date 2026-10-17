"""Durable service entry points; no inherited environment configuration."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import RuntimeConfig, database_engine


def health() -> int:
    try:
        value = json.loads(Path("/tmp/runtime-status.json").read_text("utf-8"))
        moment = datetime.fromisoformat(value["checked_at"])
        fresh = datetime.now(UTC) - timedelta(seconds=20) <= moment <= datetime.now(UTC)
        return 0 if fresh and value["code"] in {"READY", "DATA_UNAVAILABLE"} else 1
    except Exception:
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "role", choices=["execution", "worker", "watchdog", "api", "activate", "health"]
    )
    args = parser.parse_args(argv)
    # SDKs/frameworks never emit exception bodies, URLs, headers or ambient data.
    # Structured telemetry is manually created with a fixed schema below.
    logging.disable(logging.CRITICAL)
    if args.role == "health":
        return health()
    try:
        if args.role == "activate":
            from .api import activate

            activate()
            return 0
        from packages.observability.telemetry import Telemetry
        from packages.persistence.ledger import PostgresLedger

        from .common import run_loop, write_status

        config = RuntimeConfig.load()
        write_status(args.role, "STARTING")
        engine = database_engine(args.role)
        ledger = PostgresLedger(engine=engine)
        telemetry = Telemetry(args.role, stream=sys.stdout)
        telemetry.emit("DATABASE", "STARTED", "STARTING")
        if args.role == "execution":
            from packages.observability.runtime import RuntimeMetrics

            from .execution import ExecutionRuntime

            runtime = ExecutionRuntime(config, ledger, telemetry)
            # Execution must observe DB exceptions itself to latch its sticky
            # local fault; a generic preflight must not consume that evidence.
            run_loop(
                "execution",
                runtime.tick,
                telemetry=telemetry,
                sampler=RuntimeMetrics(ledger, telemetry, config.account_id),
            )
        elif args.role == "worker":
            from .worker import WorkerRuntime

            runtime = WorkerRuntime(config, ledger, telemetry)
            run_loop("worker", runtime.tick, ledger=ledger, telemetry=telemetry)
        elif args.role == "watchdog":
            from services.watchdog.main import poll_once

            def tick():
                result = poll_once(ledger=ledger, account_id=config.account_id)
                telemetry.update_health(
                    "EXECUTION",
                    "OK" if result.healthy else "UNAVAILABLE",
                    "OK" if result.healthy else "EXECUTION_HEARTBEAT_STALE",
                )
                return "READY"

            run_loop("watchdog", tick, interval=5, ledger=ledger, telemetry=telemetry)
        else:
            from .api import run_api

            run_api(config, ledger, telemetry)
        return 0
    except Exception:
        # Deliberately omit exception interpolation even for configuration errors.
        print('{"component":"runtime","code":"UNAVAILABLE"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
