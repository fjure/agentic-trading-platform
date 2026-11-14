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
        "version": 1, "reference": "fixture-log:123",
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
