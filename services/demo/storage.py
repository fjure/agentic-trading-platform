"""UUID-only financial databases and private persistent run coordination."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from sqlalchemy import create_engine, event

from services.runtime.config import secret_fields

LOGIN = "atl_demo_provisioner_login"
GROUPS = ("atl_worker", "atl_execution", "atl_control_api", "atl_watchdog")


def database_name(run_id: str) -> str:
    value = uuid.UUID(run_id)
    if str(value) != run_id or value.version != 4:
        raise ValueError("INVALID_REQUEST")
    return "lab_demo_" + value.hex


def validate_environment() -> None:
    # No environment variable can select a different provider, host or database.
    forbidden = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "ALPACA",
        "JEV",
        "BROKER_PROVIDER",
        "DECISION_PROVIDER",
        "DATABASE_URL",
    )
    if any(
        value and any(key.upper().startswith(item) for item in forbidden)
        for key, value in os.environ.items()
    ):
        raise ValueError("UNSAFE_DEMO_CONFIGURATION")
    if any(
        Path(path).exists()
        for path in (
            "/run/secrets/alpaca",
            "/run/secrets/jev",
            "/run/provider",
            "/run/providers",
        )
    ):
        raise ValueError("UNSAFE_DEMO_CONFIGURATION")


def private_engine(path: Path = Path("/run/secrets/demo_database")):
    from sqlalchemy import URL

    validate_environment()
    credentials = secret_fields(path, {"username", "password"})
    if credentials["username"] != LOGIN:
        raise ValueError("INVALID_DATABASE_IDENTITY")
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username=LOGIN,
            password=credentials["password"],
            host="postgres",
            port=5432,
            database="lab_demo",
        ),
        hide_parameters=True,
        echo=False,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def financial_engine(metadata_engine, run_id: str, *, role: str | None = None):
    name = database_name(run_id)
    if role is not None and role not in GROUPS:
        raise ValueError("INVALID_ROLE")
    engine = create_engine(
        metadata_engine.url.set(database=name),
        hide_parameters=True,
        echo=False,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    if role:

        @event.listens_for(engine, "connect")
        def assign(connection, _record):
            connection.execute(f"SET ROLE {role}")
            connection.execute("SET TIME ZONE 'UTC'")
            connection.commit()

    return engine


def initialize_metadata(engine) -> None:
    with engine.begin() as c:
        c.exec_driver_sql("""CREATE TABLE IF NOT EXISTS public.demo_runs (
          run_id uuid PRIMARY KEY, request_key varchar(200) UNIQUE NOT NULL,
          request_hash char(64) NOT NULL, actor varchar(255) NOT NULL,
          trace_id char(32) NOT NULL, initial_budget numeric(38,18) NOT NULL
            CHECK(initial_budget>0 AND initial_budget<=1000000),
          strategy varchar(32) NOT NULL CHECK(strategy IN ('mock-neutral','neutral-baseline')),
          status varchar(16) NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','PAUSED','FAILED')),
          control_action varchar(8) CHECK(control_action IN ('pause','kill')),
          control_trace char(32),control_actor varchar(255),
          error_code varchar(64), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        c.exec_driver_sql("""CREATE UNIQUE INDEX IF NOT EXISTS demo_one_active
          ON public.demo_runs ((true)) WHERE status='RUNNING'""")
        c.exec_driver_sql("REVOKE ALL ON public.demo_runs FROM PUBLIC")
        for column in (
            "control_action varchar(8)",
            "control_trace char(32)",
            "control_actor varchar(255)",
        ):
            c.exec_driver_sql("ALTER TABLE public.demo_runs ADD COLUMN IF NOT EXISTS " + column)


def provision_run(engine, run_id: str):
    """Only an internally generated canonical v4 UUID reaches the DDL boundary."""
    from alembic import command
    from alembic.config import Config
    from psycopg import sql

    name = database_name(run_id)
    # Database names, owner and grants are fixed; no HTTP-controlled identifier.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        raw = c.connection.driver_connection
        raw.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(engine.url.username)
            )
        )
    run_engine = financial_engine(engine, run_id)
    with run_engine.begin() as c:
        # Close PUBLIC access before creating any financial table or seed.
        c.exec_driver_sql(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "control_api/migrations")
    )
    with run_engine.begin() as c:
        config.attributes["connection"] = c
        command.upgrade(config, "head")
        c.exec_driver_sql("""CREATE TABLE public.demo_transport_journal (
          sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          event_key varchar(255) UNIQUE NOT NULL, event_at timestamptz NOT NULL,
          kind varchar(16) NOT NULL CHECK(kind IN ('accept','fill')),
          payload jsonb NOT NULL)""")
        c.exec_driver_sql("REVOKE ALL ON public.demo_transport_journal FROM PUBLIC")
    return run_engine


def valid_trace(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("INVALID_REQUEST")
    return value
