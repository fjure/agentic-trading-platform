"""Disposable real Paper browser owner; never attaches to the user's running lab."""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://127.0.0.1:4220"
CANARIES = ("synthetic-paper-browser-key", "synthetic-paper-browser-secret")


def relay():
    import socketserver

    from services.runtime import host_bridge

    name = os.environ.get("PAPER_BROWSER_CONTAINER", "")
    if not re.fullmatch(r"atl-paper-browser-[0-9a-f]{20}-app", name):
        raise ValueError("isolated owner required")
    host_bridge.command = lambda: [
        "docker", "exec", "-i", name, "python", "-u",
        "-m", "services.runtime.host_bridge", "guest",
    ]
    with socketserver.ThreadingTCPServer(("127.0.0.1", 4220), host_bridge.Handler) as server:
        server.serve_forever()


def host():
    from tests.integration.paper_clock.runtime import (
        POSTGRES_IMAGE,
        PYTHON_IMAGE,
        clock_environment,
        shared_offset,
    )

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 4220))
    identity = "atl-paper-browser-" + uuid.uuid4().hex[:20]
    network, postgres, application = identity + "-net", identity + "-pg", identity + "-app"
    created, child = [], None

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("isolated Docker operation failed; output redacted")
        return result.stdout.strip()

    def environment(values):
        return [part for key, value in values.items() for part in ("--env", key + "=" + value)]

    # Derive once, after preflight. Independent processes share the same moving
    # wall-clock offset; real monotonic time and the host clock stay unchanged.
    day_boundary = os.environ.get("PAPER_BROWSER_DAY_BOUNDARY", "0")
    if day_boundary not in {"0", "1"}:
        raise ValueError("invalid isolated day scenario")
    offset = shared_offset(datetime(2026, 10, 26, 23, 59, 35, tzinfo=UTC)
                           if day_boundary == "1" else None)
    crash = os.environ.get("PAPER_BROWSER_CRASH", "0")
    model = os.environ.get("PAPER_BROWSER_MODEL", "0")
    submit_crash = os.environ.get("PAPER_BROWSER_SUBMIT_CRASH", "0")
    if crash not in {"0", "1"} or model not in {"0", "1"} or submit_crash not in {"0", "1"}:
        raise ValueError("invalid isolated crash scenario")
    try:
        docker("network", "create", "--internal", network)
        created.append(("network", network))
        docker(
            "run", "--detach", "--pull=never", "--name", postgres,
            "--network", network, "--network-alias", "postgres", "--user", "postgres",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--tmpfs", "/var/lib/postgresql:uid=999,gid=999", "--tmpfs", "/tmp",
            "--tmpfs", "/var/run/postgresql:uid=999,gid=999",
            "--env", "POSTGRES_USER=lab_migration", "--env", "POSTGRES_DB=lab",
            "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env", "POSTGRES_INITDB_ARGS=--encoding=UTF8 --locale=C.UTF-8",
            *environment(clock_environment(offset)), POSTGRES_IMAGE,
        )
        created.append(("container", postgres))
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", postgres, "pg_isready", "-U", "lab_migration", "-d", "lab"],
                capture_output=True, timeout=5,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("isolated PostgreSQL unavailable")
        command = [
            "docker", "run", "--rm", "--interactive", "--pull=never", "--name", application,
            "--network", network, "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
            "--cap-add", "FOWNER", "--security-opt", "no-new-privileges:true", "--user", "0:0",
            "--tmpfs", "/tmp", "--add-host", "paper-api:127.0.0.1",
            "--add-host", "provider-setup:127.0.0.1",
            "--env", "PAPER_BROWSER_CRASH=" + crash,
            "--env", "PAPER_BROWSER_DAY_BOUNDARY=" + day_boundary,
            "--env", "PAPER_BROWSER_MODEL=" + model,
            "--env", "PAPER_BROWSER_SUBMIT_CRASH=" + submit_crash,
            *environment(clock_environment(offset, python=True)),
        ]
        for source, target in (
            (ROOT / "services", "/opt/lab/services"),
            (ROOT / "packages", "/opt/lab/packages"),
            (ROOT / "config", "/opt/lab/config"),
            (ROOT / "alembic.ini", "/opt/lab/alembic.ini"),
            (ROOT / "apps/dashboard/dist/dashboard/browser", "/opt/lab/dashboard"),
            (Path(__file__), "/opt/lab/paper_browser_support.py"),
            (Path(__file__).with_name("paper_browser_runtime.py"),
             "/opt/lab/paper_browser_runtime.py"),
            (Path(__file__).with_name("paper_browser_provider.py"),
             "/opt/lab/paper_browser_provider.py"),
            (Path(__file__).with_name("paper_browser_execution.py"),
             "/opt/lab/paper_browser_execution.py"),
        ):
            command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
        command.extend(("--entrypoint", "python", PYTHON_IMAGE, "-u",
                        "paper_browser_support.py", "guest"))
        child = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True,
        )
        created.append(("container", application))
        lines = queue.Queue()

        def collect_lines():
            for line in child.stdout:
                lines.put(line.strip())
            lines.put(None)

        threading.Thread(target=collect_lines, daemon=True).start()

        def read_line(timeout):
            try:
                line = lines.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("isolated owner response deadline exceeded") from None
            if line is None:
                raise RuntimeError("isolated owner exited before evidence")
            return line

        line = read_line(90)
        if re.fullmatch(r"PAPER_BROWSER_FAIL:[A-Z_]+:[A-Za-z]+(?::[a-z_]+[.]py:[0-9]+)?", line):
            print(line, flush=True)
            raise RuntimeError("isolated Paper guest failed before readiness")
        ready = json.loads(line)
        if set(ready) != {"ready", "activationCode"} or ready["ready"] is not True:
            raise RuntimeError("isolated real Paper API unavailable")
        env = os.environ.copy()
        activation = ready.pop("activationCode")
        env.update(
            PAPER_BROWSER_CONTAINER=application, PAPER_BROWSER_PYTHON=sys.executable,
            PAPER_BROWSER_ACTIVATION=activation, PLAYWRIGHT_NO_COPY_PROMPT="1",
        )
        result = subprocess.run(
            [str(ROOT / ".tooling/contracts/node/node-v22.23.3-win-x64/node.exe"),
             str(ROOT / "apps/dashboard/node_modules/@playwright/test/cli.js"),
             "test", "--config", "playwright.paper.config.ts"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
        )
        env.pop("PAPER_BROWSER_ACTIVATION", None)
        public = result.stdout + result.stderr
        for canary in (*CANARIES, activation, "11111111-1111-4111-8111-111111111111"):
            public = public.replace(canary, "[redacted synthetic input]")
        print(public.strip(), flush=True)
        if result.returncode:
            child.stdin.write("diagnose\n")
            child.stdin.flush()
            diagnostic = read_line(10)
            for canary in (*CANARIES, activation, "11111111-1111-4111-8111-111111111111"):
                diagnostic = diagnostic.replace(canary, "[redacted synthetic input]")
            print(diagnostic, flush=True)
            raise RuntimeError("real Paper browser acceptance failed")
        child.stdin.write("verify\n")
        child.stdin.flush()
        proof = read_line(20)
        expected = "PASS SQL: actual UTC boundary expires immutable FIRST_DAY authority" \
            if day_boundary == "1" else \
            "PASS SQL: actual Worker MODEL provenance, Risk and financial fill" \
            if model == "1" else \
            "PASS SQL: actual submit SIGKILL, lookup recovery and financial fill" \
            if submit_crash == "1" else \
            "PASS SQL: real browser Paper lifecycle, financial recovery and isolation"
        if proof != expected:
            if re.fullmatch(
                r"PAPER_BROWSER_FAIL:[A-Z_]+:[A-Za-z]+(?::[a-z_]+[.]py:[0-9]+)?", proof,
            ):
                print(proof, flush=True)
            raise RuntimeError("real Paper SQL acceptance failed; diagnostics redacted")
        print(proof, flush=True)
        child.wait(timeout=20)
        if child.returncode:
            raise RuntimeError("isolated Paper owner did not exit cleanly")
    finally:
        if child is not None and child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        for kind, name in reversed(created):
            subprocess.run(
                ["docker", "rm", "--force", name] if kind == "container"
                else ["docker", "network", "rm", name],
                capture_output=True, timeout=30, check=False,
            )


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["guest"]:
            from paper_browser_runtime import run_guest

            run_guest()
        else:
            {"host": host, "relay": relay}[sys.argv[1]]()
    except (Exception, KeyboardInterrupt) as error:
        print("FAIL isolated Paper browser: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
