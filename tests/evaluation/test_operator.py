"""Exercise the real operator entry point without opening an unverified holdout."""

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.evaluation import operator
from tests.evaluation.harness import Approval, RunBinding


def test_operator_checks_expiry_and_custody_before_reading_bound_inputs(tmp_path, monkeypatch):
    private = tmp_path / "private"
    inputs = tmp_path / "inputs"
    events = []
    now = datetime.now(UTC)
    binding = RunBinding("profile", "model", "v1", *["a" * 64] * 6)
    monkeypatch.setattr(
        sys, "argv",
        ["operator", "register", "--private-dir", str(private), "--inputs", str(inputs)],
    )
    monkeypatch.setattr(operator, "OperatorStore", lambda _: None)
    monkeypatch.setattr(operator, "_owner_file", lambda _: tmp_path / "synthetic-key.pub")
    (tmp_path / "synthetic-key.pub").write_bytes(bytes(range(32)))
    monkeypatch.setattr(operator, "_read_json", lambda _: {"holdoutSha256": "b" * 64})

    def verify(_binding, files):
        assert files["holdout"] == inputs / "holdout.v1.json"
        events.append("read-bound-inputs")
        raise ValueError("EVALUATION_BINDING_MISMATCH")

    def set_approval(expires_at):
        approval = Approval("approval", binding, expires_at, 10, Decimal(10), Decimal(1))
        monkeypatch.setattr(operator, "_approval", lambda _: approval)

    def unverified(*_, **__):
        events.append("verify-custody")
        raise ValueError("EVALUATION_CUSTODY_UNVERIFIED")

    monkeypatch.setattr(RunBinding, "verify", verify)
    monkeypatch.setattr(operator, "verify_custody", unverified)
    set_approval(now - timedelta(minutes=1))
    with pytest.raises(ValueError, match="EVALUATION_AUTHORIZATION_REQUIRED"):
        operator.main()
    assert events == []

    set_approval(now + timedelta(minutes=1))
    with pytest.raises(ValueError, match="EVALUATION_CUSTODY_UNVERIFIED"):
        operator.main()
    assert events == ["verify-custody"]

    monkeypatch.setattr(operator, "verify_custody", lambda *_, **__: events.append("verified"))
    with pytest.raises(ValueError, match="EVALUATION_BINDING_MISMATCH"):
        operator.main()
    assert events == ["verify-custody", "verified", "read-bound-inputs"]
