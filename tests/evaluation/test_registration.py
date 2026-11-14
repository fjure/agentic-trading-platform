from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .custody import _canonical, run_binding_sha256
from .harness import RunBinding
from .registration import registration_message, verify_registration


def test_registration_rejects_tampering_unknown_key_and_early_open():
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    binding = RunBinding("profile", "model", "v1", *(["a" * 64] * 6))
    now = datetime.now(UTC)
    registered = now - timedelta(minutes=1)
    receipt = {
        "version": 1,
        "reference": "fixture-log:123",
        "bindingSha256": run_binding_sha256(binding),
        "registeredAt": registered.isoformat().replace("+00:00", "Z"),
    }
    raw = _canonical(receipt)
    sig = key.sign(registration_message(receipt))
    assert verify_registration(binding, raw, sig, key.public_key(), now)["reference"] == (
        "fixture-log:123"
    )
    for payload, signature, public, opened in (
        (_canonical(receipt | {"reference": "fixture-log:124"}), sig, key.public_key(), now),
        (raw, sig, other.public_key(), now),
        (raw, sig, key.public_key(), registered),
        (raw, bytes(64), key.public_key(), now),
    ):
        with pytest.raises(ValueError, match="EVALUATION_REGISTRATION_INVALID"):
            verify_registration(binding, payload, signature, public, opened)


def test_v2_attestation_requires_independent_registrar_and_non_fixture_review(tmp_path):
    from .custody import attestation_message, verify_custody

    registrar = Ed25519PrivateKey.generate()
    custodian = Ed25519PrivateKey.generate()
    binding = RunBinding("profile", "model", "v1", *(["a" * 64] * 6))
    now = datetime.now(UTC)
    receipt = {
        "version": 1,
        "reference": "fixture-register:1",
        "bindingSha256": run_binding_sha256(binding),
        "registeredAt": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
    registration, registration_sig, attest, signature = (
        tmp_path / name for name in ("registration", "registrar.sig", "attest", "custodian.sig")
    )
    registration.write_bytes(_canonical(receipt))
    registration_sig.write_bytes(registrar.sign(registration_message(receipt)))
    document = {
        "version": 2,
        "revision": 1,
        "holdoutSha256": binding.holdout_sha256,
        "manifestSha256": binding.dataset_sha256,
        "bindingSha256": run_binding_sha256(binding),
        "registeredAt": receipt["registeredAt"],
        "openedAt": now.isoformat().replace("+00:00", "Z"),
        "registration": verify_registration(
            binding,
            registration.read_bytes(),
            registration_sig.read_bytes(),
            registrar.public_key(),
            now,
        ),
        "review": {"independence": ["SEPARATE_CONTEXT"], "fixturesOnly": True},
        "target": {
            "profile": binding.profile,
            "model": binding.model,
            "api": binding.api,
            "promptSha256": binding.prompt_sha256,
            "schemaSha256": binding.schema_sha256,
            "scorerSha256": binding.scorer_sha256,
        },
    }
    attest.write_bytes(_canonical(document))
    signature.write_bytes(custodian.sign(attestation_message(document)))
    with pytest.raises(ValueError, match="EVALUATION_CUSTODY_UNVERIFIED"):
        verify_custody(
            binding,
            attest,
            signature,
            custodian.public_key(),
            registrar_key=registrar.public_key(),
            registration_path=registration,
            registration_signature=registration_sig,
        )
