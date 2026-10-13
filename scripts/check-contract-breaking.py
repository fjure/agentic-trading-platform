"""Compare the canonical contract to a real Git baseline using pinned oasdiff."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/http/control-api.openapi.yaml"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def verified_unborn() -> bool:
    worktree = git("rev-parse", "--is-inside-work-tree")
    if worktree.returncode or worktree.stdout.strip() != "true":
        raise ValueError("cannot verify Git worktree")
    symbolic = git("symbolic-ref", "--quiet", "HEAD")
    if symbolic.returncode or not symbolic.stdout.strip().startswith("refs/heads/"):
        return False
    missing = git("show-ref", "--verify", "--quiet", symbolic.stdout.strip())
    if missing.returncode not in (0, 1):
        raise ValueError("cannot verify unborn Git reference")
    return missing.returncode == 1


def check(base_ref: str = "main", initial: bool = False) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./^-]*", base_ref):
        raise ValueError("invalid baseline ref")
    base = git("rev-parse", "--verify", "--end-of-options", f"{base_ref}^{{commit}}")
    if initial:
        parents = git("rev-list", "--parents", "-n", "1", "HEAD")
        if parents.returncode and not verified_unborn():
            raise ValueError("cannot verify initial contract: Git history is unreadable")
        if parents.returncode == 0 and len(parents.stdout.split()) != 1:
            raise ValueError("initial-contract flag requires an unborn or root commit")
        result = {"status": "initial-contract", "reason": "no predecessor commit"}
    elif base.returncode:
        if base_ref == "main" and verified_unborn():
            result = {"status": "initial-contract", "reason": "unborn repository/main"}
        else:
            raise ValueError("baseline ref is unavailable; refusing a fabricated diff")
    else:
        commit = base.stdout.strip()
        tree = git("ls-tree", "--name-only", commit, "--", CONTRACT)
        if tree.returncode:
            raise ValueError("cannot inspect baseline tree; refusing an initial-contract claim")
        if not tree.stdout.strip():
            result = {
                "status": "initial-contract",
                "baseCommit": commit,
                "reason": "verified base tree has no contract",
            }
        else:
            output = ROOT / ".tooling/output"
            output.mkdir(parents=True, exist_ok=True)
            baseline = output / "control-api.base.openapi.yaml"
            content = git("show", f"{commit}:{CONTRACT}")
            if content.returncode:
                raise ValueError("cannot read verified baseline contract")
            baseline.write_text(content.stdout, encoding="utf-8")
            exe = ROOT / ".tooling/contracts/oasdiff/oasdiff.exe"
            if not exe.is_file():
                raise ValueError("pinned oasdiff 1.33.0 missing; run contract bootstrap")
            version = subprocess.run(
                [str(exe), "--version"], capture_output=True, text=True, check=True
            ).stdout.strip()
            if version != "oasdiff version 1.33.0":
                raise ValueError("oasdiff version mismatch")
            subprocess.run(
                [str(exe), "breaking", str(baseline), str(ROOT / CONTRACT), "--fail-on", "ERR"],
                check=True,
            )
            result = {"status": "semantic-diff-passed", "baseCommit": commit, "checker": version}
    result["contractSha256"] = hashlib.sha256((ROOT / CONTRACT).read_bytes()).hexdigest()
    output = ROOT / ".tooling/output"
    output.mkdir(parents=True, exist_ok=True)
    (output / "contract-base-result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--initial-contract", action="store_true")
    args = parser.parse_args()
    check(args.base_ref, args.initial_contract)
