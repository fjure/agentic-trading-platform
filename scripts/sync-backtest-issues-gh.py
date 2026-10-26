"""Narrow Research issue synchronization; preview by default, no credentials read."""

import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "fjure/agentic-trading-platform"
CHANGE = "point-in-time-research-backtesting"
SOURCE = f"openspec/changes/{CHANGE}/tasks.md"
OUT = ROOT / ".tooling/output"
MAPPING = ROOT / "docs/development/github-backtesting-issue-sync.json"
BLOCK = re.compile(r"<!-- openspec-managed:start -->.*?<!-- openspec-managed:end -->", re.S)
APPLY = "--apply" in sys.argv


def gh(*args, payload=None):
    command = ["gh", *args]
    if payload is not None:
        p = OUT / "backtest-gh-request.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        command += ["--input", str(p)]
    r = subprocess.run(command, capture_output=True, encoding="utf-8", cwd=ROOT)
    if r.returncode:
        raise RuntimeError("GH operation failed; raw diagnostic withheld")
    return json.loads(r.stdout) if r.stdout.strip() else None


def inventory():
    pages = gh("api", f"repos/{REPO}/issues?state=all&per_page=100", "--paginate", "--slurp")
    return [i for page in pages for i in page]


origin = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
assert origin == f"https://github.com/{REPO}.git"
assert gh("api", "user", "--jq", "{login: .login}")["login"] == "fjure"
assert gh("repo", "view", REPO, "--json", "nameWithOwner")["nameWithOwner"] == REPO
source_text = (ROOT / SOURCE).read_text(encoding="utf-8")
tasks = []
for m in re.finditer(r"^- \[([ x])\] (\d+\.\d+ (BKT-\d{2}) ([^:]+):[^\n]+)$", source_text, re.M):
    done, line, task, title = m.groups()
    deps = re.search(r"Depends on: (.*?)\. Paths:", line)[1]
    deps = [] if deps == "none" else deps.split(", ")
    for field in ("Requirements:", "Paths:", "Acceptance:", "Tests:", "DoD:"):
        assert field in line
    tasks.append(dict(id=task, title=f"[{task}] {title}", line=line, deps=deps, done=done == "x"))
assert [t["id"] for t in tasks] == [f"BKT-{i:02d}" for i in range(1, 10)]
seen = set()
for t in tasks:
    assert set(t["deps"]) <= seen
    seen.add(t["id"])
previous = json.loads(MAPPING.read_text(encoding="utf-8")) if MAPPING.exists() else None
evidence = ROOT / "docs/evidence/bkt-implementation-verification.md"
integration_path = ROOT / "docs/development/github-backtesting-git-integrations.json"
integrations = (
    json.loads(integration_path.read_text(encoding="utf-8")) if integration_path.exists() else None
)
for task in tasks:
    if task["done"]:
        assert evidence.is_file() and integrations, "Completion needs test and Git evidence"
        assert integrations["repository"] == REPO
        proof = next(x for x in integrations["tasks"] if x["taskId"] == task["id"])
        assert proof["remoteVerified"] is True and re.fullmatch(r"[0-9a-f]{40}", proof["commit"])
if previous:
    assert previous["repository"] == REPO and previous["changeId"] == CHANGE
mapped = {t["taskId"]: t["issueNumber"] for t in previous["tasks"]} if previous else {}
issues = inventory()
assigned = {}
for t in tasks:
    marker = f"<!-- openspec-task-id: {t['id']} -->"
    matches = [i for i in issues if marker in (i.get("body") or "") or t["id"] in i["title"]]
    assert len(matches) <= 1, "Ambiguous task identity"
    if matches:
        i = matches[0]
        assert "pull_request" not in i and (i.get("body") or "").count(marker) == 1
        assert i["state"] in ({"open", "closed"} if t["done"] else {"open"})
        assert len(BLOCK.findall(i["body"])) == 1
        assert t["id"] not in mapped or mapped[t["id"]] == i["number"]
        assigned[t["id"]] = i
    else:
        assert t["id"] not in mapped, "Persisted issue missing"

labels = {"openspec", f"change:{CHANGE}"}
current_labels = gh("api", f"repos/{REPO}/labels?per_page=100", "--paginate", "--slurp")
existing_labels = {i["name"] for page in current_labels for i in page}


def managed(t, links=True):
    deps = (
        "\n".join(
            f"- {d}: #{assigned[d]['number']}" if links and d in assigned else f"- {d}"
            for d in t["deps"]
        )
        or "- none"
    )
    state = (
        "implemented-verified-integrated"
        if t["done"]
        else "specification-ready-implementation-pending"
    )
    proof = "docs/evidence/bkt-implementation-verification.md" if t["done"] else "pending"
    return (
        f"<!-- openspec-managed:start -->\nChange: {CHANGE}\nTask: {t['id']}\n\n{t['line']}"
        f"\n\nDepends on:\n{deps}\n\nSource: {SOURCE}\nEvidence: {proof}"
        f"\nState: {state}\n<!-- openspec-managed:end -->"
    )


preview = [
    dict(
        taskId=t["id"],
        issueNumber=assigned.get(t["id"], {}).get("number"),
        title=t["title"],
        managedBlock=managed(t),
        labels=sorted(labels),
        dependsOn=t["deps"],
    )
    for t in tasks
]
(OUT / "backtest-issue-preview.json").write_text(
    json.dumps(preview, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
if not APPLY:
    print(
        json.dumps(
            dict(
                mode="preview",
                tasks=len(tasks),
                create=len(tasks) - len(assigned),
                existing=len(assigned),
                preview=str(OUT / "backtest-issue-preview.json"),
            )
        )
    )
    sys.exit(0)

for label in sorted(labels - existing_labels):
    gh(
        "api",
        f"repos/{REPO}/labels",
        "--method",
        "POST",
        payload=dict(name=label, color="1d76db", description="OpenSpec Researchplanung"),
    )
created = updated = unchanged = 0
for t in tasks:
    if t["id"] not in assigned:
        body = f"<!-- openspec-task-id: {t['id']} -->\n\n" + managed(t, False)
        i = gh(
            "api",
            f"repos/{REPO}/issues",
            "--method",
            "POST",
            payload=dict(title=t["title"], body=body, labels=sorted(labels)),
        )
        assert "pull_request" not in i
        assigned[t["id"]] = i
        created += 1

entries = []
now = datetime.now(UTC).isoformat()
for t in tasks:
    i = gh("api", f"repos/{REPO}/issues/{assigned[t['id']]['number']}")
    assert "pull_request" not in i and i["state"] in ({"open", "closed"} if t["done"] else {"open"})
    old = BLOCK.findall(i["body"])
    assert len(old) == 1
    block = managed(t)
    body = i["body"].replace(old[0], block)
    target_labels = {x["name"] for x in i["labels"]} | labels
    target_state = "closed" if t["done"] else "open"
    if (
        body != i["body"]
        or i["title"] != t["title"]
        or target_labels != {x["name"] for x in i["labels"]}
        or i["state"] != target_state
    ):
        patch = dict(title=t["title"], body=body, labels=sorted(target_labels), state=target_state)
        if t["done"]:
            patch["state_reason"] = "completed"
        gh("api", f"repos/{REPO}/issues/{i['number']}", "--method", "PATCH", payload=patch)
        updated += 1
    else:
        unchanged += 1
    read = gh("api", f"repos/{REPO}/issues/{i['number']}")
    assert (
        "pull_request" not in read and read["state"] == target_state and read["title"] == t["title"]
    )
    assert read["body"] == body and labels <= {x["name"] for x in read["labels"]}
    assert read["html_url"] == f"https://github.com/{REPO}/issues/{i['number']}"
    entries.append(
        dict(
            taskId=t["id"],
            issueNumber=i["number"],
            issueUrl=read["html_url"],
            contentHash=hashlib.sha256(block.encode("utf-8")).hexdigest(),
            syncedAt=now,
            dependsOn=t["deps"],
            state=target_state,
        )
    )
again = inventory()
for t in tasks:
    hits = [i for i in again if f"<!-- openspec-task-id: {t['id']} -->" in (i.get("body") or "")]
    assert len(hits) == 1 and hits[0]["number"] == assigned[t["id"]]["number"]
    assert BLOCK.findall(hits[0]["body"]) == [managed(t)]
    assert hits[0]["state"] == ("closed" if t["done"] else "open")
result = dict(
    schemaVersion=1,
    repository=REPO,
    changeId=CHANGE,
    source=SOURCE,
    transport="authorized-gh-cli-fallback",
    tasks=entries,
    lastSyncRun=dict(
        syncedAt=now,
        created=created,
        updated=updated,
        unchanged=unchanged,
        readBack=len(entries),
        repositoryWideVerified=True,
    ),
)
MAPPING.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result["lastSyncRun"]))
