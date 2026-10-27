"""RDR-09: echte isolierte PostgreSQL-Kette, synthetische Schlüssel, kein Provider."""

import json
import secrets
import subprocess
from pathlib import Path
from uuid import uuid4


def host():
    from tests.integration.ledger_support import GROUPS, _admin

    root = Path(__file__).resolve().parents[2]
    name = "atl_rdr09_" + uuid4().hex[:18]
    password = secrets.token_urlsafe(32)
    groups = (*GROUPS, "atl_evidence_importer")
    for group in groups:
        _admin(
            f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{group}') "
            f"THEN CREATE ROLE {group} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT; END IF; END $$"
        )
    _admin(f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOINHERIT")
    created = False
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
        for directory in ("packages", "services", "tests", "contracts", "scripts"):
            command += [
                "--mount",
                f"type=bind,source={root / directory},target=/opt/lab/{directory},readonly",
            ]
        command += [
            "--mount",
            f"type=bind,source={root / 'alembic.ini'},target=/opt/lab/alembic.ini,readonly",
            "agentic-trading-lab-rdr09-test:local",
            "-m",
            "tests.integration.test_research_evidence_import",
            "guest",
        ]
        result = subprocess.run(
            command,
            input=json.dumps(dict(database=name, role=name, password=password)),
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("RDR09 PostgreSQL guest failed: " + result.stdout.strip()[:400])
        return result.stdout.strip()
    finally:
        if created:
            _admin(f"DROP DATABASE {name} WITH (FORCE)")
        _admin(f"DROP ROLE {name}")


def test_real_postgres_crypto_atomicity_revisions_revocation_and_isolation():
    assert host().startswith("PASS RDR09")


def guest():
    import copy
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.backtesting.coverage import coverage_result, read_coverage
    from packages.backtesting.dataset import Snapshot
    from packages.backtesting.readiness import TrustSnapshot
    from packages.research.evidence_import import EvidenceImporter
    from packages.research.evidence_storage import EvidenceStore, read_trust_snapshot
    from packages.research.evidence_trust import EvidenceRejected, fingerprint
    from packages.research.models import AssetClass, Instrument
    from packages.research.storage import ResearchStore
    from tests.integration.test_backtest_dataset import guest_engine
    from tests.security.test_research_evidence_trust import (
        INSTRUMENT,
        sign_bundle,
        signed_fixture,
        synthetic_authority,
    )

    engine = guest_engine()
    with engine.begin() as connection:
        ResearchStore().add_instrument(
            connection, Instrument(UUID(INSTRUMENT), AssetClass.ETF, "ARCX", "USD", "SPY", "USD")
        )
        baseline_ledger = connection.execute(
            text("SELECT count(*) FROM public.audit_events")
        ).scalar_one()
        empty = read_trust_snapshot(connection)
        assert empty["version"] == 0 and not empty["decisions"]
        before = Snapshot(empty["data_version"], empty["snapshot_at"])
    limited = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(limited, "connect")
    def certifier(dbapi, _):
        dbapi.execute("SET ROLE atl_evidence_importer")
        dbapi.commit()

    importer = EvidenceImporter(limited, production=False)
    source, decision = synthetic_authority()
    witness, witness_decision = synthetic_authority("WITNESS")
    unsigned_time, _, _ = signed_fixture(source=source)
    try:
        EvidenceImporter(limited).import_bytes(json.dumps(unsigned_time).encode())
    except EvidenceRejected as error:
        assert error.code == "UNTRUSTED_AUTHORITY"
    else:
        raise AssertionError("empty production registry accepted source")
    importer.admit(json.dumps(decision).encode(), 0)
    importer.admit(json.dumps(witness_decision).encode(), 1)
    # Die gleiche Betreiberentscheidung bleibt idempotent, auch mit alter expectedVersion.
    assert importer.admit(json.dumps(decision).encode(), 0)["version"] == 1
    first = importer.import_bytes(json.dumps(unsigned_time).encode())
    repeat = importer.import_bytes(json.dumps(unsigned_time).encode())
    assert first == repeat and first.commit_confirmed_at >= first.ingested_at
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT * FROM research.evidence_revisions WHERE evidence_id=:id"),
                {"id": first.entry_ids[0]},
            )
            .mappings()
            .one()
        )
        assert row["trust"]["sourceAuthenticity"]["status"] == "VERIFIED"
        assert row["trust"]["temporalAuthenticity"]["status"] == "UNKNOWN"
        assert row["ingested_at"] >= before.at
        assert not read_coverage(connection, UUID(INSTRUMENT), before)

    def counts():
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM research.evidence_revisions),"
                        "(SELECT count(*) FROM research.import_receipts)"
                    )
                ).one()
            )

    baseline = counts()
    rollback = copy.deepcopy(unsigned_time)
    entry = copy.deepcopy(rollback["entries"][0])
    entry["content"]["evidenceKey"] = "aaa-partial-must-rollback"
    rollback["entries"].insert(0, entry)
    sign_bundle(rollback, source)
    try:
        importer.import_bytes(json.dumps(rollback).encode())
    except EvidenceRejected as error:
        assert error.code == "ATOMIC_IMPORT_REJECTED"
    else:
        raise AssertionError("conflicting second entry did not rollback")
    assert counts() == baseline

    concurrent = copy.deepcopy(unsigned_time)
    concurrent["entries"][0]["content"]["evidenceKey"] = "concurrent-idempotent"
    sign_bundle(concurrent, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = tuple(
            pool.map(lambda _: importer.import_bytes(json.dumps(concurrent).encode()), range(2))
        )
    assert receipts[0].receipt_id == receipts[1].receipt_id
    assert counts() == (baseline[0] + 1, baseline[1] + 1)
    # Älterer Fingerprint darf einen bereits anders belegten neueren Schlüssel nicht überstimmen.
    try:
        importer.import_bytes(
            json.dumps(unsigned_time).encode(), bundle_key=fingerprint(concurrent)
        )
    except EvidenceRejected as error:
        assert error.code == "BUNDLE_IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("changed key payload bypassed through older fingerprint")

    revision = copy.deepcopy(unsigned_time)
    revised = revision["entries"][0]["content"]
    revised["revision"], revised["predecessorFingerprint"] = (
        2,
        fingerprint(unsigned_time["entries"][0]["content"]),
    )
    revised["statement"]["quality"] = "MISSING_EXPECTED"
    revised["statement"]["reason"] = "MISSING_BARS"
    sign_bundle(revision, source)
    second = importer.import_bytes(json.dumps(revision).encode())
    assert second.entry_ids != first.entry_ids
    with engine.connect() as connection:
        predecessor = connection.execute(
            text("SELECT predecessor_id FROM research.evidence_revisions WHERE evidence_id=:id"),
            {"id": second.entry_ids[0]},
        ).scalar_one()
        assert predecessor == first.entry_ids[0]
        assert not read_trust_snapshot(
            connection, knowledge_at=before.at, snapshot_version=before.version
        )["records"]

    # Echte Coverage-Leseports konsumieren Imports; Speicherung allein ist kein Beweis.
    actions, _, _ = signed_fixture("no-corporate-actions", source=source)
    no_time = importer.import_bytes(json.dumps(actions).encode())

    def coverage_now():
        with engine.connect() as connection:
            state = read_trust_snapshot(connection)
            snap = Snapshot(state["data_version"], state["snapshot_at"])
            proofs = read_coverage(connection, UUID(INSTRUMENT), snap)
            interval = actions["entries"][0]["content"]["interval"]
            return coverage_result(
                proofs,
                UUID(INSTRUMENT),
                datetime.fromisoformat(interval["startAt"]),
                datetime.fromisoformat(interval["endAt"]),
                snap.at,
                snap,
            )

    assert coverage_now().state == "TIME_UNPROVEN" and coverage_now().proof is None
    proven_actions = copy.deepcopy(actions)
    action_content = proven_actions["entries"][0]["content"]
    action_content["revision"] = 2
    action_content["predecessorFingerprint"] = fingerprint(actions["entries"][0]["content"])
    proof_template, _, _ = signed_fixture("publication-proof", source=source, witness=witness)
    proven_actions["entries"][0]["temporalProof"] = copy.deepcopy(
        proof_template["entries"][0]["temporalProof"]
    )
    sign_bundle(proven_actions, source, witness)
    confirmed = importer.import_bytes(json.dumps(proven_actions).encode())
    assert confirmed.entry_ids != no_time.entry_ids
    assert coverage_now().state == "VERIFIED" and coverage_now().proof is not None
    negative = copy.deepcopy(proven_actions)
    negative_content = negative["entries"][0]["content"]
    negative_content["revision"] = 3
    negative_content["predecessorFingerprint"] = fingerprint(action_content)
    negative_content["statement"]["conclusion"] = "ACTIONS_PRESENT"
    negative_content["statement"]["actions"] = [
        {"type": "SPLIT", "effectiveAt": "2026-10-23T14:00:00Z", "ratio": "2"}
    ]
    sign_bundle(negative, source, witness)
    importer.import_bytes(json.dumps(negative).encode())
    assert coverage_now().state == "NEGATIVE" and coverage_now().proof is None

    # Tatsächlicher unabhängiger Publikationsbeweis wird gespeichert; lokale Aufnahme bleibt heute.
    published, _, _ = signed_fixture("publication-proof", source=source, witness=witness)
    published["entries"][0]["content"]["evidenceKey"] = "independent-publication"
    sign_bundle(published, source, witness)
    proven = importer.import_bytes(json.dumps(published).encode())
    with engine.connect() as connection:
        state = read_trust_snapshot(connection)
        record = next(r for r in state["records"] if r["reference"] == str(proven.entry_ids[0]))
        assert record["dimensions"]["temporalAuthenticity"]["status"] == "VERIFIED"
        old_record = json.dumps(record["dimensions"], sort_keys=True)
        assert not read_trust_snapshot(
            connection, knowledge_at=before.at, snapshot_version=before.version
        )["records"]

    # Ein neuer Widerruf ändert aktuelle Gates, niemals ursprüngliche Evidenzdimensionen.
    revoked = {
        **decision,
        "decisionId": str(uuid4()),
        "predecessorDecisionId": decision["decisionId"],
        "revoked": True,
        "compromiseTimeKnown": False,
        "reason": "UNKNOWN_COMPROMISE_TIME",
    }

    # Vertrauensänderung nach Kryptoprüfung, aber vor atomarer Aufnahme, muss sperren.
    class RacingStore(EvidenceStore):
        def append_bundle(self, connection, *args, **kwargs):
            importer.admit(json.dumps(revoked).encode(), 2)
            return super().append_bundle(connection, *args, **kwargs)

    raced = copy.deepcopy(unsigned_time)
    raced["entries"][0]["content"]["evidenceKey"] = "revocation-admission-race"
    sign_bundle(raced, source)
    before_race = counts()
    try:
        EvidenceImporter(limited, production=False, store=RacingStore()).import_bytes(
            json.dumps(raced).encode()
        )
    except EvidenceRejected as error:
        assert error.code == "ATOMIC_IMPORT_REJECTED"
    else:
        raise AssertionError("stale verified trust snapshot bypassed revocation")
    assert counts() == before_race
    with engine.connect() as connection:
        current = read_trust_snapshot(connection)
        record = next(r for r in current["records"] if r["reference"] == str(proven.entry_ids[0]))
        assert record["revoked"] and json.dumps(record["dimensions"], sort_keys=True) == old_record
        assert TrustSnapshot.from_mapping(current).version == 3
        assert (
            connection.execute(text("SELECT count(*) FROM public.audit_events")).scalar_one()
            == baseline_ledger
        )
    newer = copy.deepcopy(unsigned_time)
    newer["entries"][0]["content"]["evidenceKey"] = "after-revocation"
    sign_bundle(newer, source)
    try:
        importer.import_bytes(json.dumps(newer).encode())
    except EvidenceRejected as error:
        assert error.code == "AUTHORITY_REVOKED"
    else:
        raise AssertionError("revoked authority used for new content")
    assert importer.import_bytes(json.dumps(unsigned_time).encode()).receipt_id == first.receipt_id
    with limited.connect() as connection:
        rejections = tuple(
            connection.execute(
                text("SELECT code FROM research.import_rejections ORDER BY recorded_at")
            ).scalars()
        )
        assert {
            "UNTRUSTED_AUTHORITY",
            "ATOMIC_IMPORT_REJECTED",
            "AUTHORITY_REVOKED",
            "BUNDLE_IDEMPOTENCY_CONFLICT",
        } <= set(rejections)
        for table in ("public.broker_orders", "public.audit_events", "research.ingestion_jobs"):
            assert not connection.execute(
                text("SELECT has_table_privilege(current_user,:table,'INSERT')"), {"table": table}
            ).scalar_one()
        try:
            with connection.begin_nested():
                connection.execute(
                    text("UPDATE research.evidence_revisions SET revision=revision+1")
                )
        except DBAPIError:
            pass
        else:
            raise AssertionError("offline role mutated evidence")
    limited.dispose()
    engine.dispose()
    print("PASS RDR09 Ed25519/witness/atomic-rollback/concurrency/revision/DB-time/revocation/ACL")


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        # Keine SQLparameter, Credentials oder private Testschlüssel in Fehlermeldungen.
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own = [
            frame for frame in frames if frame.filename.endswith("test_research_evidence_import.py")
        ]
        safe_code = getattr(error, "code", "UNCLASSIFIED")
        print(
            "FAIL RDR09 "
            + type(error).__name__
            + " code="
            + str(safe_code)
            + " line="
            + str(own[-1].lineno if own else 0)
        )
        raise SystemExit(1) from None
