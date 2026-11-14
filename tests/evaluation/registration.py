"""Canonical signed registration evidence from the separately pinned external registrar."""

import json
from datetime import UTC, datetime
from hashlib import sha256

from cryptography.exceptions import InvalidSignature

from .custody import _canonical, _instant, run_binding_sha256

_DOMAIN = b"atl-evaluation-external-registration-v1\0"
_FIELDS = {"version", "reference", "bindingSha256", "registeredAt"}


def registration_message(receipt):
    if (
        type(receipt) is not dict
        or receipt.keys() != _FIELDS
        or type(receipt["version"]) is not int
        or receipt["version"] != 1
        or type(receipt["reference"]) is not str
        or not 1 <= len(receipt["reference"]) <= 200
        or not receipt["reference"].isascii()
        or any(ord(char) < 33 for char in receipt["reference"])
    ):
        raise ValueError("EVALUATION_REGISTRATION_INVALID")
    return _DOMAIN + _canonical(receipt)


def verify_registration(binding, raw, signature, pinned_key, opened_at):
    """Check a signed registrar receipt for an append-only external reference."""
    try:
        if len(raw) > 4096 or len(signature) != 64:
            raise ValueError
        receipt = json.loads(raw)
        if raw != _canonical(receipt):
            raise ValueError
        message = registration_message(receipt)
        registered = _instant(receipt["registeredAt"])
        if receipt["bindingSha256"] != run_binding_sha256(
            binding
        ) or not registered < opened_at <= datetime.now(UTC):
            raise ValueError
        pinned_key.verify(signature, message)
        return receipt | {"receiptSha256": sha256(raw).hexdigest()}
    except (InvalidSignature, KeyError, ValueError, TypeError):
        raise ValueError("EVALUATION_REGISTRATION_INVALID") from None
