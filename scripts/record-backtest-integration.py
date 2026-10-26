"""Read-only Git proof before recording the exact OpenSpec completion checkbox."""

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
task, branch = sys.argv[1:]
assert re.fullmatch(r"BKT-0[1-9]", task)
mapping = json.loads((ROOT / "docs/development/github-backtesting-issue-sync.json").read_text())
issue = next(item["issueNumber"] for item in mapping["tasks"] if item["taskId"] == task)
assert re.fullmatch(f"codex/ptv-{issue}-[a-z-]+", branch)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


assert git("remote", "get-url", "origin") == "https://github.com/fjure/agentic-trading-platform.git"
commit, main = git("rev-parse", branch), git("rev-parse", "main")
assert git("ls-remote", "origin", "refs/heads/"+branch).split()[0] == commit
assert git("ls-remote", "origin", "refs/heads/main").split()[0] == main
subprocess.run(["git", "merge-base", "--is-ancestor", commit, main], cwd=ROOT, check=True)
path = ROOT / "docs/development/github-backtesting-git-integrations.json"
data = json.loads(path.read_text())
assert data["repository"] == "fjure/agentic-trading-platform"
assert not any(item["taskId"] == task for item in data["tasks"])
data["tasks"].append(dict(taskId=task, issueNumber=issue, branch=branch, commit=commit,
                          mergeCommit=main, remoteVerified=True, verifiedAt=datetime.now(UTC).isoformat()))
path.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
path = ROOT / "openspec/changes/point-in-time-research-backtesting/tasks.md"
source = path.read_text(encoding="utf-8")
source, count = re.subn(r"^- \[ \] (\d+\.\d+ "+task+r" )", r"- [x] \1", source, flags=re.M)
assert count == 1, "exact pending checkbox required"
path.write_text(source, encoding="utf-8")
print(f"{task}: branch/main verified; exact task marked complete")
