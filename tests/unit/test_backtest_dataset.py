"""Independent cutoff and sweep oracles, with explicit synthetic observation times."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.backtesting.coverage import CoverageProof, covering_proof
from packages.backtesting.dataset import (
    DatasetBlocked,
    FrozenDataset,
    Snapshot,
    materialize_fold,
)
from packages.backtesting.models import Fold
from packages.research.models import BarIdentity, BarInterval, BarRevision, PointInTimeQuery
from packages.research.query import latest_visible


def fixture():
    start = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)
    instrument = uuid4()
    snapshot = Snapshot(100, start + timedelta(days=1))
    bars = []
    for n in range(6):
        opening = start + timedelta(minutes=15 * n)
        closing = opening + timedelta(minutes=15)
        bars.append(
            BarRevision(
                uuid4(),
                BarIdentity(
                    instrument, "alpaca", "iex", BarInterval.FIFTEEN_MINUTES, opening, closing
                ),
                *(Decimal("100"),) * 4,
                Decimal("10"),
                closing + timedelta(seconds=30),
                closing + timedelta(seconds=30),
                commit_version=n + 1,
            )
        )
    row = dict(
        session_id=uuid4(),
        session_date=start.date(),
        opens_at=start,
        closes_at=start + timedelta(hours=2),
        version="a" * 64,
        source="alpaca-v2-calendar",
        commit_version=1,
        ingested_at=start - timedelta(days=1),
    )
    proof = CoverageProof(
        uuid4(),
        instrument,
        start - timedelta(days=1),
        start + timedelta(days=1),
        start - timedelta(days=2),
        "offline-certified-fixture",
        "b" * 64,
        1,
        start - timedelta(days=1),
    )
    fold = Fold(start - timedelta(minutes=30), start, start, start + timedelta(minutes=90))
    return instrument, start, snapshot, tuple(bars), (row,), proof, fold


def test_sweep_matches_each_point_query_and_corrections_do_not_leak():
    _, start, snapshot, bars, _, _, _ = fixture()
    changed = replace(
        bars[0],
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        open=Decimal("99"),
        low=Decimal("99"),
        ingested_at=start + timedelta(minutes=18),
        available_at=start + timedelta(minutes=18),
        commit_version=20,
    )
    frozen = FrozenDataset(snapshot, (*bars, changed))
    sweep = frozen.sweep()
    for minute in range(15, 92):
        at = start + timedelta(minutes=minute)
        oracle = PointInTimeQuery(at, at, snapshot.at, snapshot.version)
        assert sweep.visible(at, at) == latest_visible(frozen.revisions, oracle)
    with pytest.raises(ValueError, match="rewind"):
        sweep.visible(start, start)


def test_signal_and_outcome_have_separate_fixed_observation_cutoffs():
    instrument, start, snapshot, bars, calendar, proof, fold = fixture()
    original = bars[0]
    correction = replace(
        original,
        revision_id=uuid4(),
        local_revision=2,
        predecessor_id=original.revision_id,
        close=Decimal("101"),
        high=Decimal("101"),
        ingested_at=original.identity.bar_end + timedelta(seconds=120),
        available_at=original.identity.bar_end + timedelta(seconds=120),
        commit_version=20,
    )
    late = replace(
        correction,
        revision_id=uuid4(),
        local_revision=3,
        predecessor_id=correction.revision_id,
        ingested_at=original.identity.bar_end + timedelta(seconds=301),
        available_at=original.identity.bar_end + timedelta(seconds=301),
        commit_version=21,
    )
    result = materialize_fold(
        FrozenDataset(snapshot, (*bars, correction, late)),
        fold,
        instrument,
        0,
        calendar,
        coverage_proofs=(proof,),
    )
    assert result.steps[0].signal_bar == original
    assert result.outcome_bars[0] == correction
    assert result.steps[0].fill_target == start + timedelta(minutes=30)
    assert result.steps[-1].fill_target is None
    assert result.outcome_cutoffs[0] == (
        correction.revision_id,
        original.identity.bar_end + timedelta(seconds=300),
    )
    assert result.steps[0].input_bars == (original,)


@pytest.mark.parametrize("missing", ["coverage", "calendar", "signal", "warmup"])
def test_missing_observed_evidence_blocks_instead_of_skipping(missing):
    instrument, _, snapshot, bars, calendar, proof, fold = fixture()
    if missing == "signal":
        bars = bars[1:]
    with pytest.raises(DatasetBlocked) as error:
        materialize_fold(
            FrozenDataset(snapshot, bars),
            fold,
            instrument,
            1 if missing == "warmup" else 0,
            () if missing == "calendar" else calendar,
            coverage_proofs=() if missing == "coverage" else (proof,),
        )
    assert (
        error.value.code
        == {
            "coverage": "BLOCKED_CORPORATE_ACTIONS_UNKNOWN",
            "calendar": "BLOCKED_CALENDAR_UNKNOWN",
            "signal": "BLOCKED_MISSING_DATA",
            "warmup": "BLOCKED_WARMUP_MISSING",
        }[missing]
    )


def test_coverage_late_or_negative_certificate_cannot_rehabilitate_history():
    instrument, start, snapshot, _, _, proof, _ = fixture()
    negative = replace(proof, coverage_id=uuid4(), no_actions=False, commit_version=2)
    assert (
        covering_proof(
            (proof, negative), instrument, start, start + timedelta(hours=1), start, snapshot
        )
        is None
    )
    late = replace(proof, ingested_at=start + timedelta(minutes=1))
    assert (
        covering_proof((late,), instrument, start, start + timedelta(hours=1), start, snapshot)
        is None
    )
    partial_action = replace(
        negative, start_at=start + timedelta(minutes=15), end_at=start + timedelta(minutes=30)
    )
    assert (
        covering_proof(
            (proof, partial_action), instrument, start, start + timedelta(hours=1), start, snapshot
        )
        is None
    )


def test_long_history_retains_only_required_rolling_signal_window():
    instrument, start, _, originals, _, proof, _ = fixture()
    sessions, bars = [], []
    for offset in range(12):
        opening = start + timedelta(days=offset)
        if opening.weekday() >= 5:
            continue
        sessions.append(
            dict(
                session_id=uuid4(),
                session_date=opening.date(),
                opens_at=opening,
                closes_at=opening + timedelta(hours=6, minutes=30),
                version="a" * 64,
                source="alpaca-v2-calendar",
                commit_version=1,
                ingested_at=start - timedelta(days=1),
            )
        )
        for n in range(26):
            bar_start = opening + timedelta(minutes=15 * n)
            bar_end = bar_start + timedelta(minutes=15)
            bars.append(
                replace(
                    originals[0],
                    revision_id=uuid4(),
                    identity=replace(originals[0].identity, bar_start=bar_start, bar_end=bar_end),
                    ingested_at=bar_end + timedelta(seconds=30),
                    available_at=bar_end + timedelta(seconds=30),
                )
            )
    end = bars[-1].identity.bar_end
    snapshot = Snapshot(1000, end + timedelta(days=1))
    proof = replace(proof, end_at=snapshot.at)
    fold = Fold(start, bars[2].identity.bar_start, bars[2].identity.bar_start, end)
    frozen = FrozenDataset(snapshot, tuple(bars))
    result = materialize_fold(
        frozen, fold, instrument, 2, tuple(sessions), coverage_proofs=(proof,)
    )
    assert len(result.steps) == 258
    assert all(len(step.input_bars) == 3 for step in result.steps)
    for step in result.steps:
        q = PointInTimeQuery(step.knowledge_at, step.knowledge_at, snapshot.at, snapshot.version)
        assert step.input_bars == latest_visible(frozen.revisions, q)[-3:]


@pytest.mark.parametrize("include_alpaca", [False, True])
def test_simulation_selects_alpaca_without_relabeling_other_providers(include_alpaca):
    instrument, _, snapshot, bars, calendar, proof, fold = fixture()
    other = tuple(
        replace(bar, revision_id=uuid4(), identity=replace(bar.identity, provider="other"))
        for bar in bars
    )
    frozen = FrozenDataset(snapshot, (*bars, *other) if include_alpaca else other)
    assert len(frozen.sweep().visible(snapshot.at, snapshot.at)) == len(frozen.revisions)
    if not include_alpaca:
        with pytest.raises(DatasetBlocked, match="BLOCKED_MISSING_DATA"):
            materialize_fold(frozen, fold, instrument, 0, calendar, coverage_proofs=(proof,))
        return
    result = materialize_fold(frozen, fold, instrument, 0, calendar, coverage_proofs=(proof,))
    assert all(
        bar.identity.provider == "alpaca" for step in result.steps for bar in step.input_bars
    )
    assert result.outcome_bars == bars


def midnight_fixture():
    instrument, start, snapshot, bars, calendar, proof, fold = fixture()
    midnight = start.replace(hour=0, minute=0)
    observed = start.replace(hour=9, minute=0)
    monday = {**calendar[0], "ingested_at": observed}
    # One complete response brackets the weekend, including midnight UTC's
    # preceding New York date. It does not invent an unproven closed date.
    friday_open = start - timedelta(days=3)
    friday = {
        **monday,
        "session_id": uuid4(),
        "session_date": friday_open.date(),
        "opens_at": friday_open,
        "closes_at": friday_open + timedelta(hours=2),
    }
    fold = Fold(midnight - timedelta(days=1), midnight, midnight, fold.test_end)
    return instrument, start, snapshot, bars, (friday, monday), proof, fold


def test_midnight_test_start_binds_plan_observed_before_actual_first_signal():
    instrument, start, snapshot, bars, calendar, proof, fold = midnight_fixture()
    assert calendar[-1]["ingested_at"] > fold.test_start + timedelta(minutes=16)
    result = materialize_fold(
        FrozenDataset(snapshot, bars), fold, instrument, 0, calendar, coverage_proofs=(proof,)
    )
    assert result.expected_bar_starts[0] == start
    assert result.steps[0].knowledge_at == start + timedelta(minutes=16)
    assert result.steps[0].signal_bar == bars[0]


def test_late_first_plan_cannot_skip_first_signal_and_start_at_a_later_bar():
    instrument, start, snapshot, bars, calendar, proof, fold = midnight_fixture()
    late = tuple(
        {**row, "ingested_at": start + timedelta(minutes=16, seconds=1)} for row in calendar
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        materialize_fold(
            FrozenDataset(snapshot, bars), fold, instrument, 0, late, coverage_proofs=(proof,)
        )


def test_future_calendar_correction_cannot_backdate_an_earlier_first_signal():
    instrument, _, snapshot, bars, calendar, proof, fold = midnight_fixture()
    frozen = FrozenDataset(snapshot, bars)
    original = materialize_fold(frozen, fold, instrument, 0, calendar, coverage_proofs=(proof,))
    corrected = {
        **calendar[-1],
        "session_id": uuid4(),
        "version": "c" * 64,
        "commit_version": 50,
        "opens_at": calendar[-1]["opens_at"] - timedelta(minutes=30),
        "ingested_at": fold.test_end + timedelta(minutes=2),
    }
    after = materialize_fold(
        frozen, fold, instrument, 0, (*calendar, corrected), coverage_proofs=(proof,)
    )
    assert original == after


def test_observed_earlier_plan_does_not_skip_missing_initial_bars():
    instrument, _, snapshot, bars, calendar, proof, fold = midnight_fixture()
    earlier = tuple(
        {**row, "opens_at": row["opens_at"] - timedelta(minutes=30)} for row in calendar
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_MISSING_DATA"):
        materialize_fold(
            FrozenDataset(snapshot, bars), fold, instrument, 0, earlier, coverage_proofs=(proof,)
        )
