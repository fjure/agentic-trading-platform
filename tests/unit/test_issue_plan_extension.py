"""Safety checks for the narrowly scoped OpenSpec issue extension."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/extend-integration-plan-gh.py"
SPEC = importlib.util.spec_from_file_location("issue_plan_extension", SCRIPT)
extension = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extension)


def fixtures():
    tasks = {}
    old_tasks = {}
    for number in range(1, 26):
        task_id = f"PTV-{number:02d}"
        deps = (
            ["PTV-17", "PTV-20", "PTV-25"]
            if number == 21
            else ["PTV-16", "PTV-17"]
            if number == 24
            else ["PTV-24"]
            if number == 25
            else ["PTV-21"]
            if number == 23
            else []
        )
        task = dict(
            id=task_id,
            title=f"Task {number}",
            done=number <= 20,
            requirements=["REQ-RUN-001"],
            paths=[f"src/{number}.py"],
            dependsOn=deps,
            behavior=f"Behavior {number}.",
            acceptance=f"Acceptance {number}.",
            tests=f"T-RUN-{number:03d}",
        )
        tasks[task_id] = task
        old_tasks[task_id] = copy.deepcopy(task)
    old_tasks["PTV-21"]["paths"] = ["src/old_21.py"]
    old_tasks["PTV-21"]["dependsOn"] = ["PTV-17", "PTV-20"]
    numbers = {task_id: number + 100 for number, task_id in enumerate(tasks, 1)}
    before = {}
    mapping = {
        "schemaVersion": 1,
        "repository": extension.REPOSITORY,
        "changeId": extension.CHANGE,
        "tasks": [],
    }
    for task_id in list(tasks)[:23]:
        task = old_tasks[task_id]
        number = numbers[task_id]
        body = extension.new_body(task, numbers)
        if task_id == "PTV-21":
            body = "manual preface\n" + body + "\nmanual suffix"
        issue = dict(
            number=number,
            url=f"{extension.BASE_URL}{number}",
            title=f"[{task_id}] {task['title']}",
            body=body,
            labels=[
                {"name": "openspec"},
                {"name": f"change:{extension.CHANGE}"},
                {"name": "manual-label"},
            ],
            state="closed" if task["done"] else "open",
        )
        before[task_id] = issue
        mapping["tasks"].append(
            dict(
                taskId=task_id,
                issueNumber=number,
                issueUrl=issue["url"],
                contentHash=hashlib.sha256(extension.only_block(body).encode()).hexdigest(),
                state=issue["state"],
                dependsOn=[
                    dict(
                        taskId=dep,
                        issueNumber=numbers[dep],
                        issueUrl=f"{extension.BASE_URL}{numbers[dep]}",
                    )
                    for dep in task["dependsOn"]
                ],
                **({"completionEvidence": "evidence.md"} if task["done"] else {}),
            )
        )
    return tasks, before, mapping, numbers


def test_final_readback_keeps_manual_text_labels_and_real_dependency_numbers():
    tasks, before, mapping, numbers = fixtures()
    extension.check_existing(tasks, mapping, before)
    final = copy.deepcopy(before)
    final["PTV-21"]["body"] = extension.replace_21_sections(
        before["PTV-21"]["body"], tasks["PTV-21"], numbers
    )
    for task_id in extension.NEW_IDS:
        task = tasks[task_id]
        final[task_id] = dict(
            number=numbers[task_id],
            url=f"{extension.BASE_URL}{numbers[task_id]}",
            title=f"[{task_id}] {task['title']}",
            body=extension.new_body(task, numbers),
            state="open",
            labels=[{"name": "openspec"}, {"name": f"change:{extension.CHANGE}"}],
        )
    entries = extension.verify_final(
        tasks, before, final, mapping, {"PTV-21", "PTV-24", "PTV-25"}, "2.0.0"
    )
    updated = next(entry for entry in entries if entry["taskId"] == "PTV-21")
    assert updated["body"].startswith("manual preface\n")
    assert updated["body"].endswith("\nmanual suffix")
    assert "manual-label" in updated["labels"]
    assert "- PTV-25: #125" in updated["body"]
    assert updated["dependsOn"][-1]["issueNumber"] == 125
    assert len(entries) == 25


def test_restarted_creation_uses_marker_and_rejects_title_or_body_conflict():
    tasks, before, _, numbers = fixtures()
    partial = copy.deepcopy(before)
    task = tasks["PTV-24"]
    partial["PTV-24"] = dict(
        number=numbers["PTV-24"],
        url=f"{extension.BASE_URL}{numbers['PTV-24']}",
        title="[PTV-24] Task 24",
        body=extension.new_body(task, numbers),
        state="open",
        labels=[{"name": "openspec"}, {"name": f"change:{extension.CHANGE}"}],
    )
    plan = extension.desired_plan(tasks, partial)
    assert [entry["taskId"] for entry in plan["creations"]] == ["PTV-25"]
    assert f"- PTV-24: #{numbers['PTV-24']}" in plan["creations"][0]["body"]
    partial["PTV-24"]["title"] = "[PTV-24] unexpected title"
    with pytest.raises(ValueError, match="title drift"):
        extension.desired_plan(tasks, partial)


def test_duplicate_marker_or_pull_request_is_refused():
    issue = dict(number=124, body="<!-- openspec-task-id: PTV-24 -->")
    with pytest.raises(ValueError, match="duplicate"):
        extension.index_markers([issue, dict(issue, number=200)])
    with pytest.raises(ValueError, match="PR"):
        extension.index_markers([dict(issue, pull_request={})])


def test_rest_issue_urls_normalize_on_list_get_and_write(monkeypatch):
    number = 147
    rest_issue = dict(
        number=number,
        url=f"https://api.github.com/repos/{extension.REPOSITORY}/issues/{number}",
        html_url=f"{extension.BASE_URL}{number}",
        body="<!-- openspec-task-id: PTV-24 -->",
    )
    unrelated_pr = dict(rest_issue, number=148, pull_request={})

    def fake_gh(*args):
        if "--paginate" in args:
            return json.dumps([[rest_issue, unrelated_pr]])
        return json.dumps(rest_issue)

    monkeypatch.setattr(extension, "gh", fake_gh)
    monkeypatch.setattr(extension, "OUTPUT", extension.ROOT / ".tooling/output")
    listed = extension.issue_list()
    assert listed[0]["url"] == f"{extension.BASE_URL}{number}"
    assert listed[1] == unrelated_pr
    assert extension.read_single(number)["url"] == f"{extension.BASE_URL}{number}"
    for method in ("POST", "PATCH"):
        written = extension.api_write(method, f"repos/{extension.REPOSITORY}/issues", {})
        assert written["url"] == f"{extension.BASE_URL}{number}"
    with pytest.raises(ValueError, match="URL identity drift"):
        extension.normalize_rest_issue(dict(rest_issue, html_url=f"{extension.BASE_URL}999"))


def test_post_create_read_propagation_retries_reads_without_writes(monkeypatch):
    first = {"body": "<!-- openspec-task-id: PTV-24 -->"}
    second = {"body": "<!-- openspec-task-id: PTV-25 -->"}
    responses = iter(([first], [first, second]))
    sleeps = []
    monkeypatch.setattr(extension, "issue_list", lambda: next(responses))
    monkeypatch.setattr(extension.time, "sleep", sleeps.append)
    monkeypatch.setattr(extension, "api_write", lambda *_a, **_k: pytest.fail("unexpected write"))
    assert set(extension.read_expected_tasks({"PTV-24", "PTV-25"})) == {"PTV-24", "PTV-25"}
    assert sleeps == [1]
    monkeypatch.setattr(extension, "issue_list", lambda: [first, second])
    with pytest.raises(ValueError, match="unexpected task marker"):
        extension.read_expected_tasks({"PTV-24"})
