"""Narrow, user-authorized GH CLI issue completion with full read-back validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "fjure/agentic-trading-platform"
OUTPUT = ROOT / ".tooling/output"
MARKER = re.compile(r"<!-- openspec-task-id: (PTV-\d+) -->")
BLOCK = re.compile(r"<!-- openspec-managed:start -->.*?<!-- openspec-managed:end -->", re.S)


def gh(*args: str) -> str:
    if args[0] == "api":
        args = (*args, "--hostname", "github.com")
    completed = subprocess.run(
        ["gh", *args], cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False
    )
    if completed.returncode:
        raise RuntimeError("GH CLI operation failed; diagnostic output withheld")
    return completed.stdout


def managed(body: str) -> str:
    blocks = BLOCK.findall(body)
    if len(blocks) != 1:
        raise ValueError("missing or ambiguous managed block")
    return blocks[0]


def validate_issue(issue: dict, task: dict, mapping: dict) -> None:
    if "pull_request" in issue:
        raise ValueError("PR mutation forbidden")
    assert issue["number"] == mapping["issueNumber"]
    assert issue["url"] == mapping["issueUrl"]
    assert MARKER.findall(issue["body"]) == [task["id"]]
    assert issue["title"] == f"[{task['id']}] {task['title']}"
    labels = {label["name"] for label in issue["labels"]}
    assert {"openspec", "change:paper-trading-vertical-slice"} <= labels
    block = managed(issue["body"])
    assert f"Requirements: {', '.join(task['requirements'])}" in block
    assert all(f"`{path}`" in block for path in task["paths"])
    assert re.findall(r"^- (PTV-\d+): #", block, re.M) == task["dependsOn"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"PTV-\d{2}", args.task_id):
        raise ValueError("invalid task ID")
    evidence_path = args.evidence_file.resolve()
    assert evidence_path.is_relative_to(ROOT), "Evidence must be inside repository"
    evidence = evidence_path.read_text(encoding="utf-8").strip()
    assert evidence and "\n" not in evidence, "Single evidence paragraph required"
    assert gh("api", "user", "--jq", ".login").strip() == "fjure"
    version = gh("--version").splitlines()[0].split()[2]
    repository = json.loads(
        gh("repo", "view", f"github.com/{REPOSITORY}", "--json", "nameWithOwner,url")
    )
    assert repository["nameWithOwner"] == REPOSITORY
    assert repository["url"] == f"https://github.com/{REPOSITORY}"
    mapping_path = ROOT / "docs/development/github-issue-sync.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert mapping["repository"] == REPOSITORY
    tasks = runpy.run_path(str(ROOT / "scripts/validate-plan.py"))["read_tasks"]()
    by_task = {task["id"]: task for task in tasks}
    mapped = {task["taskId"]: task for task in mapping["tasks"]}
    assert set(mapped) == set(by_task)
    assert args.task_id in by_task and by_task[args.task_id]["done"]
    assert (
        ROOT / "openspec/changes/paper-trading-vertical-slice/implementation-evidence.md"
    ).is_file()
    raw = json.loads(
        gh("api", f"repos/{REPOSITORY}/issues?state=all&per_page=100", "--paginate", "--slurp")
    )
    assert not any(
        "pull_request" in item and MARKER.search(item.get("body") or "")
        for page in raw
        for item in page
    ), "Task marker on PR"
    issues = json.loads(
        gh(
            "issue",
            "list",
            "--repo",
            f"github.com/{REPOSITORY}",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,title,body,state,labels,url",
        )
    )
    issues = [issue for issue in issues if MARKER.search(issue.get("body") or "")]
    assert len(issues) == len(tasks)
    by_number = {issue["number"]: issue for issue in issues}
    assert len(by_number) == len(issues)
    for task in tasks:
        entry = mapped[task["id"]]
        issue = by_number[entry["issueNumber"]]
        validate_issue(issue, task, entry)
        assert hashlib.sha256(managed(issue["body"]).encode()).hexdigest() == entry["contentHash"]
        assert issue["state"].lower() == entry["state"]
        if task["id"] != args.task_id:
            assert task["done"] == (issue["state"].lower() == "closed")
    entry = mapped[args.task_id]
    before = by_number[entry["issueNumber"]]
    endpoint = f"repos/{REPOSITORY}/issues/{entry['issueNumber']}"
    single = json.loads(gh("api", endpoint))
    assert "pull_request" not in single and single["body"] == before["body"]
    old = managed(before["body"])
    updated, count = re.subn(
        r"Testevidenz \([^\n]+\):[^\n]*", lambda _: f"Testevidenz (bestanden): {evidence}", old
    )
    assert count == 1, "Evidence section missing or duplicated"
    updated = updated.replace(
        "Anwendungstasks bleiben in diesem Planungsdurchlauf offen.",
        f"OpenSpec Apply: {args.task_id} abgeschlossen; weitere Tasks separat.",
    )
    body = before["body"].replace(old, updated, 1)
    changed = body != before["body"] or before["state"].lower() != "closed"
    payload_path = OUTPUT / f"{args.task_id.lower()}-issue-patch.json"
    payload_path.write_text(
        json.dumps(
            {"body": body, "state": "closed", "state_reason": "completed"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print(
        f"PREVIEW {args.task_id}: issue #{entry['issueNumber']}; changed={changed}; "
        "only managed body/state"
    )
    if not args.apply:
        return
    if changed:
        latest = json.loads(gh("api", endpoint))
        assert "pull_request" not in latest and latest["body"] == before["body"]
        assert latest["state"] == before["state"].lower()
        gh("api", endpoint, "--method", "PATCH", "--input", str(payload_path), "--silent")
    readback = json.loads(gh("api", endpoint))
    assert readback["body"] == body and readback["state"] == "closed"
    assert readback["state_reason"] == "completed"
    after = json.loads(
        gh(
            "issue",
            "list",
            "--repo",
            f"github.com/{REPOSITORY}",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,title,body,state,labels,url",
        )
    )
    after = [issue for issue in after if MARKER.search(issue.get("body") or "")]
    assert len(after) == len(issues)
    verified = []
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for issue in sorted(after, key=lambda item: item["number"]):
        task_id = MARKER.findall(issue["body"])[0]
        task = by_task[task_id]
        original = by_number[issue["number"]]
        validate_issue(issue, task, mapped[task_id])
        assert issue["body"] == (body if task_id == args.task_id else original["body"])
        assert task["done"] == (issue["state"].lower() == "closed")
        assert issue["labels"] == original["labels"], "Manual labels must survive"
        verified.append(
            dict(
                taskId=task_id,
                issueNumber=issue["number"],
                issueUrl=issue["url"],
                title=issue["title"],
                body=issue["body"],
                managed=managed(issue["body"]),
                state=issue["state"].lower(),
                labels=[label["name"] for label in issue["labels"]],
                dependsOn=mapped[task_id]["dependsOn"],
                verifiedAt=now,
                completionEvidence="openspec/changes/paper-trading-vertical-slice/implementation-evidence.md"
                if task["done"]
                else None,
                transportVersion=version,
            )
        )
    (OUTPUT / "verified-issue-readbacks.json").write_text(
        json.dumps(verified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    subprocess.run(
        [
            str(Path(sys.executable)),
            str(ROOT / "scripts/record-issue-sync.py"),
            "--complete",
            "--transport",
            "github-cli",
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"PASS {args.task_id}: {len(verified)} CLI read-backs verified; writes={int(changed)}")


if __name__ == "__main__":
    main()
