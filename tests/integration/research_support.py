"""Isolated real PostgreSQL guest; generated test-only credentials never leave stdin."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def host():
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    name = "atl_research_" + uuid4().hex[:20]
    password = secrets.token_urlsafe(32)
    _admin(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOINHERIT")
    created = False
    try:
        _admin("\n".join(f"GRANT {group} TO {name} WITH ADMIN OPTION;" for group in GROUPS))
        _admin(f"CREATE DATABASE {name} OWNER {name}")
        created = True
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "agentic-trading-lab-dev_local",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "--workdir",
            "/opt/lab",
            "--entrypoint",
            "python",
        ]
        for source, target in [
            (root / "packages", "/opt/lab/packages"),
            (root / "services", "/opt/lab/services"),
            (root / "alembic.ini", "/opt/lab/alembic.ini"),
            (
                root / "tests/integration/research_jobs_support.py",
                "/opt/lab/research_jobs_support.py",
            ),
            (
                root / "tests/integration/research_vertical_support.py",
                "/opt/lab/research_vertical_support.py",
            ),
            (root / "contracts", "/opt/lab/contracts"),
            (
                root / "tests/integration/research_quality_support.py",
                "/opt/lab/research_quality_support.py",
            ),
            (
                root / "tests/integration/research_observability_support.py",
                "/opt/lab/research_observability_support.py",
            ),
            (
                root / "tests/integration/research_resume_support.py",
                "/opt/lab/research_resume_support.py",
            ),
            (Path(__file__), "/opt/lab/research_support.py"),
        ]:
            command += ["--mount", f"type=bind,source={source},target={target},readonly"]
        command += ["agentic-trading-lab-research-test:local", "research_support.py", "guest"]
        result = subprocess.run(
            command,
            input=json.dumps(dict(database=name, role=name, password=password)),
            capture_output=True,
            text=True,
        )
        if result.returncode:
            # Guest deliberately emits only bounded assertion names, never SQL parameters.
            raise RuntimeError("research PG guest failed: " + result.stdout.strip()[:600])
        return result.stdout.strip()
    finally:
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE)")
        _admin(f"DROP ROLE {name}")


def guest():
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.research.models import (
        AssetClass,
        BarIdentity,
        BarInterval,
        BarRevision,
        Instrument,
        SymbolMapping,
    )
    from packages.research.query import ResearchQuery
    from packages.research.storage import ResearchStore

    credentials = json.loads(sys.stdin.read())
    url = URL.create(
        "postgresql+psycopg",
        username=credentials["role"],
        password=credentials["password"],
        host="postgres",
        port=5432,
        database=credentials["database"],
        query={"connect_timeout": "5"},
    )
    engine = create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0020_paper_read_models")
        before = conn.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
        command.upgrade(config, "head")
        assert conn.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one() == before
        assert conn.execute(text("SELECT version FROM research.version_state")).scalar_one() == 0
    execution = create_engine(url, hide_parameters=True)

    @event.listens_for(execution, "connect")
    def set_execution(dbapi, record):
        dbapi.execute("SET ROLE atl_execution")
        dbapi.commit()

    control = create_engine(url, hide_parameters=True)

    @event.listens_for(control, "connect")
    def set_control(dbapi, record):
        dbapi.execute("SET ROLE atl_control_api")
        dbapi.commit()

    store = ResearchStore()
    queries = ResearchQuery()
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    start = datetime(2026, 10, 23, 13, 30, tzinfo=UTC)
    with execution.begin() as conn:
        store.add_instrument(conn, instrument)
        store.add_mapping(
            conn,
            SymbolMapping(
                instrument.instrument_id, "alpaca", "SPY", datetime(2000, 1, 1, tzinfo=UTC)
            ),
        )

    def incoming(offset=0, close="101"):
        now = datetime.now(UTC)
        identity = BarIdentity(
            instrument.instrument_id,
            "alpaca",
            "iex",
            BarInterval.FIFTEEN_MINUTES,
            start + timedelta(minutes=offset),
            start + timedelta(minutes=offset + 15),
        )
        return BarRevision(
            uuid4(),
            identity,
            Decimal("100"),
            Decimal("105"),
            Decimal("95"),
            Decimal(close),
            Decimal("1000"),
            now,
            now,
        )

    with execution.begin() as conn:
        first = store.append_bar(conn, incoming(), symbol="SPY")
        duplicate = store.append_bar(conn, incoming(), symbol="SPY")
        second = store.append_bar(conn, incoming(15), symbol="SPY")
        assert first.revision_id == duplicate.revision_id
        assert first.fingerprint == duplicate.fingerprint
    cutoff = datetime.now(UTC)
    with control.connect() as conn:
        old = queries.snapshot(conn, cutoff, cutoff)
        page = queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            old,
            limit=1,
        )
        assert len(page.items) == 1 and page.next_position is not None
    with execution.connect() as late:
        transaction = late.begin()
        later = store.append_bar(late, incoming(30), symbol="SPY")
        # Snapshot starts while the writer has reserved a higher version but cannot be seen.
        with control.connect() as conn:
            during_time = datetime.now(UTC)
            during = queries.snapshot(conn, during_time, during_time)
            assert during.snapshot_version < later.commit_version
        transaction.commit()
    with execution.begin() as conn:
        correction = store.append_bar(conn, incoming(close="102"), symbol="SPY")
        assert correction.local_revision == 2 and correction.predecessor_id == first.revision_id
    correction_time = datetime.now(UTC)
    with execution.begin() as conn:
        reverted = store.append_bar(conn, incoming(), symbol="SPY")
        repeated = store.append_bar(conn, incoming(), symbol="SPY")
        assert reverted.fingerprint == first.fingerprint
        assert reverted.local_revision == 3 and reverted.predecessor_id == correction.revision_id
        assert reverted.revision_id != first.revision_id
        assert repeated == reverted
    with control.connect() as conn:
        before_reversion = queries.snapshot(conn, correction_time, correction_time)
        between = queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            before_reversion,
        )
        assert between.items[0].revision_id == correction.revision_id
        current_time = datetime.now(UTC)
        current = queries.snapshot(conn, current_time, current_time)
        latest = queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            current,
        )
        assert latest.items[0].revision_id == reverted.revision_id
        remaining = queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            old,
            limit=1,
            after=page.next_position,
        )
        assert remaining.items == (second,)
        stable = queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            during,
        )
        assert [item.revision_id for item in stable.items] == [
            first.revision_id,
            second.revision_id,
        ]
        historic = queries.snapshot(conn, cutoff, start + timedelta(hours=1))
        assert not queries.list_bars(
            conn,
            instrument.instrument_id,
            start,
            start + timedelta(hours=1),
            BarInterval.FIFTEEN_MINUTES,
            historic,
        ).items
    # Runtime roles cannot change immutable bars, forge committed versions, or mutate schema.
    for role in ("atl_execution", "atl_worker", "atl_control_api", "atl_watchdog"):
        for statement in [
            "UPDATE research.bar_revisions SET close=1",
            "DELETE FROM research.evidence",
            "UPDATE research.version_state SET version=0",
            "CREATE TABLE research.forbidden(id int)",
        ]:
            try:
                with engine.begin() as conn:
                    conn.execute(text("SET LOCAL ROLE " + role))
                    conn.execute(text(statement))
            except DBAPIError:
                pass
            else:
                raise AssertionError("runtime mutation unexpectedly allowed")
    # Symbol ambiguity must be denied by SQL even for an authorized writer.
    try:
        with execution.begin() as conn:
            store.add_mapping(conn, SymbolMapping(instrument.instrument_id, "alpaca", "QQQ", start))
    except (DBAPIError, ValueError):
        pass
    else:
        raise AssertionError("overlapping symbol mapping allowed")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one() == before
        assert conn.execute(text("SELECT count(*) FROM research.bar_revisions")).scalar_one() == 5
    from research_jobs_support import verify as verify_jobs

    # Explicit synthetic offline entitlement, never an assertion about personal
    # provider rights. Production defaults remain DISABLED / UNKNOWN.
    with execution.begin() as conn:
        conn.execute(
            text("""UPDATE research.source_status SET state='IMPLEMENTED',
          license_status='CONFIRMED',policy_version='offline-pg-fixture-v1'
          WHERE singleton""")
        )
    verify_jobs(engine, execution, control, instrument, start)
    from research_vertical_support import verify as verify_vertical

    verify_vertical(engine, execution, control, instrument, start)
    from research_quality_support import verify as verify_quality

    verify_quality(engine, execution, control, instrument, start)
    from research_observability_support import verify as verify_observability

    verify_observability(engine, execution, control, instrument, start)
    from research_resume_support import verify as verify_resume

    verify_resume(engine, execution, control, instrument, start)
    execution.dispose()
    control.dispose()
    engine.dispose()
    print("PASS research migration/ledger/dedupe/revision/late-commit/PIT/keyset/SQL-roles/symbols")


if __name__ == "__main__":
    if sys.argv[1:] == ["guest"]:
        try:
            guest()
        except Exception as error:
            import traceback

            frames = traceback.extract_tb(error.__traceback__)
            where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames[-3:])
            print("FAIL " + type(error).__name__ + " " + where)
            raise SystemExit(1) from None
    else:
        print(host())
