"""PTV-03 contract evidence. HTTP runtime tests belong to PTV-18, not this suite."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]


def schema(name: str) -> Draft202012Validator:
    docs = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (ROOT / "contracts/schemas/v1").glob("*.schema.json")
    ]
    registry = Registry().with_resources(
        (d["$id"], Resource.from_contents(d, default_specification=DRAFT202012)) for d in docs
    )
    selected = next(d for d in docs if d["$id"].endswith(f"/{name}.schema.json"))
    return Draft202012Validator(selected, registry=registry, format_checker=FormatChecker())


def example(name: str) -> dict:
    return json.loads((ROOT / f"contracts/examples/v1/{name}.json").read_text(encoding="utf-8"))


def test_canonical_examples_and_relationship_checks():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate-contracts.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mutation", ["float-price", "local-time", "wrong-interval"])
def test_market_negative_contract(mutation):
    data = example("market-state.valid")
    if mutation == "float-price":
        data["bar"]["close"] = 100.5
    elif mutation == "local-time":
        data["asOf"] = "2026-10-12T17:00:00+02:00"
    else:
        data["interval"] = "PT1M"
    assert not schema("market-state").is_valid(data)


@pytest.mark.parametrize("mutation", ["short", "missing-answer", "extra-order", "fake-fallback"])
def test_decision_negative_contract(mutation):
    data = example("decision-result.valid")
    if mutation == "short":
        data["choice"] = "SHORT"
    elif mutation == "missing-answer":
        del data["typedAnswers"]["signalConflict"]
    elif mutation == "extra-order":
        data["typedAnswers"]["regime"]["orderQuantity"] = "100"
    else:
        data = example("decision-result.abstain")
        data["confidence"] = "0.90"
    assert not schema("decision-result").is_valid(data)


def test_oasdiff_real_breaking_changes_and_self_comparison(tmp_path):
    canonical = ROOT / "contracts/http/control-api.openapi.yaml"
    exe = ROOT / ".tooling/contracts/oasdiff/oasdiff.exe"
    assert exe.is_file(), "run pinned contract bootstrap"
    original = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    unchanged = subprocess.run(
        [str(exe), "breaking", str(canonical), str(canonical), "--fail-on", "ERR"],
        capture_output=True,
    )
    assert unchanged.returncode == 0
    for kind in ("endpoint", "conditional"):
        changed = copy.deepcopy(original)
        if kind == "endpoint":
            del changed["paths"]["/api/v1/mode"]["get"]
        else:
            for key in ("if", "then", "else"):
                del changed["components"]["schemas"]["Decision"][key]
        target = tmp_path / f"{kind}.yaml"
        target.write_text(yaml.safe_dump(changed, sort_keys=False), encoding="utf-8")
        result = subprocess.run(
            [str(exe), "breaking", str(canonical), str(target), "--fail-on", "ERR"],
            capture_output=True,
        )
        assert result.returncode == 1, result.stdout + result.stderr


def test_baseline_gate_missing_ref_is_fail_closed(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "baseline", ROOT / "scripts/check-contract-breaking.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "git", lambda *args: subprocess.CompletedProcess(args, 1, "", ""))
    with pytest.raises(ValueError, match="baseline ref is unavailable"):
        module.check("missing-ref")


def test_initial_flag_rejects_non_root_commit(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "baseline", ROOT / "scripts/check-contract-breaking.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "git",
        lambda *args: subprocess.CompletedProcess(
            args, 0, "abc def" if args[0] == "rev-list" else "abc", ""
        ),
    )
    with pytest.raises(ValueError, match="root commit"):
        module.check(initial=True)


def test_initial_flag_rejects_git_errors(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "baseline", ROOT / "scripts/check-contract-breaking.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module, "git", lambda *args: subprocess.CompletedProcess(args, 128, "", "Git error")
    )
    with pytest.raises(ValueError, match="cannot verify Git worktree"):
        module.check(initial=True)
