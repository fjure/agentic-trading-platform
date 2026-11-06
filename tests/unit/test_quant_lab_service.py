from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from packages.backtesting.dataset import Snapshot
from packages.backtesting.experiment_storage import _validator
from packages.backtesting.jobs import BacktestError
from packages.backtesting.models import BacktestBlocked
from packages.domain.models import utc_string
from services.control_api import quant_lab_service as service

NOW = datetime(2026, 1, 1, tzinfo=UTC)
KEY = b"s" * 32


def test_catalog_is_closed_and_contains_all_pinned_execution_versions():
    payload = service.strategy_catalog()
    _validator("QuantLabStrategyCatalog").validate(payload)
    assert {item["strategyId"] for item in payload["items"]} == {
        "flat-v1",
        "buy-hold-v1",
        "sma-cross-v1",
        "ema-cross-v1",
        "rsi-mean-reversion-v1",
    }
    assert all(item["fittingStatus"] == "NOT_TRAINABLE" for item in payload["items"])


def test_cursor_rejects_changed_scope_filter_tampering_and_expiry():
    lab = service.QuantLabService(None, code_version="0" * 40, cursor_key=KEY)
    query = {"phase": "test", "fold": 0, "limit": 20}
    digest, _, _ = lab._cursor("results", "session-1", query, batch="batch")
    token = lab._next(digest, "published", 20)
    assert lab._cursor("results", "session-1", query | {"cursor": token}, batch="batch")[2] == {
        "binding": digest,
        "snapshot": "published",
        "position": 20,
    }
    expired = lab._sign(
        dict(
            binding=digest, snapshot=dict(expiresAt=utc_string(NOW), value="published"), position=20
        )
    )
    for session, options, cursor in (
        ("session-2", query, token),
        ("session-1", query | {"fold": 1}, token),
        ("session-1", query, "A" * len(token)),
        ("session-1", query, expired),
    ):
        with pytest.raises(BacktestError) as raised:
            lab._cursor("results", session, options | {"cursor": cursor}, batch="batch")
        assert (raised.value.status, raised.value.code) == (400, "CURSOR_INVALID")


def test_preflight_counts_every_phase_seed_candidate_and_reference(monkeypatch):
    snapshot = Snapshot(1, NOW + timedelta(days=70))
    domain = SimpleNamespace(
        queue_slots=11,
        warmup_count=49,
        protocol=SimpleNamespace(holdout=SimpleNamespace(end=NOW + timedelta(days=69))),
    )
    starts = {
        ("training", 0): range(130),
        ("validation", 0): range(546),
        ("test", 0): range(104),
        ("holdout", None): range(130),
    }
    monkeypatch.setattr(service, "_preflight_calendar", lambda *_: (None,) * 50)
    monkeypatch.setattr(service, "_calendar_plan", lambda *_: (starts, NOW))
    monkeypatch.setattr(service, "_revision_count", lambda *_: (1150, 80))
    budget, returned = service.estimate_budget(None, {"fingerprint": "a" * 64}, domain, snapshot)
    assert returned == starts
    assert budget["simulationBarSteps"] == 9510
    assert budget["snapshotRows"] == 1150
    assert budget["calendarVerified"] and not budget["reasons"]
    assert budget["publicationBytes"] == 12_300_288 < 12 * 1024 * 1024
    monkeypatch.setattr(service, "_revision_count", lambda *_: (1500, 500))
    denied, _ = service.estimate_budget(None, {"fingerprint": "a" * 64}, domain, snapshot)
    assert denied["publicationBytes"] is None and denied["reasons"] == ["BUDGET_EXCEEDED"]


def test_no_calendar_cannot_generate_positive_admission(monkeypatch):
    domain = SimpleNamespace(warmup_count=0, research_mode="STRICT_PIT")
    monkeypatch.setattr(service, "_preflight_calendar", lambda *_: ())

    def missing(*_):
        raise BacktestBlocked("MISSING_CALENDAR")

    monkeypatch.setattr(service, "_calendar_plan", missing)
    budget, starts = service.estimate_budget(
        None, {"fingerprint": "a" * 64}, domain, Snapshot(1, NOW)
    )
    assert starts == {} and not budget["calendarVerified"]
    assert budget["simulationBarSteps"] is None and budget["reasons"] == ["MISSING_CALENDAR"]


def test_managed_retro_calendar_uses_content_without_waiving_strict_temporal_gate(monkeypatch):
    source = UUID(int=1)
    calendar = {"evidence_id": source, "managed_trust_state": "TIME_UNPROVEN"}
    dimensions = dict.fromkeys(
        (
            "integrity",
            "sourceAuthenticity",
            "temporalAuthenticity",
            "completeness",
            "domainQuality",
        ),
        {"status": "VERIFIED"},
    )
    dimensions["temporalAuthenticity"] = {"status": "UNKNOWN"}
    state = {
        "version": 1,
        "policy_version": "test",
        "records": [{"reference": str(source), "revoked": False, "dimensions": dimensions}],
    }
    monkeypatch.setattr(service, "read_schedule_rows", lambda *_: (calendar,))
    monkeypatch.setattr(service, "read_trust_snapshot", lambda *_: state)
    strict = SimpleNamespace(research_mode="STRICT_PIT")
    retro = SimpleNamespace(research_mode="RETROSPECTIVE_EXPLORATORY")
    snapshot = Snapshot(1, NOW)
    assert (
        service._preflight_calendar(None, strict, snapshot)[0]["managed_trust_state"]
        == "TIME_UNPROVEN"
    )
    assert service._preflight_calendar(None, retro, snapshot)[0]["managed_trust_state"] is None
    state["records"][0]["revoked"] = True
    with pytest.raises(BacktestBlocked, match="MISSING_CALENDAR"):
        service._preflight_calendar(None, retro, snapshot)


def test_history_schedule_round_trips_session_gaps_without_materializing_raw_bars():
    opens = NOW + timedelta(hours=14, minutes=30)
    starts = {
        ("validation", 0): (opens, opens + timedelta(minutes=15), opens + timedelta(days=1)),
        ("holdout", None): (opens + timedelta(days=2),),
    }
    encoded = service._encode_schedule(starts)
    assert encoded[0]["runs"] == [
        [utc_string(opens), 2],
        [utc_string(opens + timedelta(days=1)), 1],
    ]
    assert service._decode_schedule(encoded) == starts


@pytest.mark.parametrize("select_approval", [False, True])
def test_upgrade_approval_detail_waits_for_complete_replay_body(monkeypatch, select_approval):
    lab = service.QuantLabService(None, code_version="0" * 40, cursor_key=KEY)
    definition_id, approval_id = UUID(int=1), UUID(int=2)
    definition = {
        "definition_id": definition_id,
        "definition_version": 1,
        "fingerprint": "a" * 64,
        "created_at": NOW,
        "body": {"researchMode": "STRICT_PIT"},
        "policy_hash": "b" * 64,
        "candidate_count": 1,
    }
    history = {"registryHash": "c" * 64, "boundAt": utc_string(NOW)}
    saved = {
        "validatedAt": utc_string(NOW),
        "reasons": [],
        "researchHistory": history,
        "registryWatermark": 42,
    }
    history_arguments = []

    def history_projection(*_, **options):
        history_arguments.append(options)
        return options["saved"] or {"preview": True}

    monkeypatch.setattr(lab, "_history", history_projection)

    class Connection:
        def __init__(self, replay):
            self.replay = replay

        def execute(self, statement, params):
            if "experiment_validations" in statement.text:
                row = {"validation_id": approval_id, "body": {"simulationBarSteps": 10}}
            else:
                assert "ORDER BY validated_at,key_hash" in statement.text
                assert params == {"definition": definition_id, "approval": approval_id}
                row = self.replay
            return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: row))

    query = {"validationId": str(approval_id)} if select_approval else {}
    draft = lab._definition_wire(definition, query, scope="session", connection=Connection(None))
    assert draft["state"] == "DRAFT" and draft["researchHistory"] == {"preview": True}
    assert history_arguments[-1]["receipt"] is None
    approved = lab._definition_wire(
        definition, query, scope="session", connection=Connection({"body": saved})
    )
    assert approved["state"] == "VALIDATED" and approved["researchHistory"] == history
    assert history_arguments[-1]["receipt"]["mark"] == 42
