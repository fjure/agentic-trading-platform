"""Redacted credential-pattern scan of deliverables, never private credential stores."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"\bgh[pousr]_" + r"[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_" + r"[A-Za-z0-9_]{30,}\b"),
    re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:api_key|api_secret|password|bearer_token)\s*[:=]\s*[\"']"
        + r"[A-Za-z0-9+/=_-]{20,}[\"']"
    ),
]


def main() -> int:
    # Excludes .env/secret directories by Git ignore and explicitly excludes app/tool caches.
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    failures = []
    count = 0
    for name in filter(None, paths):
        path = Path(name)
        if any(p in {".git", "secrets", "node_modules", ".venv"} for p in path.parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        try:
            content = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        count += 1
        if any(pattern.search(content) for pattern in PATTERNS):
            failures.append(name)
    for name in failures:
        print(f"FAIL credential pattern in {name} (value redacted)")
    print(f"Credential pattern scan: {count} source files, {len(failures)} findings.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
