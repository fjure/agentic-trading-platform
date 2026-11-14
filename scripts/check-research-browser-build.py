"""Record or verify the exact Angular build used by the Research browser gate."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

LOCKFILE = Path("apps/dashboard/package-lock.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(root: Path) -> dict:
    files = sorted(path for path in (root / "browser").rglob("*") if path.is_file())
    if not (root / "browser/index.html").is_file() or any(path.is_symlink() for path in files):
        raise ValueError("Angular production build is missing or invalid")
    return {
        "sourceSha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "lockSha256": digest(LOCKFILE),
        "files": {path.relative_to(root).as_posix(): digest(path) for path in files},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("record", "check"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    manifest = args.root / "research-build.json"
    try:
        expected = identity(args.root)
        if args.command == "record":
            manifest.write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
        elif json.loads(manifest.read_text(encoding="utf-8")) != expected:
            raise ValueError("Angular build, source SHA or lockfile mismatch")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
