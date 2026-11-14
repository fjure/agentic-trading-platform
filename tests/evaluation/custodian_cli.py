"""Run custody under the dedicated operator-provisioned custody identity."""

import argparse
import os
from functools import partial
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .catalog import _read_json
from .custodian import Custodian
from .custody_release import release_attestation
from .harness import RunBinding
from .operator import _owner_file

PRIVATE_INPUTS = (
    "holdout.v1.json",
    "split-manifest.v1.json",
    "development.v1.json",
    "primary-review.json",
    "secondary-review.json",
)


def _trusted_key(config, name):
    return Ed25519PublicKey.from_public_bytes(_owner_file(config / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "seal", "release"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--revision", required=True, type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if os.name != "posix" or args.revision < 1:
        raise ValueError("CUSTODY_ISOLATION_REQUIRED")
    config = args.config
    settings = _read_json(_owner_file(config / "custodian.json"))
    parameters = dict(
        development_uid=settings["developmentUid"],
        reviewer_uid=settings["reviewerUid"],
        encryption_key_file=config / "storage.key",
        signer_key_file=config / "custodian.key",
        release_ledger=Path(settings["releaseLedger"]),
    )
    state = Path(settings["state"])
    if state.resolve() == parameters["release_ledger"].resolve():
        raise ValueError("CUSTODY_SEPARATE_LEDGER_REQUIRED")
    if args.command == "init":
        Custodian.provision(state, **parameters)
        return
    custodian = Custodian(state, **parameters)
    if args.command == "seal":
        custodian.seal(
            str(args.revision),
            {name: _owner_file(args.input / name).read_bytes() for name in PRIVATE_INPUTS},
        )
        return
    if args.output is None:
        parser.error("--output is required for release")
    args.output.mkdir(mode=0o700)
    value = _read_json(_owner_file(config / "binding.json"))
    value["repetitions"] = tuple(value["repetitions"])
    binding = RunBinding(**value)
    registration = _owner_file(config / "registration.json").read_bytes()
    signature = _owner_file(config / "registration.sig").read_bytes()
    artifacts = custodian.release(
        str(args.revision),
        verify=partial(
            release_attestation,
            binding=binding,
            revision=args.revision,
            target_provider=settings["targetProvider"],
            registrar_key=_trusted_key(config, "registrar.pub"),
            operator_key=_trusted_key(config, "operator.pub"),
            registration=registration,
            registration_signature=signature,
            decision=_owner_file(config / "release.json").read_bytes(),
            decision_signature=_owner_file(config / "release.sig").read_bytes(),
        ),
    )
    for name, data in (
        artifacts.items()
        | {
            "registration.json": registration,
            "registration.sig": signature,
        }.items()
    ):
        fd = os.open(args.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        raise SystemExit("Custody operation denied.") from None
