"""Validate custodian release against presigned operator and registrar decisions."""

from datetime import UTC, datetime
from hashlib import sha256

from cryptography.exceptions import InvalidSignature

from .catalog import _parse_json, decode_catalog
from .custody import _canonical, _instant, run_binding_sha256
from .registration import verify_registration
from .scorer import coverage_gaps


def approval_message(document):
    return b"atl-evaluation-operator-release-v1\0" + _canonical(document)


def release_attestation(
    artifacts,
    *,
    binding,
    revision,
    target_provider,
    registrar_key,
    operator_key,
    registration,
    registration_signature,
    decision,
    decision_signature,
):
    from .review_protocol import validate_review

    now = datetime.now(UTC)
    opened = now.isoformat().replace("+00:00", "Z")
    receipt = verify_registration(binding, registration, registration_signature, registrar_key, now)
    authorization = _parse_json(decision)
    if (
        decision != _canonical(authorization)
        or authorization.keys()
        != {
            "version",
            "bindingSha256",
            "revision",
            "receiptSha256",
            "expiresAt",
            "targetProvider",
        }
        or type(authorization["version"]) is not int
        or authorization["version"] != 1
        or type(authorization["revision"]) is not int
        or authorization["revision"] != revision
        or authorization["bindingSha256"] != run_binding_sha256(binding)
        or authorization["receiptSha256"] != receipt["receiptSha256"]
        or authorization["targetProvider"] != target_provider
        or now >= _instant(authorization["expiresAt"])
        or operator_key.public_bytes_raw() == registrar_key.public_bytes_raw()
    ):
        raise ValueError("EVALUATION_OPERATOR_APPROVAL_INVALID")
    try:
        operator_key.verify(decision_signature, approval_message(authorization))
    except InvalidSignature:
        raise ValueError("EVALUATION_OPERATOR_APPROVAL_INVALID") from None
    manifest, holdout = artifacts["split-manifest.v1.json"], artifacts["holdout.v1.json"]
    development, cases = decode_catalog(
        manifest,
        artifacts["development.v1.json"],
        holdout,
        expected_manifest=binding.dataset_sha256,
        expected_holdout=binding.holdout_sha256,
    )
    if coverage_gaps(development + cases):
        raise ValueError("EVALUATION_HOLDOUT_COVERAGE")
    review = validate_review(
        sha256(holdout).hexdigest(),
        cases,
        _parse_json(artifacts["primary-review.json"]),
        _parse_json(artifacts["secondary-review.json"]),
        target_provider=target_provider,
        target_model=binding.model,
    )
    if review["fixturesOnly"]:
        raise ValueError("EVALUATION_SYNTHETIC_REVIEW")
    target = {
        "profile": binding.profile,
        "model": binding.model,
        "api": binding.api,
        "promptSha256": binding.prompt_sha256,
        "schemaSha256": binding.schema_sha256,
        "scorerSha256": binding.scorer_sha256,
    }
    return _canonical(
        {
            "version": 2,
            "revision": revision,
            "holdoutSha256": binding.holdout_sha256,
            "manifestSha256": binding.dataset_sha256,
            "bindingSha256": run_binding_sha256(binding),
            "registeredAt": receipt["registeredAt"],
            "openedAt": opened,
            "registration": receipt,
            "review": review,
            "target": target,
        }
    )
