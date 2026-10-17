"""Synchronize the approved PTV-24/25 extension through narrowly scoped GH issue writes.

Dry run is the default. The existing 23 mappings are immutable input evidence; only
PTV-21, PTV-24 and PTV-25 may receive issue writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "fjure/agentic-trading-platform"
CHANGE = "paper-trading-vertical-slice"
BASE_URL = f"https://github.com/{REPOSITORY}/issues/"
OUTPUT = ROOT / ".tooling/output"
MARKER = re.compile(r"<!-- openspec-task-id: (PTV-\d+) -->")
BLOCK = re.compile(r"<!-- openspec-managed:start -->.*?<!-- openspec-managed:end -->", re.S)
REQUIRED_LABELS = {"openspec", f"change:{CHANGE}"}
NEW_IDS = ("PTV-24", "PTV-25")
WRITABLE_IDS = {"PTV-21", *NEW_IDS}


def gh(*args: str) -> str:
    if args[0] == "api":
        args = (*args, "--hostname", "github.com")
    result = subprocess.run(
        ["gh", *args], cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError("GH CLI operation failed; diagnostic output withheld")
    return result.stdout


def normalize_rest_issue(issue: dict) -> dict:
    """Use the browser issue URL for mappings while validating REST identity."""
    if "pull_request" in issue:
        return issue
    number = issue["number"]
    expected_api = f"https://api.github.com/repos/{REPOSITORY}/issues/{number}"
    expected_browser = f"{BASE_URL}{number}"
    if issue.get("url") != expected_api or issue.get("html_url") != expected_browser:
        raise ValueError("REST issue URL identity drift")
    return {**issue, "url": issue["html_url"]}


def issue_list() -> list[dict]:
    pages = json.loads(
        gh("api", f"repos/{REPOSITORY}/issues?state=all&per_page=100", "--paginate", "--slurp")
    )
    assert isinstance(pages, list) and all(isinstance(page, list) for page in pages)
    return [normalize_rest_issue(issue) for page in pages for issue in page]


def only_block(body: str) -> str:
    matches = BLOCK.findall(body)
    if len(matches) != 1:
        raise ValueError("missing or ambiguous managed block")
    return matches[0]


def labels(issue: dict) -> set[str]:
    return {label["name"] for label in issue["labels"]}


def index_markers(issues: list[dict]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for issue in issues:
        markers = MARKER.findall(issue.get("body") or "")
        if not markers:
            continue
        if "pull_request" in issue or len(markers) != 1 or markers[0] in indexed:
            raise ValueError("PR, duplicate, or ambiguous task marker")
        indexed[markers[0]] = issue
    return indexed


def read_expected_tasks(expected: set[str]) -> dict[str, dict]:
    """Allow bounded read propagation after create, never repeat a create here."""
    for attempt in range(3):
        current = index_markers(issue_list())
        if set(current) == expected:
            return current
        if set(current) - expected:
            raise ValueError("unexpected task marker after write")
        if attempt < 2:
            time.sleep(1)
    raise RuntimeError("new issue list not yet consistent; resume without recreating issues")


def issue_identity(issue: dict, task: dict) -> None:
    task_id = task["id"]
    if "pull_request" in issue or MARKER.findall(issue.get("body") or "") != [task_id]:
        raise ValueError(f"{task_id}: issue identity conflict")
    if issue["title"] != f"[{task_id}] {task['title']}":
        raise ValueError(f"{task_id}: title drift")
    if issue["url"] != f"{BASE_URL}{issue['number']}":
        raise ValueError(f"{task_id}: issue URL drift")
    if not REQUIRED_LABELS <= labels(issue):
        raise ValueError(f"{task_id}: required label missing")
    if issue["state"].lower() != ("closed" if task["done"] else "open"):
        raise ValueError(f"{task_id}: state drift")
    only_block(issue["body"])


def check_existing(tasks: dict[str, dict], mapping: dict, issues: dict[str, dict]) -> None:
    assert mapping["schemaVersion"] == 1
    assert mapping["repository"] == REPOSITORY and mapping["changeId"] == CHANGE
    previous = {entry["taskId"]: entry for entry in mapping["tasks"]}
    assert len(previous) == len(mapping["tasks"]) in (23, 25)
    assert set(previous) == {f"PTV-{i:02d}" for i in range(1, len(previous) + 1)}
    assert len({entry["issueNumber"] for entry in previous.values()}) == len(previous)
    assert set(issues) <= set(tasks), "unexpected OpenSpec marker in repository"
    for task_id, entry in previous.items():
        issue = issues[task_id]
        task = tasks[task_id]
        issue_identity(issue, task)
        if issue["number"] != entry["issueNumber"] or issue["url"] != entry["issueUrl"]:
            raise ValueError(f"{task_id}: mapped issue identity drift")
        actual_hash = hashlib.sha256(only_block(issue["body"]).encode("utf-8")).hexdigest()
        if actual_hash != entry["contentHash"]:
            # A previous run may have patched PTV-21 and then stopped before
            # the final mapping write. Accept only the exact planned sections.
            numbers = {key: value["number"] for key, value in issues.items()}
            planned_block = (
                new_block(task, numbers) if task_id == "PTV-21" and "PTV-25" in numbers else None
            )
            if planned_block != only_block(issue["body"]):
                raise ValueError(f"{task_id}: managed block hash drift")
        if issue["state"].lower() != entry["state"]:
            raise ValueError(f"{task_id}: mapped state drift")
        if task_id != "PTV-21":
            if [dep["taskId"] for dep in entry["dependsOn"]] != task["dependsOn"]:
                raise ValueError(f"{task_id}: dependency drift")
            if not all(f"`{path}`" in only_block(issue["body"]) for path in task["paths"]):
                raise ValueError(f"{task_id}: path drift")
        if task["done"] and not entry.get("completionEvidence"):
            raise ValueError(f"{task_id}: closed issue lacks mapped evidence")


def dependency_lines(task: dict, numbers: dict[str, int], *, pending: bool = False) -> str:
    if not task["dependsOn"]:
        return "- none"
    rows = []
    for task_id in task["dependsOn"]:
        number = numbers.get(task_id)
        if number is None:
            if not pending:
                raise ValueError(f"{task['id']}: dependency issue unresolved: {task_id}")
            rows.append(f"- {task_id}: pending issue link")
        else:
            rows.append(f"- {task_id}: #{number} ({BASE_URL}{number})")
    return "\n".join(rows)


def new_block(task: dict, numbers: dict[str, int], *, pending: bool = False) -> str:
    return (
        "<!-- openspec-managed:start -->\n"
        f"Change-ID: {CHANGE}\n"
        f"Task-ID: {task['id']}\n"
        f"Requirements: {', '.join(task['requirements'])}\n\n"
        "Zielpfade:\n"
        + "\n".join(f"- `{path}`" for path in task["paths"])
        + "\n\nDepends on:\n"
        + dependency_lines(task, numbers, pending=pending)
        + "\n\n"
        f"Erwartetes Verhalten: {task['behavior']}\n\n"
        f"Akzeptanz: {task['acceptance']}\n\n"
        f"Testevidenz (noch auszuführen): {task['tests']}\n\n"
        "Definition of Done: Verhalten und zugehörige Tests bestanden; "
        "Dokumentation und Contracts aktuell; "
        "secretfreie Evidenz sowie Safetyreview soweit betroffen; "
        "OpenSpec-Checkbox und GitHub-Issue "
        "im selben Arbeitsstand, Read-back und Mapping ohne Drift. "
        "Anwendungstasks bleiben in diesem "
        "Planungsdurchlauf offen.\n"
        "<!-- openspec-managed:end -->"
    )


def new_body(task: dict, numbers: dict[str, int], *, pending: bool = False) -> str:
    return f"<!-- openspec-task-id: {task['id']} -->\n{new_block(task, numbers, pending=pending)}"


def replace_21_sections(
    body: str, task: dict, numbers: dict[str, int], *, pending: bool = False
) -> str:
    old = only_block(body)
    paths = "\n".join(f"- `{path}`" for path in task["paths"])
    desired = old
    desired, path_count = re.subn(
        r"Zielpfade:\n.*?(?=\n\nDepends on:)",
        lambda _: f"Zielpfade:\n{paths}",
        desired,
        flags=re.S,
    )
    desired, dependency_count = re.subn(
        r"Depends on:\n.*?(?=\n\nErwartetes Verhalten:)",
        lambda _: f"Depends on:\n{dependency_lines(task, numbers, pending=pending)}",
        desired,
        flags=re.S,
    )
    if (path_count, dependency_count) != (1, 1):
        raise ValueError("PTV-21 target-path or dependency section ambiguous")
    return body.replace(old, desired, 1)


def desired_plan(tasks: dict[str, dict], issues: dict[str, dict]) -> dict:
    numbers = {task_id: issue["number"] for task_id, issue in issues.items()}
    patch_21 = replace_21_sections(issues["PTV-21"]["body"], tasks["PTV-21"], numbers, pending=True)
    # The dry run shows unresolved links explicitly; apply renders actual numbers.
    preview_updates = [
        {
            "taskId": "PTV-21",
            "issueNumber": issues["PTV-21"]["number"],
            "body": patch_21,
            "changed": patch_21 != issues["PTV-21"]["body"],
        }
    ]
    creations = []
    for task_id in NEW_IDS:
        task = tasks[task_id]
        if task_id not in issues:
            creations.append(
                {
                    "taskId": task_id,
                    "title": f"[{task_id}] {task['title']}",
                    "body": new_body(task, numbers, pending=True),
                    "labels": sorted(REQUIRED_LABELS),
                }
            )
        else:
            issue = issues[task_id]
            issue_identity(issue, task)
            existing_block = only_block(issue["body"])
            allowed = {
                new_block(task, numbers, pending=True),
                new_block(
                    task,
                    {key: value for key, value in numbers.items() if key not in NEW_IDS},
                    pending=True,
                ),
            }
            if set(task["dependsOn"]) <= set(numbers):
                allowed.add(new_block(task, numbers))
            if existing_block not in allowed:
                raise ValueError(f"{task_id}: existing issue content conflict")
            final_block = (
                new_block(task, numbers)
                if set(task["dependsOn"]) <= set(numbers)
                else new_block(task, numbers, pending=True)
            )
            desired_body = issue["body"].replace(existing_block, final_block, 1)
            preview_updates.append(
                {
                    "taskId": task_id,
                    "issueNumber": issue["number"],
                    "body": desired_body,
                    "changed": desired_body != issue["body"],
                }
            )
    return {
        "repository": REPOSITORY,
        "creations": creations,
        "updates": preview_updates,
        "existingNewIds": [task_id for task_id in NEW_IDS if task_id in issues],
    }


def api_write(method: str, endpoint: str, payload: dict) -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="issue-extension-", dir=OUTPUT) as directory:
        payload_path = Path(directory) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return normalize_rest_issue(
            json.loads(gh("api", endpoint, "--method", method, "--input", str(payload_path)))
        )


def read_single(number: int) -> dict:
    issue = normalize_rest_issue(json.loads(gh("api", f"repos/{REPOSITORY}/issues/{number}")))
    if "pull_request" in issue:
        raise ValueError("PR mutation forbidden")
    return issue


def verify_final(
    tasks: dict[str, dict],
    before: dict[str, dict],
    final: dict[str, dict],
    mapping: dict,
    changed: set[str],
    version: str,
) -> list[dict]:
    assert set(final) == set(tasks) and len(final) == 25
    assert sum(task["done"] for task in tasks.values()) == 20
    assert sum(issue["state"].lower() == "open" for issue in final.values()) == 5
    numbers = {task_id: issue["number"] for task_id, issue in final.items()}
    previous = {entry["taskId"]: entry for entry in mapping["tasks"]}
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    verified = []
    for task_id, task in tasks.items():
        issue = final[task_id]
        issue_identity(issue, task)
        if task_id not in WRITABLE_IDS:
            original = before[task_id]
            assert all(
                issue[key] == original[key]
                for key in ("number", "url", "title", "body", "state", "labels")
            ), f"{task_id}: unrelated issue changed"
        elif task_id == "PTV-21":
            assert issue["body"] == replace_21_sections(before[task_id]["body"], task, numbers)
            assert only_block(issue["body"]) == new_block(task, numbers)
            assert labels(issue) == labels(before[task_id])
            assert issue["number"] == previous[task_id]["issueNumber"]
        else:
            assert only_block(issue["body"]) == new_block(task, numbers)
            if task_id in before:
                original = before[task_id]
                assert issue["body"].replace(only_block(issue["body"]), "", 1) == original[
                    "body"
                ].replace(only_block(original["body"]), "", 1)
                assert labels(issue) == labels(original)
            else:
                assert issue["body"] == new_body(task, numbers)
        block = only_block(issue["body"])
        assert re.findall(r"^- (PTV-\d+): #", block, re.M) == task["dependsOn"]
        assert all(f"`{path}`" in block for path in task["paths"])
        assert f"Requirements: {', '.join(task['requirements'])}" in block
        dependencies = [
            dict(taskId=dep, issueNumber=numbers[dep], issueUrl=f"{BASE_URL}{numbers[dep]}")
            for dep in task["dependsOn"]
        ]
        if task_id not in WRITABLE_IDS:
            assert dependencies == previous[task_id]["dependsOn"]
        verified.append(
            dict(
                taskId=task_id,
                issueNumber=issue["number"],
                issueUrl=issue["url"],
                title=issue["title"],
                body=issue["body"],
                managed=block,
                state=issue["state"].lower(),
                labels=sorted(labels(issue)),
                dependsOn=dependencies,
                verifiedAt=now,
                completionEvidence=previous[task_id]["completionEvidence"]
                if task["done"]
                else None,
                transportVersion=version,
            )
        )
    assert changed <= WRITABLE_IDS
    return verified


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply", action="store_true", help="Perform only PTV-21/24/25 issue writes"
    )
    args = parser.parse_args()
    assert gh("api", "user", "--jq", ".login").strip() == "fjure"
    repository = json.loads(
        gh("repo", "view", f"github.com/{REPOSITORY}", "--json", "nameWithOwner,url")
    )
    assert repository == {"nameWithOwner": REPOSITORY, "url": f"https://github.com/{REPOSITORY}"}
    version = gh("--version").splitlines()[0].split()[2]
    tasks_list = runpy.run_path(str(ROOT / "scripts/validate-plan.py"))["read_tasks"]()
    tasks = {task["id"]: task for task in tasks_list}
    assert len(tasks_list) == len(tasks) == 25
    assert set(tasks) == {f"PTV-{i:02d}" for i in range(1, 26)}
    assert tasks["PTV-24"]["dependsOn"] == ["PTV-16", "PTV-17"]
    assert tasks["PTV-25"]["dependsOn"] == ["PTV-24"]
    assert tasks["PTV-21"]["dependsOn"] == ["PTV-17", "PTV-20", "PTV-25"]
    mapping = json.loads(
        (ROOT / "docs/development/github-issue-sync.json").read_text(encoding="utf-8")
    )
    before = index_markers(issue_list())
    check_existing(tasks, mapping, before)
    plan = desired_plan(tasks, before)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.apply:
        return
    labels_response = json.loads(
        gh("api", f"repos/{REPOSITORY}/labels?per_page=100", "--paginate", "--slurp")
    )
    available = {label["name"] for page in labels_response for label in page}
    assert REQUIRED_LABELS <= available, "Required labels must already exist"
    changed: set[str] = set()
    # Repeat the all-state marker search immediately before each create. This also
    # permits a safe restart after any subset of the two creations succeeded.
    for task_id in NEW_IDS:
        live = index_markers(issue_list())
        if task_id in live:
            desired_plan(tasks, live)
            continue
        task = tasks[task_id]
        numbers = {key: issue["number"] for key, issue in live.items()}
        created = api_write(
            "POST",
            f"repos/{REPOSITORY}/issues",
            dict(
                title=f"[{task_id}] {task['title']}",
                body=new_body(task, numbers, pending=True),
                labels=sorted(REQUIRED_LABELS),
            ),
        )
        issue_identity(created, task)
        changed.add(task_id)
    live = read_expected_tasks(set(tasks))
    assert live["PTV-21"]["body"] == before["PTV-21"]["body"], "PTV-21 changed during sync"
    numbers = {task_id: issue["number"] for task_id, issue in live.items()}
    desired = {"PTV-21": replace_21_sections(before["PTV-21"]["body"], tasks["PTV-21"], numbers)}
    for task_id in NEW_IDS:
        old = only_block(live[task_id]["body"])
        issue_identity(live[task_id], tasks[task_id])
        allowed = {
            new_block(tasks[task_id], numbers),
            new_block(
                tasks[task_id],
                {key: value for key, value in numbers.items() if key not in NEW_IDS},
                pending=True,
            ),
        }
        assert old in allowed, f"{task_id}: unexpected managed content before patch"
        if task_id in before:
            assert live[task_id]["body"].replace(old, "", 1) == before[task_id]["body"].replace(
                only_block(before[task_id]["body"]), "", 1
            )
        desired[task_id] = live[task_id]["body"].replace(old, new_block(tasks[task_id], numbers), 1)
    for task_id in ("PTV-24", "PTV-25", "PTV-21"):
        issue = live[task_id]
        if issue["body"] != desired[task_id]:
            latest = read_single(issue["number"])
            assert latest["body"] == issue["body"] and latest["state"] == "open"
            api_write(
                "PATCH", f"repos/{REPOSITORY}/issues/{issue['number']}", {"body": desired[task_id]}
            )
            changed.add(task_id)
    final = read_expected_tasks(set(tasks))
    verified = verify_final(tasks, before, final, mapping, changed, version)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "verified-issue-readbacks.json").write_text(
        json.dumps(verified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    recorder = [
        sys.executable,
        str(ROOT / "scripts/record-issue-sync.py"),
        "--complete",
        "--transport",
        "github-cli",
    ]
    subprocess.run(recorder, cwd=ROOT, check=True)
    created_count = sum(task_id not in before for task_id in NEW_IDS)
    updated_count = len(changed - set(NEW_IDS))
    print(f"PASS {len(verified)} read-backs; created={created_count}; updated={updated_count}")


if __name__ == "__main__":
    main()
