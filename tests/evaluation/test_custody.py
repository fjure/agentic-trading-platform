from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .custody import attestation_message, run_binding_sha256, verify_custody
from .harness import RunBinding


def test_custody_requires_independent_key_and_exact_binding(tmp_path):
    import json

    binding = RunBinding("test", "synthetic", "v1", *(["a" * 64] * 6))
    key = Ed25519PrivateKey.generate()
    opened = datetime.now(UTC)
    registered = opened - timedelta(seconds=1)
    document = {
        "version": 1,
        "holdoutSha256": binding.holdout_sha256,
        "manifestSha256": binding.dataset_sha256,
        "bindingSha256": run_binding_sha256(binding),
        "registeredAt": registered.isoformat().replace("+00:00", "Z"),
        "openedAt": opened.isoformat().replace("+00:00", "Z"),
    }
    data, signature = tmp_path / "custody.json", tmp_path / "custody.sig"
    data.write_text(json.dumps(document), encoding="utf-8")
    signature.write_bytes(key.sign(attestation_message(document)))
    verify_custody(binding, data, signature, key.public_key())
    with pytest.raises(ValueError, match="EVALUATION_CUSTODY_UNVERIFIED"):
        verify_custody(binding, data, signature, Ed25519PrivateKey.generate().public_key())
    with pytest.raises(ValueError, match="EVALUATION_CUSTODY_UNVERIFIED"):
        verify_custody(replace(binding, prompt_sha256="b" * 64), data, signature, key.public_key())
    document["openedAt"] = document["registeredAt"]
    data.write_text(json.dumps(document), encoding="utf-8")
    signature.write_bytes(key.sign(attestation_message(document)))
    with pytest.raises(ValueError, match="EVALUATION_CUSTODY_UNVERIFIED"):
        verify_custody(binding, data, signature, key.public_key())
