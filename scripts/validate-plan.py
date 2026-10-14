"""Read-only planning checks; --write emits derived traceability, never task status."""
import argparse
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHANGE = ROOT / "openspec/changes/paper-trading-vertical-slice"
FIELDS = {"Requirements": "requirements", "Zielpfade": "paths", "Abhängigkeiten": "dependsOn",
          "Verhalten": "behavior", "Akzeptanz": "acceptance", "Tests": "tests", "Abschluss": "dod"}

def read_tasks():
    text = (CHANGE / "tasks.md").read_text(encoding="utf-8")
    tasks = []
    for block in re.split(r"(?=^- \[)", text, flags=re.M)[1:]:
        m = re.match(r"- \[([ xX])\] (\d+\.\d+) (PTV-\d+) (.+?); Evidenz: (.+)", block)
        assert m, "Invalid checkbox"
        t = dict(done=m[1].lower()=="x", step=m[2], id=m[3], title=m[4])
        for label, field in FIELDS.items():
            v = re.search(r"^  - " + re.escape(label) + r": (.+)$", block, flags=re.M)
            assert v, f"{t['id']}: missing {label}"
            t[field] = v[1]
        t["requirements"] = re.findall(r"REQ-[A-Z]+-\d{3}", t["requirements"])
        t["paths"] = re.findall(r"`([^`]+)`", t["paths"])
        t["dependsOn"] = re.findall(r"PTV-\d+", t["dependsOn"])
        tasks.append(t)
    assert len({t["id"] for t in tasks}) == len(tasks), "Duplicate Task-ID"
    seen = set()
    for t in tasks:
        assert set(t["dependsOn"]) <= seen, f"{t['id']}: dependency missing/cyclic/not ordered"
        assert t["requirements"] and t["paths"], f"{t['id']}: empty metadata"
        assert all(not p.startswith(("/", "..")) for p in t["paths"]), "Path outside repo"
        seen.add(t["id"])
    return tasks

def collect():
    tasks = read_tasks()
    requirements = {}
    for path in sorted((CHANGE / "specs").glob("*/spec.md")):
        text = path.read_text(encoding="utf-8")
        for block in text.split("### Requirement: ")[1:]:
            req = block.split()[0]
            assert req not in requirements, f"Duplicate {req}"
            test = req.replace("REQ-", "T-")
            assert "#### Scenario: " + test in block, f"{req}: missing scenario/test"
            requirements[req] = str(path.relative_to(ROOT)).replace("\\", "/")
    testplan = (CHANGE / "test-strategy.md").read_text(encoding="utf-8")
    testpaths = dict(re.findall(r"\| (T-[A-Z]+-\d{3}) \| [^|]*?`([^`]+)`", testplan))
    api = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text(encoding="utf-8"))
    ops = {o["operationId"] for p in api["paths"].values() for method,o in p.items()
           if method in ("get","post","put","patch","delete") and "operationId" in o}
    routes = {
        "MKT": ["MarketState", "listMarketStates"],
        "DEC": ["DecisionResult", "listDecisions"],
        "RSK": ["RiskDecision", "RiskReservation", "ExecutionReport", "listRiskDecisions", "listOrders"],
        "RUN": ["EventEnvelope", "SafetyEvent", "listAuditEvents", "listAgentRuns"],
        "CTL": ["SafetyEvent", "activateLocalSession", "getSupervisorSnapshot", "streamSupervisorEvents", "getMode", "setMode",
                "pauseNewEntries", "resumeNewEntries", "activateKillSwitch", "getRiskPolicy",
                "updateRiskPolicy", "listApiCosts"],
    }
    schemas = {"MarketState":"market-state", "DecisionResult":"decision-result",
               "RiskDecision":"risk-decision", "RiskReservation":"risk-reservation",
               "SafetyEvent":"safety-event", "ExecutionReport":"execution-report", "EventEnvelope":"event-envelope"}
    decisions = {"MKT":["D-02","D-03"], "DEC":["D-02","D-07"],
                 "RSK":["D-04","D-05"], "RUN":["D-01","D-06","D-09"], "CTL":["D-08"]}
    rows = []
    for req,path in sorted(requirements.items()):
        test = req.replace("REQ-", "T-")
        assert test in testpaths, f"{req}: missing test path"
        owners = [t["id"] for t in tasks if req in t["requirements"]]
        assert owners, f"{req}: no task"
        group = req.split("-")[1]
        contracts = []
        for name in routes[group]:
            if name in schemas:
                p = "contracts/schemas/v1/" + schemas[name] + ".schema.json"
                assert (ROOT / p).exists(), p
                contracts.append(p)
            else:
                assert name in ops, f"Missing operation {name}"
                contracts.append("operation:" + name)
        rows.append(dict(requirementId=req, spec=path, decisions=decisions[group],
                         tasks=owners, testId=test, testPath=testpaths[test], contracts=contracts,
                         testStatus="planned-not-executed"))
    known = set(requirements)
    for t in tasks:
        assert set(t["requirements"]) <= known, f"{t['id']}: unknown Requirement"
    return tasks,rows

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    p.add_argument("--tasks-json", action="store_true")
    args = p.parse_args()
    tasks, rows = collect()
    if args.write:
        data = dict(schemaVersion=1, changeId="paper-trading-vertical-slice", rows=rows)
        (CHANGE / "traceability.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        table = "# Traceability\n\nAbgeleitet aus Specs, tasks.md, design.md, Vertrag und Teststrategie mit python scripts/validate-plan.py --write. Tests sind geplante Akzeptanznachweise; kein Runtime-PASS. JSON enthält konkrete Schema-/Operationreferenzen.\n\n| Requirement | Design | Tasks | Test und Zielpfad | Contract |\n|---|---|---|---|---|\n"
        for r in rows:
            table += "| " + " | ".join([r["requirementId"],", ".join(r["decisions"]),
                ", ".join(r["tasks"]),r["testId"]+" / "+r["testPath"],", ".join(r["contracts"])])+" |\n"
        (CHANGE / "traceability.md").write_text(table,encoding="utf-8")
    if args.tasks_json:
        print(json.dumps(tasks,ensure_ascii=True))
    else:
        print(f"PASS {len(rows)} requirements: scenarios, design, contracts, tasks, tests; {len(tasks)} leaf tasks: unique IDs and ordered DAG.")
