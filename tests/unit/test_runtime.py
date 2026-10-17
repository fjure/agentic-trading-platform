"""Offline runtime boundary tests. No broker endpoint or real credential is used."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.runtime.api import activate, create_bridge, static_path
from services.runtime.config import RuntimeConfig, read_json, secret_fields


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "LIVE"},
        {"broker_provider": "alpaca"},
        {"paper_enabled": "true"},
        {"paper_enabled": 1},
        {"mode": "PAPER"},
        {"broker_provider": "alpaca-paper"},
        {"instruments": ("SPY", "BTC/USD")},
        {"instruments": ("SPY", "SPY")},
        {"account_id": "external-account"},
        {"origin": "http://0.0.0.0:8000"},
        {"origin": "http://localhost:8000"},
        {"decision_provider": "remote"},
    ],
)
def test_configuration_fail_closed(value):
    with pytest.raises((TypeError, ValueError)):
        RuntimeConfig(**value)


def test_config_is_immutable_and_does_not_read_ambient_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca-paper")
    path = tmp_path / "runtime.json"
    path.write_text("{}")
    config = RuntimeConfig.load(path)
    assert config.mode == "DISABLED"
    assert config.broker_provider == "fake"
    with pytest.raises(AttributeError):
        config.mode = "PAPER"
    path.write_text('{"password":"synthetic-canary"}')
    with pytest.raises(ValueError, match="^INVALID_CONFIG$"):
        RuntimeConfig.load(path)


@pytest.mark.parametrize(
    "raw", ['{"mode":"DISABLED","mode":"PAPER"}', '{"x":NaN}', "[1]", '{"password":']
)
def test_invalid_config_never_echoes_contents(tmp_path, raw):
    path = tmp_path / "private"
    path.write_text(raw)
    with pytest.raises(ValueError, match="^INVALID_CONFIG$"):
        read_json(path)


def test_private_file_fixed_fields_and_no_control_characters(tmp_path):
    path = tmp_path / "private"
    path.write_text(json.dumps({"username": "atl_worker_login", "password": "fake\ncanary"}))
    with pytest.raises(ValueError, match="^INVALID_PRIVATE_CONFIGURATION$"):
        secret_fields(path, {"username", "password"})


def test_static_serving_rejects_traversal_and_symlink_escape(tmp_path):
    root = tmp_path / "dashboard"
    root.mkdir()
    (root / "index.html").write_text("<html>offline</html>")
    assert static_path(root, "/some/page") == root / "index.html"
    for value in ["../secret", "/%2e%2e/secret", "..\\secret", "/%00secret"]:
        with pytest.raises(ValueError):
            static_path(root, value)
    with pytest.raises(FileNotFoundError):
        static_path(root, "/missing.js")


def test_bridge_static_same_origin_guard(tmp_path):
    (tmp_path / "index.html").write_text("<html>offline</html>")
    with TestClient(
        create_bridge(RuntimeConfig(), tmp_path), base_url="http://127.0.0.1:8000"
    ) as client:
        assert client.get("/").status_code == 200
        assert client.get("/", headers={"host": "attacker.invalid"}).status_code == 400
        assert client.get("/", headers={"origin": "http://attacker.invalid"}).status_code == 400
        assert client.get("/", headers={"x-forwarded-for": "127.0.0.1"}).status_code == 400
        assert client.post("/").status_code == 405
        assert client.get("/missing.js").status_code == 404


def test_activation_refuses_noninteractive_tool_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(PermissionError, match="INTERACTIVE_OPERATOR_REQUIRED"):
        activate()


def test_runtime_worker_source_has_no_broker_or_execution_imports():
    import ast

    import services.runtime.worker as worker

    parsed = ast.parse(Path(worker.__file__).read_text())
    imports = [node.module for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)]
    assert all(
        "broker" not in (name or "") and "services.execution" not in (name or "")
        for name in imports
    )


def test_missing_quote_never_fabricates_zero_spread():
    from services.execution.intents import SubmitDenied
    from services.runtime.execution import ExecutionRuntime

    with pytest.raises(SubmitDenied, match="MARKET_SPREAD_UNAVAILABLE"):
        ExecutionRuntime._spread(SimpleNamespace(spread=None, quote_provider_time=None))


def test_health_cli_stale_file_is_unhealthy(monkeypatch):
    from services.runtime.__main__ import health

    moment = datetime.now(UTC) - timedelta(seconds=21)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kw: json.dumps({"checked_at": moment.isoformat(), "code": "READY"}),
    )
    assert health() == 1


def test_broker_failure_does_not_skip_recovery_or_outbox():
    from services.runtime.execution import ExecutionRuntime

    runtime = object.__new__(ExecutionRuntime)
    calls = []
    runtime.process = SimpleNamespace(heartbeat=lambda: calls.append("heartbeat"))
    runtime.telemetry = None
    runtime.publish_market = lambda: True
    runtime.cancel_after_halt = lambda: calls.append("halt")

    def fail_reconciliation():
        calls.append("reconcile")
        raise ValueError("synthetic provider outage")

    runtime.reconcile = fail_reconciliation
    runtime.recover_requests = lambda: calls.append("recovery")
    runtime.risk_and_submit = lambda: calls.append("submit")
    runtime.outbox = SimpleNamespace(publish_one=lambda _: calls.append("outbox"))
    assert runtime.tick() == "UNAVAILABLE"
    assert "recovery" in calls and "outbox" in calls and "submit" not in calls


@pytest.mark.skipif(sys.platform == "win32", reason="Linux tmpfs runtime; verified in Docker guest")
def test_concurrent_api_health_status_writes_are_atomic(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    import services.runtime.common as common

    target = tmp_path / "runtime-status.json"
    monkeypatch.setattr(common, "Path", lambda _: target)
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: common.write_status("api", "READY"), range(60)))
    result = json.loads(target.read_text("utf-8"))
    assert result["role"] == "api" and result["code"] == "READY" and result["checked_at"]
