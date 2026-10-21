"""One-shot operator provisioning, confined to the local Fake laboratory.

Admin and generated passwords remain inside this process. Existing role secret
files are checked by identity only and are never read or rotated implicitly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import stat
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .config import ROLES

SECRET_ROOT = Path("/run/provision")
ADMIN_SECRET = Path("/run/secrets/postgres_password")
FILES = {
    "execution": "execution.json",
    "worker": "worker.json",
    "api": "control.json",
    "watchdog": "watchdog.json",
}


def _write_new_secret(path, username, password):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump({"username": username, "password": password}, target, separators=(",", ":"))
        target.flush()
        os.fsync(target.fileno())


def _private_logins(connection, directory):
    from psycopg import sql

    for role, group in ROLES.items():
        login = group + "_login"
        path = directory / FILES[role]
        if path.is_symlink():
            raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
        row = connection.execute(
            """SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
            rolinherit,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s""",
            (login,),
        ).fetchone()
        if row is None:
            if path.exists():
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
            password = secrets.token_urlsafe(48)
            # Suppress statement echo on this private provisioning connection;
            # neither a SQL failure nor SDK diagnostics may reveal a password.
            connection.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
            connection.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN FALSE, INHERIT FALSE, SET TRUE").format(
                    sql.Identifier(group), sql.Identifier(login)
                )
            )
            _write_new_secret(path, login, password)
        else:
            if not path.is_file() or row != (True, False, False, False, False, False, False):
                raise PermissionError("PRIVATE_CONFIGURATION_INCOMPLETE")
        memberships = connection.execute(
            """SELECT r.rolname,m.admin_option,m.inherit_option,
            m.set_option FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
            JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s""",
            (login,),
        ).fetchall()
        if memberships != [(group, False, False, True)]:
            raise PermissionError("DATABASE_ROLE_MEMBERSHIP_UNSAFE")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
        # Docker Desktop preserves Linux owner/mode metadata on generated bind
        # files. Only this one-shot provisioner has CHOWN/FOWNER; services have
        # neither capability and receive exactly their own read-only secret.
        os.chown(path, 10001, 10001, follow_symlinks=False)
        os.chmod(path, 0o600, follow_symlinks=False)
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE lab_runtime TO {}").format(sql.Identifier(login))
        )


def seed(ledger, initial_cash: Decimal):
    """A technical simulation basis is explicitly stale, pending and disabled."""
    from sqlalchemy import text

    from packages.domain import models as m
    from packages.risk.engine import RiskLimits

    with ledger.transaction() as tx:
        c = tx.connection
        c.execute(text("SELECT pg_advisory_xact_lock(hashtext('atl-runtime-seed'))"))
        existing = c.execute(text("SELECT id,currency FROM public.accounts")).all()
        if existing:
            if existing != [("paper", "USD")]:
                raise ValueError("RUNTIME_ACCOUNT_MISMATCH")
            if (
                c.execute(
                    text("""SELECT count(*) FROM public.risk_account_context
                WHERE account_id='paper' """)
                ).scalar_one()
                != 1
            ):
                raise ValueError("RUNTIME_STATE_INCOMPLETE")
            return
        now = datetime.now(UTC)
        trace = uuid.uuid4().hex
        actor = "local-fake-provisioner"

        def audit(entity_id, action="runtime.initialize"):
            event = m.AuditEvent(
                uuid.uuid4().hex, now, actor, action, ("SIMULATION_BOOTSTRAP",), entity_id, 1, trace
            )
            tx.insert(event)
            return event.id

        audit("paper")
        c.execute(
            text("INSERT INTO public.accounts(id,currency,ledger_version) VALUES ('paper','USD',1)")
        )
        for symbol in ("SPY", "QQQ"):
            tx.insert(m.Instrument(symbol, m.Market.US_EQUITY, symbol, "ETF", "USD", 1, "XNYS"))
            position_id = "runtime-position-" + symbol
            audit(position_id)
            c.execute(
                text("""INSERT INTO public.positions
                (id,instrument_id,currency,quantity,average_price,realized_pnl,
                 ledger_version,account_id)
                VALUES (:id,:instrument,'USD',0,NULL,0,1,'paper')"""),
                {"id": position_id, "instrument": symbol},
            )
        snapshot_id = "runtime-initial-portfolio"
        audit(snapshot_id)
        tx.insert(
            m.PortfolioSnapshot(
                snapshot_id,
                "USD",
                initial_cash,
                initial_cash,
                Decimal("0"),
                now,
                1,
                m.Quality.STALE,
                m.Reconciliation.PENDING,
            )
        )
        policy_id = "runtime-initial-risk-policy"
        policy_audit = audit(policy_id, "risk.policy.initialize")
        limits = RiskLimits()
        tx.insert(
            m.RiskPolicy(
                policy_id,
                1,
                "USD",
                limits.max_positions_per_instrument,
                limits.max_positions_total,
                limits.max_position_fraction,
                limits.max_daily_loss_fraction,
                now,
                actor,
                policy_audit,
            )
        )
        # Same transaction audit for account context; no instrument session or
        # provider capability is invented by local provisioning.
        c.execute(
            text("""INSERT INTO public.risk_account_context
            (account_id,version,portfolio_snapshot_id,portfolio_version,risk_config_version,
             day_start_at,day_start_equity,day_start_realized_pnl,mode,safety_state,
             reconciliation_current,valid_until,actor_id,trace_id)
            VALUES ('paper',1,:snapshot,1,:config,:day,:cash,0,'DISABLED','PAUSE_NEW',
                    false,:until,:actor,:trace)"""),
            {
                "snapshot": snapshot_id,
                "config": limits.config_version,
                "day": now.replace(hour=0, minute=0, second=0, microsecond=0),
                "cash": initial_cash,
                "until": now + timedelta(hours=24),
                "actor": actor,
                "trace": trace,
            },
        )


def provision(initial_cash: Decimal):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine

    from packages.persistence.ledger import PostgresLedger

    # The launcher verifies owner-private host ACLs before this mount is writable.
    if SECRET_ROOT.is_symlink() or not SECRET_ROOT.is_dir():
        raise PermissionError("PRIVATE_CONFIGURATION_UNSAFE")
    password = ADMIN_SECRET.read_text("utf-8").strip()
    if not password or len(password) > 1024 or any(ord(c) < 32 for c in password):
        raise ValueError("PRIVATE_CONFIGURATION_INVALID")
    common = dict(
        host="postgres", port=5432, user="lab_migration", password=password, connect_timeout=5
    )
    with psycopg.connect(dbname="lab", autocommit=True, **common) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        row = connection.execute("""SELECT pg_get_userbyid(datdba) FROM pg_database
            WHERE datname='lab_runtime' """).fetchone()
        if row is None:
            connection.execute("CREATE DATABASE lab_runtime OWNER lab_migration")
        elif row != ("lab_migration",):
            raise PermissionError("RUNTIME_DATABASE_OWNER_MISMATCH")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="lab_migration",
            password=password,
            host="postgres",
            port=5432,
            database="lab_runtime",
        ),
        hide_parameters=True,
        echo=False,
        connect_args={"connect_timeout": 5},
    )
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "control_api/migrations")
    )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    seed(PostgresLedger(engine=engine), initial_cash)
    with psycopg.connect(dbname="lab_runtime", **common) as connection:
        connection.execute("SET log_statement='none'")
        connection.execute("SET log_min_error_statement='panic'")
        connection.execute("REVOKE ALL ON DATABASE lab_runtime FROM PUBLIC")
        _private_logins(connection, SECRET_ROOT)
    engine.dispose()
    from services.runtime.provision_demo import initialize_provider_volumes, provision_demo

    provision_demo(common, SECRET_ROOT)
    from services.runtime.provision_paper import provision_paper

    provision_paper(common, SECRET_ROOT)
    initialize_provider_volumes()


def main(argv=None):
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-cash", default="10000")
    args = parser.parse_args(argv)
    try:
        from packages.domain.models import decimal_value

        cash = decimal_value(args.initial_cash)
        if not Decimal("0") < cash <= Decimal("1000000"):
            raise ValueError("INVALID_SIMULATION_BASIS")
        provision(cash)
    except Exception:
        print('{"status":"UNAVAILABLE","code":"PROVISIONING_FAILED"}', file=sys.stderr)
        return 1
    print('{"status":"PROVISIONED","mode":"DISABLED","provider":"fake"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
