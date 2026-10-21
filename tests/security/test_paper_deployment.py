"""APW-02 deployment boundaries; live probes never read provider credentials."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "infra/compose/compose.yaml"
COMPOSE = yaml.safe_load(COMPOSE_PATH.read_text())
SERVICES = COMPOSE["services"]
PAPER_ROLES = {
    "paper-execution": ("paper_execution_db", "paper_execution_database"),
    "paper-worker": ("paper_worker_db", "paper_worker_database"),
    "paper-api": ("paper_control_db", "paper_control_database"),
    "paper-watchdog": ("paper_watchdog_db", "paper_watchdog_database"),
}


def test_paper_deployment_is_available_in_normal_lab_with_fixed_private_database_mounts():
    for role, (network, secret) in PAPER_ROLES.items():
        service = SERVICES[role]
        assert "lab" in service["profiles"]
        assert service["command"] == ["python", "-m", "services.runtime", role]
        assert service["secrets"] == [{"source": secret, "target": "database"}]
        assert service["configs"] == [
            {"source": "paper_runtime_config", "target": "/run/config/runtime.json"}
        ]
        assert network in service["networks"]
        assert network in SERVICES["postgres"]["networks"]
        assert all(item.startswith("paper_") for item in service["networks"])
        assert all(COMPOSE["networks"][item].get("internal") for item in service["networks"])
        assert "ports" not in service
        path = COMPOSE["secrets"][secret]["file"]
        assert path.endswith("/paper-" + ("control" if role == "paper-api" else role[6:]) + ".json")
    assert COMPOSE["configs"]["paper_runtime_config"]["file"].endswith("/paper-runtime.json")


def test_paper_service_privileges_mounts_and_control_paths_are_narrow():
    provider_mounts = {
        "paper-execution": ["alpaca_setup:/run/provider/alpaca:ro"],
        "paper-worker": ["jev_setup:/run/provider/jev:ro"],
        "paper-api": [],
        "paper-watchdog": [],
    }
    for role in [*PAPER_ROLES, "paper-execution-egress", "paper-worker-egress"]:
        service = SERVICES[role]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert not service.get("privileged")
        assert not service.get("cap_add")
        assert not service.get("devices")
        assert not service.get("pid")
        assert not service.get("network_mode")
        assert not service.get("ports")
        assert service.get("volumes", []) == provider_mounts.get(role, [])
        assert not any("docker.sock" in value for value in service.get("volumes", []))
        if role in PAPER_ROLES:
            assert "/run/control:uid=10001,gid=10001,mode=0700" in service["tmpfs"]
        else:
            assert service.get("secrets", []) == []
            assert service["configs"] == []


def test_role_network_graph_has_no_foreign_database_or_direct_internet_path():
    for role, (database_network, _secret) in PAPER_ROLES.items():
        members = {
            name
            for name, service in SERVICES.items()
            if database_network in service.get("networks", [])
        }
        assert members == {role, "postgres"}
    expected = {
        "paper_control": {"control-api", "paper-api"},
        "paper_market": {"paper-execution", "paper-worker"},
        "paper_execution_egress": {"paper-execution", "paper-execution-egress"},
        "paper_worker_egress": {"paper-worker", "paper-worker-egress"},
        "paper_execution_outbound": {"paper-execution-egress"},
        "paper_worker_outbound": {"paper-worker-egress"},
    }
    for network, roles in expected.items():
        assert {
            name for name, service in SERVICES.items() if network in service.get("networks", [])
        } == roles
    for role in ("paper-execution", "paper-worker"):
        proxy = role + "-egress"
        assert SERVICES[role]["environment"] == {
            "HTTPS_PROXY": "http://" + proxy + ":8080",
            "HTTP_PROXY": "http://" + proxy + ":8080",
            "NO_PROXY": "postgres,127.0.0.1,localhost",
        }
        assert SERVICES[proxy]["command"] == ["python", "-m", "services.runtime.egress", role[6:]]


def test_existing_lab_demo_and_provider_storage_are_preserved():
    assert COMPOSE["volumes"] == {"postgres_data": None, "alpaca_setup": None, "jev_setup": None}
    assert SERVICES["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql"]
    assert SERVICES["execution"]["command"][-1] == "execution"
    assert SERVICES["worker"]["command"][-1] == "worker"
    assert SERVICES["demo"]["secrets"] == [{"source": "demo_database", "target": "demo_database"}]
    assert SERVICES["provider-setup"]["networks"] == ["setup_control"]
    for role in ("execution", "worker", "watchdog", "demo", "provider-setup"):
        assert not any(network.startswith("paper_") for network in SERVICES[role]["networks"])


def command(args, *, value=None):
    result = subprocess.run(
        args, cwd=ROOT, input=value, text=True, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, "redacted paper deployment probe failed"
    return result.stdout.strip()


def container(role):
    identity = command(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "--profile", "lab", "ps", "-q", role]
    )
    assert identity, "paper service missing"
    return identity


@pytest.mark.skipif(os.environ.get("PAPER_DOCKER") != "1", reason="requires dormant deployment")
def test_live_paper_metadata_and_local_connectivity_boundaries():
    # Deliberately never inspect Config.Env or read mounted credential files.
    probe = """
import os,socket,sys
role=sys.argv[1]
def reaches(host,port):
    try:
        with socket.create_connection((host,port),timeout=.3): return True
    except OSError: return False
assert os.getuid()==10001
assert reaches('postgres',5432)
assert not reaches('provider-setup',8011)
assert not reaches('demo',8010)
assert reaches('control-api',8000)==(role=='paper-api')
for proxy in ('paper-execution-egress','paper-worker-egress'):
    expected=role in ('paper-execution','paper-worker') and proxy==role+'-egress'
    assert reaches(proxy,8080)==expected
    if expected:
        with socket.create_connection((proxy,8080),timeout=1) as client:
            client.sendall(b'CONNECT localhost:443 HTTP/1.1\\r\\nHost: localhost:443\\r\\n\\r\\n')
            assert client.recv(128).startswith(b'HTTP/1.1 403')
print('PASS')
"""
    for role in [*PAPER_ROLES, "paper-execution-egress", "paper-worker-egress"]:
        identity = container(role)
        assert (
            command(["docker", "inspect", "--format", "{{.Config.User}}", identity])
            == "10001:10001"
        )
        host = json.loads(
            command(["docker", "inspect", "--format", "{{json .HostConfig}}", identity])
        )
        mounts = json.loads(
            command(["docker", "inspect", "--format", "{{json .Mounts}}", identity])
        )
        assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"]
        assert not host.get("Privileged")
        assert not host.get("PortBindings")
        networks = json.loads(
            command(
                ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", identity]
            )
        )
        assert set(networks) == {
            COMPOSE["name"] + "_" + name for name in SERVICES[role]["networks"]
        }
        assert "no-new-privileges:true" in host["SecurityOpt"]
        assert not any("docker.sock" in mount["Destination"] for mount in mounts)
        providers = {
            mount["Destination"]
            for mount in mounts
            if mount["Destination"].startswith("/run/provider/")
        }
        assert providers == (
            {"/run/provider/alpaca"}
            if role == "paper-execution"
            else {"/run/provider/jev"}
            if role == "paper-worker"
            else set()
        )
        assert all(
            not mount["RW"] for mount in mounts if mount["Destination"].startswith("/run/provider/")
        )
        if role in PAPER_ROLES:
            assert (
                command(["docker", "exec", "-i", identity, "python", "-c", probe, role]) == "PASS"
            )


def test_paper_hba_rules_restrict_fixed_logins_before_existing_defaults():
    lines = [
        line.split()
        for line in (ROOT / "infra/compose/pg_hba.conf").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    logins = {
        "atl_paper_execution_login",
        "atl_paper_worker_login",
        "atl_paper_control_api_login",
        "atl_paper_watchdog_login",
    }
    assert len(lines) == 13
    for rule in lines[:6]:
        assert set(rule[2].split(",")) == logins
    assert [rule[1] for rule in lines[:6]] == [
        "lab_paper_runtime",
        "lab_paper_runtime",
        "all",
        "all",
        "replication",
        "replication",
    ]
    assert [rule[-1] for rule in lines[:6]] == [
        "scram-sha-256",
        "scram-sha-256",
        "reject",
        "reject",
        "reject",
        "reject",
    ]
    assert lines[6:] == [
        ["local", "all", "all", "trust"],
        ["host", "all", "all", "127.0.0.1/32", "trust"],
        ["host", "all", "all", "::1/128", "trust"],
        ["local", "replication", "all", "trust"],
        ["host", "replication", "all", "127.0.0.1/32", "trust"],
        ["host", "replication", "all", "::1/128", "trust"],
        ["host", "all", "all", "all", "scram-sha-256"],
    ]
    assert SERVICES["postgres"]["command"] == [
        "postgres",
        "-c",
        "hba_file=/etc/postgresql/pg_hba.conf",
    ]


@pytest.mark.skipif(os.environ.get("PAPER_DOCKER") != "1", reason="requires dormant deployment")
def test_actual_hba_refuses_paper_logins_on_every_existing_database_without_credentials():
    # Authentication denial is checked before any password exchange. No private files are read.
    postgres = container("postgres")
    for login in (
        "atl_paper_execution_login",
        "atl_paper_worker_login",
        "atl_paper_control_api_login",
        "atl_paper_watchdog_login",
    ):
        for database in ("lab", "postgres", "template1", "lab_runtime"):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "postgres",
                    postgres,
                    "psql",
                    "-X",
                    "-w",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    login,
                    "-d",
                    database,
                    "-c",
                    "SELECT 1",
                ],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert result.returncode != 0
            assert "pg_hba.conf rejects connection" in result.stderr
