"""Load only pinned evaluation fixtures and a complete, disjoint split manifest."""

import json
from hashlib import sha256
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")
IDENTITY_FIELDS = ("caseId", "version", "group", "split", "cluster", "offlineOnly")
GROUPS = ("beginner", "advanced", "expert", "negative")


def _parse_json(raw):
    def unique_fields(items):
        value = dict(items)
        if len(value) != len(items):
            raise ValueError("EVALUATION_DUPLICATE_FIELD")
        return value

    return json.loads(raw, object_pairs_hook=unique_fields)


def _read_json(path):
    return _parse_json(path.read_bytes())


def load_catalog(
    manifest_path: Path, development_path: Path, holdout_path: Path | None = None, *,
    expected_manifest: str | None = None, expected_holdout: str | None = None,
):
    return decode_catalog(
        manifest_path.read_bytes(), development_path.read_bytes(),
        holdout_path.read_bytes() if holdout_path is not None else None,
        expected_manifest=expected_manifest, expected_holdout=expected_holdout,
    )


def decode_catalog(
    raw: bytes, development_raw: bytes, holdout_raw: bytes | None, *,
    expected_manifest: str | None = None, expected_holdout: str | None = None,
):
    if expected_manifest is not None and sha256(raw).hexdigest() != expected_manifest:
        raise ValueError("EVALUATION_DATASET_HASH")
    manifest = _parse_json(raw)
    if expected_holdout is not None and manifest["holdoutSha256"] != expected_holdout:
        raise ValueError("EVALUATION_DATASET_HASH")
    if manifest["version"] != 1:
        raise ValueError("EVALUATION_MANIFEST_VERSION")
    indices = {"development": {}, "holdout": {}}
    ids = set()
    clusters = {}
    for identity in manifest["cases"]:
        if (
            set(identity) != set(IDENTITY_FIELDS)
            or identity["group"] not in GROUPS
            or identity["split"] not in indices
            or identity["caseId"] in ids
            or clusters.setdefault(identity["cluster"], identity["split"]) != identity["split"]
        ):
            raise ValueError("EVALUATION_SPLIT_INVALID")
        ids.add(identity["caseId"])
        indices[identity["split"]][identity["caseId"]] = identity
    for group in GROUPS:
        for index in indices.values():
            if sum(value["group"] == group for value in index.values()) < 6:
                raise ValueError("EVALUATION_SPLIT_COVERAGE")

    def verified(split, raw):
        if sha256(raw).hexdigest() != manifest[split + "Sha256"]:
            raise ValueError("EVALUATION_DATASET_HASH")
        document = _parse_json(raw)
        rows = document["cases"]
        metadata = {
            row["caseId"]: {key: row[key] for key in IDENTITY_FIELDS} for row in rows
        }
        if (
            document["version"] != 1 or len(rows) != len(metadata) or metadata != indices[split]
            or any(
                type(row["input"]) is not str or not row["input"].strip()
                or type(row["dialogueState"]) is not dict or type(row["offlineOnly"]) is not bool
                or row["offlineClass"] not in {None, "PII", "SECRET", "RIGHTS"}
                or row["offlineOnly"] != (row["offlineClass"] is not None)
                for row in rows
            )
        ):
            raise ValueError("EVALUATION_DATASET_MISMATCH")
        return rows

    development = verified("development", development_raw)
    holdout = verified("holdout", holdout_raw) if holdout_raw is not None else []
    return development, holdout


def read_verified_holdout(manifest: Path, holdout: Path, manifest_hash, holdout_hash) -> list[dict]:
    from .scorer import coverage_gaps

    development_file = manifest.with_name("development.v1.json")
    if not development_file.is_file():
        development_file = FIXTURES / "development.v1.json"
    development, selected = load_catalog(
        manifest, development_file, holdout,
        expected_manifest=manifest_hash, expected_holdout=holdout_hash,
    )
    if coverage_gaps(development + selected):
        raise ValueError("EVALUATION_HOLDOUT_COVERAGE")
    return selected
