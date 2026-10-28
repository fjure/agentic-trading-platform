"""Independent qualitative gates, exact Decimal thresholds and report contracts."""

from copy import copy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from uuid import UUID

import pytest

from packages.backtesting.dataset import DatasetBlocked
from packages.backtesting.models import fingerprint
from packages.backtesting.reliability import (
    DIMENSIONS,
    ReliabilityBudget,
    ReliabilityLicenseReceipt,
    assess_reliability,
    check_current_license,
)
from packages.backtesting.retrospective import (
    RetrospectiveDataset,
    RetrospectiveEvidenceReference,
    RetrospectiveManifest,
    RetrospectiveTrustContext,
    RetrospectiveTrustRecord,
    materialize_retrospective_fold,
)
from tests.contracts.test_retrospective_contracts import validator
from tests.unit.test_retrospective_dataset import fixture


def evidence(
    config,
    snapshot,
    number=1,
    *,
    conclusion="NO_ACTIONS",
    actions=(),
    kind="CORPORATE_ACTION",
    **changes,
):
    fold = config.folds[0]
    statement = dict(type="CORPORATE_ACTION_COVERAGE", conclusion=conclusion, actions=list(actions))
    if kind == "QUALITY":
        statement = dict(
            quality="VALID", reason="NORMALIZED_RULES_PASSED", ruleVersion="research-quality-v1"
        )
    return dict(
        evidence_id=UUID(int=900 + number),
        source_id="localtest",
        evidence_key=f"proof{number}",
        kind=kind,
        instrument_id=config.instrument_id,
        start_at=fold.training_start,
        end_at=fold.test_end,
        revision=1,
        commit_version=10,
        ingested_at=snapshot.at,
        fingerprint=fingerprint((number, statement)),
        statement=statement,
        **changes,
    )


def prepared(
    *,
    warmup=2,
    bars_change=None,
    calendar_change=None,
    action_factory=None,
    license_status="UNKNOWN",
    bind=True,
    quality_factory=None,
    fixture_factory=fixture,
):
    config, snapshot, bars, calendar = fixture_factory(warmup)
    if bars_change:
        bars = bars_change(bars)
    if calendar_change:
        calendar = calendar_change(calendar)
    actions = action_factory(config, snapshot) if action_factory else ()
    qualities = quality_factory(config, snapshot) if quality_factory else ()
    records = tuple(
        RetrospectiveTrustRecord(
            str(row["evidence_id"]),
            tuple(
                (name, "UNKNOWN" if name == "temporalAuthenticity" else "VERIFIED")
                for name in (
                    "integrity",
                    "sourceAuthenticity",
                    "temporalAuthenticity",
                    "completeness",
                    "domainQuality",
                )
            ),
        )
        for row in (*actions, *qualities)
    )
    trust = RetrospectiveTrustContext(snapshot, "b" * 64, records)
    receipt = ReliabilityLicenseReceipt.from_row(
        dict(
            assessment_id=UUID(int=700),
            provider="alpaca",
            status=license_status,
            recorded_at=snapshot.at,
            fingerprint="d" * 64,
        )
    )
    dataset = RetrospectiveDataset(snapshot, bars)
    manifest = None
    if bind:
        try:
            fold = materialize_retrospective_fold(
                dataset,
                config.folds[0],
                config.instrument_id,
                warmup,
                calendar,
                trust_context=trust,
            )

            def refs(rows):
                return tuple(
                    RetrospectiveEvidenceReference(
                        row["evidence_id"],
                        row["fingerprint"],
                        row["revision"],
                        row["commit_version"],
                        row["ingested_at"],
                    )
                    for row in rows
                    if row["commit_version"] <= snapshot.version
                    and row["ingested_at"] <= snapshot.at
                )

            manifest = RetrospectiveManifest(
                config,
                snapshot,
                (fold,),
                trust,
                "c" * 40,
                receipt.fingerprint,
                quality_references=refs(qualities),
                corporate_action_references=refs(actions),
            )
        except DatasetBlocked:
            pass
    return dict(
        config=config,
        dataset=dataset,
        calendar_rows=calendar,
        trust_context=trust,
        license_receipt=receipt,
        code_version="c" * 40,
        manifest=manifest,
        corporate_action_rows=actions,
        quality_rows=qualities,
    )


def codes(result):
    return {finding.code for finding in result.findings}


def report(result, **kwargs):
    return result.to_dict(report_id=UUID(int=800), assessment_id=UUID(int=700), **kwargs)


def test_unknown_actions_and_license_are_honest_exploratory_dimensions_and_complete_contract():
    inputs = prepared()
    result = assess_reliability(**inputs)
    assert result.overall_status == "EXPLORATORY_WITH_LIMITATIONS"
    assert result.corporate_action_status == "UNKNOWN"
    wire = report(result)
    assert set(wire["dimensions"]) == set(DIMENSIONS)
    assert wire["dimensions"]["CORPORATE_ACTIONS"] == dict(
        rating="UNKNOWN", reasonCodes=["CORPORATE_ACTIONS_UNKNOWN"]
    )
    assert wire["licenseStatus"] == "UNKNOWN"
    assert wire["historicalAvailabilityStatus"] == "NOT_PROVEN"
    assert set(wire["resultLimitations"]) == {
        "NOT_STRICT_PIT",
        "PRICE_ONLY",
        "DIVIDENDS_EXCLUDED",
        "IEX_PARTIAL_MARKET",
        "SURVIVORSHIP_BIAS",
        "EX_POST_REVISIONS",
        "NO_TRADING_AUTHORIZATION",
    }
    assert wire["methods"]["benchmarks"] == ["flat-v1", "buy-hold-v1"]
    assert wire["methods"]["protocol"]["parameters"] == dict(fast=2, slow=3)
    assert wire["methods"]["manifestHash"] == inputs["manifest"].manifest_hash
    assert wire["methods"]["assumptions"]["dividendsIncluded"] is False
    assert "outcomeCutoffSeconds" not in wire["methods"]["assumptions"]
    assert not any(
        key in wire for key in ("score", "netReturn", "profitability", "eligibleForTrading")
    )
    for standalone in (False, True):
        validator("BacktestReliabilityReportV1", standalone).validate(wire)
        validator("BacktestReliabilityReportV1", standalone).validate(
            report(result, phase="FINAL", run_id=UUID(int=801))
        )
        for item in result.findings:
            validator("BacktestReliabilityFinding", standalone).validate(item.to_dict())
        for item in result.revision_references:
            validator("BacktestReliabilityReference", standalone).validate(item.to_dict())
    counts = wire["folds"][0]["barCounts"]
    assert counts["warmup"] == dict(expected=2, present=2, missing=0, unexpected=0)
    assert counts["test"] == dict(expected=4, present=4, missing=0, unexpected=0)
    assert counts["training"] == dict(expected=2, present=2, missing=0, unexpected=0)


def test_verified_no_actions_is_price_only_and_unknown_never_becomes_no_actions():
    clean = assess_reliability(**prepared(action_factory=lambda c, s: (evidence(c, s),)))
    assert clean.corporate_action_status == "NO_ACTIONS"
    assert report(clean)["dimensions"]["CORPORATE_ACTIONS"]["rating"] == "PASS"
    unknown = assess_reliability(
        **prepared(action_factory=lambda c, s: (evidence(c, s, conclusion="UNKNOWN"),))
    )
    assert unknown.corporate_action_status == "UNKNOWN"
    assert unknown.overall_status == "EXPLORATORY_WITH_LIMITATIONS"
    assert "PRICE_ONLY" in report(clean)["resultLimitations"]


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("SPLIT", {"ratio": "2"}),
        ("SPLIT", {"ratio": "0.1"}),
        ("REVERSE_SPLIT", {"ratio": "0.1"}),
        ("SPIN_OFF", {}),
        ("MERGER", {}),
    ],
)
def test_known_structural_actions_block_every_strategy_even_without_price_jump(kind, fields):
    def actions(config, snapshot):
        action = dict(
            type=kind,
            effectiveAt=config.folds[0].test_start.isoformat().replace("+00:00", "Z"),
            **fields,
        )
        return (evidence(config, snapshot, conclusion="ACTIONS_PRESENT", actions=(action,)),)

    inputs = prepared(action_factory=actions)
    for strategy, parameters in (
        ("sma-cross-v1", (("fast", 2), ("slow", 3))),
        ("flat-v1", ()),
        ("buy-hold-v1", ()),
    ):
        changed = dict(
            inputs,
            config=replace(inputs["config"], strategy_id=strategy, parameters=parameters),
            manifest=None,
        )
        result = assess_reliability(**changed)
        assert result.overall_status == "BLOCKED"
        assert result.corporate_action_status == "STRUCTURAL_ACTION"
        assert "STRUCTURAL_CORPORATE_ACTION" in codes(result)


def test_cash_dividend_is_read_from_actions_and_warns_without_becoming_structural():
    def actions(config, snapshot):
        return (
            evidence(
                config,
                snapshot,
                conclusion="ACTIONS_PRESENT",
                actions=(
                    dict(
                        type="CASH_DIVIDEND",
                        amount="1.25",
                        currency="USD",
                        effectiveAt=config.folds[0].test_start.isoformat().replace("+00:00", "Z"),
                    ),
                ),
            ),
        )

    inputs = prepared(action_factory=actions)
    # Legacy no_actions would be False for this row; direct typed action must win.
    result = assess_reliability(**inputs)
    assert result.corporate_action_status == "DIVIDENDS_EXCLUDED"
    assert result.overall_status == "EXPLORATORY_WITH_LIMITATIONS"
    assert "DIVIDENDS_EXCLUDED" in codes(result)
    assert "STRUCTURAL_CORPORATE_ACTION" not in codes(result)


def test_conflicting_action_claims_block_and_new_positive_cannot_hide_old_negative():
    def rows(config, snapshot):
        action = dict(
            type="SPLIT",
            ratio="2",
            effectiveAt=config.folds[0].test_start.isoformat().replace("+00:00", "Z"),
        )
        return (
            evidence(config, snapshot, 1, conclusion="ACTIONS_PRESENT", actions=(action,)),
            evidence(config, snapshot, 2),
        )

    result = assess_reliability(**prepared(action_factory=rows))
    assert result.corporate_action_status == "CONFLICT"
    assert {"STRUCTURAL_CORPORATE_ACTION", "CORPORATE_ACTION_CONFLICT"} <= codes(result)
    assert result.overall_status == "BLOCKED"


@pytest.mark.parametrize(
    "opening,blocked",
    [
        ("119.999", False),
        ("120", True),
        ("120.001", True),
        ("80.001", False),
        ("80", True),
        ("79.999", True),
    ],
)
def test_raw_discontinuity_exact_19999_20_20001_percent_in_both_directions(opening, blocked):
    def change(bars):
        other = Decimal(opening)
        return (
            *bars[:3],
            replace(
                bars[3], open=other, high=max(other, bars[3].high), low=min(other, bars[3].low)
            ),
            *bars[4:],
        )

    inputs = prepared(bars_change=change)
    with localcontext() as caller:
        caller.prec = 5
        result = assess_reliability(**inputs)
    assert ("RAW_DISCONTINUITY" in codes(result)) is blocked
    assert result.overall_status == ("BLOCKED" if blocked else "EXPLORATORY_WITH_LIMITATIONS")
    assert result.corporate_action_status == "UNKNOWN"


@pytest.mark.parametrize("index", (0, 2, 5))
def test_missing_warmup_signal_fill_or_valuation_bar_blocks_without_interpolation(index):
    result = assess_reliability(
        **prepared(bars_change=lambda bars: bars[:index] + bars[index + 1 :])
    )
    assert result.overall_status == "BLOCKED"
    assert "MISSING_BARS" in codes(result)
    counts = dict(result.folds[0].counts)
    assert counts["warmup" if index < 2 else "test"].missing == 1


def test_unknown_calendar_has_no_invented_denominator_and_schema_valid_blocked_diagnostics():
    result = assess_reliability(**prepared(calendar_change=lambda rows: ()))
    assert result.overall_status == "BLOCKED"
    assert "CALENDAR_UNKNOWN" in codes(result)
    for _name, counts in result.folds[0].counts:
        assert counts.expected is counts.missing is None
    assert result.corporate_action_status == "NOT_EVALUATED"
    assert report(result)["dimensions"]["CORPORATE_ACTIONS"]["rating"] == "NOT_EVALUATED"
    validator("BacktestReliabilityReportV1").validate(report(result))


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("feed", "sip", "MIXED_FEED"),
        ("provider", "other", "MIXED_FEED"),
        ("adjustment", "split", "MIXED_ADJUSTMENT"),
        ("instrument_id", UUID(int=999), "INSTRUMENT_UNRESOLVED"),
    ],
)
def test_mixed_series_block_without_normalizing_identity(field, value, code):
    class UnsupportedAdjustment(StrEnum):
        SPLIT = "split"

    def changed(bars):
        identity = copy(bars[0].identity)
        object.__setattr__(
            identity, field, UnsupportedAdjustment.SPLIT if field == "adjustment" else value
        )
        return (replace(bars[0], identity=identity), *bars[1:])

    result = assess_reliability(**prepared(bars_change=changed))
    assert result.overall_status == "BLOCKED"
    assert code in codes(result)


def test_invalid_ohlcv_and_negative_quality_evidence_cannot_be_hidden_by_later_valid_claim():
    def corrupted(bars):
        first = copy(bars[0])
        object.__setattr__(first, "high", Decimal("1"))
        return (first, *bars[1:])

    result = assess_reliability(**prepared(bars_change=corrupted, bind=False))
    assert "INVALID_OHLCV" in codes(result)

    def rows(config, snapshot):
        invalid = evidence(config, snapshot, kind="QUALITY")
        invalid["statement"] = dict(
            quality="INVALID", ruleVersion="research-quality-v1", reason="INVALID_OHLC"
        )
        return (invalid, evidence(config, snapshot, 2, kind="QUALITY"))

    result = assess_reliability(**prepared(quality_factory=rows))
    assert result.overall_status == "BLOCKED"
    assert "INVALID_OHLCV" in codes(result)


@pytest.mark.parametrize(
    "status,blocks,normalized",
    [
        ("UNKNOWN", False, "UNKNOWN"),
        ("CONFIRMED", False, "DECLARED_CONFIRMED"),
        ("BLOCKED", True, "DENIED"),
        ("DENIED", True, "DENIED"),
    ],
)
def test_license_mapping_and_current_gate_preserve_real_unknowns(status, blocks, normalized):
    result = assess_reliability(**prepared(license_status=status))
    assert report(result)["licenseStatus"] == normalized
    assert (result.overall_status == "BLOCKED") is blocks
    before = result.report_hash
    decision = check_current_license(status)
    assert decision.status == normalized
    assert decision.blocks_new_execution is blocks
    assert check_current_license(None).blocks_new_execution
    assert check_current_license(None).code == "TRUST_UNAVAILABLE"
    assert result.report_hash == before


def test_missing_receipt_or_unbound_manifest_cannot_produce_positive_preview():
    inputs = prepared()
    missing = assess_reliability(**dict(inputs, license_receipt=None))
    assert missing.overall_status == "BLOCKED"
    assert "TRUST_UNAVAILABLE" in codes(missing)
    unbound = assess_reliability(**dict(inputs, manifest=None))
    assert unbound.overall_status == "BLOCKED"
    assert report(unbound)["methods"]["manifestHash"] is None
    assert "REFERENCE_MISSING" in codes(unbound)


def test_manifest_cannot_bind_an_old_head_while_diagnostics_assess_a_newer_revision():
    inputs = prepared()
    original = inputs["dataset"].revisions[2]
    corrected = replace(
        original,
        revision_id=UUID(int=9999),
        local_revision=2,
        predecessor_id=original.revision_id,
        close=Decimal("101"),
        high=Decimal("101"),
    )
    dataset = replace(inputs["dataset"], revisions=(*inputs["dataset"].revisions, corrected))
    result = assess_reliability(**dict(inputs, dataset=dataset))
    assert result.overall_status == "BLOCKED"
    assert result.manifest_hash is None
    assert "REFERENCE_MISSING" in codes(result)


@pytest.mark.parametrize(
    "budget",
    [
        ReliabilityBudget(max_rows=1),
        ReliabilityBudget(max_findings=1),
        ReliabilityBudget(max_publication_bytes=1),
    ],
)
def test_exhausted_budgets_leave_checks_not_evaluated_and_never_positive(budget):
    result = assess_reliability(**prepared(), budget=budget)
    assert result.overall_status == "BLOCKED"
    assert result.evaluation_complete is False
    assert result.corporate_action_status == "NOT_EVALUATED"
    assert all(
        rating == "NOT_EVALUATED"
        for name, rating, _ in result.dimensions
        if name not in ("LICENSE", "HISTORICAL_AVAILABILITY")
    )
    assert dict((name, rating) for name, rating, _ in result.dimensions)["LICENSE"] == "UNKNOWN"
    assert codes(result) == {"BUDGET_EXHAUSTED"}
    validator("BacktestReliabilityReportV1").validate(report(result))


def test_same_snapshot_hash_ignores_runtime_identity_phase_and_current_warnings():
    inputs = prepared()
    result = assess_reliability(**inputs)
    assert assess_reliability(**inputs) == result
    assert assess_reliability(**inputs).report_hash == result.report_hash
    changed_ids = result.to_dict(
        report_id=UUID(int=1000), assessment_id=UUID(int=1001), phase="FINAL", run_id=UUID(int=1002)
    )
    assert changed_ids["reportHash"] == report(result)["reportHash"]
    assert check_current_license("DENIED").blocks_new_execution
    assert assess_reliability(**inputs).report_hash == result.report_hash
    with pytest.raises(FrozenInstanceError):
        result.corporate_action_status = "NO_ACTIONS"


def test_session_conflict_and_revision_conflict_block_without_uuid_tiebreak():
    def calendars(rows):
        return (
            *rows,
            dict(
                rows[0],
                session_id=UUID(int=999),
                version="e" * 64,
                closes_at=rows[0]["closes_at"] - timedelta(minutes=15),
            ),
        )

    result = assess_reliability(**prepared(calendar_change=calendars))
    assert result.overall_status == "BLOCKED"
    assert "CALENDAR_UNKNOWN" in codes(result) or "SESSION_CONFLICT" in codes(result)
    result = assess_reliability(
        **prepared(
            bars_change=lambda bars: (
                *bars,
                replace(
                    bars[0], revision_id=UUID(int=999), close=Decimal("101"), high=Decimal("101")
                ),
            )
        )
    )
    assert result.overall_status == "BLOCKED"
    assert "REVISION_CONFLICT" in codes(result)


def calendar_fixture(first_open, second_open, second_count=26, opening="100"):
    """Independent supplied-session oracle: 26 regular bars, optional 14-bar early close."""

    def make(warmup):
        config, snapshot, sample, calendar = fixture(warmup)
        end = second_open + timedelta(minutes=15 * second_count)
        observed = end + timedelta(days=1)
        snapshot = replace(snapshot, at=observed)
        bars, sessions = [], []
        for index, (start, count) in enumerate(((first_open, 26), (second_open, second_count))):
            sessions.append(
                dict(
                    calendar[0],
                    session_id=UUID(int=30 + index),
                    session_date=start.date(),
                    opens_at=start,
                    closes_at=start + timedelta(minutes=15 * count),
                    ingested_at=observed,
                )
            )
            for offset in range(count):
                at = start + timedelta(minutes=15 * offset)
                price = Decimal(opening) if index == 1 and offset == 0 else Decimal("100")
                bars.append(
                    replace(
                        sample[0],
                        revision_id=UUID(int=100 + len(bars)),
                        identity=replace(
                            sample[0].identity, bar_start=at, bar_end=at + timedelta(minutes=15)
                        ),
                        open=price,
                        high=max(price, Decimal("100")),
                        low=min(price, Decimal("100")),
                        ingested_at=observed,
                        available_at=observed,
                    )
                )
        fold = replace(
            config.folds[0],
            training_start=first_open,
            training_end=second_open,
            test_start=second_open,
            test_end=end,
        )
        return replace(config, folds=(fold,)), snapshot, tuple(bars), tuple(sessions)

    return make


@pytest.mark.parametrize(
    "first,second,count",
    [
        (datetime(2026, 10, 30, 13, 30, tzinfo=UTC), datetime(2026, 11, 2, 14, 30, tzinfo=UTC), 26),
        (
            datetime(2026, 11, 25, 14, 30, tzinfo=UTC),
            datetime(2026, 11, 27, 14, 30, tzinfo=UTC),
            14,
        ),
    ],
)
def test_supplied_calendar_counts_dst_holiday_and_early_close_without_artificial_gaps(
    first, second, count
):
    inputs = prepared(fixture_factory=calendar_fixture(first, second, count))
    result = assess_reliability(**inputs)
    assert result.overall_status == "EXPLORATORY_WITH_LIMITATIONS"
    counts = dict(result.folds[0].counts)
    assert counts["test"].to_dict() == dict(expected=count, present=count, missing=0, unexpected=0)
    assert counts["training"].expected == 26
    assert counts["warmup"].expected == 2


@pytest.mark.parametrize(
    "opening,blocked",
    [
        ("119.999", False),
        ("120", True),
        ("120.001", True),
        ("80.001", False),
        ("80", True),
        ("79.999", True),
    ],
)
def test_overnight_discontinuity_threshold_is_inclusive_in_both_directions(opening, blocked):
    first, second = (datetime(2026, 10, day, 13, 30, tzinfo=UTC) for day in (19, 20))
    result = assess_reliability(
        **prepared(fixture_factory=calendar_fixture(first, second, opening=opening))
    )
    assert ("RAW_DISCONTINUITY" in codes(result)) is blocked
    assert (result.overall_status == "BLOCKED") is blocked


def test_corporate_action_entries_cannot_bypass_row_budget_or_checkpoint():
    def rows(config, snapshot):
        action = dict(
            type="CASH_DIVIDEND",
            amount="1",
            currency="USD",
            effectiveAt=config.folds[0].test_start.isoformat(),
        )
        return (evidence(config, snapshot, conclusion="ACTIONS_PRESENT", actions=(action,) * 20),)

    result = assess_reliability(
        **prepared(action_factory=rows), budget=ReliabilityBudget(max_rows=10)
    )
    assert result.overall_status == "BLOCKED"
    assert result.evaluation_complete is False
    assert "BUDGET_EXHAUSTED" in codes(result)
