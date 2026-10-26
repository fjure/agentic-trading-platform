"""RDR-01: Strukturvertrag für REQ-RDR-003/004/007/008 und T-RDR-005/006/013–016.

Synthetische Signaturbytes authentifizieren nichts. Schlüsselzulassung, echte
Ed25519-Verifikation, RFC-8785-Kanonisierung, Chronologie, atomare Persistenz und
unabhängige Publikationsnachweise gehören zu RDR-09. Diese Tests behaupten keine
solche Runtimeprüfung; insbesondere bedeutet eine gültige Form niemals VERIFIED.
"""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "contracts/schemas/research-evidence-bundle.schema.json").read_text("utf-8")
)
FIXTURES = ROOT / "contracts/examples/readiness-bundle"
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=contract_formats())


def example(name="quality"):
    return json.loads((FIXTURES / f"{name}.valid.json").read_text("utf-8"))


def nested_objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nested_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_objects(item)


def test_schema_is_valid_and_reuses_canonical_scalar_patterns():
    Draft202012Validator.check_schema(SCHEMA)
    common = json.loads((ROOT / "contracts/schemas/v1/common.schema.json").read_text("utf-8"))
    backtest = json.loads(
        (ROOT / "contracts/schemas/v1/backtesting.schema.json").read_text("utf-8")
    )
    assert SCHEMA["$defs"]["Decimal"]["pattern"] == common["$defs"]["decimal"]["pattern"]
    assert SCHEMA["$defs"]["Utc"] == {
        key: value
        for key, value in backtest["$defs"]["BacktestTimestamp"].items()
        if key != "description"
    }
    research = json.loads((ROOT / "contracts/schemas/v1/research.schema.json").read_text("utf-8"))
    assert SCHEMA["$defs"]["QualityStatement"]["properties"]["quality"] == (
        research["$defs"]["ResearchQuality"]
    )


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.valid.json")), ids=lambda p: p.stem)
def test_all_dedicated_examples_are_structurally_valid(path):
    value = json.loads(path.read_text("utf-8"))
    VALIDATOR.validate(value)
    assert path.stat().st_size < SCHEMA["x-maxSerializedBytes"]


@pytest.mark.parametrize("name", ["calendar", "corporate-action", "publication-proof"])
def test_unknown_fields_are_rejected_at_every_nested_object(name):
    value = example(name)
    for target in nested_objects(value):
        target["untrustedPayload"] = "forbidden"
        assert not VALIDATOR.is_valid(value)
        del target["untrustedPayload"]


@pytest.mark.parametrize(
    "field",
    [
        "publicKey", "privateKey", "certificate", "certificateChain", "trustAnchor",
        "trustRegistry", "ingestedAt", "commitVersion", "commitConfirmedAt", "certifiedAt",
        "availableAt", "eligibility", "temporalAuthenticity", "rawPayload", "url", "program",
    ],
)
@pytest.mark.parametrize("location", ["bundle", "entry", "content", "signature", "proof"])
def test_supplied_authority_ingestion_and_result_fields_are_rejected(field, location):
    value = example("publication-proof")
    entry = value["entries"][0]
    target = {
        "bundle": value, "entry": entry, "content": entry["content"],
        "signature": entry["signature"], "proof": entry["temporalProof"],
    }[location]
    target[field] = "untrusted-claim"
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize(
    "invalid",
    [
        "2026-10-23T13:30:00+00:00", "2026-10-23T13:30:00", "2026-02-30T13:30:00Z",
        "2026-10-23t13:30:00z", "2026-10-23T13:30:00.1234567Z", "2026-10-23T25:00:00Z",
        "2026-10-23T13:30:00Z\n", 1792762200,
    ],
)
@pytest.mark.parametrize(
    "location", ["interval", "observed", "published", "session", "proof", "signed"]
)
def test_invalid_utc_is_rejected_in_each_time_scope(invalid, location):
    value = example("publication-proof" if location == "proof" else "calendar")
    content = value["entries"][0]["content"]
    if location == "interval":
        content["interval"]["startAt"] = invalid
    elif location == "observed":
        content["sourceObservedAt"] = invalid
    elif location == "published":
        content["publishedAt"] = invalid
    elif location == "session":
        content["statement"]["sessions"][0]["opensAt"] = invalid
    elif location == "signed":
        content["signedAt"] = invalid
    else:
        value["entries"][0]["temporalProof"]["payload"]["witnessObservedAt"] = invalid
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize(
    "invalid", [2, 0.125, "NaN", "Infinity", "1e2", "01", "+2", " 2", "2\n",
                "1.1234567890123456789", "123456789012345678901"]
)
@pytest.mark.parametrize("index,field", [(0, "ratio"), (1, "amount")])
def test_invalid_decimal_is_rejected(invalid, index, field):
    value = example("corporate-action")
    value["entries"][0]["content"]["statement"]["actions"][index][field] = invalid
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("sourceId", "https://source.invalid/evidence"), ("sourceId", "source\n"),
        ("evidenceKey", "../../source"), ("policyVersion", ""), ("bundleVersion", "v2"),
        ("kind", "RAW_PROVIDER_PAYLOAD"), ("revision", 0), ("revision", 1.5),
        ("instrumentId", "AAPL"), ("sourceDocumentFingerprint", "a" * 64 + "\n"),
    ],
)
def test_identity_version_and_fingerprint_values_are_closed(field, invalid):
    value = example()
    value["entries"][0]["content"][field] = invalid
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize("revision,predecessor", [(1, "a" * 64), (2, None), (2, "unknown")])
def test_revision_requires_typed_predecessor_binding(revision, predecessor):
    value = example()
    value["entries"][0]["content"].update(
        revision=revision, predecessorFingerprint=predecessor
    )
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize("kind", ["CALENDAR", "CORPORATE_ACTION"])
def test_entry_kind_cannot_relabel_an_unrelated_statement(kind):
    value = example()
    value["entries"][0]["content"]["kind"] = kind
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("algorithm", "none"), ("algorithm", "RSA"),
        ("canonicalization", "json-sort-keys"), ("canonicalization", "RFC8785-UTF8-v2"),
        ("value", ""), ("value", "A" * 85 + "B=="), ("value", "A" * 86 + "==\n"),
        ("value", "_" * 86 + "=="), ("value", "a" * 128),
    ],
)
def test_only_fixed_detached_signature_format_is_accepted(field, invalid):
    value = example()
    value["entries"][0]["signature"][field] = invalid
    assert not VALIDATOR.is_valid(value)


def test_missing_signature_is_rejected_even_when_a_fingerprint_is_supplied():
    value = example()
    del value["entries"][0]["signature"]
    assert not VALIDATOR.is_valid(value)


@pytest.mark.parametrize("location", ["content", "payload"])
def test_key_identity_is_required_inside_the_signed_bytes(location):
    value = example("publication-proof")
    entry = value["entries"][0]
    target = entry["content"] if location == "content" else entry["temporalProof"]["payload"]
    assert target["keyId"] == (
        entry["signature"]["keyId"] if location == "content"
        else entry["temporalProof"]["signature"]["keyId"]
    )
    del target["keyId"]
    assert not VALIDATOR.is_valid(value)


def test_temporal_proof_requires_complete_explicit_scope():
    for field in SCHEMA["$defs"]["TemporalScope"]["required"]:
        value = example("publication-proof")
        del value["entries"][0]["temporalProof"]["payload"]["scope"][field]
        assert not VALIDATOR.is_valid(value), field


@pytest.mark.parametrize("field,invalid", [("assertion", "VERIFIED"), ("method", "self-signed")])
def test_temporal_proof_cannot_supply_a_result_or_unknown_procedure(field, invalid):
    value = example("publication-proof")
    value["entries"][0]["temporalProof"]["payload"][field] = invalid
    assert not VALIDATOR.is_valid(value)


def test_empty_calendar_response_does_not_assert_coverage():
    value = example("calendar")
    value["entries"][0]["content"]["statement"]["sessions"] = []
    assert not VALIDATOR.is_valid(value)


def test_no_actions_assertion_cannot_contain_conflicting_actions():
    value = example("corporate-action")
    statement = value["entries"][0]["content"]["statement"]
    statement["conclusion"] = "NO_ACTIONS"
    assert not VALIDATOR.is_valid(value)
    statement.update(conclusion="ACTIONS_PRESENT", actions=[])
    assert not VALIDATOR.is_valid(value)


def test_bundle_count_bounds_are_enforced():
    value = example()
    entry = value["entries"][0]
    value["entries"] = []
    assert not VALIDATOR.is_valid(value)
    value["entries"] = [entry] * 1000
    VALIDATOR.validate(value)
    value["entries"].append(entry)
    assert not VALIDATOR.is_valid(value)


def test_byte_limit_is_an_explicit_preparse_requirement_not_a_schema_claim():
    """JSON Schema zählt Einträge, keine serialisierten UTF-8-Bytes (RDR-09)."""
    value = example("corporate-action")
    value["entries"] *= 1000
    assert len(json.dumps(value, separators=(",", ":")).encode()) > 1048576
    assert SCHEMA["x-maxSerializedBytes"] == 1048576
    VALIDATOR.validate(value)


def test_schema_does_not_claim_crypto_chronology_or_cross_field_authentication():
    """Bewusst valide Struktur mit falschen Bindungen: RDR-09 MUSS sie ablehnen."""
    value = copy.deepcopy(example("publication-proof"))
    entry = value["entries"][0]
    entry["contentFingerprint"] = "0" * 64
    entry["content"]["sourceId"] = "substituted-source"
    entry["content"]["keyId"] = "substituted-key"
    entry["content"]["interval"]["endAt"] = "2026-10-22T00:00:00Z"
    entry["temporalProof"]["payload"]["scope"]["revision"] = 42
    # Nullbytes sind strukturelle Signaturplatzhalter, keine gültige Evidenz.
    VALIDATOR.validate(value)


def test_missing_and_existence_only_time_proofs_never_claim_publication_verification():
    without = example()
    assert "temporalProof" not in without["entries"][0]
    existence = example("existence-proof")
    publication = example("publication-proof")
    assert existence["entries"][0]["temporalProof"]["payload"]["assertion"] == "EXISTENCE_BY"
    assert publication["entries"][0]["temporalProof"]["payload"]["assertion"] == (
        "PUBLICATION_OBSERVED_BY"
    )
    for value in [without, existence, publication]:
        VALIDATOR.validate(value)
        assert all("status" not in item for item in nested_objects(value))
    assert "UNKNOWN/TIME_UNPROVEN" in SCHEMA["$defs"]["Entry"]["description"]
    assert "UNKNOWN/TIME_UNPROVEN" in SCHEMA["$defs"]["TemporalProof"]["description"]
