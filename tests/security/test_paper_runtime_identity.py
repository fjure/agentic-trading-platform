"""APW-02 fixed identities and provider-free inactive startup."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from services.runtime import config, paper


def paper_config(**changes):
    return config.RuntimeConfig(**dict(
        deployment="paper", broker_provider="alpaca-paper", **changes,
    ))


@pytest.mark.parametrize("changes", [
    {"mode": "PAPER"}, {"paper_enabled": True}, {"decision_provider": "jev"},
    {"reconciliation_fee_profile": "alpaca-paper-us-retail-equity-zero-v1"},
])
def test_paper_deployment_cannot_activate_through_static_configuration(changes):
    with pytest.raises(ValueError):
        paper_config(**changes)
    assert paper_config().mode == "DISABLED"
    assert config.RuntimeConfig().deployment == "lab"
    with pytest.raises(ValueError):
        config.RuntimeConfig(deployment="lab_paper_runtime")


@pytest.mark.parametrize("role", config.PAPER_ROLES)
def test_engine_binds_exact_database_login_and_set_role(tmp_path, role):
    group = config.PAPER_ROLES[role]
    path = tmp_path / "database"
    path.write_text(json.dumps({"username": group + "_login", "password": "synthetic-only"}))
    engine = config.database_engine(role, path, deployment="paper")
    assert engine.url.host == "postgres" and engine.url.database == "lab_paper_runtime"
    assert engine.url.username == group + "_login"
    statements = []

    class Connection:
        committed = False
        rolled_back = False
        identity = (group, "lab_paper_runtime", group + "_login")

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, statement):
            statements.append(statement)

        def fetchone(self):
            return self.identity

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

    connection = Connection()
    # Invoke only our listener; SQLAlchemy's dialect listener needs a real socket.
    listener = list(engine.pool.dispatch.connect)[-1]
    assert event.contains(engine, "connect", listener)
    listener(connection, None)
    assert connection.committed
    assert statements[0] == "SET ROLE " + group
    connection.identity = (group, "lab_runtime", group + "_login")
    with pytest.raises(PermissionError, match="DATABASE_ROLE_UNAVAILABLE"):
        listener(connection, None)
    assert connection.rolled_back
    engine.dispose()


@pytest.mark.parametrize("login,deployment", [
    ("atl_execution_login", "paper"), ("atl_paper_worker_login", "paper"),
    ("atl_paper_execution_login", "lab"),
])
def test_swapped_role_or_deployment_secret_is_rejected(tmp_path, login, deployment):
    path = tmp_path / "database"
    path.write_text(json.dumps({"username": login, "password": "synthetic-only"}))
    with pytest.raises(ValueError, match="INVALID_DATABASE_IDENTITY"):
        config.database_engine("execution", path, deployment=deployment)


@pytest.mark.parametrize("role", config.PAPER_ROLES)
def test_dormant_role_ticks_without_loading_providers_or_finance(tmp_path, monkeypatch, role):
    loaded = set(sys.modules)
    monkeypatch.setattr(config.RuntimeConfig, "load", lambda: paper_config())
    statuses = []
    monkeypatch.setattr(paper, "write_status", lambda *value: statuses.append(value))
    monkeypatch.setattr(paper.signal, "signal", lambda *_: None)
    engine = SimpleNamespace(dispose=lambda: None)
    monkeypatch.setattr(paper, "database_engine", lambda *_, **__: engine)
    monkeypatch.setattr(paper, "check_identity", lambda *_: "READY")
    if role == "api":
        from services.runtime import paper_api

        monkeypatch.setattr(paper_api, "run_private_paper_api",
                            lambda _: statuses.append((role, "READY")))
    elif role == "execution":
        from services.runtime import paper_verification

        monkeypatch.setattr(paper_verification, "PaperVerificationRuntime",
                            lambda _: SimpleNamespace(tick=lambda: "READY"))

    class Once:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, _):
            self.set()

    monkeypatch.setattr(paper, "Event", Once)
    paper.run(role)
    assert statuses == [(role, "STARTING"), (role, "READY")]
    assert not any(name.startswith((
        "packages.brokers.", "services.runtime.execution", "services.runtime.worker",
    )) for name in set(sys.modules) - loaded)
    assert not list(tmp_path.iterdir())


def test_no_provider_paths_in_dormant_process():
    source = Path(paper.__file__).read_text()
    assert "secret_fields" not in source and "/run/provider" not in source
