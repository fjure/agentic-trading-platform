"""Deployment secret and network topology invariants; no real credentials are loaded."""

import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/compose/compose.yaml"


def test_lab_roles_are_separate_and_hardened():
    config = yaml.safe_load(COMPOSE.read_text())
    roles = {"execution", "worker", "control-api", "watchdog"}
    networks = []
    for role in roles:
        service = config["services"][role]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["healthcheck"]["test"] == [
            "CMD", "python", "-m", "services.runtime", "health"
        ]
        assert service["networks"] == {
            "execution": ["execution_db"], "worker": ["worker_db"],
            "control-api": ["control_db", "demo_control", "setup_control", "paper_control"],
            "watchdog": ["watchdog_db"],
        }[role]
        network = service["networks"][0]
        networks.append(network)
        assert config["networks"][network]["internal"] is True
        assert service["secrets"] == [{
            "source": {"execution": "execution_database", "worker": "worker_database",
                       "control-api": "control_database", "watchdog": "watchdog_database"}[role],
            "target": "database",
        }]
        if role == "control-api":
            assert service["ports"] == ["127.0.0.1:8000:8000"]
            assert service["volumes"] == [
                "../../apps/dashboard/dist/dashboard/browser:/opt/lab/dashboard:ro"
            ]
        else:
            assert "ports" not in service
            assert service.get("volumes", []) == {
                "execution": ["alpaca_setup:/run/provider/alpaca:ro"],
                "worker": ["jev_setup:/run/provider/jev:ro"], "watchdog": [],
            }[role]
    assert len(set(networks)) == 4
    assert config["networks"]["paper_control"]["internal"] is True
    assert {
        name for name, service in config["services"].items()
        if "paper_control" in service.get("networks", [])
    } == {"control-api", "paper-api"}
    assert not {"execution-egress", "worker-egress"} & config["services"].keys()


def test_demo_and_setup_have_no_public_port_or_provider_egress():
    config = yaml.safe_load(COMPOSE.read_text())
    for role, networks in (("demo", ["demo_control", "demo_db"]),
                           ("provider-setup", ["setup_control"])):
        service = config["services"][role]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["networks"] == networks
        assert all(config["networks"][network]["internal"] for network in networks)
        assert "ports" not in service
        assert "environment" not in service
        assert service["configs"] == []
    assert config["services"]["demo"].get("volumes", []) == []
    assert config["services"]["demo"]["secrets"] == [
        {"source": "demo_database", "target": "demo_database"}]
    assert config["services"]["provider-setup"]["secrets"] == []
    assert config["services"]["provider-setup"]["volumes"] == [
        "alpaca_setup:/run/provider/alpaca", "jev_setup:/run/provider/jev"]


def test_paper_only_assigns_each_provider_secret_to_its_role():
    config = yaml.safe_load((COMPOSE.parent / "compose.paper.yaml").read_text())
    execution, worker = config["services"]["execution"], config["services"]["worker"]
    assert execution["secrets"] == [{"source": "alpaca_credentials", "target": "alpaca"}]
    assert worker["secrets"] == [{"source": "jev_credentials", "target": "jev"}]
    assert "control-api" not in config["services"]
    assert "watchdog" not in config["services"]
    for role in ("execution", "worker"):
        proxy = config["services"][f"{role}-egress"]
        assert proxy["read_only"] is True
        assert proxy["user"] == "10001:10001"
        assert "secrets" not in proxy and "volumes" not in proxy and "ports" not in proxy
        assert proxy["networks"] == [f"{role}_egress", f"{role}_outbound"]
        assert config["networks"][f"{role}_egress"]["internal"] is True
        assert config["networks"][f"{role}_outbound"] == {}


def test_container_build_cannot_copy_local_private_runtime_files():
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".tooling" in dockerignore and "secrets" in dockerignore
    dockerfile = (ROOT / "infra/containers/Dockerfile").read_text()
    assert "COPY . " not in dockerfile
    assert "--frozen --no-dev --no-editable --no-python-downloads" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "COPY config /opt/lab/config" in dockerfile


@pytest.mark.skipif(os.environ.get("PTV21_DOCKER") != "1", reason="requires running lab profile")
def test_deployed_lab_process_secret_and_network_boundaries():
    """Inspect actual containers and execute probes; output contains booleans only."""
    compose = ["docker", "compose", "-f", str(COMPOSE), "--profile", "lab"]
    roles = ("execution", "worker", "control-api", "watchdog")
    ids = {}
    addresses = {}
    for role in roles:
        container_id = subprocess.check_output(
            [*compose, "ps", "-q", role], text=True, cwd=ROOT
        ).strip()
        assert container_id
        raw = subprocess.check_output(["docker", "inspect", container_id], text=True)
        data = json.loads(raw)[0]
        ids[role] = container_id
        addresses[role] = next(iter(data["NetworkSettings"]["Networks"].values()))["IPAddress"]
        assert data["Config"]["User"] == "10001:10001"
        assert data["HostConfig"]["ReadonlyRootfs"]
        assert data["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in data["HostConfig"]["SecurityOpt"]
        for mount in data["Mounts"]:
            if mount["Type"] != "tmpfs":
                assert mount["RW"] is False
            assert "docker.sock" not in mount["Destination"]
    # Peer networks are not shared. Probe actual peer IPs to avoid DNS-only assertions.
    for role in roles:
        targets = [addresses[peer] for peer in roles if peer != role]
        probe = '''
import json, os, pathlib, socket, sys
targets = json.loads(sys.stdin.read())
def can_connect(host, port):
    try:
        with socket.create_connection((host, port), timeout=.5):
            return True
    except OSError:
        return False
assert os.getuid() == 10001
assert pathlib.Path('/run/secrets/database').is_file()
assert not pathlib.Path('/run/secrets/alpaca').exists()
assert not pathlib.Path('/run/secrets/jev').exists()
assert can_connect('postgres', 5432)
assert not any(can_connect(peer, port) for peer in targets for port in (8000, 8001, 8080))
try:
    pathlib.Path('/opt/lab/write-denied').write_text('synthetic')
    raise AssertionError('root filesystem writable')
except OSError:
    pass
print('boundaries verified')
'''
        result = subprocess.run(
            ["docker", "exec", "-i", ids[role], "python", "-c", probe],
            input=json.dumps(targets), text=True, capture_output=True, timeout=20,
        )
        assert result.returncode == 0, "container boundary probe failed (output suppressed)"
        assert result.stdout.strip() == "boundaries verified"


@pytest.mark.skipif(os.environ.get("PTV21_DOCKER") != "1", reason="requires running lab profile")
def test_deployed_lab_cannot_reach_isolated_external_bridge():
    """A reachable synthetic local TCP sink stands in for external network destinations."""
    compose = ["docker", "compose", "-f", str(COMPOSE), "--profile", "lab"]
    container = subprocess.check_output([*compose, "ps", "-q", "execution"], text=True).strip()
    image = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0][
        "Image"
    ]
    fixture = "ptv21-boundary-" + uuid.uuid4().hex
    label = "atl.ptv21.boundary=" + fixture

    def docker(*args, **kwargs):
        return subprocess.run(
            ["docker", *args], text=True, capture_output=True, timeout=30, **kwargs
        )

    network_created = False
    sink_created = False
    try:
        assert docker("network", "create", "--label", label, fixture).returncode == 0
        network_created = True
        sink = '''
import socket
s = socket.socket()
s.bind(('0.0.0.0',443))
s.listen()
while True:
    c, _ = s.accept()
    c.close()
'''
        result = docker(
            "run", "-d", "--name", fixture, "--network", fixture, "--label", label,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--user", "10001:10001", image, "python", "-c", sink,
        )
        assert result.returncode == 0
        sink_created = True
        data = json.loads(docker("inspect", fixture).stdout)[0]
        address = data["NetworkSettings"]["Networks"][fixture]["IPAddress"]
        connect = "import socket,sys; socket.create_connection((sys.argv[1],443),timeout=1).close()"
        # A control attached to this ordinary noninternal bridge can reach the sink.
        for _ in range(15):
            control = docker(
                "run", "--rm", "--network", fixture, "--label", label,
                "--read-only", "--cap-drop", "ALL", "--user", "10001:10001",
                image, "python", "-c", connect, address,
            )
            if control.returncode == 0:
                break
            time.sleep(.1)
        assert control.returncode == 0, "synthetic external bridge control was unreachable"
        deny = '''
import socket, sys
try:
    socket.create_connection((sys.argv[1],443), timeout=1).close()
except OSError:
    print('denied')
else:
    raise SystemExit(1)
'''
        for role in ("execution", "worker", "control-api", "watchdog"):
            result = subprocess.run(
                [*compose, "exec", "-T", role, "python", "-c", deny, address],
                text=True, capture_output=True, timeout=10,
            )
            assert result.returncode == 0 and result.stdout.strip() == "denied"
    finally:
        if sink_created:
            data = json.loads(docker("inspect", fixture).stdout)[0]
            assert data["Config"]["Labels"].get("atl.ptv21.boundary") == fixture
            assert docker("rm", "-f", fixture).returncode == 0
        if network_created:
            data = json.loads(docker("network", "inspect", fixture).stdout)[0]
            assert data["Labels"].get("atl.ptv21.boundary") == fixture
            assert docker("network", "rm", fixture).returncode == 0


@pytest.mark.skipif(os.environ.get("PTV21_DOCKER") != "1", reason="requires running lab profile")
def test_synthetic_broker_mount_is_confined_to_execution():
    """A one-off Fake role reads an ephemeral generated secret and emits only a boolean."""
    compose = ["docker", "compose", "-f", str(COMPOSE), "--profile", "lab"]
    path = ROOT / ".tooling/application/runtime" / ("synthetic-" + uuid.uuid4().hex + ".json")
    value = secrets.token_urlsafe(32)
    try:
        path.write_text(json.dumps({"synthetic": value}), encoding="utf-8")
        check = '''
import json, pathlib, sys
actual = json.loads(pathlib.Path('/run/secrets/alpaca').read_text())['synthetic']
assert actual == sys.stdin.read()
print('canary confined')
'''
        result = subprocess.run(
            [*compose, "run", "--rm", "--no-deps", "-T", "--volume",
             str(path) + ":/run/secrets/alpaca:ro", "execution", "python", "-c", check],
            input=value, text=True, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, "synthetic mount check failed (output suppressed)"
        assert result.stdout.strip() == "canary confined"
        for role in ("worker", "control-api", "watchdog"):
            result = subprocess.run(
                [*compose, "exec", "-T", role, "python", "-c",
                 "from pathlib import Path; assert not Path('/run/secrets/alpaca').exists()"],
                text=True, capture_output=True, timeout=10,
            )
            assert result.returncode == 0, "broker mount escaped execution"
    finally:
        path.unlink(missing_ok=True)
