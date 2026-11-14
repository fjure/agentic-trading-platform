"""Classify browser relevance from a complete, checked-out pull request diff."""

import argparse
import re
import subprocess
import sys

SHA = re.compile(r"[a-f0-9]{40}")
DOC = re.compile(rb"(?:README\.md|docs/[A-Za-z0-9_./-]+\.md)")


def required(event: str, base: str, head: str) -> bool:
    if event != "pull_request" or not SHA.fullmatch(base) or not SHA.fullmatch(head):
        return True
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
            timeout=20, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if not diff.endswith(b"\0"):
        return True
    return any(not DOC.fullmatch(path) for path in diff[:-1].split(b"\0"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    sys.stdout.write("true\n" if required(args.event, args.base, args.head) else "false\n")


if __name__ == "__main__":
    main()
