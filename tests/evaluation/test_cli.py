"""Exercise the installed scorer against explicitly selected registered inputs."""

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

from tests.evaluation.catalog import FIXTURES, IDENTITY_FIELDS

ROOT = Path(__file__).resolve().parents[2]


def test_cli_model_mode_uses_selected_manifest_and_requires_complete_observations(tmp_path):
    manifest = tmp_path / "registered-manifest.json"
    development = tmp_path / "registered-development.json"
    development.write_bytes((FIXTURES / "development.v1.json").read_bytes())
    cases = json.loads(development.read_text())["cases"]
    synthetic = [
        case | {"caseId": "CLI-" + case["caseId"], "cluster": "cli-" + case["cluster"],
                "split": "holdout"}
        for case in cases
    ]
    holdout = tmp_path / "synthetic-holdout.json"
    holdout.write_text(json.dumps({"version": 1, "cases": synthetic}))
    manifest.write_text(json.dumps({
        "version": 1,
        "developmentSha256": sha256(development.read_bytes()).hexdigest(),
        "holdoutSha256": sha256(holdout.read_bytes()).hexdigest(),
        "cases": [
            {field: case[field] for field in IDENTITY_FIELDS}
            for case in [*cases, *synthetic]
        ],
    }))
    responses = tmp_path / "observations.json"
    responses.write_text("{}")
    report = tmp_path / "model-report.json"
    result = subprocess.run(
        [
            sys.executable, "-m", "tests.evaluation",
            "--manifest", str(manifest), "--development", str(development),
            "--responses", str(responses), "--output", str(report),
            "--split", "holdout", "--holdout", str(holdout),
            "--mode", "model", "--require-pass",
        ],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stderr
    assert report.is_file(), result.stderr
    document = json.loads(report.read_text())
    assert document["manifestSha256"] == sha256(manifest.read_bytes()).hexdigest()
    assert document["score"]["mode"] == "model"
    assert document["score"]["complete"] is False
    assert document["score"]["passed"] is False
