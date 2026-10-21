"""Inactive isolated Paper processes. No financial or provider composition yet."""

import signal
from threading import Event

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
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    try:
        # No ExecutionRuntime/WorkerRuntime, ledger seed or credentials reader.
        # APW-03/05 will add persistent commands before any provider composition.
        while not stop.is_set():
            try:
                write_status(role, check_identity(engine, role))
            except Exception:
                write_status(role, "UNAVAILABLE")
            stop.wait(5)
    finally:
        engine.dispose()
