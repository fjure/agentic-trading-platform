"""Actual Windows loopback transport; never issues an activation credential."""

import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

import httpx
import pytest

from services.runtime.host_bridge import command, main


def test_stdio_target_cannot_be_selected_by_a_request_or_cli():
    value = command()
    assert value[-6:] == [
        "control-api", "python", "-u", "-m", "services.runtime.host_bridge", "guest"
    ]
    assert "--profile" in value and "lab" in value
    with pytest.raises(SystemExit):
        main(["https://arbitrary.invalid"])


def test_stdio_tunnel_preserves_binary_bytes_and_half_close(monkeypatch, capsys):
    import services.runtime.host_bridge as bridge

    monkeypatch.setattr(bridge, "command", lambda: [
        sys.executable, "-u", "-c",
        "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data[::-1])",
    ])
    payload = b"synthetic-offline-canary\x00\xff\r\n"
    with bridge.Server(("127.0.0.1", 0), bridge.Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=5) as client:
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                received = bytearray()
                while data := client.recv(16384):
                    received.extend(data)
            assert received == payload[::-1]
        finally:
            server.shutdown()
            thread.join(timeout=5)
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.skipif(os.environ.get("PTV21_DOCKER") != "1", reason="requires local lab")
def test_actual_loopback_dashboard_and_control_boundaries():
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=15) as client:
        for path in ("/health", "/ready"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["checks"]["DATABASE"] == "HEALTHY"
        page = client.get("/")
        assert page.status_code == 200 and "<atl-root" in page.text
        policy = page.headers["content-security-policy"]
        assert "object-src 'none'" in policy and "frame-ancestors 'none'" in policy
        assert "script-src 'unsafe-inline'" not in policy
        assert client.get("/api/v1/mode").status_code == 401
        for headers in (
            {"Host": "evil.invalid"}, {"Origin": "http://evil.invalid"},
            {"Forwarded": "for=127.0.0.1"}, {"X-Forwarded-Host": "127.0.0.1:8000"},
        ):
            assert client.get("/ready", headers=headers).status_code == 400


@pytest.mark.skipif(os.environ.get("PTV21_DOCKER") != "1", reason="requires local lab")
def test_aborted_real_client_does_not_leave_docker_guest_processes():
    # Inspect only command-argument membership of our fixed, non-secret guest
    # command in this one container. Never inspect process environments.
    inspect = (
        "from pathlib import Path; count=0\n"
        "for p in Path('/proc').glob('[0-9]*/cmdline'):\n"
        " try:\n"
        "  args=p.read_bytes().split(bytes([0]))[:-1]\n"
        "  count+=args[-2:]==[b'services.runtime.host_bridge',b'guest']\n"
        " except (FileNotFoundError,PermissionError,ProcessLookupError): pass\n"
        "print(count)"
    )
    root = Path(__file__).resolve().parents[2]

    def guests():
        result = subprocess.run(
            ["docker", "compose", "-f", str(root / "infra/compose/compose.yaml"),
             "--profile", "lab", "exec", "-T", "control-api", "python", "-c", inspect],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return int(result.stdout.strip())

    # Wait for earlier HTTP keep-alives to complete before taking a baseline.
    for _ in range(12):
        baseline = guests()
        if baseline == 0:
            break
        time.sleep(1)
    assert baseline == 0
    client = socket.create_connection(("127.0.0.1", 8000), timeout=10)
    try:
        # An incomplete request cannot finish normally: the real guest blocks
        # waiting for a response until the client abort reaches its stdin/pipe.
        client.sendall(b"GET /ready HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n")
        for _ in range(12):
            if guests() >= 1:
                break
            time.sleep(1)
        else:
            raise AssertionError("positive control did not reach the actual Docker guest")
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
    finally:
        client.close()
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        if guests() == 0:
            return
        time.sleep(1)
    raise AssertionError("aborted client left a Docker guest beyond the bounded idle period")
