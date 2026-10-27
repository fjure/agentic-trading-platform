"""RDR-02 independent adversarial PIT, trust, window and measurement oracles."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from packages.backtesting.calendar import read_schedule_rows
from packages.backtesting.coverage import (
    coverage_result,
    covering_proof,
    managed_trust_state,
    quality_at,
    quality_result,
    read_coverage,
    read_quality,
)
from packages.backtesting.dataset import DatasetBlocked, FrozenDataset, Snapshot, materialize_fold
from packages.backtesting.models import ExperimentConfig, Fold
from packages.backtesting.readiness import (
    AssessmentBudget,
    EvidenceTrust,
    SafeReference,
    TimelinessObservation,
    TrustDimensions,
    TrustResult,
    TrustSnapshot,
    TrustStatus,
    aggregate,
    assess,
    latency_metric,
)
from packages.research.models import AvailabilityBasis, BarInterval, QualityStatus
from tests.unit.test_backtest_dataset import fixture


def scenario():
    instrument, start, snapshot, bars, calendar, proof, fold = fixture()
    config = ExperimentConfig(
        instrument,
        "SPY",
        "flat-v1",
        (),
        Decimal("1000"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0.000001"),
        (fold,),
    )
    return config, start, FrozenDataset(snapshot, bars), calendar, (proof,)


def observed(bar, *, first_delay=20, confirmed_delay=1):
    return TimelinessObservation(
        bar.identity,
        bar.revision_id,
        bar.identity.bar_end + timedelta(seconds=first_delay),
        bar.ingested_at,
        bar.ingested_at + timedelta(microseconds=confirmed_delay),
        bar.commit_version,
        clock_status="COMPARABLE",
        receipt_id=uuid4(),
    )


def trust(proof, *, status="VERIFIED", revoked=False, dimension="temporal_authenticity"):
    result = TrustResult(
        TrustStatus(status),
        "SYNTHETIC_OFFLINE_FIXTURE",
        "test-v1",
        "TIME_UNPROVEN" if status == "UNKNOWN" else "SYNTHETIC_CHECK",
    )
    dimensions = replace(TrustDimensions(), **{dimension: result})
    return TrustSnapshot(
        7,
        "test-trust-v1",
        (EvidenceTrust(str(proof.coverage_id), "CORPORATE_ACTION", dimensions, revoked),),
    )


@pytest.mark.parametrize(
    "states,expected",
    [
        ([], "ELIGIBLE"),
        (["ELIGIBLE", "NOT_EVALUATED"], "NOT_EVALUATED"),
        (["NOT_EVALUATED", "UNKNOWN"], "UNKNOWN"),
        (["UNKNOWN", "BLOCKED", "NOT_EVALUATED"], "BLOCKED"),
    ],
)
def test_required_state_precedence(states, expected):
    assert aggregate(states) == expected


def test_positive_parity_has_independent_narrow_local_statements_and_complete_windows():
    config, _, frozen, calendar, proofs = scenario()
    result = assess(config, frozen, calendar, coverage_proofs=proofs)
    materialized = materialize_fold(
        frozen, config.folds[0], config.instrument_id, 0, calendar, coverage_proofs=proofs
    )
    assert result.eligibility == "ELIGIBLE"
    assert result.status == "SUCCEEDED" and result.publication_complete
    assert not result.findings
    assert len(result.windows) == 5
    assert {len(window.bars) for window in result.windows} == {len(materialized.steps)}
    assert {r.method for r in result.trust.values()} == {
        "NORMALIZED_CONTENT",
        "DB_INGESTION_PROVENANCE",
        "DB_COMMITTED_VISIBILITY",
        "SHARED_CALENDAR_IDENTITIES",
        "SHARED_OHLC_QUALITY",
    }
    assert result.trust.temporal_authenticity.reason == "LOCAL_VISIBILITY_NOT_PROVIDER_PUBLICATION"
    assert result.report_dict(uuid4())["providerRights"]["providerVerification"] == "UNVERIFIED"
    # No reception/commit measurement exists for legacy bars; eligibility is the
    # old rule, but missing measurements must never turn into perfect quotas.
    metric = result.metrics[0]
    assert metric["usableBySignalDeadline"]["numerator"] == 0
    assert metric["usableBySignalDeadline"]["unknownCount"] == 6


@pytest.mark.parametrize(
    "change,code,category",
    [
        ("bar", "BLOCKED_MISSING_DATA", "BAR_MISSING"),
        ("late", "BLOCKED_MISSING_DATA", "OBSERVED_TOO_LATE"),
        ("calendar", "BLOCKED_CALENDAR_UNKNOWN", "CALENDAR_UNKNOWN"),
        ("coverage", "BLOCKED_CORPORATE_ACTIONS_UNKNOWN", "CORPORATE_ACTIONS_UNKNOWN"),
        ("negative", "BLOCKED_CORPORATE_ACTIONS_UNKNOWN", "CORPORATE_ACTIONS_CONFLICT"),
        ("quality", "BLOCKED_QUALITY", "QUALITY_REJECTED"),
    ],
)
def test_materializer_and_diagnosis_preserve_failure_causes(change, code, category):
    config, start, frozen, calendar, proofs = scenario()
    quality = ()
    if change == "bar":
        frozen = replace(frozen, revisions=frozen.revisions[1:])
    if change == "late":
        bars = (
            replace(
                frozen.revisions[0],
                ingested_at=frozen.revisions[0].identity.bar_end + timedelta(seconds=61),
                available_at=frozen.revisions[0].identity.bar_end + timedelta(seconds=61),
            ),
            *frozen.revisions[1:],
        )
        frozen = replace(frozen, revisions=bars)
    if change == "calendar":
        calendar = ()
    if change == "coverage":
        proofs = ()
    if change == "negative":
        proofs = (
            proofs[0],
            replace(proofs[0], coverage_id=uuid4(), no_actions=False, commit_version=2),
        )
    if change == "quality":
        quality = (
            dict(
                observation_id=uuid4(),
                start_at=start,
                end_at=config.folds[0].test_end,
                quality="INVALID",
                ingested_at=start - timedelta(seconds=1),
                commit_version=1,
            ),
        )
    result = assess(config, frozen, calendar, quality, proofs)
    with pytest.raises(DatasetBlocked) as error:
        materialize_fold(
            frozen, config.folds[0], config.instrument_id, 0, calendar, quality, proofs
        )
    assert error.value.code == code
    assert category in {finding.category for finding in result.findings}
    assert result.eligibility != "ELIGIBLE"
    assert not [w for w in result.windows if w.kind == "COMPLETE_EXPERIMENT"]


def test_unknown_calendar_has_independent_missing_coverage_and_no_fabricated_identities():
    config, _, frozen, _, _ = scenario()
    result = assess(config, frozen, (), coverage_proofs=())
    assert result.eligibility == "UNKNOWN"
    assert {f.category for f in result.findings} == {
        "CALENDAR_UNKNOWN",
        "CORPORATE_ACTIONS_UNKNOWN",
        "NOT_EVALUATED",
    }
    assert {f.area for f in result.findings if f.category == "NOT_EVALUATED"} == {
        "WARMUP",
        "SIGNAL",
        "OUTCOME",
        "QUALITY",
    }
    assert all(not f.references for f in result.findings)
    assert result.metrics[0]["expectedBars"] == 0
    assert result.metrics[0]["unknownCalendarDays"] == 1
    assert result.metrics[0]["missingBars"]["status"] == "N/A"
    assert not result.windows


def test_multiple_independent_blockers_are_not_first_error_only():
    config, start, frozen, calendar, _ = scenario()
    quality = (
        dict(
            observation_id=uuid4(),
            start_at=start,
            end_at=config.folds[0].test_end,
            quality="INVALID",
            ingested_at=start - timedelta(seconds=1),
            commit_version=1,
        ),
    )
    result = assess(config, replace(frozen, revisions=()), calendar, quality, ())
    assert result.eligibility == "BLOCKED"
    assert {"BAR_MISSING", "QUALITY_REJECTED", "CORPORATE_ACTIONS_UNKNOWN"} <= {
        finding.category for finding in result.findings
    }
    report = result.report_dict(uuid4())
    assert report["categoryOverlaps"]
    assert result.trust.completeness.status == "REJECTED"
    assert result.trust.domain_quality.status == "REJECTED"


@pytest.mark.parametrize("case", ["negative", "conflict", "revoked"])
def test_unknown_calendar_does_not_hide_independent_known_coverage_blocker(case):
    config, _, frozen, _, proofs = scenario()
    current = TrustSnapshot()
    if case == "negative":
        proofs = (replace(proofs[0], no_actions=False),)
    elif case == "conflict":
        proofs = (proofs[0], replace(proofs[0], coverage_id=uuid4(), no_actions=False))
    else:
        current = trust(proofs[0], revoked=True)
    result = assess(config, frozen, (), coverage_proofs=proofs, trust=current)
    assert result.eligibility == "BLOCKED"
    assert "CALENDAR_UNKNOWN" in {finding.category for finding in result.findings}
    expected = "EVIDENCE_REVOKED" if case == "revoked" else "CORPORATE_ACTIONS_CONFLICT"
    assert expected in {finding.category for finding in result.findings}


@pytest.mark.parametrize(
    "seconds,micros,signal,outcome",
    [
        (60, 0, True, True),
        (60, 1, False, True),
        (300, 0, False, True),
        (300, 1, False, False),
    ],
)
def test_exact_signal_and_outcome_boundaries(seconds, micros, signal, outcome):
    config, _, frozen, calendar, proofs = scenario()
    first = frozen.revisions[0]
    at = first.identity.bar_end + timedelta(seconds=seconds, microseconds=micros)
    frozen = replace(
        frozen, revisions=(replace(first, ingested_at=at, available_at=at), *frozen.revisions[1:])
    )
    result = assess(config, frozen, calendar, coverage_proofs=proofs)
    for scope, accepted in (("SIGNAL", signal), ("OUTCOME", outcome)):
        members = {
            bar.bar_start
            for w in result.windows
            if w.kind == "BAR_OBSERVATION" and w.scope == scope
            for bar in w.bars
        }
        assert (first.identity.bar_start in members) is accepted
    if not signal:
        assert "OBSERVED_TOO_LATE" in {f.category for f in result.findings}


def test_maximal_signal_runs_split_at_late_bar_but_outcome_is_one_run():
    config, _, frozen, calendar, proofs = scenario()
    config = replace(
        config, folds=(replace(config.folds[0], test_end=frozen.revisions[4].identity.bar_end),)
    )
    bars = list(frozen.revisions[:5])
    bars[2] = replace(
        bars[2],
        ingested_at=bars[2].identity.bar_end + timedelta(seconds=90),
        available_at=bars[2].identity.bar_end + timedelta(seconds=90),
    )
    result = assess(
        config, replace(frozen, revisions=tuple(bars)), calendar, coverage_proofs=proofs
    )
    assert [
        len(w.bars) for w in result.windows if (w.kind, w.scope) == ("BAR_OBSERVATION", "SIGNAL")
    ] == [2, 2]
    assert [
        len(w.bars) for w in result.windows if (w.kind, w.scope) == ("BAR_OBSERVATION", "OUTCOME")
    ] == [5]
    assert not [w for w in result.windows if w.kind == "COMPLETE_EXPERIMENT"]


def test_late_warmup_is_checked_at_signal_knowledge_not_its_own_deadline():
    config, start, frozen, calendar, proofs = scenario()
    fold = Fold(
        start,
        start + timedelta(minutes=30),
        start + timedelta(minutes=30),
        config.folds[0].test_end,
    )
    config = replace(
        config, strategy_id="sma-cross-v1", parameters=(("fast", 2), ("slow", 3)), folds=(fold,)
    )
    first = frozen.revisions[0]
    late = first.identity.bar_end + timedelta(seconds=90)
    frozen = replace(
        frozen,
        revisions=(replace(first, ingested_at=late, available_at=late), *frozen.revisions[1:]),
    )
    result = assess(config, frozen, calendar, coverage_proofs=proofs)
    materialized = materialize_fold(
        frozen, fold, config.instrument_id, 2, calendar, coverage_proofs=proofs
    )
    assert len(materialized.warmup) == 2 and result.eligibility == "ELIGIBLE"
    assert all(len(w.bars) == 4 for w in result.windows)


def test_missing_warmup_does_not_invent_future_inputs_or_move_test_start():
    config, _, frozen, calendar, proofs = scenario()
    config = replace(config, strategy_id="sma-cross-v1", parameters=(("fast", 2), ("slow", 3)))
    result = assess(config, frozen, calendar, coverage_proofs=proofs)
    assert result.eligibility == "BLOCKED"
    assert {f.category for f in result.findings} == {"WARMUP_MISSING"}
    assert not [w for w in result.windows if w.kind == "COMPLETE_EXPERIMENT"]


def test_quality_resolution_must_be_one_later_full_cover_not_sum_of_parts():
    _, start, frozen, _, _ = scenario()
    end = start + timedelta(hours=1)
    rejected = dict(
        observation_id=uuid4(),
        start_at=start,
        end_at=end,
        quality="INVALID",
        ingested_at=start,
        commit_version=1,
    )
    left = {
        **rejected,
        "observation_id": uuid4(),
        "quality": "VALID",
        "end_at": start + timedelta(minutes=30),
        "commit_version": 2,
    }
    right = {
        **left,
        "observation_id": uuid4(),
        "start_at": left["end_at"],
        "end_at": end,
        "commit_version": 3,
    }
    at = end + timedelta(minutes=1)
    assert (
        quality_result((rejected, left, right), start, end, at, frozen.snapshot).state == "REJECTED"
    )
    full = {**left, "observation_id": uuid4(), "end_at": end, "commit_version": 4}
    assert quality_at((rejected, left, right, full), start, end, at, frozen.snapshot)[0]
    full["managed_trust_state"] = "TIME_UNPROVEN"
    assert not quality_at((rejected, full), start, end, at, frozen.snapshot)[0]


@pytest.mark.parametrize("state", ["UNKNOWN", "NOT_EVALUATED", "REJECTED"])
def test_independent_trust_dimensions_do_not_promote_signature_to_time_proof(state):
    config, _, frozen, calendar, proofs = scenario()
    current = trust(proofs[0], status=state)
    result = assess(config, frozen, calendar, coverage_proofs=proofs, trust=current)
    expected = {"UNKNOWN": "UNKNOWN", "NOT_EVALUATED": "NOT_EVALUATED", "REJECTED": "BLOCKED"}[
        state
    ]
    assert result.eligibility == expected
    assert result.trust.integrity.status == "VERIFIED"
    assert result.trust.source_authenticity.status == "VERIFIED"
    assert result.trust.temporal_authenticity.status == state
    assert not [w for w in result.windows if w.kind == "STRICT_PIT"]
    with pytest.raises(DatasetBlocked, match="BLOCKED_CORPORATE_ACTIONS_UNKNOWN"):
        materialize_fold(
            frozen,
            config.folds[0],
            config.instrument_id,
            0,
            calendar,
            coverage_proofs=proofs,
            trust_check=current.gate,
        )


def test_current_revocation_blocks_old_data_snapshot_without_mutating_old_result():
    config, _, frozen, calendar, proofs = scenario()
    previous = assess(config, frozen, calendar, coverage_proofs=proofs, trust=trust(proofs[0]))
    original = previous.report_dict(config.instrument_id)
    current = replace(trust(proofs[0], revoked=True), version=8)
    result = assess(config, frozen, calendar, coverage_proofs=proofs, trust=current)
    assert previous.eligibility == "ELIGIBLE" and result.eligibility == "BLOCKED"
    assert "EVIDENCE_REVOKED" in {f.category for f in result.findings}
    assert result.trust.source_authenticity.status == "REJECTED"
    assert previous.report_dict(config.instrument_id) == original
    assert original["snapshot"]["trustVersion"] == 7
    assert result.report_dict(config.instrument_id)["snapshot"]["trustVersion"] == 8


def test_managed_proof_is_intrinsically_gated_without_an_explicit_hook():
    config, start, frozen, calendar, proofs = scenario()
    unproven = replace(proofs[0], managed_trust_state="TIME_UNPROVEN")
    assert (
        covering_proof(
            (unproven,),
            config.instrument_id,
            start,
            config.folds[0].test_end,
            start + timedelta(minutes=16),
            frozen.snapshot,
        )
        is None
    )
    with pytest.raises(DatasetBlocked, match="BLOCKED_CORPORATE_ACTIONS_UNKNOWN"):
        materialize_fold(
            frozen, config.folds[0], config.instrument_id, 0, calendar, coverage_proofs=(unproven,)
        )


def test_structured_negative_conflict_and_unknown_are_distinct_without_changing_wrapper():
    config, start, frozen, _, proofs = scenario()
    kwargs = (config.instrument_id, start, config.folds[0].test_end, start, frozen.snapshot)
    assert coverage_result((), *kwargs).state == "UNKNOWN"
    negative = replace(proofs[0], no_actions=False)
    assert coverage_result((negative,), *kwargs).state == "NEGATIVE"
    assert (
        coverage_result((proofs[0], replace(negative, coverage_id=uuid4())), *kwargs).state
        == "CONFLICT"
    )
    assert covering_proof((negative,), *kwargs) is None


def test_ten_bar_denominators_duplicates_and_confirmation_measurements():
    config, start, frozen, calendar, proofs = scenario()
    bars = list(frozen.revisions)
    for index in (6, 7):
        opening = start + timedelta(minutes=15 * index)
        closing = opening + timedelta(minutes=15)
        bars.append(
            replace(
                bars[0],
                revision_id=uuid4(),
                identity=replace(bars[0].identity, bar_start=opening, bar_end=closing),
                ingested_at=closing + timedelta(seconds=90),
                available_at=closing + timedelta(seconds=90),
                commit_version=index + 1,
            )
        )
    end = start + timedelta(minutes=150)
    config = replace(config, folds=(replace(config.folds[0], test_end=end),))
    calendar = ({**calendar[0], "closes_at": end},)
    receipts = tuple(observed(bar) for bar in bars)
    receipts += (
        replace(receipts[0], duplicate=True, receipt_id=uuid4()),
        replace(receipts[1], duplicate=True, receipt_id=uuid4()),
    )
    result = assess(
        config,
        replace(frozen, revisions=tuple(bars)),
        calendar,
        coverage_proofs=proofs,
        observations=receipts,
    )
    metric = result.metrics[0]
    assert metric["expectedBars"] == 10 and metric["observedBars"] == 8
    for key, numerator in (
        ("usableBySignalDeadline", 6),
        ("usableByOutcomeDeadline", 8),
        ("missingBars", 2),
        ("lateForSignal", 2),
        ("lateForOutcome", 0),
    ):
        assert (metric[key]["numerator"], metric[key]["denominator"]) == (numerator, 10)
    assert metric["duplicateResponses"]["numerator"] == 2
    assert metric["duplicateResponses"]["denominator"] == 10
    assert metric["receivedBySignalDeadline"]["numerator"] == 8


def test_receipt_before_signal_ingestion_after_signal_is_outcome_only():
    config, _, frozen, calendar, proofs = scenario()
    bar = frozen.revisions[0]
    at = bar.identity.bar_end + timedelta(seconds=75)
    late = replace(bar, ingested_at=at, available_at=at)
    bars = (late, *frozen.revisions[1:])
    result = assess(
        config,
        replace(frozen, revisions=bars),
        calendar,
        coverage_proofs=proofs,
        observations=tuple(observed(item) for item in bars),
    )
    metric = result.metrics[0]
    assert metric["receivedBySignalDeadline"]["numerator"] == 6
    assert metric["usableBySignalDeadline"]["numerator"] == 5
    assert metric["usableByOutcomeDeadline"]["numerator"] == 6
    assert metric["ingestionMinusReception"]["maximumSeconds"] == "55"
    assert "OBSERVED_TOO_LATE" in {f.category for f in result.findings}


def test_unknown_or_late_commit_confirmation_does_not_improve_quota_or_change_bkt_rule():
    config, _, frozen, calendar, proofs = scenario()
    receipts = [observed(bar) for bar in frozen.revisions]
    receipts[0] = replace(receipts[0], commit_confirmed_at=None)
    receipts[1] = replace(
        receipts[1], commit_confirmed_at=receipts[1].identity.bar_end + timedelta(seconds=75)
    )
    result = assess(config, frozen, calendar, coverage_proofs=proofs, observations=tuple(receipts))
    assert result.eligibility == "ELIGIBLE"
    assert result.metrics[0]["usableBySignalDeadline"]["numerator"] == 4
    assert result.metrics[0]["usableBySignalDeadline"]["unknownCount"] == 2
    assert result.metrics[0]["usableByOutcomeDeadline"]["numerator"] == 5


def test_nearest_rank_uses_exact_valid_pairs_and_reports_unknown_negative_and_clock_failures():
    start = datetime(2026, 10, 19, tzinfo=UTC)
    pairs = [(start, start + timedelta(seconds=n), "COMPARABLE") for n in range(1, 21)]
    pairs += [
        (start, None, "UNKNOWN"),
        (start, start - timedelta(microseconds=1), "COMPARABLE"),
        (start, start + timedelta(seconds=1), "INVALID"),
    ]
    metric = latency_metric(pairs)
    assert metric == dict(
        status="MEASURED",
        p50Seconds="10",
        p95Seconds="19",
        maximumSeconds="20",
        sampleCount=20,
        unknownPairCount=1,
        invalidPairCount=2,
    )


def test_correction_content_return_does_not_rewrite_earlier_deadline_or_denominator():
    config, _, frozen, calendar, proofs = scenario()
    original = frozen.revisions[0]
    correction = replace(
        original,
        revision_id=uuid4(),
        predecessor_id=original.revision_id,
        local_revision=2,
        high=Decimal("101"),
        close=Decimal("101"),
        ingested_at=original.identity.bar_end + timedelta(seconds=120),
        available_at=original.identity.bar_end + timedelta(seconds=120),
        commit_version=20,
    )
    returned = replace(
        original,
        revision_id=uuid4(),
        predecessor_id=correction.revision_id,
        local_revision=3,
        ingested_at=original.identity.bar_end + timedelta(seconds=240),
        available_at=original.identity.bar_end + timedelta(seconds=240),
        commit_version=21,
    )
    bars = (*frozen.revisions, correction, returned)
    result = assess(
        config,
        replace(frozen, revisions=bars),
        calendar,
        coverage_proofs=proofs,
        observations=tuple(observed(bar) for bar in bars),
    )
    materialized = materialize_fold(
        replace(frozen, revisions=bars),
        config.folds[0],
        config.instrument_id,
        0,
        calendar,
        coverage_proofs=proofs,
    )
    assert original.fingerprint == returned.fingerprint != correction.fingerprint
    assert materialized.steps[0].signal_bar == original
    assert materialized.outcome_bars[0] == returned
    assert result.metrics[0]["expectedBars"] == 6
    assert result.metrics[0]["correctedBars"]["numerator"] == 1
    assert result.metrics[0]["usableBySignalDeadline"]["numerator"] == 6


@pytest.mark.parametrize(
    "budget", [AssessmentBudget(max_findings=1), AssessmentBudget(max_publication_bytes=1)]
)
def test_budget_failure_never_publishes_positive_or_truncated_report(budget):
    config, _, frozen, calendar, _ = scenario()
    result = assess(config, replace(frozen, revisions=()), calendar, budget=budget)
    assert result.status == "FAILED" and result.eligibility == "UNKNOWN"
    assert result.failure_code == "BUDGET_EXHAUSTED" and not result.publication_complete
    assert not result.findings and not result.windows and not result.metrics
    with pytest.raises(ValueError, match="incomplete"):
        result.report_dict(uuid4())


def test_elapsed_deadline_and_metadata_budgets_fail_closed():
    config, _, frozen, calendar, proofs = scenario()
    times = iter([0, 300])
    assert (
        assess(
            config, frozen, calendar, coverage_proofs=proofs, clock=lambda: next(times)
        ).failure_code
        == "BUDGET_EXHAUSTED"
    )
    assert assess(config, frozen, calendar * 100001).failure_code == "BUDGET_EXHAUSTED"


def test_lost_fence_checkpoint_is_not_swallowed_as_a_successful_report():
    config, _, frozen, calendar, proofs = scenario()

    def lost():
        raise RuntimeError("fence lost")

    with pytest.raises(RuntimeError, match="fence lost"):
        assess(config, frozen, calendar, coverage_proofs=proofs, checkpoint=lost)


def test_snapshot_forbids_future_revision_and_does_not_diagnose_from_external_future_data():
    config, _, frozen, calendar, proofs = scenario()
    absent = replace(frozen, revisions=frozen.revisions[1:])
    result = assess(config, absent, calendar, coverage_proofs=proofs)
    assert "BAR_MISSING" in {f.category for f in result.findings}
    assert "OBSERVED_TOO_LATE" not in {f.category for f in result.findings}
    later = replace(frozen.revisions[0], commit_version=frozen.snapshot.version + 1)
    with pytest.raises(ValueError, match="outside committed snapshot"):
        replace(absent, revisions=(*absent.revisions, later))


def test_ambiguous_identity_and_changed_calendar_have_specific_categories():
    config, start, frozen, calendar, proofs = scenario()
    original = frozen.revisions[0]
    ambiguous = replace(
        original,
        revision_id=uuid4(),
        identity=replace(
            original.identity,
            interval=BarInterval.ONE_DAY,
            bar_end=original.identity.bar_end - timedelta(seconds=1),
        ),
    )
    result = assess(
        config,
        replace(frozen, revisions=(*frozen.revisions, ambiguous)),
        calendar,
        coverage_proofs=proofs,
    )
    assert "AMBIGUOUS_DATA" in {f.category for f in result.findings}
    with pytest.raises(DatasetBlocked, match="BLOCKED_AMBIGUOUS_DATA"):
        materialize_fold(
            replace(frozen, revisions=(*frozen.revisions, ambiguous)),
            config.folds[0],
            config.instrument_id,
            0,
            calendar,
            coverage_proofs=proofs,
        )
    short = {**calendar[0], "closes_at": start + timedelta(minutes=60)}
    extended = {
        **calendar[0],
        "session_id": uuid4(),
        "version": "c" * 64,
        "commit_version": 50,
        "ingested_at": start + timedelta(minutes=17),
    }
    changed = assess(config, frozen, (short, extended), coverage_proofs=proofs)
    assert "CALENDAR_CHANGED" in {f.category for f in changed.findings}


def test_dst_bracketed_holiday_and_early_close_follow_visible_calendar():
    config, _, frozen, calendar, proofs = scenario()
    ny = ZoneInfo("America/New_York")
    first_day, last_day = datetime(2026, 10, 30).date(), datetime(2026, 11, 27).date()
    bars, sessions = [], []
    day = first_day
    while day <= last_day:
        if day.weekday() < 5 and day != datetime(2026, 11, 26).date():
            opening = datetime.combine(
                day, datetime.min.time().replace(hour=9, minute=30), ny
            ).astimezone(UTC)
            close_hour = 13 if day == last_day else 16
            closing = datetime.combine(
                day, datetime.min.time().replace(hour=close_hour), ny
            ).astimezone(UTC)
            sessions.append(
                {
                    **calendar[0],
                    "session_id": uuid4(),
                    "session_date": day,
                    "opens_at": opening,
                    "closes_at": closing,
                }
            )
            cursor = opening
            while cursor < closing:
                end = cursor + timedelta(minutes=15)
                bars.append(
                    replace(
                        frozen.revisions[0],
                        revision_id=uuid4(),
                        identity=replace(
                            frozen.revisions[0].identity, bar_start=cursor, bar_end=end
                        ),
                        ingested_at=end + timedelta(seconds=20),
                        available_at=end + timedelta(seconds=20),
                        commit_version=len(bars) + 1,
                    )
                )
                cursor = end
        day += timedelta(days=1)
    start, end = bars[0].identity.bar_start, bars[-1].identity.bar_end
    config = replace(config, folds=(Fold(start - timedelta(hours=1), start, start, end),))
    snapshot = Snapshot(1000, end + timedelta(days=1))
    sessions = tuple({**row, "ingested_at": start - timedelta(days=1)} for row in sessions)
    proof = replace(
        proofs[0],
        start_at=start - timedelta(days=1),
        end_at=snapshot.at,
        certified_at=start - timedelta(days=2),
        ingested_at=start - timedelta(days=1),
    )
    result = assess(
        config, FrozenDataset(snapshot, tuple(bars)), sessions, coverage_proofs=(proof,)
    )
    assert result.eligibility == "ELIGIBLE"
    assert bars[0].identity.bar_start.hour == 13
    assert (
        next(b.identity.bar_start for b in bars if b.identity.bar_start.date().day == 2).hour == 14
    )
    assert bars[-1].identity.bar_end.hour == 18
    assert not [b for b in bars if b.identity.bar_start.date() == datetime(2026, 11, 26).date()]
    assert all(len(w.bars) == len(bars) for w in result.windows)


def test_result_findings_are_stable_sorted_and_public_projection_matches_contract():
    config, _, frozen, calendar, proofs = scenario()
    result = assess(
        config, replace(frozen, revisions=frozen.revisions[1:]), calendar, coverage_proofs=proofs
    )
    again = assess(
        config, replace(frozen, revisions=frozen.revisions[1:]), calendar, coverage_proofs=proofs
    )
    assert result.findings == again.findings
    assert list(result.findings) == sorted(
        result.findings, key=lambda f: (f.fold_index, f.area, f.start, f.category, f.finding_id)
    )
    root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load(
        (root / "contracts/http/control-api.openapi.yaml").read_text(encoding="utf-8")
    )
    uri = "https://agentic-trading-lab.invalid/readiness-domain"
    registry = Registry().with_resource(
        uri, Resource.from_contents(document, default_specification=DRAFT202012)
    )
    for name, value in [
        ("ReadinessReport", result.report_dict(uuid4())),
        *[("ReadinessFinding", f.to_dict()) for f in result.findings],
    ]:
        Draft202012Validator(
            {"$ref": uri + "#/components/schemas/" + name}, registry=registry
        ).validate(value)
    with pytest.raises(TypeError):
        result.metrics[0]["expectedBars"] = 999
    assert json.dumps(result.report_dict(uuid4()))


def test_trust_snapshot_factory_normalizes_storage_shape_and_rejects_unsafe_refs():
    config, _, _, _, proofs = scenario()
    dimensions = TrustDimensions().to_dict()
    for value in dimensions.values():
        value["evidenceRefs"] = value.pop("references")
    value = dict(
        version=3,
        policy_version="storage-v1",
        records=(
            dict(
                reference=str(proofs[0].coverage_id),
                kind="CORPORATE_ACTION",
                dimensions=dimensions,
                revoked=True,
            ),
        ),
    )
    snapshot = TrustSnapshot.from_mapping(value)
    assert (
        snapshot.gate(
            "CORPORATE_ACTIONS_REVISION", str(proofs[0].coverage_id), config.folds[0].test_start
        )
        == "EVIDENCE_REVOKED"
    )
    with pytest.raises(ValueError, match="safe opaque"):
        SafeReference("SOURCE", "https://private.invalid/secret")
    assert managed_trust_state(value["records"][0]) == "EVIDENCE_REVOKED"


class _Rows:
    def __init__(self, rows=(), scalar=False):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return iter(self.rows)

    def scalar_one(self):
        return self.scalar


class _PreMigrationConnection:
    def __init__(self, rows=()):
        self.rows = rows
        self.sql = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.sql.append(sql)
        if "to_regclass" in sql:
            return _Rows(scalar=False)
        assert "evidence_revisions" not in sql
        return _Rows(self.rows)


def test_readers_work_before_rdr03_without_importing_new_storage_modules(monkeypatch):
    import builtins

    config, start, frozen, calendar, _ = scenario()
    original_import = builtins.__import__

    def forbid(name, *args, **kwargs):
        if name == "packages.research.evidence_storage":
            raise AssertionError("RDR02 may not depend on RDR03 installation")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid)
    connection = _PreMigrationConnection()
    assert read_coverage(connection, config.instrument_id, frozen.snapshot) == ()
    assert (
        read_quality(
            connection, config.instrument_id, start, config.folds[0].test_end, frozen.snapshot
        )
        == ()
    )
    assert read_schedule_rows(_PreMigrationConnection(calendar), frozen.snapshot) == calendar


def test_normalized_read_adapters_keep_actual_ingestion_and_current_revocation(monkeypatch):
    import packages.backtesting.coverage as coverage

    config, start, frozen, _, proofs = scenario()
    ingested = start + timedelta(minutes=20)
    evidence_id = uuid4()
    row = dict(
        evidence_id=evidence_id,
        instrument_id=config.instrument_id,
        start_at=start,
        end_at=config.folds[0].test_end,
        source_observed_at=start - timedelta(days=1),
        fingerprint="d" * 64,
        commit_version=7,
        ingested_at=ingested,
        managed_trust_state="VERIFIED",
        statement=dict(conclusion="NO_ACTIONS"),
    )
    monkeypatch.setattr(coverage, "read_managed_evidence", lambda *args: (row,))
    imported = read_coverage(_PreMigrationConnection(), config.instrument_id, frozen.snapshot)
    assert imported[0].coverage_id == evidence_id
    assert imported[0].ingested_at == ingested and imported[0].commit_version == 7
    assert (
        covering_proof(
            imported,
            config.instrument_id,
            start,
            row["end_at"],
            start + timedelta(minutes=16),
            frozen.snapshot,
        )
        is None
    )
    assert (
        covering_proof(
            imported, config.instrument_id, start, row["end_at"], ingested, frozen.snapshot
        )
        == imported[0]
    )
    row["managed_trust_state"] = "EVIDENCE_REVOKED"
    revoked = read_coverage(_PreMigrationConnection(), config.instrument_id, frozen.snapshot)
    assert (
        covering_proof(
            revoked, config.instrument_id, start, row["end_at"], ingested, frozen.snapshot
        )
        is None
    )
    row["statement"] = dict(quality="VALID")
    quality = read_quality(
        _PreMigrationConnection(), config.instrument_id, start, row["end_at"], frozen.snapshot
    )
    assert quality[0]["observation_id"] == evidence_id
    assert not quality_at(quality, start, row["end_at"], ingested, frozen.snapshot)[0]


def test_partial_calendar_checks_known_regions_without_bridging_unknown_date():
    config, start, frozen, calendar, proofs = scenario()
    later = start + timedelta(days=2)
    extra = tuple(
        replace(
            bar,
            revision_id=uuid4(),
            identity=replace(
                bar.identity,
                bar_start=bar.identity.bar_start + timedelta(days=2),
                bar_end=bar.identity.bar_end + timedelta(days=2),
            ),
            ingested_at=bar.ingested_at + timedelta(days=2),
            available_at=bar.available_at + timedelta(days=2),
        )
        for bar in frozen.revisions
    )
    rows = (
        {**calendar[0], "closes_at": start + timedelta(minutes=90)},
        {
            **calendar[0],
            "session_id": uuid4(),
            "version": "c" * 64,
            "session_date": later.date(),
            "opens_at": later,
            "closes_at": later + timedelta(minutes=90),
        },
    )
    end = extra[-1].identity.bar_end
    config = replace(config, folds=(replace(config.folds[0], test_end=end),))
    snapshot = Snapshot(100, end + timedelta(days=1))
    proof = replace(proofs[0], end_at=snapshot.at)
    result = assess(
        config, FrozenDataset(snapshot, (*frozen.revisions, *extra)), rows, coverage_proofs=(proof,)
    )
    assert result.eligibility == "UNKNOWN"
    assert result.metrics[0]["unknownCalendarDays"] == 1
    assert result.metrics[0]["expectedBars"] == 12
    raw = [w for w in result.windows if (w.kind, w.scope) == ("BAR_OBSERVATION", "SIGNAL")]
    assert [len(w.bars) for w in raw] == [6, 6]
    assert raw[0].right_reason == "CALENDAR_UNKNOWN"
    assert raw[1].left_reason == "CALENDAR_UNKNOWN"
    assert not [w for w in result.windows if w.kind == "STRICT_PIT"]
    assert "CALENDAR_UNKNOWN" in {f.category for f in result.findings}


def test_late_calendar_never_moves_first_test_signal_to_repair_protocol():
    config, start, frozen, calendar, proofs = scenario()
    late = ({**calendar[0], "ingested_at": start + timedelta(minutes=16, microseconds=1)},)
    result = assess(config, frozen, late, coverage_proofs=proofs)
    assert result.eligibility == "UNKNOWN"
    assert not result.windows
    assert "BAR_MISSING" not in {f.category for f in result.findings}
    with pytest.raises(DatasetBlocked, match="BLOCKED_CALENDAR_UNKNOWN"):
        materialize_fold(
            frozen, config.folds[0], config.instrument_id, 0, late, coverage_proofs=proofs
        )


@pytest.mark.parametrize("late_field", ["ingested_at", "available_at"])
def test_max_of_available_and_ingested_remains_signal_gate(late_field):
    config, _, frozen, calendar, proofs = scenario()
    original = frozen.revisions[0]
    early = original.identity.bar_end + timedelta(seconds=20)
    late = original.identity.bar_end + timedelta(seconds=75)
    fields = dict(ingested_at=early, available_at=early)
    fields[late_field] = late
    bar = replace(
        original,
        availability_basis=AvailabilityBasis.VERIFIED_PUBLICATION,
        published_at=original.identity.bar_end + timedelta(seconds=10),
        **fields,
    )
    result = assess(
        config,
        replace(frozen, revisions=(bar, *frozen.revisions[1:])),
        calendar,
        coverage_proofs=proofs,
    )
    assert result.eligibility == "BLOCKED"
    assert "OBSERVED_TOO_LATE" in {finding.category for finding in result.findings}
    assert not any(
        bar.identity in window.bars
        for window in result.windows
        if (window.kind, window.scope) == ("BAR_OBSERVATION", "SIGNAL")
    )


def test_outcome_quality_does_not_leak_back_into_signal_knowledge():
    config, _, frozen, calendar, proofs = scenario()
    first = frozen.revisions[0]
    quality = (
        dict(
            observation_id=uuid4(),
            start_at=first.identity.bar_start,
            end_at=first.identity.bar_end,
            quality="INVALID",
            commit_version=20,
            ingested_at=first.identity.bar_end + timedelta(seconds=90),
        ),
    )
    result = assess(config, frozen, calendar, quality, proofs)
    assert result.eligibility == "BLOCKED"
    assert any(
        window.bars[0] == first.identity
        for window in result.windows
        if (window.kind, window.scope) == ("STRICT_PIT", "SIGNAL")
    )
    assert not any(
        first.identity in window.bars
        for window in result.windows
        if (window.kind, window.scope) == ("STRICT_PIT", "OUTCOME")
    )


def test_invalid_bar_quality_is_independent_of_missing_quality_observation_rows():
    config, _, frozen, calendar, proofs = scenario()
    invalid = replace(frozen.revisions[0], quality=QualityStatus.INVALID)
    result = assess(
        config,
        replace(frozen, revisions=(invalid, *frozen.revisions[1:])),
        calendar,
        coverage_proofs=proofs,
    )
    assert {"BAR_MISSING", "QUALITY_REJECTED"} <= {f.category for f in result.findings}
    assert result.trust.domain_quality.status == "REJECTED"


def test_publication_claim_and_boolean_commit_version_cannot_become_measurement_proof():
    _, _, frozen, _, _ = scenario()
    receipt = observed(frozen.revisions[0])
    with pytest.raises(ValueError, match="independent publication"):
        replace(receipt, provider_published_at=receipt.identity.bar_end)
    with pytest.raises(ValueError, match="typed commit"):
        replace(receipt, commit_version=True)


def test_warmup_calendar_unknown_is_unknown_not_invented_missing_warmup():
    config, start, frozen, calendar, proofs = scenario()
    fold = Fold(
        start - timedelta(days=1),
        start + timedelta(minutes=30),
        start + timedelta(minutes=30),
        config.folds[0].test_end,
    )
    config = replace(
        config, strategy_id="sma-cross-v1", parameters=(("fast", 2), ("slow", 3)), folds=(fold,)
    )
    result = assess(config, frozen, calendar, coverage_proofs=proofs)
    assert result.eligibility == "UNKNOWN"
    assert "CALENDAR_UNKNOWN" in {finding.category for finding in result.findings}
    assert result.metrics[0]["unknownCalendarDays"] == 0
    assert not [window for window in result.windows if window.kind == "STRICT_PIT"]


def test_window_budget_fails_closed_instead_of_truncating_two_valid_runs():
    config, _, frozen, calendar, proofs = scenario()
    bars = list(frozen.revisions)
    bars[2] = replace(
        bars[2],
        ingested_at=bars[2].identity.bar_end + timedelta(seconds=90),
        available_at=bars[2].identity.bar_end + timedelta(seconds=90),
    )
    result = assess(
        config,
        replace(frozen, revisions=tuple(bars)),
        calendar,
        coverage_proofs=proofs,
        budget=AssessmentBudget(max_windows_per_kind=1),
    )
    assert result.failure_code == "BUDGET_EXHAUSTED" and not result.publication_complete
