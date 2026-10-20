"""Eng begrenzter APW-Abgleich vorhandener Issues; Vorschau ohne --apply."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "fjure/agentic-trading-platform"
CHANGE = "alpaca-paper-workflow"
SOURCE = f"openspec/changes/{CHANGE}/tasks.md"
MAPPING = ROOT / "docs/development/github-paper-workflow-issue-sync.json"
BLOCK = re.compile(r"<!-- openspec-managed:start -->.*?<!-- openspec-managed:end -->", re.S)


def gh(*args, payload=None):
    command = ["gh", *args]
    if payload is not None:
        dest = ROOT / ".tooling/output/apw-sync-request.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        command += ["--input", str(dest)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("GH operation failed; diagnostic output withheld")
    return json.loads(result.stdout) if result.stdout.strip() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    apply = parser.parse_args().apply
    origin = subprocess.check_output(
        ["git", "remote", "get-url", "origin"], cwd=ROOT, text=True
    ).strip()
    if origin != f"https://github.com/{REPO}.git":
        raise ValueError("Unexpected origin")
    if gh("api", "user")["login"] != "fjure":
        raise ValueError("Unexpected identity")
    if gh("repo", "view", REPO, "--json", "nameWithOwner")["nameWithOwner"] != REPO:
        raise ValueError("Unexpected repository")
    tasks = []
    for match in re.finditer(
        r"^- \[([ x])\] (\d+\.\d+ (APW-\d{2}) ([^:]+):[^\n]+)$",
        (ROOT / SOURCE).read_text(encoding="utf-8"), re.M,
    ):
        done, line, task, title = match.groups()
        deps = re.search(r"Depends on: (.*?)\. Paths:", line).group(1)
        tasks.append((task, title, line, done == "x", [] if deps == "none" else deps.split(", ")))
    if [t[0] for t in tasks] != [f"APW-{i:02d}" for i in range(1, 11)]:
        raise ValueError("Unexpected task inventory")
    seen = set()
    for task, _, _, done, deps in tasks:
        if not set(deps) <= seen:
            raise ValueError("Unresolved or cyclic dependencies")
        seen.add(task)
        if done:
            evidence = ROOT / f"docs/evidence/{task.lower()}.md"
            if not evidence.is_file() or not re.search(
                r"passed|bestanden", evidence.read_text(encoding="utf-8"), re.I
            ):
                raise ValueError("Task completion requires evidence")
    persisted = json.loads(MAPPING.read_text(encoding="utf-8"))
    if persisted["repository"] != REPO or persisted["changeId"] != CHANGE:
        raise ValueError("Unexpected mapping")
    mapped = {entry["taskId"]: entry["issueNumber"] for entry in persisted["tasks"]}
    inventory = gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "1000",
                   "--json", "number,title,body,state,labels,url")
    if len(inventory) >= 1000:
        raise ValueError("Issue enumeration requires pagination")
    assigned = {}
    for task, *_ in tasks:
        marker = f"<!-- openspec-task-id: {task} -->"
        matches = [i for i in inventory if marker in (i.get("body") or "")]
        if len(matches) != 1 or matches[0]["number"] != mapped.get(task):
            raise ValueError("Missing, ambiguous or conflicting task mapping")
        issue = gh("api", f"repos/{REPO}/issues/{mapped[task]}")
        if "pull_request" in issue or issue["body"].count(marker) != 1:
            raise ValueError("Not a unique task issue")
        assigned[task] = issue
    entries, updates = [], 0
    for task, title, line, done, deps in tasks:
        issue = assigned[task]
        blocks = BLOCK.findall(issue["body"])
        if len(blocks) != 1:
            raise ValueError("Missing or ambiguous managed block")
        links = "\n".join(f"- {dep}: #{mapped[dep]}" for dep in deps) or "- none"
        evidence = f"docs/evidence/{task.lower()}.md" if done else "pending"
        status = "implementation-complete" if done else "specification-ready-implementation-pending"
        block = (
            f"<!-- openspec-managed:start -->\nChange: {CHANGE}\nTask: {task}\n\n{line}"
            f"\n\nDepends on:\n{links}\n\nSource: {SOURCE}\nEvidence: {evidence}"
            f"\nState: {status}\n<!-- openspec-managed:end -->"
        )
        body = issue["body"].replace(blocks[0], block)
        desired_title = f"[{task}] {title}"
        labels = {entry["name"] for entry in issue["labels"]} | {"openspec", "change:" + CHANGE}
        state = "closed" if done else "open"
        if issue["state"] == "closed" and not done:
            raise ValueError("Closed issue conflicts with source; no implicit reopen")
        changed = (body != issue["body"] or issue["title"] != desired_title
                   or issue["state"] != state
                   or labels != {entry["name"] for entry in issue["labels"]})
        if changed and apply:
            payload = dict(title=desired_title, body=body, state=state, labels=sorted(labels))
            if done:
                payload["state_reason"] = "completed"
            gh("api", f"repos/{REPO}/issues/{mapped[task]}", "--method", "PATCH", payload=payload)
        updates += int(changed)
        checked = gh("api", f"repos/{REPO}/issues/{mapped[task]}")
        if apply and ("pull_request" in checked or checked["body"] != body
                      or checked["title"] != desired_title or checked["state"] != state
                      or labels != {entry["name"] for entry in checked["labels"]}):
            raise ValueError("Issue read-back mismatch")
        if apply:
            entries.append(dict(
                taskId=task, issueNumber=mapped[task], issueUrl=checked["html_url"], state=state,
                contentHash=hashlib.sha256(block.encode("utf-8")).hexdigest(),
                dependencies=deps, syncedAt=datetime.now(UTC).isoformat(),
            ))
    final = gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "1000",
               "--json", "number,body")
    if len(final) >= 1000:
        raise ValueError("Issue enumeration requires pagination")
    for task, *_ in tasks:
        marker = f"<!-- openspec-task-id: {task} -->"
        if [i["number"] for i in final if marker in (i.get("body") or "")] != [mapped[task]]:
            raise ValueError("Final repository-wide read-back mismatch")
    counts = dict(created=0, updated=updates, unchanged=len(tasks) - updates)
    if apply:
        persisted.update(status="synchronized-read-back-verified", tasks=entries,
                         lastSyncRun=counts, lastReadBackAt=datetime.now(UTC).isoformat(),
                         blockers=[], note="OpenSpec authoritative; local certification only.")
        MAPPING.write_text(json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(dict(apply=apply, **counts)))


if __name__ == "__main__":
    main()
