from dataclasses import replace

import pytest

from packages.agent_research.companion.testability import (
    Approximation,
    Classification,
    DataSuitability,
    HypothesisIdentity,
    Option,
    Reason,
    StrategyMapping,
    assess_testability,
    ema_crossover_mapping,
    hypothesis_signature,
)
from packages.backtesting.experiment import PROTOCOL_VERSION
from packages.backtesting.strategies import CATALOG_VERSION, resolve_strategy

HYPOTHESIS = HypothesisIdentity(
    market="QQQ",
    trigger="EMA crossover",
    signal="EMA(10) above EMA(30)",
    observation_window="2024-01-01/2024-12-31",
    target="net return",
    measurement_horizon="entire evaluation window",
    benchmark="buy-hold-v1",
    evaluation_criterion="return greater than benchmark",
    constraints=(("currency", "USD"), ("interval", "15Min")),
)
MODE = "STRICT_PIT"


def mapping_for(hypothesis=HYPOTHESIS):
    strategy = resolve_strategy("ema-cross-v1", require_execution=True)
    return StrategyMapping(
        hypothesis_signature(hypothesis),
        strategy.strategy_id,
        strategy.version,
        strategy.implementation_fingerprint,
        (("fast", 10), ("slow", 30)),
        CATALOG_VERSION,
        PROTOCOL_VERSION,
        MODE,
    )


def data_for(mapping):
    return DataSuitability(mapping.binding, True, True, True, True)


def assess(hypothesis=HYPOTHESIS, **kwargs):
    return assess_testability(hypothesis, research_mode=MODE, in_scope=True, **kwargs)


def test_signature_canonicalizes_order_and_unicode_but_preserves_semantics():
    composed = replace(HYPOTHESIS, market="Marché")
    decomposed = replace(composed, market=" Marche\u0301 ", constraints=composed.constraints[::-1])
    assert hypothesis_signature(composed) == hypothesis_signature(decomposed)
    assert hypothesis_signature(composed) != hypothesis_signature(
        replace(composed, market="MARCHÉ")
    )
    assert hypothesis_signature(composed) != hypothesis_signature(
        replace(composed, target="Sharpe")
    )
    with pytest.raises(ValueError, match="unique"):
        hypothesis_signature(replace(composed, constraints=(("x", "a"), (" x ", "b"))))


def test_incomplete_hypothesis_has_no_identity_or_testability():
    incomplete = replace(HYPOTHESIS, target=None, measurement_horizon=" ")
    assert hypothesis_signature(incomplete) is None
    result = assess(incomplete)
    assert result.classification is None
    assert result.reasons == (Reason.CLARIFICATION_REQUIRED,)
    assert result.missing_fields == ("TARGET", "MEASUREMENT_HORIZON")


def test_exact_requires_current_catalog_parameters_and_data_binding():
    mapping = mapping_for()
    assert (
        assess(mapping=mapping, data=data_for(mapping)).classification
        == Classification.EXACTLY_TESTABLE
    )
    for stale in (
        replace(mapping, parameters=(("fast", 30), ("slow", 10))),
        replace(mapping, protocol_version="stale"),
        replace(mapping, strategy_fingerprint="0" * 64),
        replace(mapping, research_mode="RETROSPECTIVE_EXPLORATORY"),
    ):
        assert assess(mapping=stale, data=data_for(stale)).reasons == (Reason.MAPPING_UNAVAILABLE,)
    assert assess(mapping=mapping).classification == Classification.INSUFFICIENT_DATA


def test_data_gates_are_independent_and_preserve_strict_pit_mode():
    mapping = mapping_for()
    data = replace(
        data_for(mapping),
        available=False,
        quality_sufficient=False,
        rights_available=False,
        strict_pit_verified=False,
    )
    result = assess(mapping=mapping, data=data)
    assert result.classification == Classification.INSUFFICIENT_DATA
    assert result.reasons == (
        Reason.DATA_UNAVAILABLE,
        Reason.DATA_QUALITY_INSUFFICIENT,
        Reason.DATA_RIGHTS_UNAVAILABLE,
        Reason.STRICT_PIT_UNVERIFIED,
    )
    assert result.mapping.research_mode == MODE


def test_nonmatching_price_rule_cannot_be_exact_for_earnings_event():
    earnings = replace(HYPOTHESIS, trigger="negative earnings surprise")
    mapping = mapping_for()
    result = assess(earnings, mapping=mapping, data=data_for(mapping), required_data_missing=True)
    assert result.classification == Classification.REQUIRES_NEW_STRATEGY
    assert result.reasons == (Reason.NO_EXACT_MAPPING, Reason.DATA_UNAVAILABLE)


def test_reviewed_ema_catalog_mapping_requires_matching_signal_and_protocol_semantics():
    parameters = (("fast", 10), ("slow", 30))
    mapping = ema_crossover_mapping(HYPOTHESIS, parameters, MODE)
    assert mapping == mapping_for()
    for changed in (
        replace(HYPOTHESIS, market="NASDAQ"),
        replace(HYPOTHESIS, signal="EMA(8) above EMA(30)"),
        replace(HYPOTHESIS, trigger="negative earnings surprise"),
        replace(HYPOTHESIS, measurement_horizon="next day"),
        replace(HYPOTHESIS, constraints=(("interval", "1Min"),)),
    ):
        assert ema_crossover_mapping(changed, parameters, MODE) is None


def test_out_of_scope_remains_unsupported_despite_available_mapping():
    mapping = mapping_for()
    result = assess_testability(
        HYPOTHESIS, research_mode=MODE, in_scope=False, mapping=mapping, data=data_for(mapping)
    )
    assert result.classification == Classification.UNSUPPORTED


def test_approximation_binds_original_substitute_and_idea_revision():
    mapping = mapping_for()
    original = replace(HYPOTHESIS, trigger="three consecutive down days")
    approximation = Approximation(
        "idea-1",
        2,
        hypothesis_signature(original),
        HYPOTHESIS,
        mapping,
        "Use an EMA signal instead of a sequence of losses.",
        ("Cannot measure the requested loss sequence.",),
    )
    kwargs = {
        "approximation": approximation,
        "data": data_for(mapping),
        "idea_binding": ("idea-1", 2),
    }
    proposed = assess(original, **kwargs)
    assert proposed.classification == Classification.TESTABLE_WITH_DECLARED_APPROXIMATION
    assert proposed.options == (Option.REVIEW_APPROXIMATION,)
    assert not proposed.approximation_accepted
    accepted = assess(original, **kwargs, accepted_approximation_binding=approximation.binding)
    assert accepted.options == (Option.PREPARE_VALIDATION,)
    assert accepted.approximation_accepted
    assert accepted.limitations == approximation.limitations
    assert assess(
        original,
        **(kwargs | {"idea_binding": ("idea-1", 3)}),
        accepted_approximation_binding=approximation.binding,
    ).classification == (Classification.REQUIRES_NEW_STRATEGY)
    assert (
        assess(original, **(kwargs | {"data": None})).classification
        == Classification.INSUFFICIENT_DATA
    )
