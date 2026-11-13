"""Run the Research BYOK mount proof using a disposable Compose project."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SETUP = """
import os
import stat
from pathlib import Path

root = Path('/run/research-credentials')
private = Path('/run/research-setup/private')
assert (os.getuid(), os.getgid()) == (10004, 10002)
assert (root.stat().st_uid, root.stat().st_gid, stat.S_IMODE(root.stat().st_mode)) == (
    10004, 10002, 0o2750,
)
assert (private.stat().st_uid, stat.S_IMODE(private.stat().st_mode)) == (10004, 0o700)
with os.fdopen(os.open(root / 'mount-proof', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640),
               'wb') as proof:
    proof.write(b'synthetic-only')
"""
GATEWAY = """
import os
from pathlib import Path

root = Path('/run/research-credentials')
assert (os.getuid(), os.getgid()) == (10002, 10002)
assert (root / 'mount-proof').read_bytes() == b'synthetic-only'
assert os.statvfs(root).f_flag & os.ST_RDONLY
assert not Path('/run/research-setup').exists()
"""
SOCKET_FLOW = r"""
import socket
from pathlib import Path
from time import monotonic, sleep

import httpx

activation_path = '/run/research-setup/private/activate.sock'
setup_path = '/run/research-setup/setup.sock'
deadline = monotonic() + 10
while not (Path(activation_path).is_socket() and Path(setup_path).is_socket()):
    if monotonic() >= deadline:
        raise RuntimeError('Research setup sockets unavailable')
    sleep(0.05)

def grant(action):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(2)
        client.connect(activation_path)
        client.sendall(f'OPENAI {action}\n'.encode('ascii'))
        return client.recv(128).decode('ascii').strip()

origin = 'http://127.0.0.1:8000'
path = '/api/v1/integrations/research-setup/providers/OPENAI'
with httpx.Client(transport=httpx.HTTPTransport(uds=setup_path), trust_env=False) as client:
    def send(method, suffix, body, *, token=None, request_origin=origin):
        reply = client.request(
            method, origin + path + suffix, json=body,
            headers={'Origin': request_origin,
                     'X-Research-Setup-Grant': token or grant(suffix[1:] or 'save')},
        )
        assert 'synthetic-only' not in reply.text
        return reply.status_code, reply.json()

    first_grant = grant('save')
    initial = {'expectedCredentialVersion': 1, 'credential': 'synthetic-only-first'}
    denied = send('PUT', '', initial, token=first_grant, request_origin='http://localhost:8000')
    assert denied[0] == 403
    status, body = send('PUT', '', initial, token=first_grant)
    assert status == 200 and body['credentialGeneration'] == 1
    assert send('PUT', '', initial, token=first_grant)[0] == 403
    rotation = {'expectedCredentialVersion': body['credentialVersion'],
                'expectedCredentialGeneration': 1, 'credential': 'synthetic-only-second'}
    status, body = send('POST', '/rotate', rotation)
    assert status == 200 and body['credentialGeneration'] == 2
    revoke = {'expectedCredentialVersion': body['credentialVersion'], 'credentialGeneration': 2}
    status, body = send('POST', '/revoke', revoke)
    assert status == 200 and body['state'] == 'REVOKED'
print('PASS mounted setup sockets, one-time grant, save, rotate, revoke and response redaction')
"""
ISSUE_GRANT = r"""
import socket
import sys

with socket.socket(socket.AF_UNIX) as client:
    client.settimeout(2)
    client.connect('/run/research-setup/private/activate.sock')
    client.sendall(b'ANTHROPIC save\n')
    sys.stdout.buffer.write(client.recv(128))
"""
BRIDGE = """
import asyncio
import sys
from pathlib import Path

import httpx
from services.control_api.auth import LocalAuth
from services.runtime.api import create_bridge
from services.runtime.config import RuntimeConfig

async def main():
    assert not Path('/run/research-credentials').exists()
    try:
        Path('/run/research-setup/private/activate.sock').stat()
    except PermissionError:
        pass
    else:
        raise AssertionError('Frontend can inspect private setup activation socket')
    auth = LocalAuth()
    session = auth.activate(auth.issue_activation_code(), 'mount-proof-session')
    grant = sys.stdin.readline().strip()
    assert len(grant) == 43
    transport = httpx.ASGITransport(app=create_bridge(RuntimeConfig(), auth=auth))
    async with httpx.AsyncClient(
        transport=transport, base_url=auth.origin, trust_env=False,
    ) as client:
        path = '/api/v1/integrations/research-setup/providers/ANTHROPIC'
        headers = {'Origin': auth.origin, 'X-Research-Setup-Grant': grant}
        payload = {'expectedCredentialVersion': 1, 'credential': 'synthetic-only-bridge'}
        denied = await client.put(path, headers=headers, json=payload)
        assert denied.status_code == 401
        client.cookies.set(auth.cookie_name, session)
        accepted = await client.put(path, headers=headers, json=payload)
        assert accepted.status_code == 200 and accepted.json()['credentialGeneration'] == 1
        assert 'synthetic-only-bridge' not in accepted.text
        status = await client.get(path)
        assert status.status_code == 200 and status.json()['credentialGeneration'] == 1

asyncio.run(main())
print('PASS authenticated frontend bridge over read-only setup socket mount')
"""


def main():
    project = "atl-arp98-mount-" + uuid4().hex[:12]
    compose = [
        "docker", "compose", "-p", project,
        "-f", "infra/compose/compose.yaml",
        "-f", "infra/compose/compose.research-setup.yaml",
        "--profile", "lab", "--profile", "research-gateway",
        "--profile", "research-provider-setup",
    ]

    def run(*args):
        subprocess.run([*compose, *args], cwd=ROOT, check=True)

    model = json.loads(subprocess.check_output(
        [*compose, "config", "--format", "json"], cwd=ROOT,
    ))
    services = model["services"]
    for volume, expected in {
        "research_credentials": {
            "research-setup-volumes": False, "research-setup": False,
            "research-llm-gateway": True,
        },
        "research_setup_socket": {
            "research-setup-volumes": False, "research-setup": False, "control-api": True,
        },
    }.items():
        actual = {
            service: mount.get("read_only", False)
            for service, value in services.items()
            for mount in value.get("volumes", [])
            if mount.get("source") == volume
        }
        assert actual == expected
    for service in ("research-setup", "research-setup-volumes"):
        assert services[service]["network_mode"] == "none"
        assert not services[service].get("ports")
    try:
        run("run", "--build", "--rm", "--no-deps", "research-setup-volumes")
        run("run", "--rm", "--no-deps", "research-setup", "python", "-c", SETUP)
        run("run", "--build", "--rm", "--no-deps", "research-llm-gateway", "python", "-c", GATEWAY)
        run("run", "--rm", "--no-deps", "research-setup-volumes")
        run("up", "--build", "-d", "--no-deps", "research-setup")
        run("exec", "-T", "research-setup", "python", "-c", SOCKET_FLOW)
        grant = subprocess.check_output(
            [*compose, "exec", "-T", "research-setup", "python", "-c", ISSUE_GRANT], cwd=ROOT,
        )
        subprocess.run([
            "docker", "run", "--rm", "-i", "--network", "none", "--user", "10001:10001",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--mount",
            f"source={project}_research_setup_socket,target=/run/research-setup,readonly",
            f"{project}-research-setup", "python", "-c", BRIDGE,
        ], input=grant, cwd=ROOT, check=True)
    finally:
        run("down", "--volumes")
    print("PASS Research setup UID/GID, mount permissions, isolation and volume initialization")


if __name__ == "__main__":
    main()
