"""Independent retrospective receipt, snapshot, calendar and identity oracles."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import jsonschema
import pytest

from packages.backtesting import retrospective as retro
from packages.backtesting.dataset import DatasetBlocked, FrozenDataset, Snapshot, materialize_fold
from packages.backtesting.models import (
    ExperimentConfig,
    Fold,
    RetrospectivePolicyV1,
    StrictPitPolicyV1,
    fingerprint,
    selection_policy,
)
from packages.research.models import (
    AvailabilityBasis,
    BarIdentity,
    BarInterval,
    BarRevision,
    QualityStatus,
)
from tests.unit.test_backtest_models import config as legacy_config
from tests.unit.test_backtest_models import manifest as legacy_manifest


def fixture(warmup=0):
    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    observed = start + timedelta(days=1)
    snapshot = Snapshot(100, observed + timedelta(hours=1))
    instrument = UUID(int=1)
    bars = tuple(
        BarRevision(
            UUID(int=10 + n),
            BarIdentity(
                instrument,
                "alpaca",
                "iex",
                BarInterval.FIFTEEN_MINUTES,
                start + timedelta(minutes=15 * n),
                start + timedelta(minutes=15 * (n + 1)),
            ),
            *(Decimal("100"),) * 4,
            Decimal("10"),
            observed,
            observed,
            commit_version=n + 1,
        )
        for n in range(6)
    )
    calendar = (
        dict(
            session_id=UUID(int=30),
            session_date=start.date(),
            opens_at=start,
            closes_at=start + timedelta(minutes=90),
            version="a" * 64,
            source="alpaca-v2-calendar",
            commit_version=1,
            ingested_at=observed,
        ),
    )
    test_start = start + timedelta(minutes=15 * warmup)
    fold = Fold(
        start if warmup else start - timedelta(minutes=30),
        test_start,
        test_start,
        start + timedelta(minutes=90),
    )
    config = ExperimentConfig(
        instrument,
        "SPY",
        "sma-cross-v1" if warmup else "buy-hold-v1",
        (("fast", 2), ("slow", warmup + 1)) if warmup else (),
        Decimal("1000"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0.000001"),
        (fold,),
    )
    return config, snapshot, bars, calendar


def materialized(config, snapshot, bars, calendar, **kwargs):
    return retro.materialize_retrospective_fold(
        retro.RetrospectiveDataset(snapshot, bars),
        config.folds[0],
        config.instrument_id,
        dict(config.parameters).get("slow", 1) - 1,
        calendar,
        **kwargs,
    )


def manifest(config, snapshot, dataset, **changes):
    value = retro.RetrospectiveManifest(
        config,
        snapshot,
        (dataset,),
        retro.RetrospectiveTrustContext(snapshot, "b" * 64),
        "c" * 40,
        license_receipt_fingerprint="d" * 64,
    )
    return replace(value, **changes)


def test_backfilled_current_receipts_are_preserved_and_strict_is_still_blocked():
    config, snapshot, bars, calendar = fixture()
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        materialize_fold(
            FrozenDataset(snapshot, bars), config.folds[0], config.instrument_id, 0, calendar
        )
    selected = materialized(config, snapshot, bars, calendar)
    assert selected.outcome_bars == bars
    assert all(a is b for a, b in zip(selected.outcome_bars, bars, strict=True))
    assert selected.steps[0].signal_bar is bars[0]
    assert selected.steps[0].input_bars == (bars[0],)
    assert selected.steps[0].knowledge_at == bars[0].identity.bar_end + timedelta(seconds=60)
    assert selected.steps[0].fill_target == bars[2].identity.bar_start
    assert selected.steps[-1].fill_target is None
    assert bars[0].ingested_at > selected.steps[0].knowledge_at
    assert selected.calendar_references[0].ingested_at == calendar[0]["ingested_at"]
    wire = selected.revision_references[0].to_dict()
    assert wire["ingestedAt"] == "2026-10-20T13:30:00Z"
    assert wire["availableAt"] == wire["ingestedAt"]
    assert wire["firstObservedAt"] is None
    assert wire["publishedAt"] is None


def test_default_policy_and_legacy_hash_preimages_unchanged():
    assert type(selection_policy()) is StrictPitPolicyV1
    assert selection_policy("STRICT_PIT") == selection_policy()
    assert (
        type(selection_policy("RETROSPECTIVE_EXPLORATORY", "retrospective-v1"))
        is RetrospectivePolicyV1
    )
    assert (
        legacy_config().input_hash
        == "82ec4a7121c7bf3ade8aeb031f23ce60f3df28d1306924d35a8d863cd71c6cd6"
    )
    assert (
        legacy_manifest().manifest_hash
        == "8c51a2f3e7868b8c90e0eb86bdf152177dbbbf3c7f05e20f5c69f13d0bd3a330"
    )


@pytest.mark.parametrize(
    "mode,version",
    [
        (None, "retrospective-v1"),
        ("STRICT_PIT", "retrospective-v1"),
        ("RETROSPECTIVE_EXPLORATORY", None),
        ("RETROSPECTIVE_EXPLORATORY", "strict-pit-v1"),
        ("AUTO", None),
        (True, None),
    ],
)
def test_unknown_mode_and_policy_mismatch_never_fall_back(mode, version):
    with pytest.raises(ValueError):
        selection_policy(mode, version)


def test_closed_policy_and_dataset_variants_reject_mixups():
    config, snapshot, bars, calendar = fixture()
    with pytest.raises(ValueError, match="retrospective policy"):
        retro.RetrospectiveDataset(snapshot, bars, StrictPitPolicyV1())
    with pytest.raises(ValueError, match="retrospective dataset"):
        retro.materialize_retrospective_fold(
            FrozenDataset(snapshot, bars), config.folds[0], config.instrument_id, 0, calendar
        )
    retrospective = retro.RetrospectiveDataset(snapshot, bars)
    with pytest.raises(ValueError, match="Strict PIT"):
        materialize_fold(retrospective, config.folds[0], config.instrument_id, 0, calendar)
    dataset = materialized(config, snapshot, bars, calendar)
    with pytest.raises(ValueError, match="retrospective policy"):
        replace(dataset, policy=StrictPitPolicyV1())
    with pytest.raises(FrozenInstanceError):
        retrospective.policy = StrictPitPolicyV1()


def test_new_snapshot_selects_highest_local_revision_old_bindings_restore_exactly():
    config, snapshot, bars, calendar = fixture()
    original = materialized(config, snapshot, bars, calendar)
    correction = replace(
        bars[0],
        revision_id=UUID(int=40),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        high=Decimal("102"),
        close=Decimal("102"),
        commit_version=101,
        # An early timestamp does not make a later commit visible to the old snapshot.
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_SNAPSHOT_MISMATCH"):
        retro.RetrospectiveDataset(snapshot, (*bars, correction))
    newer = replace(snapshot, version=101)
    later = materialized(config, newer, (*bars, correction), calendar)
    assert later.steps[0].signal_bar is correction
    assert original.steps[0].signal_bar is bars[0]
    assert original.dataset_hash != later.dataset_hash
    assert (
        manifest(config, snapshot, original).manifest_hash
        != manifest(config, newer, later).manifest_hash
    )
    restored = retro.RetrospectiveDataset(newer, (*bars, correction)).restore(
        original.revision_references
    )
    assert restored == bars
    assert bars[0].ingested_at == correction.ingested_at


@pytest.mark.parametrize("what", ["ingestion", "availability", "commit"])
def test_both_snapshot_boundaries_and_actual_availability_are_enforced(what):
    _, snapshot, bars, _ = fixture()
    changes = (
        {"commit_version": snapshot.version + 1}
        if what == "commit"
        else {
            "ingested_at" if what == "ingestion" else "available_at": snapshot.at
            + timedelta(seconds=1),
            "availability_basis": AvailabilityBasis.VERIFIED_PUBLICATION,
            "published_at": bars[0].identity.bar_end,
        }
    )
    changed = replace(bars[0], **changes)
    with pytest.raises(DatasetBlocked, match="BLOCKED_SNAPSHOT_MISMATCH"):
        retro.RetrospectiveDataset(snapshot, (changed,))


def test_conflicting_tied_local_revisions_cannot_be_resolved_by_uuid():
    _, snapshot, bars, _ = fixture()
    tied = replace(bars[0], revision_id=UUID(int=999), close=Decimal("101"), high=Decimal("101"))
    for order in ((bars[0], tied), (tied, bars[0])):
        with pytest.raises(DatasetBlocked, match="BLOCKED_AMBIGUOUS_DATA"):
            retro.RetrospectiveDataset(snapshot, order).resolve()


@pytest.mark.parametrize("status", [QualityStatus.INVALID, QualityStatus.CONFLICT])
def test_newer_invalid_revision_never_silently_falls_back_to_older_valid_revision(status):
    config, snapshot, bars, calendar = fixture()
    invalid = replace(
        bars[0],
        revision_id=UUID(int=50),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        quality=status,
        commit_version=50,
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_QUALITY|BLOCKED_AMBIGUOUS_DATA"):
        materialized(config, snapshot, (*bars, invalid), calendar)


@pytest.mark.parametrize("change", ["missing", "price", "identity", "receipt", "local", "commit"])
def test_exact_references_reject_loss_or_changed_evidence(change):
    _, snapshot, bars, _ = fixture()
    reference = retro.RetrospectiveRevisionReference.from_bar(bars[0])
    if change == "missing":
        candidates = ()
    else:
        changes = {
            "price": {"close": Decimal("101"), "high": Decimal("101")},
            "identity": {"identity": replace(bars[0].identity, provider="other")},
            "receipt": {
                "available_at": bars[0].available_at + timedelta(seconds=1),
                "ingested_at": bars[0].ingested_at + timedelta(seconds=1),
            },
            "local": {"local_revision": 2, "predecessor_id": UUID(int=999)},
            "commit": {"commit_version": 99},
        }[change]
        candidates = (replace(bars[0], **changes),)
    with pytest.raises(DatasetBlocked, match="BLOCKED_REVISION_"):
        retro.RetrospectiveDataset(snapshot, candidates).restore((reference,))


def test_future_market_bars_and_fill_prices_never_enter_signal_inputs():
    config, snapshot, bars, calendar = fixture(warmup=2)
    dataset = materialized(config, snapshot, bars, calendar)
    assert dataset.warmup == bars[:2]
    assert dataset.steps[0].input_bars == bars[:3]
    changed = (
        *bars[:3],
        *(replace(b, close=Decimal("150"), high=Decimal("150")) for b in bars[3:]),
    )
    other = materialized(config, snapshot, changed, calendar)
    assert fingerprint(dataset.steps[0].input_bars) == fingerprint(other.steps[0].input_bars)
    assert dataset.steps[0].fill_target == bars[4].identity.bar_start
    assert bars[4] not in dataset.steps[0].input_bars
    assert dataset.outcome_bars[1] != other.outcome_bars[1]


def test_calendar_correction_changes_new_snapshot_not_existing_binding():
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    correction = {
        **calendar[0],
        "session_id": UUID(int=99),
        "version": "e" * 64,
        "commit_version": 101,
        "closes_at": bars[3].identity.bar_end,
    }
    old = materialized(config, snapshot, bars, (*calendar, correction))
    assert old == dataset
    later = materialized(config, replace(snapshot, version=101), bars, (*calendar, correction))
    assert later.expected_bar_starts == tuple(b.identity.bar_start for b in bars[:4])
    assert dataset.expected_bar_starts == tuple(b.identity.bar_start for b in bars)


def test_calendar_replay_uses_exact_immutable_references_and_fails_on_loss_or_tampering():
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    later = {
        **calendar[0],
        "session_id": UUID(int=99),
        "version": "e" * 64,
        "commit_version": 50,
        "closes_at": bars[3].identity.bar_end,
    }
    _, references = retro.restore_retrospective_calendar(
        (*calendar, later),
        dataset.calendar_references,
        snapshot,
    )
    assert references == dataset.calendar_references
    with pytest.raises(DatasetBlocked, match="BLOCKED_REVISION_MISSING"):
        retro.restore_retrospective_calendar((), dataset.calendar_references, snapshot)
    altered = ({**calendar[0], "closes_at": bars[3].identity.bar_end},)
    with pytest.raises(DatasetBlocked, match="BLOCKED_REVISION_MISMATCH"):
        retro.restore_retrospective_calendar(altered, dataset.calendar_references, snapshot)
    calendar[0]["closes_at"] = bars[3].identity.bar_end
    assert dataset.calendar_references[0].closes_at == bars[-1].identity.bar_end
    assert len(dataset.expected_bar_starts) == 6


@pytest.mark.parametrize("kind", ["unknown", "conflict", "missing_bar", "warmup"])
def test_complete_calendar_and_required_market_windows_are_not_skipped(kind):
    config, snapshot, bars, calendar = fixture(warmup=2 if kind == "warmup" else 0)
    if kind == "unknown":
        calendar = ()
    elif kind == "conflict":
        calendar = (
            *calendar,
            {
                **calendar[0],
                "session_id": UUID(int=99),
                "version": "e" * 64,
                "closes_at": bars[3].identity.bar_end,
            },
        )
    elif kind == "missing_bar":
        bars = (*bars[:2], *bars[3:])
    else:
        bars = bars[1:]
    with pytest.raises(DatasetBlocked, match="BLOCKED_"):
        materialized(config, snapshot, bars, calendar)


def test_forged_subset_of_expected_bars_and_missing_calendar_reference_are_rejected():
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        replace(dataset, calendar_references=())
    with pytest.raises(DatasetBlocked):
        replace(
            dataset,
            expected_bar_starts=dataset.expected_bar_starts[1:],
            steps=dataset.steps[1:],
            outcome_bars=dataset.outcome_bars[1:],
            revision_references=dataset.revision_references[1:],
        )


def test_mutable_inputs_and_same_uuid_with_altered_signal_content_are_rejected():
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    mutable = replace(dataset.steps[0], input_bars=list(dataset.steps[0].input_bars))
    with pytest.raises(DatasetBlocked, match="BLOCKED_POINT_IN_TIME_INPUT"):
        replace(dataset, steps=(mutable, *dataset.steps[1:]))
    forged_bar = replace(bars[0], close=Decimal("101"), high=Decimal("101"))
    forged = replace(dataset.steps[0], signal_bar=forged_bar, input_bars=(forged_bar,))
    with pytest.raises(DatasetBlocked, match="BLOCKED_REVISION_MISMATCH"):
        replace(dataset, steps=(forged, *dataset.steps[1:]))


def trust_record(reference, *, temporal="UNKNOWN", integrity="VERIFIED", revoked=False):
    return retro.RetrospectiveTrustRecord(
        str(reference),
        (
            ("integrity", integrity),
            ("sourceAuthenticity", "VERIFIED"),
            ("temporalAuthenticity", temporal),
            ("completeness", "VERIFIED"),
            ("domainQuality", "VERIFIED"),
        ),
        revoked,
    )


def test_managed_current_calendar_content_can_be_used_without_claiming_historical_time():
    config, snapshot, bars, calendar = fixture()
    evidence_id = UUID(int=88)
    managed = ({**calendar[0], "evidence_id": evidence_id, "managed_trust_state": "TIME_UNPROVEN"},)
    trust = retro.RetrospectiveTrustContext(snapshot, "b" * 64, (trust_record(evidence_id),))
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        materialized(config, snapshot, bars, managed)
    dataset = materialized(config, snapshot, bars, managed, trust_context=trust)
    assert dataset.calendar_references[0].evidence_id == evidence_id
    assert dataset.calendar_references[0].ingested_at == managed[0]["ingested_at"]
    for bad in (
        trust_record(evidence_id, revoked=True),
        trust_record(evidence_id, temporal="REJECTED"),
        trust_record(evidence_id, integrity="UNKNOWN"),
    ):
        with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
            materialized(
                config, snapshot, bars, managed, trust_context=replace(trust, records=(bad,))
            )


def test_negative_calendar_marker_cannot_be_hidden_by_older_positive_trust():
    config, snapshot, bars, calendar = fixture()
    evidence_id = UUID(int=88)
    trust = retro.RetrospectiveTrustContext(snapshot, "b" * 64, (trust_record(evidence_id),))
    managed = (
        {**calendar[0], "evidence_id": evidence_id, "managed_trust_state": "EVIDENCE_REVOKED"},
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        materialized(config, snapshot, bars, managed, trust_context=trust)


def test_manifest_identity_binds_policy_build_snapshot_trust_and_evidence():
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    bound = manifest(config, snapshot, dataset)
    assert bound.manifest_hash == manifest(config, snapshot, dataset).manifest_hash
    assert bound.input_hash != config.input_hash
    assert bound.manifest_hash != replace(bound, code_version="e" * 40).manifest_hash
    assert bound.manifest_hash != replace(bound, license_receipt_fingerprint="e" * 64).manifest_hash
    assert (
        bound.manifest_hash
        != replace(
            bound, trust_context=replace(bound.trust_context, version="e" * 64)
        ).manifest_hash
    )
    reference = retro.RetrospectiveEvidenceReference(
        UUID(int=88), "f" * 64, 1, 1, bars[0].ingested_at
    )
    assert bound.manifest_hash != replace(bound, quality_references=(reference,)).manifest_hash
    context = bound.validation_context()
    context.validate(config, dataset)
    with pytest.raises(DatasetBlocked, match="BLOCKED_DATASET_BINDING"):
        context.validate(replace(config, fee_usd=Decimal("1")), dataset)
    with pytest.raises(ValueError, match="policy/dataset mismatch"):
        context.validate(config, FrozenDataset(snapshot, bars))


def test_revision_metadata_and_publication_budgets_are_enforced(monkeypatch):
    config, snapshot, bars, calendar = fixture()
    dataset = materialized(config, snapshot, bars, calendar)
    monkeypatch.setattr(retro, "MAX_REVISIONS", 6)
    with pytest.raises(retro.DatasetBudgetExceeded, match="combined"):
        materialized(config, snapshot, bars, calendar)
    monkeypatch.setattr(retro, "MAX_REVISIONS", 100000)
    monkeypatch.setattr(retro, "MAX_PUBLICATION_BYTES", 100)
    with pytest.raises(retro.DatasetBudgetExceeded, match="byte budget"):
        manifest(config, snapshot, dataset)


def test_checkpoint_budget_failure_terminates_materialization():
    config, snapshot, bars, calendar = fixture()
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise retro.DatasetBudgetExceeded("deadline exhausted")

    with pytest.raises(retro.DatasetBudgetExceeded, match="deadline"):
        materialized(config, snapshot, bars, calendar, checkpoint=checkpoint)
    assert calls == 3


def test_reference_wire_shape_matches_canonical_schema():
    import json
    from pathlib import Path

    schema = json.loads(Path("contracts/schemas/v1/backtesting.schema.json").read_text())
    _, _, bars, _ = fixture()
    schema["$ref"] = "#/$defs/BacktestRetrospectiveRevision"
    jsonschema.Draft202012Validator(schema).validate(
        retro.RetrospectiveRevisionReference.from_bar(bars[0]).to_dict()
    )
