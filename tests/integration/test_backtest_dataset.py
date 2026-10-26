"""Real isolated PostgreSQL snapshot, keyset, schedule and immutable proof checks."""

import json
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def host(module="tests.integration.test_backtest_dataset"):
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    name = "atl_bkt_" + uuid4().hex[:20]
    password = secrets.token_urlsafe(32)
    _admin(
        "DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atl_backtest_worker') "
        "THEN CREATE ROLE atl_backtest_worker NOLOGIN NOSUPERUSER NOCREATEDB "
        "NOCREATEROLE NOINHERIT; END IF; END $$"
    )
    _admin(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOINHERIT")
    created = False
    try:
        _admin(
            "\n".join(
                f"GRANT {group} TO {name} WITH ADMIN OPTION;"
                for group in GROUPS
            )
        )
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
        for directory in ("packages", "services", "tests", "contracts"):
            command += [
                "--mount",
                f"type=bind,source={root / directory},target=/opt/lab/{directory},readonly",
            ]
        command += [
            "--mount",
            f"type=bind,source={root / 'alembic.ini'},target=/opt/lab/alembic.ini,readonly",
            "agentic-trading-lab-research-test:local",
            "-m",
            module,
            "guest",
        ]
        result = subprocess.run(
            command,
            input=json.dumps(dict(database=name, role=name, password=password)),
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("backtest PostgreSQL guest failed: " + result.stdout.strip()[:600])
        return result.stdout.strip()
    finally:
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE)")
        _admin(f"DROP ROLE {name}")


def guest_engine():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

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
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


def guest():
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.calendar import read_schedule_rows, schedule_at
    from packages.backtesting.coverage import read_coverage
    from packages.backtesting.dataset import (
        DatasetPort,
        FrozenDataset,
        materialize_fold,
    )
    from packages.backtesting.models import Fold
    from packages.research.calendar_storage import read_calendar
    from packages.research.models import (
        AssetClass,
        BarIdentity,
        BarInterval,
        BarRevision,
        Instrument,
        PointInTimeQuery,
        SymbolMapping,
    )
    from packages.research.query import ResearchQuery
    from packages.research.storage import ResearchStore, identity_parameters

    engine = guest_engine()
    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    store, port = ResearchStore(), DatasetPort()
    with engine.begin() as conn:
        store.add_instrument(conn, instrument)
        for provider in ("alpaca", "other"):
            store.add_mapping(
                conn,
                SymbolMapping(
                    instrument.instrument_id, provider, "SPY", start - timedelta(days=40)
                ),
            )
    bars = []
    for n in range(6):
        opening = start + timedelta(minutes=15 * n)
        closing = opening + timedelta(minutes=15)
        bars.append(
            BarRevision(
                uuid4(),
                BarIdentity(
                    instrument.instrument_id,
                    "alpaca",
                    "iex",
                    BarInterval.FIFTEEN_MINUTES,
                    opening,
                    closing,
                ),
                *(Decimal("100"),) * 4,
                Decimal("10"),
                closing + timedelta(seconds=30),
                closing + timedelta(seconds=30),
                commit_version=10 + n,
            )
        )
    correction = replace(
        bars[0],
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        close=Decimal("101"),
        high=Decimal("101"),
        ingested_at=start + timedelta(minutes=17),
        available_at=start + timedelta(minutes=17),
        commit_version=30,
    )
    other = replace(
        bars[0],
        revision_id=uuid4(),
        identity=replace(bars[0].identity, provider="other"),
        commit_version=31,
    )
    coverage_id = uuid4()
    # These are explicit synthetic past observations in a disposable database.
    # Production stamps remain enforced; no production data is backdated.
    with engine.begin() as conn:
        for table in (
            "research.bar_revisions",
            "research.calendar_versions",
            "backtest.corporate_action_coverage",
        ):
            conn.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER a_stamp"))
        for bar in (*bars, correction, other):
            params = dict(
                identity_parameters(bar.identity),
                id=bar.revision_id,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                fingerprint=bar.fingerprint,
                local_revision=bar.local_revision,
                predecessor=bar.predecessor_id,
                ingested=bar.ingested_at,
                version=bar.commit_version,
            )
            conn.execute(
                text("""INSERT INTO research.bar_revisions
                (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,
                 bar_end,adjustment,open,high,low,close,volume,fingerprint,local_revision,
                 predecessor_id,quality,commit_version,ingested_at)
                VALUES(:id,:instrument_id,:provider,'SPY',:feed,:interval,:bar_start,
                 :bar_end,:adjustment,:open,:high,:low,:close,:volume,:fingerprint,
                 :local_revision,:predecessor,'VALID',:version,:ingested)"""),
                params,
            )
        # DST transition, normal/early close and a later historical correction.
        for day, hour, minutes in ((19, 13, 120), (30, 13, 210)):
            opening = datetime(2026, 10, day, hour, 30, tzinfo=UTC)
            conn.execute(
                text("""INSERT INTO research.calendar_versions
                VALUES(:id,:day,:opening,:closing,:version,'alpaca-v2-calendar',1,:ingested)"""),
                dict(
                    id=uuid4(),
                    day=opening.date(),
                    opening=opening,
                    closing=opening + timedelta(minutes=minutes),
                    version=f"{day:064x}",
                    ingested=start - timedelta(days=3),
                ),
            )
        nov = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
        conn.execute(
            text("""INSERT INTO research.calendar_versions
            VALUES(:id,:day,:opening,:closing,:version,'alpaca-v2-calendar',2,:ingested)"""),
            dict(
                id=uuid4(),
                day=nov.date(),
                opening=nov,
                closing=nov + timedelta(hours=6, minutes=30),
                version="d" * 64,
                ingested=start - timedelta(days=3),
            ),
        )
        conn.execute(
            text("""INSERT INTO research.calendar_versions
            VALUES(:id,:day,:opening,:closing,:version,'alpaca-v2-calendar',40,:ingested)"""),
            dict(
                id=uuid4(),
                day=start.date(),
                opening=start,
                closing=start + timedelta(hours=1),
                version="c" * 64,
                ingested=start + timedelta(hours=3),
            ),
        )
        conn.execute(
            text("""INSERT INTO backtest.corporate_action_coverage
            VALUES(:id,:instrument,:start,:end,:certified,'offline-certified-fixture',
                   :fingerprint,true,3,:ingested)"""),
            dict(
                id=coverage_id,
                instrument=instrument.instrument_id,
                start=start - timedelta(days=1),
                end=nov + timedelta(days=1),
                certified=start - timedelta(days=3),
                fingerprint="f" * 64,
                ingested=start - timedelta(days=2),
            ),
        )
        for table in (
            "research.bar_revisions",
            "research.calendar_versions",
            "backtest.corporate_action_coverage",
        ):
            conn.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER a_stamp"))
        conn.execute(text("UPDATE research.version_state SET version=50"))
    with engine.connect() as conn:
        snapshot = port.snapshot(conn)
        frozen = port.freeze(
            conn, instrument.instrument_id, start, start + timedelta(minutes=90), snapshot
        )
        assert len(frozen.revisions) == 8
        # Natural identities share both end and local revision at page boundaries.
        after, paged = None, []
        while True:
            page = port.page(
                conn,
                instrument.instrument_id,
                start,
                start + timedelta(minutes=90),
                snapshot,
                limit=1,
                after=after,
            )
            paged.extend(page.items)
            after = page.next_position
            if after is None:
                break
        assert tuple(paged) == frozen.revisions
        assert len({b.revision_id for b in paged}) == 8
        oracle, sweep = ResearchQuery(), frozen.sweep()
        for n in range(16, 92):
            knowledge = start + timedelta(minutes=n)
            q = PointInTimeQuery(knowledge, knowledge, snapshot.at, snapshot.version)
            actual = oracle.list_bars(
                conn,
                instrument.instrument_id,
                start,
                start + timedelta(minutes=90),
                BarInterval.FIFTEEN_MINUTES,
                q,
                limit=200,
            ).items
            assert sweep.visible(knowledge, knowledge) == actual
        rows = read_schedule_rows(conn, snapshot)
        intraday = start + timedelta(minutes=16)
        schedule = schedule_at(rows, intraday, snapshot)
        old = read_calendar(
            conn,
            start,
            start + timedelta(hours=2),
            PointInTimeQuery(intraday, intraday, snapshot.at, snapshot.version),
        )
        assert not old.sessions and len(schedule.sessions) == 3
        assert schedule.sessions[0].closes_at == start + timedelta(hours=2)
        assert schedule.sessions[1].closes_at - schedule.sessions[1].opens_at == timedelta(
            minutes=210
        )
        assert schedule.sessions[2].opens_at.hour == 14  # persisted DST shift
        changed = schedule_at(rows, start + timedelta(hours=4), snapshot)
        assert changed.sessions[0].closes_at == start + timedelta(hours=1)
        proofs = read_coverage(conn, instrument.instrument_id, snapshot)
        only_alpaca = FrozenDataset(
            snapshot, tuple(b for b in frozen.revisions if b.identity.provider == "alpaca")
        )
        fold = Fold(start - timedelta(minutes=30), start, start, start + timedelta(minutes=90))
        dataset = materialize_fold(
            only_alpaca, fold, instrument.instrument_id, 0, rows, coverage_proofs=proofs
        )
        assert dataset.steps[0].signal_bar.revision_id == bars[0].revision_id
        assert dataset.outcome_bars[0].revision_id == correction.revision_id
        assert dataset.steps[0].fill_target == start + timedelta(minutes=30)
        assert dataset.coverage_ids == (str(coverage_id),)
    # A real late commit has a higher cap, including a writer open across snapshot.
    with engine.begin() as writer:
        late = replace(
            bars[1],
            revision_id=uuid4(),
            local_revision=2,
            predecessor_id=bars[1].revision_id,
            close=Decimal("102"),
            high=Decimal("102"),
        )
        store.append_bar(writer, late, symbol="SPY")
        with engine.connect() as reader:
            bound = port.snapshot(reader)
            assert bound.version == snapshot.version
    with engine.connect() as reader:
        old = port.freeze(
            reader, instrument.instrument_id, start, start + timedelta(minutes=90), bound
        )
        assert old.revisions == frozen.revisions
        newer = port.freeze(
            reader,
            instrument.instrument_id,
            start,
            start + timedelta(minutes=90),
            port.snapshot(reader),
        )
        assert len(newer.revisions) == 9
    for sql in (
        "UPDATE backtest.corporate_action_coverage SET no_actions=false",
        "DELETE FROM backtest.corporate_action_coverage",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except DBAPIError:
            pass
        else:
            raise AssertionError("immutable offline coverage")
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE atl_backtest_worker"))
        assert (
            conn.execute(text("SELECT count(*) FROM backtest.corporate_action_coverage")).scalar()
            == 1
        )
        assert not conn.execute(
            text(
                "SELECT has_table_privilege(current_user,"
                "'backtest.corporate_action_coverage','INSERT')"
            )
        ).scalar()
    engine.dispose()
    print("PASS backtest PG snapshot/keyset/oracle/schedule/DST/earlyclose/cutoffs/coverage")


def test_backtest_dataset_real_postgresql():
    assert host().startswith("PASS backtest PG snapshot/keyset/oracle/schedule")


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
