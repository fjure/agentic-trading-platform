"""Fail-closed behavior for the versioned Research CI evidence gate."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-research-evidence.py"
SPEC = importlib.util.spec_from_file_location("research_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)
SOURCE = "a" * 40


def run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, check=False,
    )


def reports(root, *, defect=None):
    cases, _ = EVIDENCE.manifest_snapshot()
    for gate in EVIDENCE.GATES:
        xml = ET.Element("testsuite")
        for index, (classname, name) in enumerate(cases[gate]):
            current = gate == "db" and index == 0
            if current and defect == "absent":
                continue
            case = ET.SubElement(xml, "testcase", classname=classname, name=name)
            if current and defect == "skipped":
                ET.SubElement(case, "skipped", message="private details")
            if current and defect == "duplicate":
                ET.SubElement(xml, "testcase", classname=classname, name=name)
        ET.SubElement(xml, "testcase", classname="random", name="private details")
        ET.SubElement(xml, "system-out").text = "private details"
        report = root / (gate + ".xml")
        report.write_bytes(ET.tostring(xml))
        sha = "b" * 40 if defect == "sha" and gate == "db" else SOURCE
        if defect == "missing_environment" and gate == "db":
            status, junit = "missing_environment", []
        else:
            status, junit = "passed", ["--junit", report]
        result = run(
            "record", "--gate", gate, "--source-sha", sha, "--output", root / gate, "--status",
            status, *junit,
        )
        expected = gate == "db" and defect in {
            "absent", "skipped", "duplicate", "missing_environment",
        }
        assert result.returncode == int(expected), result.stderr
    return report


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        (None, ""),
        ("skipped", "Gate status: skipped"),
        ("absent", "Gate status: failed"),
        ("duplicate", "Gate status: failed"),
        ("sha", "Artifact identity does not match"),
        ("missing_artifact", "junit.xml"),
        ("modified_artifact", "JUnit checksum does not match"),
        ("missing_environment", "Gate status: missing_environment"),
        ("wrong_manifest", "Artifact identity does not match"),
    ],
)
def test_required_gate_evidence(tmp_path, defect, message):
    reports(tmp_path, defect=defect)
    junit = tmp_path / "db" / "junit.xml"
    if defect == "missing_artifact":
        junit.unlink()
    elif defect == "modified_artifact":
        junit.write_bytes(junit.read_bytes() + b"\n")
    elif defect == "wrong_manifest":
        evidence = tmp_path / "db" / "evidence.json"
        item = json.loads(evidence.read_text(encoding="utf-8"))
        item["manifestSha256"] = "f" * 64
        evidence.write_text(json.dumps(item), encoding="utf-8")
    result = run(
        "check", "--artifacts", tmp_path, "--source-sha", SOURCE, "--browser-required", "true",
    )
    assert result.returncode == int(defect is not None), result.stderr
    assert message in result.stderr
    assert "private details" not in (tmp_path / "fast" / "junit.xml").read_text(encoding="utf-8")


def test_browser_omission_is_reported_and_requires_independent_policy(tmp_path):
    reports(tmp_path)
    result = run(
        "record", "--gate", "browser", "--source-sha", SOURCE, "--output", tmp_path / "browser",
        "--status", "not_required", "--reason", "documentation-only paths",
    )
    assert result.returncode == 0, result.stderr
    for required, expected in (("false", 0), ("true", 1)):
        result = run(
            "check", "--artifacts", tmp_path, "--source-sha", SOURCE,
            "--browser-required", required,
        )
        assert result.returncode == expected, result.stderr


def test_malformed_junit_cannot_leave_stale_pass_evidence(tmp_path):
    report = reports(tmp_path)
    report.write_text("<testcases", encoding="utf-8")
    result = run(
        "record", "--gate", "browser", "--source-sha", SOURCE, "--output", tmp_path / "browser",
        "--status", "passed", "--junit", report,
    )
    assert result.returncode == 1
    assert "invalid" in result.stderr
    assert not (tmp_path / "browser" / "junit.xml").exists()
    evidence = json.loads((tmp_path / "browser" / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
