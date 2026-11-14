"""Aggregate manifest-pinned, offline Research CI evidence."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

MANIFEST_DIR = Path(__file__).resolve().parent / "research-evidence"
GATES = ("fast", "db", "browser")
STATES = (
    "passed", "failed", "skipped", "missing_environment", "not_run", "not_required",
)
SHA = re.compile(r"[a-f0-9]{40}")
CASE_ID = re.compile(r"ARP-[A-Z0-9-]+")
EVIDENCE_ERRORS = (KeyError, TypeError, OSError, ValueError, ET.ParseError, UnicodeError)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def read_json(content):
    def unique_fields(pairs):
        fields = {}
        for key, value in pairs:
            if key in fields:
                raise ValueError("Duplicate evidence field")
            fields[key] = value
        return fields

    return json.loads(content, object_pairs_hook=unique_fields)


def manifest_snapshot():
    cases = {gate: {} for gate in GATES}
    files = {}
    identifiers = set()
    suites = set()
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        raw = path.read_bytes()
        files[path.name] = digest(raw)
        manifest = read_json(raw)
        if set(manifest) != {
            "schemaVersion", "packageId", "suite", "gate", "mode", "artifact", "cases",
        } or (
            manifest["schemaVersion"] != 1 or manifest["mode"] != "offline"
            or not re.fullmatch(r"ARP-[0-9]{2}", manifest["packageId"])
            or not re.fullmatch(r"[a-z][a-z0-9-]+", manifest["suite"])
            or manifest["gate"] not in GATES or not manifest["cases"]
            or manifest["artifact"] != "research-" + manifest["gate"]
            or manifest["suite"] in suites
        ):
            raise ValueError(f"Invalid manifest {path.name}")
        suites.add(manifest["suite"])
        gate_cases = cases[manifest["gate"]]
        for case in manifest["cases"]:
            if set(case) != {"id", "class", "name"}:
                raise ValueError(f"Invalid case in {path.name}")
            case_id = case["id"]
            selector = case["class"], case["name"]
            if (
                not CASE_ID.fullmatch(case_id) or not all(selector)
                or case_id in identifiers or selector in gate_cases
            ):
                raise ValueError(f"Duplicate or invalid case in {path.name}")
            identifiers.add(case_id)
            gate_cases[selector] = case_id
    if any(not gate_cases for gate_cases in cases.values()):
        raise ValueError("A gate has no required cases")
    snapshot = digest(json.dumps(files, sort_keys=True).encode())
    return cases, snapshot


def parse_junit(raw):
    # Evidence reports have no DTD, entities or processor instructions beyond the XML header.
    if b"<!" in raw.upper() or bytes(1) in raw:
        raise ValueError("Unexpected JUnit markup")
    root = ET.fromstring(raw)
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Unexpected JUnit root")
    results = []
    for testcase in root.iter("testcase"):
        selector = testcase.get("classname", ""), testcase.get("name", "")
        tags = [child.tag for child in testcase]
        terminal = [tag for tag in tags if tag in {"error", "failure", "skipped"}]
        if len(terminal) > 1 or any(
            tag not in {"error", "failure", "skipped", "properties", "system-out", "system-err"}
            for tag in tags
        ):
            raise ValueError("Unexpected JUnit case markup")
        status = "passed"
        if terminal and terminal[0] in {"error", "failure"}:
            status = "failed"
        elif terminal:
            status = "skipped"
        results.append((selector, status))
    if not results:
        raise ValueError("JUnit contains no cases")
    return results


def redact_junit(results, required):
    root = ET.Element("testsuite", name="research", tests=str(len(results)))
    for index, (selector, status) in enumerate(results):
        classname, name = selector if selector in required else ("unselected", str(index))
        testcase = ET.SubElement(root, "testcase", classname=classname, name=name)
        if status != "passed":
            ET.SubElement(testcase, "failure" if status == "failed" else "skipped")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def inspect(required, results):
    observed = {}
    problem = False
    for selector, state in results:
        case_id = required.get(selector)
        if case_id is None:
            if state != "passed":
                print(f"Unselected case {state}", file=sys.stderr)
                problem = True
            continue
        if case_id in observed:
            print(f"Duplicate required case {case_id}", file=sys.stderr)
            problem = True
        observed[case_id] = state
    for case_id in required.values():
        state = observed.setdefault(case_id, "not_run")
        if state != "passed":
            print(f"{case_id}: {state}", file=sys.stderr)
            problem = True
    return observed, problem


def record(args, required, snapshot):
    args.output.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output / "evidence.json"
    junit_path = args.output / "junit.xml"
    status = args.status
    observed = (
        {} if status == "not_required"
        else {case_id: "not_run" for case_id in required.values()}
    )
    junit_hash = None
    if args.status == "not_required" and args.junit is not None:
        raise ValueError("An omitted browser gate cannot contain JUnit")
    if args.junit is not None:
        try:
            results = parse_junit(args.junit.read_bytes())
        except EVIDENCE_ERRORS:
            print("JUnit is unreadable or invalid", file=sys.stderr)
            status = "failed"
        else:
            observed, problem = inspect(required, results)
            safe_junit = redact_junit(results, required)
            junit_path.write_bytes(safe_junit)
            junit_hash = digest(safe_junit)
            if status == "passed" and problem:
                status = "skipped" if "skipped" in observed.values() else "failed"
    elif status == "passed":
        status = "not_run"
    if junit_hash is None:
        junit_path.unlink(missing_ok=True)
    if status == "not_required" and (not args.reason or args.gate != "browser" or junit_hash):
        raise ValueError("An omitted browser gate must carry an explicit reason and no JUnit")
    evidence = {
        "schemaVersion": 1, "sourceSha": args.source_sha, "manifestSha256": snapshot,
        "gate": args.gate, "status": status, "caseStatus": observed, "junitSha256": junit_hash,
        "reason": args.reason,
    }
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return status not in {"passed", "not_required"}


def check_gate(args, gate, required, snapshot):
    artifact = args.artifacts / gate
    evidence = read_json((artifact / "evidence.json").read_bytes())
    if (
        evidence["schemaVersion"] != 1 or evidence["gate"] != gate
        or evidence["sourceSha"] != args.source_sha or evidence["manifestSha256"] != snapshot
    ):
        raise ValueError("Artifact identity does not match source or manifest")
    junit_path = artifact / "junit.xml"
    status = evidence["status"]
    if gate == "browser" and args.browser_required == "false" and status == "not_required":
        if junit_path.exists() or evidence["junitSha256"] or evidence["caseStatus"]:
            raise ValueError("Omitted browser gate contains conflicting test evidence")
        if not isinstance(evidence["reason"], str) or not evidence["reason"].strip():
            raise ValueError("Omitted browser gate has no reason")
        return False
    if status != "passed":
        raise ValueError(f"Gate status: {status if status in STATES else 'invalid'}")
    raw = junit_path.read_bytes()
    if digest(raw) != evidence["junitSha256"]:
        raise ValueError("JUnit checksum does not match evidence")
    observed, problem = inspect(required, parse_junit(raw))
    if observed != evidence["caseStatus"]:
        raise ValueError("Recorded case statuses do not match JUnit")
    return problem


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recorder = commands.add_parser("record")
    recorder.add_argument("--gate", required=True, choices=GATES)
    recorder.add_argument("--output", required=True, type=Path)
    recorder.add_argument("--status", required=True, choices=STATES)
    recorder.add_argument("--junit", type=Path)
    recorder.add_argument("--reason")
    checker = commands.add_parser("check")
    checker.add_argument("--artifacts", required=True, type=Path)
    checker.add_argument("--browser-required", required=True, choices=("true", "false"))
    for command in (recorder, checker):
        command.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if not SHA.fullmatch(args.source_sha):
        parser.error("source SHA must be a lowercase 40-character Git commit SHA")
    try:
        cases, snapshot = manifest_snapshot()
        if args.command == "record":
            return int(record(args, cases[args.gate], snapshot))
        failed = False
        for gate in GATES:
            try:
                failed = check_gate(args, gate, cases[gate], snapshot) or failed
            except EVIDENCE_ERRORS as error:
                print(f"{gate}: {error}", file=sys.stderr)
                failed = True
        if not failed:
            print(f"Research CI evidence verified for {args.source_sha}")
        return int(failed)
    except EVIDENCE_ERRORS as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
