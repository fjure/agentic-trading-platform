"""Disposable browser harness Execution child; never a deployment entrypoint.

Only the transport is synthetic: all credentials, ownership, Risk, durable
attempts and recovery use the existing production components and database.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path


def execution(private_root: Path):
    import httpx
    from sqlalchemy import URL, create_engine, event

    from packages.brokers.alpaca_market_data import AlpacaMarketDataProvider
    from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
    from packages.persistence.ledger import PostgresLedger
    from services.execution.paper_credentials import PaperGenerationReader
    from services.execution.paper_daily_evidence import PaperPreparationCollector
    from services.execution.paper_daily_refresh import PaperDailyRefreshCollector
    from services.execution.paper_verification import PaperAccountVerifier
    from services.runtime.common import Clock
    from services.runtime.config import RuntimeConfig
    from services.runtime.paper_coordinator import PaperRuntimeCoordinator

    logging.disable(logging.CRITICAL)
    private_root = private_root.resolve(strict=True)
    if not private_root.is_dir():
        raise ValueError("SYNTHETIC_PRIVATE_DIRECTORY_REQUIRED")
    engine = create_engine(URL.create(
        "postgresql+psycopg", username="atl_paper_execution_login", host="postgres",
        database="lab_paper_runtime",
    ), hide_parameters=True, connect_args={"connect_timeout": 5})
    transactions = threading.local()

    @event.listens_for(engine, "connect")
    def initialize(connection, _record):
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE atl_paper_execution")
            cursor.execute("SET TIME ZONE 'UTC'")
        connection.commit()

    @event.listens_for(engine, "begin")
    def begin(_connection):
        transactions.depth = getattr(transactions, "depth", 0) + 1

    def ended(_connection):
        transactions.depth = getattr(transactions, "depth", 0) - 1
        assert transactions.depth >= 0, "EXECUTION_TRANSACTION_EVENT_UNPAIRED"

    event.listen(engine, "commit", ended)
    event.listen(engine, "rollback", ended)

    forward = httpx.Client(trust_env=False, follow_redirects=False, timeout=5)

    class SyntheticReadTimeout(httpx.ReadTimeout):
        """Only the exact offline provider fault envelope selects this fault."""

    def provider(request):
        if (request.url.scheme != "https"
                or request.url.host not in {"paper-api.alpaca.markets", "data.alpaca.markets"}
                or request.url.port not in {None, 443}
                or request.method not in {"GET", "POST", "DELETE"}
                or len(request.content) > 65536):
            raise AssertionError("SYNTHETIC_PROVIDER_BOUNDARY")
        # This checks the calling child thread, including account preflight,
        # collection, submit and the independent cancellation adapter.
        if getattr(transactions, "depth", 0) != 0:
            raise AssertionError("PROVIDER_IO_INSIDE_EXECUTION_TRANSACTION")
        reply = forward.post("http://127.0.0.1:8021/request", json={
            "method": request.method, "url": str(request.url),
            "headers": list(request.headers.multi_items()),
            "body": request.content.decode("utf-8"),
        })
        if reply.status_code != 200 or len(reply.content) > 2 * 1024 * 1024:
            raise AssertionError("SYNTHETIC_FORWARD_FAILED")
        value = reply.json()
        if value == {"fault": "TIMEOUT"}:
            raise SyntheticReadTimeout("SYNTHETIC_PROVIDER_TIMEOUT", request=request)
        if (type(value) is not dict or set(value) != {"status", "headers", "responseBody"}
                or type(value["status"]) is not int or not 100 <= value["status"] <= 599
                or type(value["headers"]) is not list
                or any(type(pair) is not list or len(pair) != 2
                       or any(type(item) is not str for item in pair)
                       for pair in value["headers"])
                or type(value["responseBody"]) is not str):
            raise AssertionError("SYNTHETIC_FORWARD_RESPONSE_INVALID")
        return httpx.Response(value["status"], headers=value["headers"],
                              content=value["responseBody"].encode("utf-8"))

    transport = httpx.MockTransport(provider)

    class Reader(PaperGenerationReader):
        def __init__(self):
            super().__init__(private_root, owner_uid=os.getuid())

    coordinator = PaperRuntimeCoordinator(
        RuntimeConfig(deployment="paper", broker_provider="alpaca-paper"),
        PostgresLedger(engine=engine), clock=Clock(), reader_factory=Reader,
        broker_factory=lambda *a, **k: AlpacaPaperBrokerAdapter(*a, **k, transport=transport),
        market_factory=lambda *a, **k: AlpacaMarketDataProvider(*a, **k, transport=transport),
        daily_factory=lambda **k: PaperDailyRefreshCollector(**k, transport=transport),
    )
    coordinator.verifier.verifier_factory = lambda **k: PaperAccountVerifier(
        **k, transport=transport,
    )
    coordinator.verifier.preparation_factory = lambda **k: PaperPreparationCollector(
        **k, transport=transport,
    )
    stop = threading.Event()
    threads = []
    reported = set()
    report_lock = threading.Lock()

    def schedule(callback, seconds):
        def loop():
            while not stop.is_set():
                try:
                    callback()
                except Exception as error:
                    # Keep invoking ordinary production ports; no exception
                    # grants authority or modifies ownership/lease deadlines.
                    identity = type(error).__name__
                    with report_lock:
                        if identity not in reported:
                            reported.add(identity)
                            print("FAIL:SCHEDULER:" + identity, flush=True)
                stop.wait(seconds)
        thread = threading.Thread(target=loop, daemon=True)
        threads.append(thread)
        thread.start()

    try:
        # Construction is complete; owner acquisition still uses normal SQL
        # deadlines. Keep the initial handshake ahead of scheduler diagnostics.
        print("READY", flush=True)
        schedule(coordinator.command_tick, 0.5)
        schedule(coordinator.heartbeat_tick, 1)
        schedule(coordinator.cancel_tick, 0.5)
        schedule(coordinator.recovery_tick, 1)
        if sys.stdin.readline().strip() != "stop":
            raise ValueError("SYNTHETIC_STOP_REQUIRED")
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=7)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("SYNTHETIC_SCHEDULER_STOP_FAILED")
        coordinator.close()
        forward.close()
        engine.dispose()


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "execution":
            raise ValueError("SYNTHETIC_EXECUTION_ARGUMENTS_REQUIRED")
        execution(Path(sys.argv[2]))
    except Exception as error:
        print("FAIL:EXECUTION:" + type(error).__name__, flush=True)
        raise SystemExit(1) from None
