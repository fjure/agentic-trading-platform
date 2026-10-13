"""Verify build entrypoint cannot activate trading or accept external configuration."""

from services.bootstrap import main


def test_safe_defaults(monkeypatch, capsys):
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.delenv("BROKER_PROVIDER", raising=False)
    assert main() == 0
    assert '"mode": "DISABLED"' in capsys.readouterr().out


def test_non_disabled_mode_rejected(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    assert main() == 2


def test_external_provider_rejected(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    assert main() == 2


def test_external_config_rejected_without_disclosure(monkeypatch, capsys):
    monkeypatch.setenv("JEV_API_KEY", "synthetic-test-only")
    assert main() == 2
    output = capsys.readouterr()
    assert "synthetic-test-only" not in output.out + output.err
