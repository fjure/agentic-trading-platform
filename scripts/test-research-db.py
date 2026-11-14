"""Run the required Research integration tests on disposable Docker infrastructure."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "tests/integration/test_agent_research_runtime.py",
    "tests/integration/test_arp_model_attempts.py",
    "tests/integration/test_llm_provider_probe.py",
    "tests/integration/test_companion_runtime.py",
    "tests/recovery/test_discovery_runtime.py",
    "tests/recovery/test_quant_lab_execution.py",
    "tests/security/test_quant_lab_isolation.py",
    "tests/integration/test_research_network_smoke.py",
)


def run(args, *, env=None, timeout=900, quiet=False, check=True):
    return subprocess.run(
        args, cwd=ROOT, env=env, timeout=timeout, check=check, text=True,
        capture_output=quiet,
    )


def provision_and_test(project: str, directory: Path, junit: Path, compose: list[str]) -> int:
    api = run(["docker", "version", "--format", "{{.Server.Os}}"], timeout=20, quiet=True)
    if api.stdout.strip() != "linux":
        raise RuntimeError("A Linux Docker server is required.")

    password = directory / "postgres-password"
    password.write_text(secrets.token_hex(32), encoding="ascii")
    # The pinned Postgres image runs as its unprivileged postgres user.
    # The containing host directory is private and the secret is synthetic.
    password.chmod(0o644)
    (directory / "postgres.yaml").write_text(
        """services:
  postgres:
    networks: !override [local]
    ports: !reset []
    healthcheck:
      test: [CMD, pg_isready, -h, 127.0.0.1, -U, lab_migration, -d, lab]
networks:
  local:
    internal: true
secrets:
  postgres_password:
    file: """ + json.dumps(password.as_posix()) + "\n",
        encoding="utf-8",
    )
    run([*compose, "up", "--wait", "--wait-timeout", "90", "postgres"], timeout=180)
    container = run([*compose, "ps", "-q", "postgres"], quiet=True, timeout=20).stdout.strip()
    if not container:
        raise RuntimeError("The isolated PostgreSQL container is missing.")

    revision = run(["git", "rev-parse", "HEAD"], quiet=True, timeout=10).stdout.strip()
    base_image = f"{project}-base:local"
    test_image = f"{project}-test:local"
    run([
        "docker", "build", "--tag", base_image, "--file", "infra/containers/Dockerfile",
        "--build-arg", f"SOURCE_REVISION={revision}", ".",
    ])
    run([
        "docker", "build", "--tag", test_image,
        "--file", "infra/containers/research-test.Dockerfile",
        "--build-arg", f"RESEARCH_TEST_BASE={base_image}", ".",
    ])
    return run([
        sys.executable, "-m", "pytest", *TESTS, "--tb=short", "--show-capture=no",
        "-o", "junit_logging=no",
        "--junitxml", str(junit),
    ], env=os.environ | {
        "ATL_RESEARCH_TEST_POSTGRES": container,
        "ATL_RESEARCH_TEST_NETWORK": f"{project}_local",
        "ATL_RESEARCH_TEST_IMAGE": test_image,
    }, timeout=1200, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junitxml", required=True, type=Path)
    args = parser.parse_args()
    junit = args.junitxml.resolve()
    junit.unlink(missing_ok=True)
    project = "atl-arp-db-" + uuid4().hex[:12]
    with TemporaryDirectory(prefix=project + "-") as scratch:
        directory = Path(scratch)
        compose = [
            "docker", "compose", "--project-name", project, "--profile", "fake",
            "--file", "infra/compose/compose.yaml", "--file", str(directory / "postgres.yaml"),
        ]
        try:
            status = provision_and_test(project, directory, junit, compose)
            # A pytest usage or collection failure represents an incomplete test run.
            status = 0 if status == 0 else 1
        except (OSError, RuntimeError, subprocess.SubprocessError):
            print("Research database test environment is unavailable.", file=sys.stderr)
            status = 2
        finally:
            try:
                if (directory / "postgres.yaml").is_file():
                    run([*compose, "down", "--volumes"], timeout=90)
            except (OSError, subprocess.SubprocessError):
                print("Research database test project cleanup failed.", file=sys.stderr)
                status = 2
            for label in ("test", "base"):
                try:
                    run(["docker", "image", "rm", f"{project}-{label}:local"],
                        timeout=45, quiet=True, check=False)
                except (OSError, subprocess.SubprocessError):
                    status = 2
    return status


if __name__ == "__main__":
    sys.exit(main())
