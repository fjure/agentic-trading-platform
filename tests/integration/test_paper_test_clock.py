"""Actual disposable PostgreSQL/Python process-clock compatibility, no trading."""

import json
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tests.integration.paper_clock.runtime import (
    POSTGRES_IMAGE,
    PYTHON_IMAGE,
    SOURCE_SHA256,
    clock_environment,
    shared_offset,
)


def docker(*args, timeout=120):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        marker = re.search(r"CLOCK_FAIL:[A-Z_]+:[A-Za-z]+", result.stdout)
        metrics = re.findall(r"CLOCK_METRIC:[A-Z_]+:[0-9.]+", result.stdout)
        for metric in metrics:
            print(metric)
        raise RuntimeError(marker.group() if marker else "isolated test clock command failed")
    return result.stdout.strip()


def env_args(offset, *, python=False):
    return [
        part
        for key, value in clock_environment(offset, python=python).items()
        for part in ("--env", key + "=" + value)
    ]


def test_clock_configuration_forbids_rate_freeze_and_external_config():
    import pytest

    for offset in (None, "@2026-10-26 14:30:05", "+0 x0", "+0 x2", "+1\n", "/tmp/time"):
        with pytest.raises(ValueError):
            clock_environment(offset)


def test_actual_paper_process_clock_compatibility():
    root = Path(__file__).resolve().parents[2]
    clock_sources = Path(__file__).with_name("paper_clock").resolve()
    offset = shared_offset()
    host_started = datetime.now(UTC)
    host_monotonic = time.monotonic()
    identity = "atl-paper-clock-" + uuid.uuid4().hex[:20]
    network, postgres = identity + "-net", identity + "-pg"
    network_created = container_created = False
    try:
        for image in (PYTHON_IMAGE, POSTGRES_IMAGE):
            defaults = json.loads(
                docker("image", "inspect", image, "--format", "{{json .Config.Env}}")
            )
            assert not any(
                value.split("=", 1)[0]
                in {
                    "LD_PRELOAD",
                    "FAKETIME",
                    "FAKETIME_DONT_FAKE_MONOTONIC",
                    "NO_FAKE_STAT",
                }
                for value in defaults
            )
        docker("network", "create", "--internal", network)
        network_created = True
        docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            postgres,
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--user",
            "postgres",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/var/lib/postgresql:uid=999,gid=999",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/var/run/postgresql:uid=999,gid=999",
            "--env",
            "POSTGRES_USER=clock_probe",
            "--env",
            "POSTGRES_DB=clock_probe",
            "--env",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            *env_args(offset),
            POSTGRES_IMAGE,
        )
        container_created = True
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", postgres, "pg_isready", "-U", "clock_probe"],
                capture_output=True,
                timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("disposable clock PostgreSQL unavailable")
        result = json.loads(
            docker(
                "run",
                "--rm",
                "--pull=never",
                "--network",
                network,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp",
                *env_args(offset, python=True),
                "--mount",
                f"type=bind,source={clock_sources},target=/opt/clock-probe,readonly",
                "--entrypoint",
                "python",
                PYTHON_IMAGE,
                "/opt/clock-probe/compatibility.py",
            )
        )
        assert len(result["passed"]) == 16
        assert datetime.now(UTC).weekday() == host_started.weekday()
        assert (
            abs(
                (datetime.now(UTC) - host_started).total_seconds()
                - (time.monotonic() - host_monotonic)
            )
            < 0.5
        )
        evidence = root / ".tooling/output/paper-clock/compatibility.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                result
                | {
                    "sourceSha256": SOURCE_SHA256,
                    "pythonImageId": docker(
                        "image", "inspect", PYTHON_IMAGE, "--format", "{{.Id}}"
                    ),
                    "postgresImageId": docker(
                        "image", "inspect", POSTGRES_IMAGE, "--format", "{{.Id}}"
                    ),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        assert re.fullmatch(r"atl-paper-clock-[0-9a-f]{20}", identity)
        if container_created:
            docker("rm", "--force", postgres)
        if network_created:
            docker("network", "rm", network)
