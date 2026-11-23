"""Verify the private evaluation entry point ships in the actual production-derived image."""

import os
import subprocess

import pytest


def test_packaged_private_evaluation_operator():
    image = os.environ.get("ATL_RESEARCH_TEST_IMAGE")
    if not image:
        pytest.skip("Run scripts/test-research-db.py for the Docker integration gate.")
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", image,
         "python", "-m", "tests.evaluation.operator", "--help"],
        check=True, timeout=30, capture_output=True,
    )
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", image, "python", "-c",
         "from pathlib import Path; "
         "from services.runtime.research_evaluation import ENCODING; "
         "assert ENCODING == 'EVALUATION_CASE_V1'; "
         "assert not list(Path('/opt/lab/tests/evaluation').glob('test_*.py')); "
         "assert not list(Path('/opt/lab').rglob('holdout.v1.json'))"],
        check=True, timeout=30, capture_output=True,
    )
