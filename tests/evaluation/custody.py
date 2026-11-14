"""Verify the decision of the separately trusted holdout custodian."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_FIELDS = {
    "version", "holdoutSha256", "manifestSha256", "bindingSha256", "registeredAt",
    "openedAt",
}
_DOMAIN = b"atl-research-holdout-custody-v1\0"


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")


def attestation_message(document: dict) -> bytes:
    """Build the domain-bound bytes to sign from a reviewer-authored document."""
    return _DOMAIN + _canonical(document)


def run_binding_sha256(binding) -> str:
    return sha256(_canonical(asdict(binding))).hexdigest()


def _instant(value: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("custody timestamps must be UTC")
    return datetime.fromisoformat(value)


def verify_custody(
    binding, document_path: Path, signature_path: Path, trusted_key: Ed25519PublicKey,
) -> None:
    """Reject absent, mismatched or unverifiable attestations.

    The caller provisions trusted_key independently of the document and holdout.
    """
    def unique_fields(items):
        document = dict(items)
        if len(document) != len(items):
            raise ValueError("duplicate custody field")
        return document

    try:
        with document_path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("custody document too large")
        document = json.loads(raw, object_pairs_hook=unique_fields)
        with signature_path.open("rb") as stream:
            signature = stream.read(65)
        if (
            not isinstance(trusted_key, Ed25519PublicKey) or type(document) is not dict
            or document.keys() != _FIELDS or type(document["version"]) is not int
            or document["version"] != 1 or len(signature) != 64
            or document["holdoutSha256"] != binding.holdout_sha256
            or document["manifestSha256"] != binding.dataset_sha256
            or document["bindingSha256"] != run_binding_sha256(binding)
            or not _instant(document["registeredAt"]) < _instant(document["openedAt"])
            <= datetime.now(UTC)
        ):
            raise ValueError("invalid custody binding")
        trusted_key.verify(signature, attestation_message(document))
    except (InvalidSignature, OSError, ValueError, TypeError) as error:
        raise ValueError("EVALUATION_CUSTODY_UNVERIFIED") from error
