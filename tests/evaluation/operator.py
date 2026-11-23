"""Operator entry point for an isolated, privately mounted evaluation deployment."""

import argparse
import json
import os
import stat
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import scorer
from .catalog import FIXTURES, _read_json, read_verified_holdout
from .custody import verify_custody
from .harness import Approval, RunBinding, run_holdout
from .operator_store import OperatorStore


def _owner_file(path: Path) -> Path:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise ValueError("EVALUATION_PRIVATE_FILE_REQUIRED")
    return path


def _approval(path: Path) -> Approval:
    value = _read_json(path)
    binding = value["binding"]
    binding["repetitions"] = tuple(binding["repetitions"])
    return Approval(
        value["id"],
        RunBinding(**binding),
        datetime.fromisoformat(value["expires_at"]),
        value["max_calls"],
        Decimal(value["max_cost"]),
        Decimal(value["cost_bound"]),
    )


def _paths(directory: Path) -> dict[str, Path]:
    files = {
        name: directory / filename
        for name, filename in {
            "dataset": "split-manifest.v1.json",
            "holdout": "holdout.v1.json",
            "prompt": "prompt.txt",
            "schema": "schema.json",
            "settings": "settings.json",
        }.items()
    }

    files["scorer"] = Path(scorer.__file__).resolve()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "register", "run", "status", "purge"))
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--inputs", type=Path)
    args = parser.parse_args()
    private = args.private_dir
    if args.command == "init":
        OperatorStore.provision(private)
        return 0
    store = OperatorStore(private)
    if args.command == "purge":
        print(store.purge_expired())
        return 0
    approval = _approval(_owner_file(private / "approval.json"))
    binding = approval.binding
    if args.command == "status":
        print(json.dumps(store.states(approval), sort_keys=True))
        return 0
    if args.inputs is None:
        parser.error("--inputs is required to register or run")
    if approval.expires_at.utcoffset() is None or approval.expires_at <= datetime.now(UTC):
        raise ValueError("EVALUATION_AUTHORIZATION_REQUIRED")
    rehearsal = _read_json(FIXTURES / "split-manifest.v1.json")
    if binding.holdout_sha256 == rehearsal["holdoutSha256"]:
        raise ValueError("EVALUATION_REHEARSAL_HOLDOUT")
    verify_custody(
        binding,
        args.inputs / "reviewer-attestation.json",
        args.inputs / "reviewer-attestation.sig",
        Ed25519PublicKey.from_public_bytes(_owner_file(private / "reviewer.pub").read_bytes()),
        registrar_key=Ed25519PublicKey.from_public_bytes(
            _owner_file(private / "registrar.pub").read_bytes(),
        ),
        registration_path=args.inputs / "registration.json",
        registration_signature=args.inputs / "registration.sig",
    )
    files = _paths(args.inputs)
    binding.verify(files)
    if args.command == "register":
        cases = read_verified_holdout(
            files["dataset"],
            files["holdout"],
            binding.dataset_sha256,
            binding.holdout_sha256,
        )
        store.register(approval, tuple(case["caseId"] for case in cases if not case["offlineOnly"]))
        return 0
    deployment = _read_json(_owner_file(private / "deployment.json"))
    module = deployment["supervisorModule"]
    factory = import_module(module).evaluation_supervisor
    supervisor = (
        factory(store, approval, files=files)
        if module == "services.runtime.research_evaluation" else factory(store, approval)
    )
    try:
        result = run_holdout(binding, files, approval, supervisor)
    finally:
        close = getattr(supervisor, "close", None)
        if close is not None:
            close()
    print(json.dumps(result, sort_keys=True))
    return int(not result["complete"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        raise SystemExit("Evaluation preflight or execution failed.") from None
