"""RDR-09: echte Offline-Ed25519-Prüfung mit ausschließlich Testautoritäten."""

import base64
import copy
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from packages.research.evidence_import import parse_bundle, parse_document, verify_entry
from packages.research.evidence_trust import (
    POLICY_VERSION,
    EvidenceRejected,
    TrustRegistry,
    canonical_bytes,
    fingerprint,
    validate_decision,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
INSTRUMENT = "11111111-1111-4111-8111-111111111111"


def synthetic_authority(kind="SOURCE", version=1, key=None, **changes):
    key = key or Ed25519PrivateKey.generate()
    value = {
        "decisionId": str(uuid4()),
        "sourceId": "synthetic-source-v1",
        "keyId": "synthetic-source-key-v1" if kind == "SOURCE" else "synthetic-witness-key-v1",
        "authorityType": kind,
        "scope": {
            "instrumentIds": [INSTRUMENT],
            "kinds": ["CALENDAR", "QUALITY", "CORPORATE_ACTION"],
        },
        "effectiveAt": "2026-10-01T00:00:00Z",
        "validFrom": "2026-10-01T00:00:00Z",
        "validTo": "2027-10-01T00:00:00Z",
        "revoked": False,
        "compromisedAt": None,
        "compromiseTimeKnown": True,
        "policyVersion": POLICY_VERSION,
        "reason": "SYNTHETIC_TEST_APPROVAL",
        "publicKey": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        "algorithm": "Ed25519",
        "methods": ["RFC8785-UTF8-v1" if kind == "SOURCE" else "offline-witness-ed25519-v1"],
        "actorRef": "synthetic-offline-certifier",
        "documentFingerprint": "a" * 64,
        "testOnly": True,
        "predecessorDecisionId": None,
    }
    if kind == "WITNESS":
        value["witnessId"] = "synthetic-independent-witness-v1"
        value["scope"]["assertions"] = ["EXISTENCE_BY", "PUBLICATION_OBSERVED_BY"]
    value.update(changes)
    return key, value


def stored(decision, version):
    return {
        **decision,
        "version": version,
        "commitVersion": version,
        "recordedAt": "2026-10-26T08:00:00Z",
    }


def registry(*decisions, production=False):
    values = [stored(d, i + 1) for i, d in enumerate(decisions)]
    return TrustRegistry(
        {"version": len(values), "policy_version": POLICY_VERSION, "decisions": values},
        NOW,
        production=production,
    )


def sign_bundle(bundle, source, witness=None):
    """Signieren existiert ausschließlich im synthetischen Testharness."""
    for entry in bundle["entries"]:
        entry["contentFingerprint"] = fingerprint(entry["content"])
        entry["signature"]["value"] = base64.b64encode(
            source.sign(canonical_bytes(entry["content"]))
        ).decode()
        if "temporalProof" in entry:
            payload = entry["temporalProof"]["payload"]
            payload["scope"] = {
                k: copy.deepcopy(entry["content"][k])
                for k in ("sourceId", "evidenceKey", "revision", "kind", "instrumentId", "interval")
            }
            payload["scope"].update(
                contentFingerprint=entry["contentFingerprint"],
                statementFingerprint=fingerprint(entry["content"]["statement"]),
            )
            entry["temporalProof"]["signature"]["value"] = base64.b64encode(
                witness.sign(canonical_bytes(payload))
            ).decode()
    return bundle


def signed_fixture(name="quality", *, source=None, witness=None):
    bundle = json.loads(
        (ROOT / f"contracts/examples/readiness-bundle/{name}.valid.json").read_text("utf-8")
    )
    source = source or synthetic_authority()[0]
    if name in {"publication-proof", "existence-proof"}:
        witness = witness or synthetic_authority("WITNESS")[0]
    return sign_bundle(bundle, source, witness), source, witness


def check(bundle, trust):
    parse_bundle(json.dumps(bundle).encode())
    return verify_entry(bundle["entries"][0], bundle, trust)


def test_signature_alone_preserves_independent_time_and_completeness_unknown():
    key, decision = synthetic_authority()
    bundle, _, _ = signed_fixture(source=key)
    result = check(bundle, registry(decision))
    assert result["trust"]["integrity"]["status"] == "VERIFIED"
    assert result["trust"]["sourceAuthenticity"]["status"] == "VERIFIED"
    assert result["trust"]["temporalAuthenticity"]["status"] == "UNKNOWN"
    assert result["trust"]["temporalAuthenticity"]["reason"] == "TIME_UNPROVEN"
    assert result["trust"]["completeness"]["status"] == "UNKNOWN"
    assert "eligibility" not in result


@pytest.mark.parametrize(
    "name,expected", [("publication-proof", "VERIFIED"), ("existence-proof", "UNKNOWN")]
)
def test_independent_witness_binds_exact_scope_and_only_precise_time_statement(name, expected):
    source, source_record = synthetic_authority()
    witness, witness_record = synthetic_authority("WITNESS")
    bundle, _, _ = signed_fixture(name, source=source, witness=witness)
    result = check(bundle, registry(source_record, witness_record))
    assert result["trust"]["temporalAuthenticity"]["status"] == expected
    assert (result["publicationObservedAt"] is not None) == (expected == "VERIFIED")


@pytest.mark.parametrize(
    "change,code",
    [
        ({"revoked": True}, "AUTHORITY_REVOKED"),
        ({"compromiseTimeKnown": False}, "AUTHORITY_COMPROMISED"),
        ({"compromisedAt": "2026-10-25T00:00:00Z"}, "AUTHORITY_COMPROMISED"),
        ({"validTo": "2026-10-26T00:00:00Z"}, "KEY_NOT_VALID_AT_SIGNING"),
        ({"validTo": "2026-10-27T00:00:00Z"}, "KEY_NOT_CURRENTLY_VALID"),
        (
            {"scope": {"instrumentIds": [str(UUID(int=2))], "kinds": ["QUALITY"]}},
            "AUTHORITY_SCOPE_MISMATCH",
        ),
    ],
)
def test_revocation_compromise_expiry_and_scope_fail_closed(change, code):
    key, decision = synthetic_authority(**change)
    bundle, _, _ = signed_fixture(source=key)
    with pytest.raises(EvidenceRejected, match=code):
        check(bundle, registry(decision))


def test_empty_production_registry_and_test_keys_cannot_be_used():
    key, decision = synthetic_authority()
    bundle, _, _ = signed_fixture(source=key)
    with pytest.raises(EvidenceRejected, match="UNTRUSTED_AUTHORITY"):
        check(bundle, registry(production=True))
    with pytest.raises(EvidenceRejected, match="TEST_AUTHORITY_FORBIDDEN"):
        check(bundle, registry(decision, production=True))


@pytest.mark.parametrize(
    "location", ["content", "digest", "signature", "signed-key", "signed-time"]
)
def test_tampered_content_digest_signature_and_signed_metadata_rejected(location):
    key, decision = synthetic_authority()
    bundle, _, _ = signed_fixture(source=key)
    entry = bundle["entries"][0]
    if location == "content":
        entry["content"]["statement"]["reason"] = "MISSING_BARS"
    elif location == "digest":
        entry["contentFingerprint"] = "0" * 64
    elif location == "signature":
        entry["signature"]["value"] = "A" * 86 + "=="
    elif location == "signed-key":
        entry["signature"]["keyId"] = "other-key"
    else:
        entry["content"]["signedAt"] = "2026-10-26T10:00:00Z"
        entry["contentFingerprint"] = fingerprint(entry["content"])
    with pytest.raises(EvidenceRejected):
        check(bundle, registry(decision))


@pytest.mark.parametrize(
    "field", ["sourceId", "revision", "statementFingerprint", "contentFingerprint", "interval"]
)
def test_witness_valid_signature_over_substituted_scope_still_rejected(field):
    source, decision = synthetic_authority()
    witness, witness_decision = synthetic_authority("WITNESS")
    bundle, _, _ = signed_fixture("publication-proof", source=source, witness=witness)
    proof = bundle["entries"][0]["temporalProof"]
    replacements = {
        "sourceId": "other-source",
        "revision": 99,
        "statementFingerprint": "f" * 64,
        "contentFingerprint": "f" * 64,
        "interval": {"startAt": "2026-10-20T00:00:00Z", "endAt": "2026-10-21T00:00:00Z"},
    }
    proof["payload"]["scope"][field] = replacements[field]
    proof["signature"]["value"] = base64.b64encode(
        witness.sign(canonical_bytes(proof["payload"]))
    ).decode()
    with pytest.raises(EvidenceRejected, match="TEMPORAL_SCOPE_MISMATCH"):
        check(bundle, registry(decision, witness_decision))


def test_source_key_cannot_be_its_own_independent_witness():
    source, decision = synthetic_authority()
    _, witness_decision = synthetic_authority("WITNESS", key=source)
    bundle, _, _ = signed_fixture("publication-proof", source=source, witness=source)
    with pytest.raises(EvidenceRejected, match="WITNESS_NOT_INDEPENDENT"):
        check(bundle, registry(decision, witness_decision))


def test_rotation_is_append_only_old_key_revocation_does_not_disappear():
    old, old_record = synthetic_authority()
    new, new_record = synthetic_authority(keyId="rotated-source-key")
    revoked = {
        **old_record,
        "decisionId": str(uuid4()),
        "predecessorDecisionId": old_record["decisionId"],
        "revoked": True,
    }
    trust = registry(old_record, new_record, revoked)
    old_bundle, _, _ = signed_fixture(source=old)
    with pytest.raises(EvidenceRejected, match="AUTHORITY_REVOKED"):
        check(old_bundle, trust)
    new_bundle, _, _ = signed_fixture(source=new)
    entry = new_bundle["entries"][0]
    entry["content"]["keyId"] = entry["signature"]["keyId"] = "rotated-source-key"
    sign_bundle(new_bundle, new)
    assert check(new_bundle, trust)["trust"]["sourceAuthenticity"]["status"] == "VERIFIED"


def test_missing_trust_predecessor_blocks_chain():
    _, decision = synthetic_authority(predecessorDecisionId=str(uuid4()))
    with pytest.raises(EvidenceRejected, match="INVALID_TRUST_CHAIN"):
        registry(decision)


def test_signed_domain_invalid_split_is_not_rehabilitated_by_crypto():
    key, decision = synthetic_authority()
    bundle, _, _ = signed_fixture("corporate-action", source=key)
    bundle["entries"][0]["content"]["statement"]["actions"][0]["ratio"] = "-2"
    sign_bundle(bundle, key)
    result = check(bundle, registry(decision))
    assert result["trust"]["sourceAuthenticity"]["status"] == "VERIFIED"
    assert result["trust"]["domainQuality"]["status"] == "REJECTED"


def test_no_actions_in_future_is_not_certified_future_event_freedom():
    key, decision = synthetic_authority()
    bundle, _, _ = signed_fixture("no-corporate-actions", source=key)
    bundle["entries"][0]["content"]["interval"]["endAt"] = "2026-12-01T00:00:00Z"
    sign_bundle(bundle, key)
    assert check(bundle, registry(decision))["trust"]["domainQuality"]["status"] == "REJECTED"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1.5}',
        b'{"a":"\\ud800"}',
        b"\xff",
        b"[" * 1500,
    ],
)
def test_non_ijson_or_duplicate_input_is_rejected(raw):
    with pytest.raises(EvidenceRejected):
        parse_document(raw)


def test_byte_limit_is_applied_before_json_parse():
    with pytest.raises(EvidenceRejected, match="INPUT_BUDGET_EXHAUSTED"):
        parse_document(b" " * 1048577)


@pytest.mark.parametrize(
    "field",
    ["publicKey", "privateKey", "trustAnchor", "ingestedAt", "commitVersion", "certificate"],
)
def test_caller_supplied_keys_certificates_ingestion_and_versions_are_rejected(field):
    bundle, _, _ = signed_fixture()
    bundle[field] = "not-trusted"
    with pytest.raises(EvidenceRejected, match="INVALID_BUNDLE_SCHEMA"):
        parse_bundle(json.dumps(bundle).encode())


def test_registry_input_is_closed_and_cannot_supply_database_timestamps():
    _, decision = synthetic_authority()
    validate_decision(decision)
    with pytest.raises(EvidenceRejected, match="INVALID_TRUST_DECISION"):
        validate_decision({**decision, "recordedAt": "2026-10-01T00:00:00Z"})


def test_application_has_no_signing_network_or_credential_consumer():
    for filename in ["packages/research/evidence_import.py", "packages/research/evidence_trust.py"]:
        source = (ROOT / filename).read_text("utf-8")
        assert "Ed25519PrivateKey" not in source
        assert ".sign(" not in source
        assert "httpx" not in source and "urllib" not in source
        assert "PaperGenerationReader" not in source


def test_cli_pins_both_libpq_host_and_hostaddr_despite_service_environment(monkeypatch, capsys):
    """hostaddr bestimmt das Netz-Ziel selbst bei gesetztem host; kein Netzwerk im Test."""
    from packages.research.evidence_storage import EvidenceReceipt

    module = runpy.run_path(str(ROOT / "scripts/import-research-evidence.py"))
    captured = {}

    class Engine:
        def dispose(self):
            captured["disposed"] = True

    def create_engine(url, **kwargs):
        captured.update(kwargs)
        assert url.query["service"] == "synthetic-local-service"
        return Engine()

    class Importer:
        def __init__(self, engine):
            assert isinstance(engine, Engine)

        def import_bytes(self, document, *, bundle_key):
            assert document and bundle_key is None
            return EvidenceReceipt(uuid4(), (uuid4(),), 1, NOW, 1, NOW)

    monkeypatch.setenv("PGHOSTADDR", "198.51.100.1")
    monkeypatch.setitem(module["main"].__globals__, "create_engine", create_engine)
    monkeypatch.setitem(module["main"].__globals__, "EvidenceImporter", Importer)
    result = module["main"](
        [
            "--service",
            "synthetic-local-service",
            "import",
            str(ROOT / "contracts/examples/readiness-bundle/quality.valid.json"),
        ]
    )
    assert result == 0
    assert captured["connect_args"] == {
        "host": "127.0.0.1",
        "hostaddr": "127.0.0.1",
        "connect_timeout": 5,
    }
    assert captured["disposed"]
    assert json.loads(capsys.readouterr().out)["researchEligibility"] == "NOT_EVALUATED"
