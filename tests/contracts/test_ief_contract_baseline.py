"""Preserve non-ASCII contract content in the real Git comparison baseline."""
import runpy
import subprocess
from pathlib import Path


def test_git_baseline_preserves_utf8_contract_bytes():
    root = Path(__file__).resolve().parents[2]
    helper = runpy.run_path(str(root / 'scripts/check-contract-breaking.py'))['git']
    blob = subprocess.check_output(
        ['git', 'show', 'HEAD:contracts/http/control-api.openapi.yaml'], cwd=root
    )
    assert any(byte > 127 for byte in blob), 'Fixture must exercise UTF-8 beyond ASCII'
    decoded = helper('show', 'HEAD:contracts/http/control-api.openapi.yaml')
    assert decoded.returncode == 0
    assert decoded.stdout.encode('utf-8') == blob
