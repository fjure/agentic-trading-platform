"""Run the private gateway protocol smoke across two isolated Docker processes."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
MODULE = "tests.security.llm_gateway_network_smoke"


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args], cwd=ROOT, capture_output=True, text=True, timeout=45, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"Private gateway smoke command failed: {args[0]}")
    return result.stdout


def test_research_gateway_private_network_smoke():
    name = "atl-research-network-" + uuid4().hex[:12]
    image = os.environ.get("ATL_RESEARCH_TEST_IMAGE", "agentic-trading-lab-research-test:local")
    common = (
        "--pull", "never", "--network", name, "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--tmpfs", "/tmp",
        "--workdir", "/opt/lab", "--entrypoint", "python",
        "--mount", f"type=bind,source={ROOT / 'tests'},target=/opt/lab/tests,readonly",
    )
    docker("network", "create", "--internal", name)
    try:
        docker("run", "-d", "--name", name, "--network-alias", "apf03-supervisor",
               *common, image, "-m", MODULE, "supervisor")
        output = docker("run", "--rm", *common, image, "-m", MODULE, "gateway")
        assert output.strip() == (
            "PASS: private HTTP admission, exact fixture dispatch, replay and revoked generation"
        )
    finally:
        docker("rm", "-f", name, check=False)
        docker("network", "rm", name)
