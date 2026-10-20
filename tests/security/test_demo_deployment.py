"""Opt-in live deployment metadata checks; no credential contents or environments."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", "infra/compose/compose.yaml", "--profile", "lab"]
pytestmark = pytest.mark.skipif(
    os.environ.get("DEMO_DOCKER") != "1", reason="requires deployed local demo services"
)


def command(args, *, value=None):
    result = subprocess.run(
        args, cwd=ROOT, input=value, text=True, capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, "redacted deployment probe failed"
    return result.stdout.strip()


def container(role):
    value = command([*COMPOSE, "ps", "-q", role])
    assert value
    return value


def test_only_expected_roles_receive_private_provider_volumes():
    expected = {
        "execution": {"/run/provider/alpaca"},
        "worker": {"/run/provider/jev"},
        "provider-setup": {"/run/provider/alpaca", "/run/provider/jev"},
        "control-api": set(),
        "watchdog": set(),
        "demo": set(),
    }
    for role, paths in expected.items():
        identity = container(role)
        # Deliberately project only these fields; never inspect Config.Env.
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
        assert "no-new-privileges:true" in host["SecurityOpt"]
        provider_mounts = {
            m["Destination"]: m for m in mounts if m["Destination"].startswith("/run/provider/")
        }
        assert set(provider_mounts) == paths
        assert all(m["RW"] is (role == "provider-setup") for m in provider_mounts.values())
        assert not any("docker.sock" in m["Destination"] for m in mounts)


def test_live_internal_service_and_filesystem_boundaries():
    probe = """
import json,os,pathlib,socket,stat,sys
role=sys.argv[1]
def reaches(host,port):
    try:
        with socket.create_connection((host,port),timeout=.3): return True
    except OSError: return False
assert os.getuid()==10001
assert reaches('provider-setup',8011)==(role in ('control-api','provider-setup'))
assert reaches('demo',8010)==(role in ('control-api','demo'))
assert reaches('postgres',5432)==(role!='provider-setup')
if role=='provider-setup':
    for name in ('alpaca','jev'):
        directory=pathlib.Path('/run/provider')/name
        info=directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid==10001
        assert stat.S_IMODE(info.st_mode)==0o700
        target=directory/'credentials.json'
        if target.exists():
            info=target.lstat()
            assert stat.S_ISREG(info.st_mode) and info.st_uid==10001
            assert stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1
print('PASS')
"""
    for role in ("execution", "worker", "control-api", "watchdog", "demo", "provider-setup"):
        assert (
            command(["docker", "exec", "-i", container(role), "python", "-c", probe, role])
            == "PASS"
        )


def test_demo_database_login_cannot_connect_to_normal_ledger():
    sql = """SELECT NOT has_database_privilege('atl_demo_provisioner_login',
        'lab_runtime','CONNECT') AND NOT rolsuper AND rolcreatedb AND NOT rolcreaterole
        AND NOT rolinherit AND NOT rolreplication AND NOT rolbypassrls
        FROM pg_roles WHERE rolname='atl_demo_provisioner_login';"""
    result = command(
        [
            *COMPOSE,
            "exec",
            "-T",
            "--user",
            "postgres",
            "postgres",
            "psql",
            "-X",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "lab_migration",
            "-d",
            "lab",
        ],
        value=sql,
    )
    assert result == "t"
