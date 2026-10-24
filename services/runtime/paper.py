"""Isolated Paper processes with explicit read-only verification commands."""

import signal
from threading import Event, Thread

from .common import write_status
from .config import PAPER_ROLES, RuntimeConfig, database_engine


def check_identity(engine, role):
    from sqlalchemy import text

    if role not in PAPER_ROLES:
        raise ValueError("INVALID_ROLE")
    with engine.connect() as connection:
        identity = connection.execute(text(
            "SELECT current_database(),current_user,session_user"
        )).one()
        if tuple(identity) != (
            "lab_paper_runtime", PAPER_ROLES[role], PAPER_ROLES[role] + "_login"
        ):
            raise PermissionError("PAPER_DATABASE_IDENTITY_MISMATCH")
    return "READY"


def run(role):
    config = RuntimeConfig.load()
    if config.deployment != "paper" or role not in PAPER_ROLES:
        raise ValueError("DEPLOYMENT_ENTRYPOINT_MISMATCH")
    write_status(role, "STARTING")
    engine = database_engine(role, deployment="paper")
    if role == "api":
        from .paper_api import run_private_paper_api

        try:
            run_private_paper_api(engine)
        finally:
            engine.dispose()
        return
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    from packages.persistence.ledger import PostgresLedger

    ledger = PostgresLedger(engine=engine)
    coordinator, tick = None, None
    threads = []

    def schedule(callback, *, health=False):
        def loop():
            while not stop.is_set():
                try:
                    result = callback() or "READY"
                    if health:
                        write_status(role, result)
                except Exception:
                    if health:
                        write_status(role, "UNAVAILABLE")
                stop.wait(5)

        thread = Thread(target=loop, daemon=True)
        threads.append(thread)
        thread.start()

    if role == "execution":
        from .paper_coordinator import PaperRuntimeCoordinator

        coordinator = PaperRuntimeCoordinator(config, ledger)
        schedule(coordinator.command_tick)
        schedule(coordinator.cancel_tick)
        schedule(coordinator.heartbeat_tick, health=True)
        tick = coordinator.recovery_tick
    elif role == "worker":
        from sqlalchemy import text

        from .worker import WorkerRuntime

        worker = WorkerRuntime(config, ledger)

        def worker_tick():
            try:
                result = worker.tick()
            except Exception:
                result = "DECISION_UNAVAILABLE"
            with engine.begin() as connection:
                connection.execute(text("SELECT public.paper_worker_heartbeat(:state)"),
                                   {"state": "READY" if result == "READY" else "UNAVAILABLE"})
            return result

        tick = worker_tick
    elif role == "watchdog":
        from sqlalchemy import text

        def watchdog_tick():
            import uuid

            with engine.begin() as connection:
                connection.execute(text("SELECT public.paper_watchdog_poll(:trace)"),
                                   {"trace": uuid.uuid4().hex})
            return "READY"

        tick = watchdog_tick
    try:
        while not stop.is_set():
            try:
                check_identity(engine, role)
                result = tick() if tick else "READY"
                write_status(role, result)
            except Exception:
                write_status(role, "UNAVAILABLE")
            stop.wait(5)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
        if coordinator is not None:
            coordinator.close()
        engine.dispose()
