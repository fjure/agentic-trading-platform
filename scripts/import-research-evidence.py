"""Lokales Einmalwerkzeug; keine Schlüsselargumente, kein HTTP und keine Signierfunktion."""

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.research.evidence_import import MAX_BYTES, EvidenceImporter  # noqa: E402
from packages.research.evidence_trust import EvidenceRejected  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lokaler normalisierter Research-Evidenzimport")
    parser.add_argument(
        "--service",
        default="atl_evidence_importer",
        help="Lokaler libpq-Service; niemals Kennwörter in Argumenten",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    imported = sub.add_parser("import", help="Vollständig geprüftes Bündel atomar importieren")
    imported.add_argument("document", type=Path)
    imported.add_argument("--bundle-key")
    admitted = sub.add_parser("admit", help="Ausdrückliche lokale Quellen-/Schlüsselentscheidung")
    admitted.add_argument("document", type=Path)
    admitted.add_argument("--expected-version", type=int, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.service):
        parser.error("Opaker lokaler Service erforderlich")
    engine = None
    try:
        limit = 65536 if args.command == "admit" else MAX_BYTES
        with args.document.open("rb") as source:
            document = source.read(limit + 1)
        # Verbindungen nur zur lokalen Datenbank; Passwörter bleiben in libpq/OS-Authentifizierung.
        engine = create_engine(
            URL.create("postgresql+psycopg", query={"service": args.service}),
            connect_args={"host": "127.0.0.1", "hostaddr": "127.0.0.1", "connect_timeout": 5},
            hide_parameters=True,
            echo=False,
        )
        importer = EvidenceImporter(engine)  # Produktionsmodus ist nicht über CLI abschaltbar.
        if args.command == "admit":
            decision = importer.admit(document, args.expected_version)
            result = {
                "status": "RECORDED",
                "decisionId": decision["decisionId"],
                "trustVersion": decision["version"],
                "rightsVerification": "UNVERIFIED",
            }
        else:
            receipt = importer.import_bytes(document, bundle_key=args.bundle_key)
            values = asdict(receipt)
            result = {
                "status": "IMPORTED",
                "receiptId": str(values["receipt_id"]),
                "entryCount": len(values["entry_ids"]),
                "commitVersion": values["commit_version"],
                "trustVersion": values["trust_version"],
                "ingestedAt": str(values["ingested_at"]),
                "commitConfirmedAt": str(values["commit_confirmed_at"]),
                "researchEligibility": "NOT_EVALUATED",
            }
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except EvidenceRejected as error:
        print(json.dumps({"status": "REJECTED", "code": error.code}), file=sys.stderr)
        return 1
    except Exception:
        print('{"status":"REJECTED","code":"OFFLINE_OPERATION_FAILED"}', file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
