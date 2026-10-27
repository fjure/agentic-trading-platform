"""Actual constrained worker container, internal network and fixed PostgreSQL identity."""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

MARKER = "PASS backtest actual container/CPU/memory/mounts/internal-network/SQL-role/no-provider"


def isolated_worker_host(module="tests.security.test_backtest_isolation", marker=MARKER):
    from tests.integration.test_paper_provision import POSTGRES

    root = Path(__file__).resolve().parents[2]
    tag = "atl-backtest-isolation-" + uuid4().hex[:16]
    admin_net, worker_net, postgres, worker = (
        tag + s for s in ("-admin", "-worker", "-pg", "-app")
    )
    created = []

    def docker(*args, input_text=None):
        result = subprocess.run(
            ["docker", *args], input=input_text, capture_output=True, text=True, timeout=120
        )
        if result.returncode:
            marker = next(
                (
                    line
                    for line in result.stdout.splitlines()
                    if line.startswith("FAIL backtest isolation ")
                ),
                None,
            )
            raise RuntimeError(marker or "isolated Backtest container check failed")
        return result.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="atl-backtest-isolation-") as directory:
        try:
            for network in (admin_net, worker_net):
                docker("network", "create", "--internal", network)
                created.append(("network", network))
            networks = [
                json.loads(docker("network", "inspect", name))[0]
                for name in (admin_net, worker_net)
            ]
            assert all(n["Internal"] for n in networks)
            admin_cidr, worker_cidr = (n["IPAM"]["Config"][0]["Subnet"] for n in networks)
            hba = Path(directory) / "pg_hba.conf"
            hba.write_text(
                "local all all trust\n"
                f"host all lab_migration {admin_cidr} trust\n"
                f"host lab atl_backtest_worker_login {worker_cidr} trust\n"
                "host all all 0.0.0.0/0 reject\n"
                "host all all ::0/0 reject\n",
                encoding="ascii",
            )
            docker(
                "run",
                "--detach",
                "--pull=never",
                "--name",
                postgres,
                "--network",
                admin_net,
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
                "--mount",
                f"type=bind,source={hba},target=/etc/postgresql/test_hba.conf,readonly",
                "--env",
                "POSTGRES_USER=lab_migration",
                "--env",
                "POSTGRES_DB=lab",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                POSTGRES,
                "postgres",
                "-c",
                "hba_file=/etc/postgresql/test_hba.conf",
            )
            created.append(("container", postgres))
            docker("network", "connect", "--alias", "postgres", worker_net, postgres)
            for _ in range(60):
                ready = subprocess.run(
                    ["docker", "exec", postgres, "pg_isready", "-U", "lab_migration", "-d", "lab"],
                    capture_output=True,
                    timeout=5,
                )
                if ready.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("isolated PostgreSQL did not become ready")
            # Test-only source mounts; no application/private directory, Docker
            # socket, provider credential, or host network enters the worker.
            mounts = []
            for source in ("packages", "services", "tests", "contracts", "alembic.ini"):
                mounts += [
                    "--mount",
                    f"type=bind,source={root / source},target=/opt/lab/{source},readonly",
                ]
            common = [
                "--pull=never",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp",
                "--workdir",
                "/opt/lab",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                *mounts,
                "--entrypoint",
                "python",
            ]
            image = "agentic-trading-lab-research-test:local"
            assert (
                docker("run", "--rm", "--network", admin_net, *common, image, "-m", module, "seed")
                == "SEEDED"
            )
            created.append(("container", worker))
            result = docker(
                "run",
                "--name",
                worker,
                "--network",
                worker_net,
                "--user",
                "10001:10001",
                "--cpus",
                "0.5",
                "--memory",
                "256m",
                *common,
                image,
                "-m",
                module,
                "probe",
            )
            assert result == marker
            actual = json.loads(docker("inspect", worker))[0]
            assert list(actual["NetworkSettings"]["Networks"]) == [worker_net]
            assert actual["HostConfig"]["ReadonlyRootfs"] is True
            assert actual["HostConfig"]["NanoCpus"] == 500000000
            assert actual["HostConfig"]["Memory"] == 256 * 1024 * 1024
            assert actual["HostConfig"]["CapDrop"] == ["ALL"]
            assert actual["Config"]["User"] == "10001:10001"
            binds = [mount for mount in actual["Mounts"] if mount["Type"] == "bind"]
            assert all(not mount.get("RW") for mount in binds)
            assert {mount["Destination"] for mount in binds} == {
                "/opt/lab/" + name
                for name in ("packages", "services", "tests", "contracts", "alembic.ini")
            }
            return result
        finally:
            for kind, name in reversed(created):
                subprocess.run(
                    ["docker", "rm", "--force", name]
                    if kind == "container"
                    else ["docker", "network", "rm", name],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )


def seed():
    from datetime import UTC, datetime, timedelta

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, create_engine, text

    from packages.backtesting.jobs import BacktestJobs
    from packages.domain.models import utc_string
    from tests.integration.backtest_browser_support import seed_backtest_fixture

    engine = create_engine(
        URL.create("postgresql+psycopg", username="lab_migration", host="postgres", database="lab"),
        hide_parameters=True,
    )
    config = Config("alembic.ini")
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        conn.execute(text("CREATE ROLE atl_backtest_worker_login LOGIN NOINHERIT"))
        conn.execute(text("GRANT atl_backtest_worker TO atl_backtest_worker_login"))
    evidence = seed_backtest_fixture(engine)
    start = datetime.fromisoformat(evidence["start"].replace("Z", "+00:00"))
    request = dict(
        instrumentId=evidence["instrument"],
        strategyId="buy-hold-v1",
        version="1",
        parameters={},
        startCash="10000",
        feeUsd="0",
        slippageBps="0",
        quantityStep="0.000001",
        folds=[
            dict(
                trainingStart=utc_string(start - timedelta(days=1)),
                trainingEnd=evidence["start"],
                testStart=evidence["start"],
                testEnd=evidence["end"],
            )
        ],
    )
    from packages.backtesting.request import parse_request

    parse_request(request, datetime.now(UTC))
    BacktestJobs(engine).enqueue(
        request, "local-operator", "backtest-container-isolation-001", "a" * 32, "b" * 40
    )
    engine.dispose()
    print("SEEDED")


def probe():
    import os
    from unittest.mock import patch

    from sqlalchemy import URL, create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from services.worker.backtesting import BacktestWorker

    assert os.getuid() == 10001
    assert not Path("/run/secrets").exists()
    assert not Path("/run/provision").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert not any(key.startswith(("ALPACA", "JEV", "BROKER")) for key in os.environ)
    assert int(Path("/sys/fs/cgroup/memory.max").read_text()) <= 256 * 1024 * 1024
    quota, period = map(int, Path("/sys/fs/cgroup/cpu.max").read_text().split())
    assert quota / period <= 0.5
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username="atl_backtest_worker_login",
            host="postgres",
            database="lab",
        ),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )

    @event.listens_for(engine, "connect")
    def restrict(dbapi, _record):
        dbapi.execute("SET ROLE atl_backtest_worker")
        dbapi.commit()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == "atl_backtest_worker"
    with patch("httpx.Client.send", side_effect=AssertionError("provider HTTP forbidden")) as http:
        assert BacktestWorker(engine, enabled=True, code_version="b" * 40).tick()
        assert http.call_count == 0
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM backtest.runs WHERE status='SUCCEEDED'")
            ).scalar_one()
            == 1
        )
        assert conn.execute(text("SELECT count(*) FROM backtest.results")).scalar_one() == 1
    for sql in (
        "SELECT count(*) FROM public.audit_events",
        "DELETE FROM backtest.corporate_action_coverage",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except DBAPIError:
            pass
        else:
            raise AssertionError("worker SQL role escaped")
    # HBA rejects an admin identity from the actual worker-only network.
    forbidden = create_engine(
        URL.create("postgresql+psycopg", username="lab_migration", host="postgres", database="lab"),
        hide_parameters=True,
        connect_args={"connect_timeout": 1},
    )
    try:
        with forbidden.connect():
            raise AssertionError("worker network reached migration identity")
    except DBAPIError:
        pass
    finally:
        forbidden.dispose()
        engine.dispose()
    print(MARKER)


def test_actual_backtest_container_isolation():
    assert isolated_worker_host() == MARKER


if __name__ == "__main__":
    try:
        {"seed": seed, "probe": probe}[sys.argv[1]]()
    except Exception as error:
        import traceback

        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            "FAIL backtest isolation "
            + type(error).__name__
            + " "
            + Path(frame.filename).name
            + ":"
            + str(frame.lineno)
        )
        raise SystemExit(1) from None
