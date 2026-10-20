"""Narrow authorized GH CLI sync; source tasks remain authoritative. Default: preview."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "fjure/agentic-trading-platform"
CHANGE = "dashboard-trading-demo"
MAPPING = ROOT / "docs/development/github-demo-issue-sync.json"
BLOCK = re.compile(r"<!-- openspec-managed:start -->.*?<!-- openspec-managed:end -->", re.S)
ORDER = ("DEMO-01", "DEMO-02", "DEMO-05", "DEMO-06", "DEMO-03", "DEMO-04")
EXTRA_DEPS = {"DEMO-03": ["DEMO-05", "DEMO-06"], "DEMO-04": ["DEMO-05", "DEMO-06"]}
EXTRAS = {
    "DEMO-01": "Zusätzlich sämtliche Setup-/Profilverträge für REQ-SETUP-001 bis REQ-SETUP-005.",
    "DEMO-02": "Zusätzlich unabhängige finanzielle Basis pro Run; REQ-SETUP-004 und REQ-SETUP-005.",
    "DEMO-03": "Zusätzlich Alpaca-/Jev-Dialoge, Secretlöschung, Budget-/Variantenwahl; "
    "REQ-SETUP-001 bis REQ-SETUP-005.",
    "DEMO-04": "Zusätzlich echte Browserabnahme der Dialog-/Canary-/Budgetprüfungen; "
    "REQ-SETUP-001 bis REQ-SETUP-005.",
}
EVIDENCE = {
    "DEMO-01": "demo-01-contracts.md",
    "DEMO-02": "demo-02-runtime.md",
    "DEMO-03": "demo-03-dashboard.md",
    "DEMO-04": "demo-04-browser.md",
    "DEMO-05": "demo-05-setup.md",
    "DEMO-06": "demo-06-budget.md",
}


def gh(*args):
    result = subprocess.run(
        ["gh", *args], cwd=ROOT, capture_output=True, encoding="utf-8", check=False
    )
    if result.returncode:
        raise RuntimeError("GH CLI operation failed; diagnostic output withheld")
    return json.loads(result.stdout) if result.stdout.strip() else None


def tasks():
    content = (ROOT / f"openspec/changes/{CHANGE}/tasks.md").read_text(encoding="utf-8")
    items = {}
    for match in re.finditer(r"^- \[([ x])\] ([^\n]*?(DEMO-\d{2}) ([^:]+):[^\n]+)$", content, re.M):
        done, line, task, title = match.groups()
        if task in items:
            raise ValueError("Ambiguous source task")
        raw_deps = re.search(r"Depends on: (.*?)\. Paths:", line).group(1)
        dependencies = [] if raw_deps == "none" else raw_deps.split(", ")
        dependencies += EXTRA_DEPS.get(task, [])
        evidence = ROOT / "docs/evidence" / EVIDENCE[task]
        if done == "x" and (
            not evidence.is_file()
            or not re.search(r"passed|bestanden", evidence.read_text(encoding="utf-8"), re.I)
        ):
            raise ValueError("Completion requires task evidence")
        items[task] = {
            "title": title,
            "line": line,
            "done": done == "x",
            "dependencies": dependencies,
        }
    if set(items) != set(ORDER):
        raise ValueError("Unexpected demo task inventory")
    visited = set()
    for task in ORDER:
        if not set(items[task]["dependencies"]) <= visited:
            raise ValueError("Unresolved or cyclic dependencies")
        visited.add(task)
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = tasks()
    if (
        gh("api", "user")["login"] != "fjure"
        or gh("repo", "view", REPOSITORY, "--json", "nameWithOwner")["nameWithOwner"] != REPOSITORY
    ):
        raise ValueError("Unexpected GitHub identity or repository")
    origin = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    if origin != f"https://github.com/{REPOSITORY}.git":
        raise ValueError("Unexpected origin")
    issues = gh(
        "issue",
        "list",
        "--repo",
        REPOSITORY,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,body,state,labels,url",
    )
    if len(issues) >= 1000:
        raise ValueError("Issue enumeration requires pagination")
    by_task = {}
    for task in ORDER:
        marker = f"<!-- openspec-task-id: {task} -->"
        matches = [issue for issue in issues if marker in (issue.get("body") or "")]
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous task; initial creation requires reviewed plan")
        issue = gh("api", f"repos/{REPOSITORY}/issues/{matches[0]['number']}")
        if "pull_request" in issue or issue["body"].count(marker) != 1:
            raise ValueError("Not a unique task issue")
        by_task[task] = issue
    entries = []
    updated = 0
    for task in ORDER:
        item, issue = source[task], by_task[task]
        blocks = BLOCK.findall(issue["body"])
        if len(blocks) != 1:
            raise ValueError("Missing or ambiguous managed block")
        dependencies = (
            "\n".join(f"- {dep}: #{by_task[dep]['number']}" for dep in item["dependencies"])
            or "- none"
        )
        state = "closed" if item["done"] else "open"
        if issue["state"] == "closed" and state == "open":
            raise ValueError("Closed issue conflicts with open source; no automatic reopen")
        evidence = f"docs/evidence/{EVIDENCE[task]}" if item["done"] else "pending"
        block = (
            f"<!-- openspec-managed:start -->\nChange: {CHANGE}\nTask: {task}\n\n"
            + item["line"]
            + "\n\n"
            + EXTRAS.get(task, "")
            + "\n\nDepends on:\n"
            + dependencies
            + "\n\nEvidence: "
            + evidence
            + f"\nSource: openspec/changes/{CHANGE}/tasks.md\n<!-- openspec-managed:end -->"
        )
        body = issue["body"].replace(blocks[0], block)
        title = f"[{task}] {item['title']}"
        labels = {entry["name"] for entry in issue["labels"]}
        required_labels = {"openspec", "change:" + CHANGE}
        changed = (
            body != issue["body"]
            or title != issue["title"]
            or state != issue["state"]
            or not required_labels <= labels
        )
        if changed and args.apply:
            payload = ROOT / f".tooling/output/{task}-sync.json"
            payload.parent.mkdir(parents=True, exist_ok=True)
            payload.write_text(
                json.dumps(
                    {
                        "title": title,
                        "body": body,
                        "state": state,
                        "labels": sorted(labels | required_labels),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            gh(
                "api",
                f"repos/{REPOSITORY}/issues/{issue['number']}",
                "--method",
                "PATCH",
                "--input",
                str(payload),
            )
        updated += int(changed)
        verified = gh("api", f"repos/{REPOSITORY}/issues/{issue['number']}")
        if args.apply and (
            verified["body"] != body
            or verified["title"] != title
            or verified["state"] != state
            or "pull_request" in verified
            or not required_labels <= {entry["name"] for entry in verified["labels"]}
        ):
            raise ValueError("Issue read-back mismatch")
        entries.append(
            {
                "taskId": task,
                "issueNumber": issue["number"],
                "issueUrl": verified["html_url"],
                "state": state,
                "contentHash": hashlib.sha256(block.encode("utf-8")).hexdigest(),
                "dependsOn": item["dependencies"],
                "syncedAt": datetime.now(UTC).isoformat(),
            }
        )
    if args.apply:
        result = {
            "schemaVersion": 1,
            "repository": REPOSITORY,
            "changeId": CHANGE,
            "status": "synchronized-read-back-verified",
            "transport": "official-gh-cli-user-authorized-fallback",
            "lastReadBackAt": datetime.now(UTC).isoformat(),
            "lastSyncRun": {
                "created": 0,
                "updated": updated,
                "unchanged": len(ORDER) - updated,
                "duplicates": 0,
            },
            "tasks": sorted(entries, key=lambda entry: entry["taskId"]),
        }
        MAPPING.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"apply": args.apply, "updates": updated, "unchanged": len(ORDER) - updated}))


if __name__ == "__main__":
    main()
