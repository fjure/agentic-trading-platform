"""D-07: fixed synthetic corpus; no labeled outcomes or live provider access."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from packages.decisions.baseline import BaselineConfig, input_hash
from packages.decisions.replay import (
    CachedResponse,
    ReplayCase,
    ReplayConfig,
    compare,
    replay,
)
from packages.domain.models import (
    Choice,
    EvidenceBundle,
    Market,
    MarketBar,
    MarketState,
    PortfolioSnapshot,
    Position,
    Quality,
    Reconciliation,
)

NOW = datetime(2026, 10, 12, 14, 0, 10, tzinfo=UTC)
CONFIG = ReplayConfig("rules.v1", BaselineConfig())


def state(index: int = 0) -> MarketState:
    end = NOW - timedelta(seconds=10)
    bar = MarketBar(f"bar-{index}", "SPY", end - timedelta(minutes=15), end,
                    Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"),
                    Decimal("1000"), end, end, end, Quality.VALID)
    evidence = EvidenceBundle(f"evidence-{index}", "SPY", ("fixture",), end,
                              (), Quality.VALID)
    portfolio = PortfolioSnapshot(f"portfolio-{index}", "USD", Decimal("10000"),
                                  Decimal("10000"), Decimal("0"), NOW, 1, Quality.VALID,
                                  Reconciliation.CURRENT)
    position = Position(f"position-{index}", "SPY", "USD", Decimal("0"), None,
                        Decimal("0"), 1)
    return MarketState(f"state-{index}", "SPY", Market.US_EQUITY, bar, evidence,
                       portfolio, position,
                       {"last_close": Decimal("101"), "previous_close": Decimal("100"),
                        "window_volume": Decimal("1000")}, Decimal("0.02"),
                       end, end, end, NOW, Quality.VALID, "a" * 32)


def corpus() -> tuple[ReplayCase, ...]:
    """100 stable versioned cases, including stale, conflicted and incomplete states."""
    cases = []
    for index in range(100):
        source = state(index)
        if index % 10 == 0:
            source = replace(source, quality=Quality.STALE)
        elif index % 10 == 1:
            source = replace(source, evidence=replace(source.evidence,
                                                       conflicts=("synthetic",)))
        elif index % 10 == 2:
            source = replace(source, features={"last_close": Decimal("101")})
        cases.append(ReplayCase(f"corpus.v1-{index:03}", source, NOW))
    return tuple(cases)


def test_100_case_replay_stability_and_coverage() -> None:
    cases = corpus()
    reports = [replay(cases, CONFIG) for _ in range(3)]
    assert reports[0] == reports[1] == reports[2]
    assert reports[0].metrics.total == 100
    assert reports[0].metrics.schema_failures == 0
    assert reports[0].metrics.abstention_rate >= Decimal("0.3")
    assert reports[0].metrics.latency_p95_ms == 0
    assert reports[0].metrics.calibration is None
    assert reports[0].metrics.reported_api_units is None
    assert reports[0].metrics.reported_estimated_cost is None
    assert all(r.result.choice is Choice.ABSTAIN for i, r in enumerate(reports[0].records)
               if i % 10 in (0, 1, 2))
    assert compare(reports[0], reports[1]).differences == ()
    with localcontext() as context:
        context.prec = 6
        assert replay(cases, CONFIG) == reports[0]


def test_correction_and_config_change_preserve_prior_corpus() -> None:
    first = ReplayCase("corpus.v1-000", state(), NOW)
    corrected = ReplayCase("corpus.v2-000", replace(
        first.state, bar=replace(first.state.bar, version=2)), NOW)
    old = replay((first,), CONFIG)
    newer = replay((corrected,), CONFIG)
    assert old.records[0].result.input_hash != newer.records[0].result.input_hash
    assert replay((first,), CONFIG) == old
    altered = replay((first,), ReplayConfig("rules.v2", CONFIG.rules))
    diff = compare(old, altered)
    assert diff.left_config_hash != diff.right_config_hash
    assert diff.differences[0].changed_fields
    assert diff.left_coverage == diff.right_coverage == ("corpus.v1-000",)


@pytest.mark.parametrize("part", ["state", "bar", "evidence", "portfolio"])
def test_future_nested_evidence_is_rejected_before_execution(part: str) -> None:
    source = state()
    future = NOW + timedelta(seconds=1)
    if part == "state":
        source = replace(source, as_of=future)
    elif part == "bar":
        source = replace(source, bar=replace(source.bar, received_at=future))
    elif part == "evidence":
        source = replace(source, evidence=replace(source.evidence, available_at=future),
                         as_of=future)
    else:
        source = replace(source, portfolio=replace(source.portfolio, as_of=future))
    with pytest.raises(ValueError, match="future replay evidence"):
        ReplayCase("future", source, NOW)


@pytest.mark.parametrize("failure,reason", [
    ("timeout", "ABSTAIN_UNAVAILABLE"),
    ("missing_response", "ABSTAIN_INVALID_RESPONSE"),
    ("invalid_response", "ABSTAIN_INVALID_RESPONSE"),
])
def test_mock_failures_are_safe_abstentions(failure: str, reason: str) -> None:
    report = replay((ReplayCase("fault", state(), NOW),), CONFIG,
                    source="mock", failure=failure)
    result = report.records[0].result
    assert result.choice is Choice.ABSTAIN and result.reason_code == reason
    assert result.typed_answers is result.confidence is None
    assert report.metrics.failure_counts == ((reason, 1),)
    assert report.metrics.schema_failures == (reason == "ABSTAIN_INVALID_RESPONSE")


def test_cached_response_binding_expiry_and_full_payload() -> None:
    case = ReplayCase("one", state(), NOW)
    captured = replay((case,), CONFIG).records[0].result
    captured = replace(captured, latency_ms=120, api_units=Decimal("3"),
                       estimated_cost=Decimal("0.002"))
    cached = replay((case,), CONFIG, source="cached",
                    cached={"one": CachedResponse(captured)})
    assert cached.records[0].result == captured
    assert cached.metrics.reported_api_units == Decimal("3")
    assert cached.metrics.reported_estimated_cost == Decimal("0.002")
    assert cached.metrics.latency_p95_ms == 120
    assert cached.records[0].result.typed_answers is not None
    for changed in (
        replace(captured, input_hash="0" * 64),
        replace(captured, question_version="questions.v2"),
        replace(captured, prompt_version="prompt.v2"),
        replace(captured, config_version="rules.v2"),
        replace(captured, model_id="model.v2"),
        replace(captured, valid_until=NOW),
    ):
        with pytest.raises(ValueError, match="cached response binding or expiry mismatch"):
            replay((case,), CONFIG, source="cached",
                   cached={"one": CachedResponse(changed)})
    with pytest.raises(ValueError, match="binding"):
        replay((ReplayCase("one", replace(state(), bar=replace(state().bar, version=2)), NOW),),
               CONFIG, source="cached", cached={"one": CachedResponse(captured)})


def test_cached_expiry_must_fit_configured_ttl_even_for_abstention() -> None:
    case = ReplayCase("one", state(), NOW)
    accepted = replay((case,), CONFIG).records[0].result
    ttl = CONFIG.rules.decision_ttl_seconds
    assert replay((case,), CONFIG, source="cached",
                  cached={"one": CachedResponse(replace(
                      accepted, valid_until=NOW + timedelta(seconds=ttl)))}
                  ).records[0].result.valid_until == NOW + timedelta(seconds=ttl)
    abstained = replay((case,), CONFIG, source="mock",
                       failure="timeout").records[0].result
    for response in (accepted, abstained):
        for expiry in (NOW + timedelta(seconds=ttl + 1),
                       datetime(2099, 1, 1, tzinfo=UTC)):
            with pytest.raises(ValueError, match="binding or expiry"):
                replay((case,), CONFIG, source="cached",
                       cached={"one": CachedResponse(replace(
                           response, valid_until=expiry))})


def test_only_local_sources_are_admitted() -> None:
    case = ReplayCase("one", state(), NOW)
    with pytest.raises(ValueError, match="unsupported replay source"):
        replay((case,), CONFIG, source="external")
    with pytest.raises(ValueError, match="coverage mismatch"):
        replay((case,), CONFIG, source="cached", cached={})


@pytest.mark.parametrize("defect", ["stale", "conflict", "incomplete"])
def test_cached_long_cannot_bypass_input_admission(defect: str) -> None:
    good = ReplayCase("one", state(), NOW)
    captured = replay((good,), CONFIG).records[0].result
    source = good.state
    if defect == "stale":
        source = replace(source, quality=Quality.STALE)
    elif defect == "conflict":
        source = replace(source, evidence=replace(source.evidence, conflicts=("late",)))
    else:
        source = replace(source, features={"last_close": Decimal("101")})
    case = ReplayCase("one", source, NOW)
    forged = replace(captured, input_hash=input_hash(source, CONFIG.rules, CONFIG.version))
    with pytest.raises(ValueError, match="unsafe"):
        replay((case,), CONFIG, source="cached", cached={"one": CachedResponse(forged)})


def test_cached_direction_must_meet_confidence_and_distribution_rules() -> None:
    case = ReplayCase("one", state(), NOW)
    captured = replay((case,), CONFIG).records[0].result
    wrong = (
        replace(captured, confidence=Decimal("0.6")),
        replace(captured, distribution={"LONG": Decimal("0.1"),
                                        "FLAT": Decimal("0.8"),
                                        "ABSTAIN": Decimal("0.1")}),
    )
    for response in wrong:
        with pytest.raises(ValueError, match="unsafe"):
            replay((case,), CONFIG, source="cached",
                   cached={"one": CachedResponse(response)})


def test_expected_concrete_provider_model_is_bound_without_invocation() -> None:
    case = ReplayCase("one", state(), NOW)
    captured = replay((case,), CONFIG).records[0].result
    provider_config = ReplayConfig("rules.v1", CONFIG.rules,
                                   expected_model_id="captured-provider-model.v7")
    authentic = replace(captured, model_id="captured-provider-model.v7")
    report = replay((case,), provider_config, source="cached",
                    cached={"one": CachedResponse(authentic)})
    assert report.records[0].result.model_id == "captured-provider-model.v7"
    assert report.config_hash != replay((case,), CONFIG).config_hash
    assert compare(report, report).left_versions.model_ids == ("captured-provider-model.v7",)
    with pytest.raises(ValueError, match="binding"):
        replay((case,), provider_config, source="cached",
               cached={"one": CachedResponse(captured)})


def test_semantic_stability_tracks_technical_changes_separately() -> None:
    case = ReplayCase("one", state(), NOW)
    first = replay((case,), CONFIG)
    captured = replace(first.records[0].result, id="captured-id", latency_ms=240,
                       api_units=Decimal("5"), estimated_cost=Decimal("0.01"))
    second = replay((case,), CONFIG, source="cached",
                    cached={"one": CachedResponse(captured)})
    assert first.records[0].semantic_hash == second.records[0].semantic_hash
    assert first.records[0].response_hash != second.records[0].response_hash
    diff = compare(first, second)
    assert diff.same_corpus
    assert diff.differences[0].changed_fields == ()
    assert diff.differences[0].technical_changed_fields == (
        "api_units", "estimated_cost", "id", "latency_ms")
    assert second.records[0].result == captured


def test_corpus_order_visible_and_normal_mock_comparison() -> None:
    cases = corpus()
    baseline = replay(cases, CONFIG)
    mock = replay(cases, CONFIG, source="mock")
    normal = compare(baseline, mock)
    assert normal.same_corpus and normal.differences == ()
    assert normal.left_versions == normal.right_versions
    assert normal.left_versions.config_versions == (CONFIG.version,)
    assert len(normal.left_versions.input_hashes) == 100
    reordered = compare(baseline, replay(tuple(reversed(cases)), CONFIG))
    assert not reordered.same_corpus
    assert reordered.left_corpus_hash != reordered.right_corpus_hash
    assert reordered.differences == ()
