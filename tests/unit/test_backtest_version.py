"""The manifest versions actual built bytes, independent of dirty Git metadata."""

import pytest

from services.runtime.backtesting import source_fingerprint
from services.worker.backtesting import BacktestWorker


def test_version_changes_with_copied_code_or_dependency_lock(tmp_path):
    for directory in ("packages", "services", "config"):
        (tmp_path / directory).mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='synthetic'\n")
    (tmp_path / "uv.lock").write_text("version=1\n")
    source = tmp_path / "packages/simulator.py"
    source.write_text("MODEL=1\n")
    original = source_fingerprint(tmp_path)
    assert len(original) == 40
    assert source_fingerprint(tmp_path) == original
    source.write_text("MODEL=2\n")
    changed = source_fingerprint(tmp_path)
    assert changed != original
    (tmp_path / "uv.lock").write_text("version=2\n")
    assert source_fingerprint(tmp_path) not in {original, changed}


def test_enabled_worker_requires_its_actual_version_and_disabled_never_opens_sql():
    class NoDatabase:
        def begin(self):
            raise AssertionError("disabled dispatcher opened SQL")

    assert BacktestWorker(NoDatabase()).tick() is False
    with pytest.raises(ValueError):
        BacktestWorker(NoDatabase(), enabled=True)
