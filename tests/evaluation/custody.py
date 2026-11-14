"""Verify the decision of the separately trusted holdout custodian."""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_FIELDS = {
    "version",
    "holdoutSha256",
    "manifestSha256",
    "bindingSha256",
    "registeredAt",
    "openedAt",
}
_DOMAIN = b"atl-research-holdout-custody-v1\0"


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def attestation_message(document: dict) -> bytes:
    """Build the domain-bound bytes to sign from a reviewer-authored document."""
    return (
        _DOMAIN if document.get("version") == 1 else b"atl-ai-holdout-custody-v2\0"
    ) + _canonical(document)


def run_binding_sha256(binding) -> str:
    return sha256(_canonical(asdict(binding))).hexdigest()


def _instant(value: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("custody timestamps must be UTC")
    return datetime.fromisoformat(value)


def verify_custody(
    binding,
    document_path: Path,
    signature_path: Path,
    trusted_key: Ed25519PublicKey,
    *,
    registrar_key: Ed25519PublicKey | None = None,
    registration_path: Path | None = None,
    registration_signature: Path | None = None,
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
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("custody document too large")
        document = json.loads(raw, object_pairs_hook=unique_fields)
        with signature_path.open("rb") as stream:
            signature = stream.read(65)
        if (
            not isinstance(trusted_key, Ed25519PublicKey)
            or type(document) is not dict
            or type(document["version"]) is not int
            or len(signature) != 64
            or document["holdoutSha256"] != binding.holdout_sha256
            or document["manifestSha256"] != binding.dataset_sha256
            or document["bindingSha256"] != run_binding_sha256(binding)
            or not _instant(document["registeredAt"])
            < _instant(document["openedAt"])
            <= datetime.now(UTC)
        ):
            raise ValueError("invalid custody binding")
        trusted_key.verify(signature, attestation_message(document))
        if registrar_key is None:
            if document["version"] != 1 or document.keys() != _FIELDS:
                raise ValueError("unexpected custody version")
        else:
            from .registration import verify_registration

            if (
                document["version"] != 2
                or document.keys() != _FIELDS | {"revision", "registration", "review", "target"}
                or raw != _canonical(document)
                or type(document["revision"]) is not int
                or document["revision"] < 1
                or trusted_key.public_bytes_raw() == registrar_key.public_bytes_raw()
                or document["target"]
                != {
                    "profile": binding.profile,
                    "model": binding.model,
                    "api": binding.api,
                    "promptSha256": binding.prompt_sha256,
                    "schemaSha256": binding.schema_sha256,
                    "scorerSha256": binding.scorer_sha256,
                }
                or not document["review"].get("independence")
                or registration_path is None
                or registration_signature is None
            ):
                raise ValueError("invalid AI custody")
            receipt = verify_registration(
                binding,
                registration_path.read_bytes(),
                registration_signature.read_bytes(),
                registrar_key,
                _instant(document["openedAt"]),
            )
            if (
                receipt != document["registration"]
                or receipt["registeredAt"] != document["registeredAt"]
            ):
                raise ValueError("invalid registration binding")
    except (InvalidSignature, OSError, ValueError, TypeError) as error:
        raise ValueError("EVALUATION_CUSTODY_UNVERIFIED") from error
