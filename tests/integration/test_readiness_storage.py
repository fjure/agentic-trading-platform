"""RDR-03: echte isolierte PostgreSQL-Migration, Atomarität, ACLs und Restore."""

import json
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

TABLES = (
    "research.trust_version_state",
    "research.trust_decisions",
    "research.evidence_revisions",
    "research.import_receipts",
    "research.import_rejections",
    "research.commit_confirmations",
    "research.collection_plans",
    "research.plan_revisions",
    "research.due_jobs",
    "research.observation_receipts",
    "backtest.readiness_jobs",
    "backtest.readiness_reports",
    "backtest.readiness_findings",
)


def host(module="tests.integration.test_readiness_storage"):
    from tests.integration.ledger_support import GROUPS, PSQL, _admin, _runtime_privileges

    root = Path(__file__).resolve().parents[2]
    name = "atl_rdr_" + uuid4().hex[:20]
    restored = name + "_restore"
    password = secrets.token_urlsafe(32)
    groups = (*GROUPS, "atl_evidence_importer")
    _admin(
        "\n".join(
            f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{group}') "
            f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; "
            "END IF; END $$;"
            for group in groups
        )
    )
    _admin(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOINHERIT")
    created = restore_created = False
    try:
        _admin("\n".join(f"GRANT {group} TO {name} WITH ADMIN OPTION;" for group in groups))
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
            raise RuntimeError("readiness PostgreSQL guest failed: " + result.stdout.strip()[:600])
        dumped = subprocess.run(
            [*PSQL[:11], "pg_dump", "-U", "lab_migration", "-d", name, "--no-owner"],
            capture_output=True,
            text=True,
            check=False,
        )
        if dumped.returncode or not dumped.stdout:
            raise RuntimeError("readiness backup failed; diagnostic redacted")
        _admin(f"CREATE DATABASE {restored} OWNER {name}")
        restore_created = True
        if "CREATE SCHEMA public;" in dumped.stdout:
            _admin("DROP SCHEMA public CASCADE", database=restored)
        _admin(dumped.stdout, database=restored, restore_diagnostic=True)

        def fingerprint(database):
            return _admin(
                " UNION ALL ".join(
                    f"SELECT '{table}',md5(COALESCE(jsonb_agg(to_jsonb(r) ORDER BY "
                    f"to_jsonb(r)::text)::text,'[]')) FROM {table} r"
                    for table in TABLES
                )
                + " ORDER BY 1",
                database=database,
            )

        assert fingerprint(name) == fingerprint(restored)
        assert _runtime_privileges(name) == _runtime_privileges(restored)
        assert _admin("SELECT count(*) FROM public.audit_events", database=name) == (
            _admin("SELECT count(*) FROM public.audit_events", database=restored)
        )
        return result.stdout.strip() + "/restore"
    finally:
        if restore_created:
            _admin(f"DROP DATABASE {restored} WITH (FORCE)")
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE)")
        _admin(f"DROP ROLE {name}")


def guest():
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.research.evidence_storage import (
        EvidenceInput,
        EvidenceStore,
        read_trust_snapshot,
        unknown_trust,
    )
    from packages.research.models import AssetClass, Instrument
    from packages.research.storage import ResearchStore
    from tests.security.test_readiness_roles import verify_roles

    credentials = json.loads(sys.stdin.read())
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=credentials["role"],
            password=credentials["password"],
            host="postgres",
            port=5432,
            database=credentials["database"],
            query={"connect_timeout": "5"},
        ),
        hide_parameters=True,
    )
    config = Config("alembic.ini")

    def financial_acl(connection):
        return tuple(
            connection.execute(
                text("""
          SELECT r.rolname,c.relname,has_table_privilege(r.oid,c.oid,'SELECT'),
            has_table_privilege(r.oid,c.oid,'INSERT'),has_table_privilege(r.oid,c.oid,'UPDATE'),
            has_table_privilege(r.oid,c.oid,'DELETE')
          FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE r.rolname IN ('atl_execution','atl_worker','atl_control_api','atl_watchdog',
            'atl_backtest_worker') AND n.nspname='public' AND c.relkind IN ('r','p','v','m')
          ORDER BY r.rolname,c.relname
        """)
            )
        )

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0029_backtest_jobs")
        original_financial_acl = financial_acl(connection)
        command.upgrade(config, "0030_readiness_storage")
        assert financial_acl(connection) == original_financial_acl
    store = EvidenceStore()
    now = datetime.now(UTC)
    start = now - timedelta(days=10)
    instrument = Instrument(uuid4(), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
    with engine.begin() as connection:
        ResearchStore().add_instrument(connection, instrument)
        baseline = connection.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
        assert not connection.execute(
            text("SELECT count(*) FROM research.trust_decisions")
        ).scalar_one()

    def entry(key="one", fingerprint="a" * 64):
        return EvidenceInput(
            uuid4(),
            "synthetic-source",
            key,
            "CORPORATE_ACTION",
            instrument.instrument_id,
            start,
            start + timedelta(days=1),
            now,
            None,
            1,
            None,
            fingerprint,
            {"type": "CORPORATE_ACTION_COVERAGE", "conclusion": "NO_ACTIONS", "actions": []},
            unknown_trust(),
            {"content": {"keyId": "synthetic-key"}},
        )

    initial = entry()
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        first = store.append_bundle(connection, "synthetic-one", "1" * 64, (initial,), 0)
        assert first.ingested_at >= now and first.commit_confirmed_at is None
        try:
            with connection.begin_nested():
                store.confirm_commit(connection, first.receipt_id)
        except DBAPIError:
            pass
        else:
            raise AssertionError("same-transaction confirmation accepted")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        confirmed = store.confirm_commit(connection, first.receipt_id)
        assert confirmed >= first.ingested_at
        again = store.append_bundle(connection, "synthetic-one", "1" * 64, (initial,), 0)
        assert again.receipt_id == first.receipt_id and again.commit_confirmed_at == confirmed
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.append_bundle(connection, "synthetic-one", "2" * 64, (entry(),), 0)
    except DBAPIError:
        pass
    else:
        raise AssertionError("conflicting bundle accepted")
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.append_bundle(
                connection,
                "synthetic-invalid",
                "3" * 64,
                (
                    entry("partial"),
                    replace(entry("future"), source_observed_at=now + timedelta(days=1)),
                ),
                0,
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("future source observation accepted")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        version_before = connection.execute(
            text("SELECT version FROM research.version_state WHERE singleton")
        ).scalar_one()
        rejection_id = store.record_rejection(connection, "FUTURE_SOURCE_OBSERVATION")
        rejection = connection.execute(
            text("""
          SELECT code,recorded_at FROM research.import_rejections WHERE rejection_id=:id
        """),
            {"id": rejection_id},
        ).one()
        assert rejection.code == "FUTURE_SOURCE_OBSERVATION" and rejection.recorded_at >= now
        assert (
            connection.execute(
                text("SELECT version FROM research.version_state WHERE singleton")
            ).scalar_one()
            == version_before
        )
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.evidence_revisions")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(text("SELECT count(*) FROM research.import_receipts")).scalar_one()
            == 1
        )
        historic = read_trust_snapshot(connection, knowledge_at=start)
        assert not historic["records"]
    revised = replace(
        initial,
        evidence_id=uuid4(),
        revision=2,
        predecessor_id=initial.evidence_id,
        fingerprint="b" * 64,
        statement={"type": "CORPORATE_ACTION_COVERAGE", "conclusion": "UNKNOWN", "actions": []},
    )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        correction = store.append_bundle(connection, "synthetic-revision", "4" * 64, (revised,), 0)
        assert store.resolve_predecessor(connection, "synthetic-source", "one", "a" * 64) == (
            initial.evidence_id
        )
    late = entry("late")
    with engine.connect() as writer:
        transaction = writer.begin()
        writer.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        pending = store.append_bundle(writer, "synthetic-late", "5" * 64, (late,), 0)
        with engine.connect() as reader:
            during = read_trust_snapshot(reader)
            assert during["data_version"] < pending.commit_version
            assert str(late.evidence_id) not in {r["reference"] for r in during["records"]}
        transaction.commit()

    concurrent_entry = entry("concurrent")

    def append_concurrent(_):
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            return store.append_bundle(
                connection, "synthetic-concurrent", "6" * 64, (concurrent_entry,), 0
            ).receipt_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert len(set(executor.map(append_concurrent, range(2)))) == 1
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.append_bundle(connection, "synthetic-concurrent", "1" * 64, (initial,), 0)
    except DBAPIError:
        pass
    else:
        raise AssertionError("bundle key redirected to a different existing receipt")
    calendar_entry = replace(
        entry("calendar", "c" * 64),
        kind="CALENDAR",
        statement={
            "type": "CALENDAR_SESSIONS",
            "calendarVersion": "synthetic-calendar-v1",
            "sessions": [
                {
                    "sessionDate": start.date().isoformat(),
                    "opensAt": start.isoformat(),
                    "closesAt": (start + timedelta(hours=6)).isoformat(),
                }
            ],
        },
    )
    quality_entry = replace(
        entry("quality", "d" * 64),
        kind="QUALITY",
        statement={
            "type": "QUALITY_OBSERVATION",
            "quality": "VALID",
            "ruleVersion": "research-quality-v1",
            "reason": "NORMALIZED_RULES_PASSED",
        },
    )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        combined = store.append_bundle(
            connection, "synthetic-combined", "8" * 64, (calendar_entry, quality_entry), 0
        )
        rows = connection.execute(
            text("""
          SELECT commit_version,ingested_at FROM research.evidence_revisions
          WHERE evidence_id=ANY(:ids)
        """),
            {"ids": list(combined.entry_ids)},
        ).all()
        assert len(rows) == 2 and all(
            row == (combined.commit_version, combined.ingested_at) for row in rows
        )
        assert all(result["status"] == "UNKNOWN" for result in unknown_trust().values())
    from packages.research.calendar_storage import read_evidence_calendar_rows

    with engine.connect() as connection:
        rows = read_evidence_calendar_rows(connection, combined.commit_version, datetime.now(UTC))
        assert len(rows) == 1 and rows[0]["evidence_id"] == calendar_entry.evidence_id
        assert rows[0]["managed_trust_state"] == "TIME_UNPROVEN"
    due_id, observation_id = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_execution"))
        connection.execute(
            text("""
          INSERT INTO research.collection_plans(instrument_id) VALUES(:instrument);
        """),
            {"instrument": instrument.instrument_id},
        )
        connection.execute(
            text("""
          INSERT INTO research.due_jobs(due_id,instrument_id,generation,purpose,due_at,
            start_at,end_at) VALUES(:due,:instrument,0,'CALENDAR',:start,:start,:end)
        """),
            dict(
                due=due_id,
                instrument=instrument.instrument_id,
                start=start,
                end=start + timedelta(days=1),
            ),
        )
        observed = connection.execute(
            text("""
          INSERT INTO research.observation_receipts(receipt_id,due_id,instrument_id,generation,
            purpose,due_at,fence,state,first_observed_at,ingested_at,commit_version,transaction_id)
          VALUES(:id,:due,:instrument,0,'CALENDAR',:start,1,'STARTED',:observed,:start,0,'forged')
          RETURNING ingested_at,commit_version,transaction_id
        """),
            dict(
                id=observation_id,
                due=due_id,
                instrument=instrument.instrument_id,
                start=start,
                observed=now,
            ),
        ).one()
        assert observed.ingested_at >= now and observed.commit_version > combined.commit_version
        assert observed.transaction_id != "forged"
        try:
            with connection.begin_nested():
                store.confirm_commit(connection, observation_id, "OBSERVATION")
        except DBAPIError:
            pass
        else:
            raise AssertionError("same-XID observation confirmation accepted")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_execution"))
        assert (
            store.confirm_commit(connection, observation_id, "OBSERVATION") >= observed.ingested_at
        )
        try:
            with connection.begin_nested():
                store.confirm_commit(connection, first.receipt_id)
        except DBAPIError:
            pass
        else:
            raise AssertionError("execution confirmed certified import")
        invalid_clock_receipt = uuid4()
        invalid_clock = connection.execute(
            text("""
          INSERT INTO research.observation_receipts(receipt_id,due_id,dispatch_id,instrument_id,
            generation,purpose,due_at,fence,state,first_observed_at)
          VALUES(:id,:due,:dispatch,:instrument,0,'CALENDAR',:due_at,1,'COMPLETED',:observed)
          RETURNING first_observed_at,ingested_at
        """),
            dict(
                id=invalid_clock_receipt,
                due=due_id,
                dispatch=observation_id,
                instrument=instrument.instrument_id,
                due_at=start,
                observed=now + timedelta(days=1),
            ),
        ).one()
        assert invalid_clock.first_observed_at > invalid_clock.ingested_at
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.confirm_commit(connection, observation_id, "OBSERVATION")
    except DBAPIError:
        pass
    else:
        raise AssertionError("certifier confirmed an execution observation")
    decision = {
        "decisionId": str(uuid4()),
        "sourceId": "synthetic-source",
        "keyId": "synthetic-key",
        "witnessId": None,
        "authorityType": "SOURCE",
        "effectiveAt": start,
        "validFrom": start,
        "validTo": now + timedelta(days=1),
        "revoked": True,
        "compromisedAt": None,
        "compromiseTimeKnown": False,
        "policyVersion": "synthetic-v1",
        "reason": "SYNTHETIC_REVOCATION",
        "scope": {"instrumentIds": [str(instrument.instrument_id)], "kinds": ["CORPORATE_ACTION"]},
        "publicKey": "A" * 43 + "=",
        "algorithm": "Ed25519",
        "methods": ["RFC8785-UTF8-v1"],
        "actorRef": "synthetic-operator",
        "documentFingerprint": "d" * 64,
        "testOnly": True,
        "predecessorDecisionId": None,
    }
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        appended = store.append_trust_decision(connection, decision, 0)
        assert appended["version"] == 1
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        # Replay bleibt auch nach Truständerung exakt derselbe bestätigte Receipt.
        assert (
            store.append_bundle(connection, "synthetic-one", "1" * 64, (initial,), 0).receipt_id
            == first.receipt_id
        )
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.append_bundle(connection, "synthetic-stale-trust", "7" * 64, (entry("stale"),), 0)
    except DBAPIError:
        pass
    else:
        raise AssertionError("stale trust snapshot accepted")
    with engine.connect() as connection:
        state = read_trust_snapshot(connection, snapshot_version=first.commit_version)
        assert state["version"] == 1 and all(item["revoked"] for item in state["records"])
        assert (
            connection.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
            == baseline
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM research.evidence_revisions")
            ).scalar_one()
            == 6
        )
        assert correction.commit_version > first.commit_version
    for table in (
        "research.evidence_revisions",
        "research.trust_decisions",
        "research.import_receipts",
        "research.commit_confirmations",
        "research.import_rejections",
    ):
        assignment = "code='REWRITTEN'" if table.endswith("rejections") else "commit_version=0"
        for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET {assignment}"):
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
            except DBAPIError:
                pass
            else:
                raise AssertionError("immutable evidence changed")
    import hashlib

    admission = {
        **decision,
        "decisionId": str(uuid4()),
        "sourceId": "synthetic-validity-source",
        "keyId": "synthetic-validity-key",
        "revoked": False,
        "compromiseTimeKnown": True,
        "predecessorDecisionId": None,
    }
    signed_at = start + timedelta(days=1)
    positive_source = {name: dict(result) for name, result in unknown_trust().items()}
    positive_source["sourceAuthenticity"] = {
        "status": "VERIFIED",
        "method": "synthetic-signature-fixture",
        "ruleVersion": "synthetic-v1",
        "reason": "SYNTHETIC_SOURCE_VALID",
        "evidenceRefs": [
            {
                "kind": "KEY",
                "id": admission["keyId"],
                "revision": "2",
                "fingerprint": hashlib.sha256(bytes(32)).hexdigest(),
            }
        ],
    }
    validity_entry = replace(
        entry("validity", "e" * 64),
        source_id=admission["sourceId"],
        source_observed_at=signed_at,
        trust=positive_source,
        provenance={"content": {"keyId": admission["keyId"], "signedAt": signed_at.isoformat()}},
    )
    bad_reference = replace(
        validity_entry,
        evidence_id=uuid4(),
        evidence_key="bad-key-digest",
        fingerprint="f" * 64,
        trust={
            **positive_source,
            "sourceAuthenticity": {
                **positive_source["sourceAuthenticity"],
                "evidenceRefs": [
                    {
                        **positive_source["sourceAuthenticity"]["evidenceRefs"][0],
                        "fingerprint": "f" * 64,
                    }
                ],
            },
        },
    )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        store.append_trust_decision(connection, admission, 1)
        store.append_bundle(
            connection, "synthetic-validity", "9" * 64, (validity_entry, bad_reference), 2
        )
        current = read_trust_snapshot(connection)
        assert not next(
            r for r in current["records"] if r["reference"] == str(validity_entry.evidence_id)
        )["revoked"]
        assert next(
            r for r in current["records"] if r["reference"] == str(bad_reference.evidence_id)
        )["revoked"]
    narrowed = {
        **admission,
        "decisionId": str(uuid4()),
        "validFrom": now,
        "predecessorDecisionId": admission["decisionId"],
    }
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        store.append_trust_decision(connection, narrowed, 2)
        current = read_trust_snapshot(connection)
        assert next(
            r for r in current["records"] if r["reference"] == str(validity_entry.evidence_id)
        )["revoked"]
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
            store.append_trust_decision(
                connection,
                {
                    **narrowed,
                    "decisionId": str(uuid4()),
                    "predecessorDecisionId": narrowed["decisionId"],
                    "publicKey": "AQ" + "A" * 41 + "=",
                },
                3,
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("same key identity changed public key")
    verify_roles(engine)
    engine.dispose()
    print(
        "PASS readiness migration/atomic/replay/revision/DB-time/late-commit/concurrent/trust/roles"
    )


def test_readiness_storage_actual_postgresql():
    assert host().endswith("/trust/roles/restore")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own_frames = [frame for frame in frames if frame.filename.startswith("/opt/lab/")]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own_frames[-4:])
        original = getattr(error, "orig", None)
        diagnostic = getattr(original, "diag", None)
        primary = getattr(diagnostic, "message_primary", "") or ""
        print("FAIL " + type(error).__name__ + " " + where + " " + primary[:180])
        raise SystemExit(1) from None
