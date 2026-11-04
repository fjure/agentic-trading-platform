"""Policy, protocol and seed oracles independent of selection/signal implementations."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from packages.backtesting import retrospective as retro
from packages.backtesting import strategies as qsl_strategies
from packages.backtesting.dataset import (
    DatasetBlocked,
    FrozenDataset,
    materialize_fold,
    materialize_quant_phases,
    validate_quant_phase_dataset,
)
from packages.backtesting.experiment import (
    ExperimentDefinition,
    ExperimentFold,
    PhaseProtocol,
    PhaseSeedBinding,
    PhaseWindow,
    QuantExperimentManifest,
    SearchSpace,
    StrategySelectionPolicy,
    selection_identity,
)
from packages.backtesting.models import DataReference, Fold, selection_policy
from packages.backtesting.strategies import QUANT_REGISTRY, REGISTRY, resolve_strategy
from packages.research.models import _canonical
from tests.unit.test_backtest_dataset import fixture as strict_fixture
from tests.unit.test_backtest_models import T
from tests.unit.test_backtest_models import config as legacy_config
from tests.unit.test_backtest_models import manifest as legacy_manifest
from tests.unit.test_retrospective_dataset import (
    fixture as retrospective_fixture,
)
from tests.unit.test_retrospective_dataset import (
    manifest as retrospective_manifest,
)
from tests.unit.test_retrospective_dataset import (
    materialized,
)

POLICY_ID = "MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1"


def test_closed_versioned_catalog_exposes_research_metadata_without_changing_legacy_union():
    assert tuple(s.strategy_id for s in REGISTRY) == ("flat-v1", "buy-hold-v1", "sma-cross-v1")
    assert {s.strategy_id for s in QUANT_REGISTRY} == {
        "flat-v1",
        "buy-hold-v1",
        "sma-cross-v1",
        "ema-cross-v1",
        "rsi-mean-reversion-v1",
    }
    assert len(QUANT_REGISTRY) == 5
    for item in QUANT_REGISTRY:
        assert item.version == "1"
        assert item.display_name and item.description and item.category
        assert item.symbols == ("SPY", "QQQ")
        assert (item.currency, item.adjustment, item.feed, item.interval, item.input_type) == (
            "USD",
            "raw",
            "IEX",
            "15Min",
            "CLOSE",
        )
        assert item.target_states == ("LONG", "FLAT")
        assert item.research_modes == ("STRICT_PIT", "RETROSPECTIVE_EXPLORATORY")
        assert item.fitting_status == "NOT_TRAINABLE"
        assert item.signal_version and item.warmup_version and item.limitations
        assert len(item.implementation_fingerprint) == 64
        assert set(item.implementation_fingerprint) <= set("0123456789abcdef")
        assert resolve_strategy(item.strategy_id, "1", item.implementation_fingerprint) is item
        with pytest.raises(FrozenInstanceError):
            item.version = "2"
    assert "FINITE_COMMON_SEED" in resolve_strategy("ema-cross-v1").limitations
    assert "FINITE_COMMON_SEED" in resolve_strategy("rsi-mean-reversion-v1").limitations


def test_resolver_rejects_missing_versions_and_changed_source_under_old_version(monkeypatch):
    import packages.backtesting.strategies as strategies

    for strategy, version in (("anything", "1"), ("ema-cross-v1", "2")):
        with pytest.raises(ValueError, match="VERSION_UNAVAILABLE"):
            resolve_strategy(strategy, version)
    with pytest.raises(ValueError, match="fingerprint"):
        resolve_strategy("ema-cross-v1", expected_fingerprint="0" * 64)
    monkeypatch.setattr(strategies, "SIMULATOR_VERSION", "changed-under-existing-strategy-version")
    with pytest.raises(ValueError, match="fingerprint"):
        resolve_strategy("ema-cross-v1")


def policy_mapping(**changes):
    return {
        "policyId": POLICY_ID,
        "maxDrawdown": "0.10",
        "minValidationSessions": 20,
        "minimumEntryCount": None,
        "minimumInvestedBarRatio": None,
        **changes,
    }


def policy(**changes):
    return StrategySelectionPolicy.from_mapping(policy_mapping(**changes))


def protocol():
    windows = tuple(
        PhaseWindow(T + timedelta(days=n), T + timedelta(days=n, hours=1)) for n in range(4)
    )
    return PhaseProtocol((ExperimentFold(*windows[:3]),), windows[3])


def definition(**changes):
    value = ExperimentDefinition(
        UUID(int=1),
        "SPY",
        "Compare fixed price-only hypotheses",
        (SearchSpace("ema-cross-v1", (("fast", (2,)), ("slow", (3,)))),),
        protocol(),
        policy(),
    )
    return replace(value, **changes)


def quant_manifest(bound_definition=None):
    value = definition() if bound_definition is None else bound_definition
    bindings = tuple(
        PhaseSeedBinding(
            index,
            phase,
            window,
            tuple(
                window.start - timedelta(minutes=15 * n) for n in range(value.warmup_count, 0, -1)
            ),
            tuple(
                window.start + timedelta(minutes=15 * n)
                for n in range((window.end - window.start) // timedelta(minutes=15))
            ),
        )
        for index, phase, _, window in value.protocol.phases
    )
    reference = DataReference(UUID(int=500), "a" * 64, T)
    return QuantExperimentManifest(
        value,
        5,
        max(T + timedelta(days=5), value.protocol.holdout.end + timedelta(days=1)),
        bindings,
        (reference,),
        (reference,),
        ("calendar-1",),
        ("quality-1",),
        ("coverage-1",),
        "b" * 40,
        "c" * 64,
        "d" * 64,
    )


def test_policy_and_selection_have_independently_hashed_literal_preimages():
    assert policy().selector_fingerprint == (
        "61ab136098bb3c364f0dacd4365d98565d98db20742edb563f3399293564d958"
    )
    assert policy().policy_hash == (
        "cbae80ae6fb17dfc936b3560cce207cd6a65bf3b305ffec948adde88347fa483"
    )
    # SHA256 of the specified policy preimage, final scope, one Validation hash
    # and a completed no-selection result, with sorted canonical JSON keys.
    assert (
        selection_identity(
            policy(),
            scope="final",
            validation_input_hashes=("a" * 64,),
            result={"selectedCandidateId": None, "status": "NO_ELIGIBLE_CANDIDATE"},
        )
        == "6d73d17e7c34b41ff66c6ebfe6f6a95a350edaae1ca100afb073d1b9d0542148"
    )


def test_manifest_snapshot_union_and_research_roles_are_bound_once_for_all_candidates():
    bound = quant_manifest()
    assert definition().fitting_status == "NOT_TRAINABLE"
    assert tuple((key, role) for _, key, role, _ in protocol().phases) == (
        ("training", "IN_SAMPLE_ANALYSIS"),
        ("validation", "PARAMETER_SELECTION"),
        ("test", "WALK_FORWARD_OOS"),
        ("holdout", "FINAL_HOLDOUT"),
    )
    assert [b.role for b in bound.phase_bindings] == [
        "IN_SAMPLE_ANALYSIS",
        "PARAMETER_SELECTION",
        "WALK_FORWARD_OOS",
        "FINAL_HOLDOUT",
    ]
    assert len(bound.snapshot_union) == 24  # Four disjoint phases: two seeds + four bars.
    assert bound.snapshot_union[0] == T - timedelta(minutes=30)
    assert bound.manifest_hash != replace(bound, snapshot_version=6).manifest_hash
    assert bound.manifest_hash != replace(bound, code_version="e" * 40).manifest_hash
    assert (
        bound.manifest_hash
        != replace(bound, decision_data=(DataReference(UUID(int=501), "a" * 64, T),)).manifest_hash
    )
    with pytest.raises(ValueError):
        replace(bound, phase_bindings=bound.phase_bindings[:-1])
    wrong_seed = replace(bound.phase_bindings[0], expected_seed_starts=(T - timedelta(minutes=15),))
    with pytest.raises(ValueError):
        replace(bound, phase_bindings=(wrong_seed, *bound.phase_bindings[1:]))


def test_manifest_366_day_bound_includes_seed_and_is_inclusive():
    original = protocol()
    exact_end = T + timedelta(days=366, minutes=-30)
    exact_protocol = replace(
        original, holdout=PhaseWindow(exact_end - timedelta(minutes=15), exact_end)
    )
    assert quant_manifest(definition(protocol=exact_protocol)).manifest_hash
    excessive = replace(
        original,
        holdout=PhaseWindow(exact_end, exact_end + timedelta(minutes=15)),
    )
    # The performance protocol alone fits; its two required seed bars do not.
    excessive_definition = definition(protocol=excessive)
    with pytest.raises(ValueError, match="seed-inclusive"):
        quant_manifest(excessive_definition)


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
@pytest.mark.parametrize("width", [0, 2])
def test_phase_port_366_day_gate_counts_actual_seed_and_ignores_empty_fold_dummy(
    mode, width, monkeypatch
):
    import packages.backtesting.dataset as datasets

    instrument, start, snapshot, _, calendar, proof, _ = seed_fixture(mode)
    windows = tuple(
        PhaseWindow(start + timedelta(minutes=45 + 15 * n), start + timedelta(minutes=60 + 15 * n))
        for n in range(3)
    )
    exact_end = windows[0].start + timedelta(days=366, minutes=-15 * width)
    holdout = PhaseWindow(exact_end - timedelta(minutes=15), exact_end)
    spaces = (
        (SearchSpace("flat-v1", ()),)
        if width == 0
        else (SearchSpace("ema-cross-v1", (("fast", (2,)), ("slow", (3,)))),)
    )
    value = definition(
        instrument_id=instrument,
        protocol=PhaseProtocol((ExperimentFold(*windows),), holdout),
        search_spaces=spaces,
        research_mode="STRICT_PIT" if mode == "strict" else "RETROSPECTIVE_EXPLORATORY",
    )
    snapshot = replace(snapshot, at=exact_end + timedelta(days=1))

    class ReachedMaterialization(Exception):
        pass

    def stop_before_loading_year_of_prices(*args, **kwargs):
        # This test isolates the span gate; actual short materializations and
        # their immutable contents are exercised by the real phase tests below.
        raise ReachedMaterialization

    if mode == "strict":
        monkeypatch.setattr(datasets, "materialize_fold", stop_before_loading_year_of_prices)
        shared = FrozenDataset(snapshot, ())

        def materialize(candidate):
            return materialize_quant_phases(shared, candidate, calendar, coverage_proofs=(proof,))
    else:
        monkeypatch.setattr(
            retro, "materialize_retrospective_fold", stop_before_loading_year_of_prices
        )
        shared = retro.RetrospectiveDataset(snapshot, ())

        def materialize(candidate):
            return retro.materialize_quant_retrospective_phases(shared, candidate, calendar)

    with pytest.raises(ReachedMaterialization):
        materialize(value)
    longer_holdout = replace(holdout, end=exact_end + timedelta(minutes=15))
    if width:
        excessive = replace(value, protocol=replace(value.protocol, holdout=longer_holdout))
        with pytest.raises(ValueError, match="seed-inclusive"):
            materialize(excessive)
    else:
        with pytest.raises(ValueError, match="366"):
            replace(value.protocol, holdout=longer_holdout)


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_version": True},
        {"snapshot_version": 0},
        {"snapshot_at": T},
        {"code_version": "latest"},
        {"trust_fingerprint": "unbound"},
        {"license_receipt_fingerprint": "unbound"},
        {"decision_data": ()},
        {"outcome_data": ()},
        {"calendar_revisions": ()},
        {"simulator_version": "latest"},
        {"metrics_version": "latest"},
        {"fill_version": "latest"},
    ],
)
def test_manifest_rejects_incomplete_or_unversioned_evidence(change):
    with pytest.raises(ValueError):
        replace(quant_manifest(), **change)


def test_legacy_identity_literals_captured_on_qsl01_baseline():
    assert legacy_config().input_hash == (
        "82ec4a7121c7bf3ade8aeb031f23ce60f3df28d1306924d35a8d863cd71c6cd6"
    )
    assert legacy_manifest().manifest_hash == (
        "8c51a2f3e7868b8c90e0eb86bdf152177dbbbf3c7f05e20f5c69f13d0bd3a330"
    )
    expected = (
        '{"adjustment":"raw","currency":"USD","fee_usd":"0","feed":"iex",'
        '"folds":[{"test_end":"2026-10-20T15:00:00Z",'
        '"test_start":"2026-10-20T14:00:00Z","training_end":"2026-10-20T14:00:00Z",'
        '"training_start":"2026-10-19T14:00:00Z"}],'
        '"instrument_id":"00000000-0000-0000-0000-000000000001","interval":"15Min",'
        '"parameters":[],"quantity_step":"0.000001","slippage_bps":"0",'
        '"start_cash":"100","strategy_id":"buy-hold-v1","strategy_version":"1","symbol":"SPY"}'
    )
    assert (
        json.dumps(_canonical(legacy_config()), sort_keys=True, separators=(",", ":")) == expected
    )
    assert selection_policy().selection_policy_version == "strict-pit-v1"
    assert selection_policy(
        "RETROSPECTIVE_EXPLORATORY", "retrospective-v1"
    ).selection_policy_version == ("retrospective-v1")


def test_legacy_retrospective_data_and_manifest_keep_literal_hashes():
    config, snapshot, bars, calendar = retrospective_fixture(warmup=2)
    dataset = materialized(config, snapshot, bars, calendar)
    bound = retrospective_manifest(config, snapshot, dataset)
    assert config.input_hash == "cfa03ea02b0564b37a7fb2020bf5e2a242c50b85027480692b651813d45e36a0"
    assert (
        dataset.dataset_hash == "d1bb2e31aed7c701ca02ffc21a790ac211ded840f821cc4da028f95ddd662c03"
    )
    assert bound.manifest_hash == "9b4ea1b0ec774385d4beb04c20768376f38de6811cd9c155bdd45483524952a1"


@pytest.mark.parametrize(
    "change",
    [
        {"policyId": "CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1"},
        {"maxDrawdown": "0.11"},
        {"minValidationSessions": 21},
        {"minimumEntryCount": 0},
        {"minimumInvestedBarRatio": "0"},
    ],
)
def test_each_policy_parameter_including_null_to_zero_changes_definition_identity(change):
    base, changed = policy(), policy(**change)
    assert base.policy_hash != changed.policy_hash
    assert (
        definition().definition_hash
        != definition(strategy_selection_policy=changed).definition_hash
    )
    assert (
        quant_manifest().manifest_hash
        != quant_manifest(definition(strategy_selection_policy=changed)).manifest_hash
    )
    assert selection_identity(
        base,
        scope="final",
        validation_input_hashes=("a" * 64,),
        result={"selectedCandidateId": None},
    ) != selection_identity(
        changed,
        scope="final",
        validation_input_hashes=("a" * 64,),
        result={"selectedCandidateId": None},
    )
    assert legacy_config().input_hash == (
        "82ec4a7121c7bf3ade8aeb031f23ce60f3df28d1306924d35a8d863cd71c6cd6"
    )


def test_policy_decimal_spelling_and_immutable_definition():
    assert policy().policy_hash == policy(maxDrawdown="0.100000000000").policy_hash
    assert definition().research_mode == "STRICT_PIT"
    assert definition().warmup_count == 2
    with pytest.raises(FrozenInstanceError):
        policy().max_drawdown = "0.2"


@pytest.mark.parametrize(
    "change",
    [
        {"policyId": "free-code"},
        {"unknown": 1},
        {"maxDrawdown": None},
        {"maxDrawdown": "NaN"},
        {"maxDrawdown": "Infinity"},
        {"maxDrawdown": "-0.1"},
        {"maxDrawdown": "1.1"},
        {"maxDrawdown": "0.1000000000001"},
        {"maxDrawdown": Decimal("0.1")},
        {"maxDrawdown": 0.1},
        {"maxDrawdown": True},
        {"minValidationSessions": True},
        {"minValidationSessions": 20.0},
        {"minValidationSessions": "20"},
        {"minValidationSessions": 19},
        {"minValidationSessions": 253},
        {"minimumEntryCount": False},
        {"minimumEntryCount": -1},
        {"minimumEntryCount": 100001},
        {"minimumEntryCount": "1"},
        {"minimumInvestedBarRatio": "NaN"},
        {"minimumInvestedBarRatio": "Infinity"},
        {"minimumInvestedBarRatio": "1.01"},
        {"minimumInvestedBarRatio": "0.0000000000001"},
        {"minimumInvestedBarRatio": 0},
    ],
)
def test_closed_policy_rejects_invalid_input_before_job_creation(change):
    with pytest.raises(ValueError):
        policy(**change)


@pytest.mark.parametrize("missing", tuple(policy_mapping()))
def test_policy_requires_explicit_optional_null_fields_as_well_as_required_parameters(missing):
    values = policy_mapping()
    del values[missing]
    with pytest.raises(ValueError):
        StrategySelectionPolicy.from_mapping(values)


@pytest.mark.parametrize("mode", [None, "auto", "LIVE", True])
def test_no_implicit_mode_fallback(mode):
    with pytest.raises(ValueError):
        definition(research_mode=mode)


def test_common_warmup_comes_from_all_candidate_definitions():
    spaces = (
        SearchSpace("sma-cross-v1", (("fast", (2,)), ("slow", (6,)))),
        SearchSpace("ema-cross-v1", (("fast", (2,)), ("slow", (5,)))),
        SearchSpace(
            "rsi-mean-reversion-v1", (("period", (7,)), ("entry", ("30",)), ("exit", ("70",)))
        ),
        SearchSpace("flat-v1", ()),
    )
    assert definition(search_spaces=spaces).warmup_count == 7


def test_protocol_rejects_missing_overlapping_out_of_order_and_unaligned_phases():
    base = protocol()
    fold = base.folds[0]
    invalid = (
        {"folds": ()},
        {"folds": (fold,) * 5},
        {"holdout": fold.test},
    )
    for changes in invalid:
        with pytest.raises(ValueError):
            replace(base, **changes)
    with pytest.raises(ValueError):
        replace(fold, validation=fold.training)
    with pytest.raises(ValueError):
        replace(fold, test=fold.validation)
    with pytest.raises(ValueError):
        PhaseWindow(T, T)
    with pytest.raises(ValueError):
        PhaseWindow(T + timedelta(seconds=1), T + timedelta(hours=1))


@pytest.mark.parametrize(
    "change",
    [
        {"symbol": "AAPL"},
        {"definition_version": True},
        {"definition_version": 0},
        {"hypothesis": ""},
        {"hypothesis": "x" * 2001},
        {"search_spaces": ()},
        {"start_cash": 10000},
        {"start_cash": Decimal("NaN")},
        {"fee_usd": Decimal("-1")},
        {"quantity_step": Decimal("0.01")},
    ],
)
def test_invalid_definition_is_rejected_by_pure_constructor(change):
    with pytest.raises(ValueError):
        definition(**change)


def seed_fixture(mode):
    _, start, snapshot, originals, calendar, proof, _ = strict_fixture()
    instrument = UUID(int=101)
    bars = tuple(
        replace(
            bar,
            revision_id=UUID(int=200 + n),
            identity=replace(bar.identity, instrument_id=instrument),
        )
        for n, bar in enumerate(originals)
    )
    calendar = ({**calendar[0], "session_id": UUID(int=300)},)
    proof = replace(proof, instrument_id=instrument, coverage_id=UUID(int=400))
    # Give 3 bars before the phase and 3 within it, in a proven regular session.
    fold = Fold(
        start,
        start + timedelta(minutes=45),
        start + timedelta(minutes=45),
        start + timedelta(minutes=90),
    )
    return instrument, start, snapshot, bars, calendar, proof, fold


def seeded(mode, bars=None, snapshot=None):
    instrument, _, original_snapshot, original, calendar, proof, fold = seed_fixture(mode)
    snapshot = original_snapshot if snapshot is None else snapshot
    bars = original if bars is None else bars
    if mode == "strict":
        return materialize_fold(
            FrozenDataset(snapshot, bars),
            fold,
            instrument,
            3,
            calendar,
            coverage_proofs=(proof,),
            seed_once=True,
        )
    return retro.materialize_retrospective_fold(
        retro.RetrospectiveDataset(snapshot, bars), fold, instrument, 3, calendar, seed_once=True
    )


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
def test_seed_is_bound_once_then_only_one_new_close_is_exposed(mode):
    result = seeded(mode)
    assert len(result.warmup) == 3
    assert [len(step.input_bars) for step in result.steps] == [4, 1, 1]
    assert result.steps[0].input_bars[:-1] == result.warmup
    assert tuple(step.signal_bar for step in result.steps) == result.outcome_bars
    assert all(step.input_bars[-1] == step.signal_bar for step in result.steps)
    assert all(
        step.knowledge_at == step.signal_bar.identity.bar_end + timedelta(seconds=60)
        for step in result.steps
    )
    assert all(
        step.fill_target is None or step.fill_target >= result.fold.test_start
        for step in result.steps
    )
    assert all(bar.identity.bar_start < result.fold.test_start for bar in result.warmup)


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
def test_missing_common_seed_blocks_even_for_zero_history_reference(mode):
    _, _, _, bars, _, _, _ = seed_fixture(mode)
    with pytest.raises(DatasetBlocked, match="BLOCKED_WARMUP_MISSING"):
        seeded(mode, bars[1:])


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
def test_later_phase_prices_cannot_change_seed_or_prior_decision_inputs(mode):
    _, _, _, bars, _, _, _ = seed_fixture(mode)
    original = seeded(mode, bars)
    future = replace(bars[-1], close=Decimal("999"), high=Decimal("999"))
    changed = seeded(mode, (*bars[:-1], future))
    assert changed.warmup == original.warmup
    assert changed.steps[:-1] == original.steps[:-1]
    assert changed.steps[-1].input_bars == (future,)
    assert changed.expected_bar_starts == original.expected_bar_starts


def test_strict_seed_is_not_replaced_by_correction_learned_after_initialization():
    _, _, _, bars, _, _, _ = seed_fixture("strict")
    original = seeded("strict", bars)
    learned = original.steps[0].knowledge_at + timedelta(seconds=1)
    correction = replace(
        bars[0],
        revision_id=UUID(int=999),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        close=Decimal("999"),
        high=Decimal("999"),
        ingested_at=learned,
        available_at=learned,
        commit_version=20,
    )
    changed = seeded("strict", (*bars, correction))
    assert changed.warmup == original.warmup
    assert changed.steps == original.steps
    assert all(correction not in s.input_bars for s in changed.steps)


def test_retrospective_new_snapshot_binds_new_seed_without_mutating_old_binding():
    _, _, snapshot, bars, _, _, _ = seed_fixture("retrospective")
    original = seeded("retrospective", bars)
    correction = replace(
        bars[0],
        revision_id=UUID(int=999),
        local_revision=2,
        predecessor_id=bars[0].revision_id,
        close=Decimal("999"),
        high=Decimal("999"),
        ingested_at=snapshot.at + timedelta(seconds=1),
        available_at=snapshot.at + timedelta(seconds=1),
        commit_version=101,
    )
    newer = replace(snapshot, version=101, at=snapshot.at + timedelta(minutes=1))
    changed = seeded("retrospective", (*bars, correction), newer)
    assert original.warmup[0] == bars[0]
    assert changed.warmup[0] == correction
    assert changed.dataset_hash != original.dataset_hash
    assert seeded("retrospective", bars).dataset_hash == original.dataset_hash
    assert original.steps[0].input_bars[0] == bars[0]


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
def test_every_research_phase_uses_same_snapshot_and_common_seed_even_for_flat(mode):
    instrument, start, snapshot, bars, calendar, proof, _ = seed_fixture(mode)
    final_bar = replace(
        bars[-1],
        revision_id=UUID(int=206),
        commit_version=7,
        identity=replace(
            bars[-1].identity,
            bar_start=start + timedelta(minutes=90),
            bar_end=start + timedelta(minutes=105),
        ),
        available_at=start + timedelta(minutes=105, seconds=30),
        ingested_at=start + timedelta(minutes=105, seconds=30),
    )
    bars = (*bars, final_bar)
    windows = tuple(
        PhaseWindow(start + timedelta(minutes=45 + 15 * n), start + timedelta(minutes=60 + 15 * n))
        for n in range(4)
    )
    value = definition(
        instrument_id=instrument,
        protocol=PhaseProtocol((ExperimentFold(*windows[:3]),), windows[3]),
        search_spaces=(
            SearchSpace("flat-v1", ()),
            SearchSpace("sma-cross-v1", (("fast", (2,)), ("slow", (3,)))),
            SearchSpace("ema-cross-v1", (("fast", (2,)), ("slow", (4,)))),
            SearchSpace(
                "rsi-mean-reversion-v1", (("entry", ("30",)), ("exit", ("70",)), ("period", (2,)))
            ),
        ),
        research_mode="STRICT_PIT" if mode == "strict" else "RETROSPECTIVE_EXPLORATORY",
    )
    assert value.warmup_count == 3
    if mode == "strict":
        shared = FrozenDataset(snapshot, bars)
        phases = materialize_quant_phases(shared, value, calendar, coverage_proofs=(proof,))
    else:
        shared = retro.RetrospectiveDataset(snapshot, bars)
        phases = retro.materialize_quant_retrospective_phases(shared, value, calendar)
    assert len(phases) == 4
    for n, phase in enumerate(phases):
        assert phase.binding.window == windows[n]
        assert phase.dataset.warmup == bars[n : n + 3]
        assert phase.dataset.steps[0].input_bars == bars[n : n + 4]
        assert phase.dataset.steps[0].signal_bar is bars[n + 3]
        assert phase.dataset.steps[0].fill_target is None
    assert shared.snapshot is snapshot
    assert shared.revisions == bars
    decisions = tuple(
        sorted(
            {
                DataReference(
                    bar.revision_id,
                    bar.fingerprint,
                    step.knowledge_at if mode == "strict" else snapshot.at,
                )
                for phase in phases
                for step in phase.dataset.steps
                for bar in step.input_bars
            }
        )
    )
    outcomes = tuple(
        sorted(
            {
                DataReference(
                    bar.revision_id,
                    bar.fingerprint,
                    bar.identity.bar_end + timedelta(seconds=300)
                    if mode == "strict"
                    else snapshot.at,
                )
                for phase in phases
                for bar in phase.dataset.outcome_bars
            }
        )
    )
    calendar_refs = tuple(
        sorted({ref for phase in phases for ref in phase.dataset.calendar_revision_ids})
    )
    quality_refs = tuple(
        sorted(
            {ref for phase in phases for ref in getattr(phase.dataset, "quality_revision_ids", ())}
        )
    )
    coverage_refs = tuple(
        sorted({ref for phase in phases for ref in getattr(phase.dataset, "coverage_ids", ())})
    )
    # Strict publishes a fingerprint of its complete known-empty quality state;
    # retrospective quality references are optional observed current-snapshot inputs.
    assert len(quality_refs) == (1 if mode == "strict" else 0)
    assert coverage_refs == (str(proof.coverage_id),) if mode == "strict" else coverage_refs == ()
    bound = QuantExperimentManifest.from_phase_datasets(
        value,
        phases,
        snapshot,
        code_version="b" * 40,
        trust_fingerprint="c" * 64,
        license_receipt_fingerprint="d" * 64,
    )
    assert bound.decision_data == decisions
    assert bound.outcome_data == outcomes
    assert bound.calendar_revisions == calendar_refs
    assert bound.quality_revisions == quality_refs
    assert bound.coverage_revisions == coverage_refs
    assert bound.manifest_hash
    assert bound.snapshot_union == tuple(bar.identity.bar_start for bar in bars)
    with pytest.raises(ValueError):
        replace(bound, calendar_revisions=())
    if mode == "strict":
        with pytest.raises(ValueError):
            replace(bound, coverage_revisions=())
        with pytest.raises(ValueError):
            replace(bound, quality_revisions=())


@pytest.mark.parametrize("changed", [{"warmup_rule": "NONE"}, {"parameter_definitions": ()}])
def test_review_source_pin_rejects_initialization_metadata_changes(monkeypatch, changed):
    monkeypatch.setattr(
        qsl_strategies, "QUANT_REGISTRY", tuple(
            replace(item, **changed) if item.strategy_id == "sma-cross-v1" else item
            for item in qsl_strategies.QUANT_REGISTRY
        ),
    )
    with pytest.raises(ValueError, match="fingerprint"):
        qsl_strategies.resolve_strategy("sma-cross-v1")


def test_review_source_pin_rejects_changed_warmup_code(monkeypatch):
    def changed_warmup(*args):
        return 0

    monkeypatch.setattr(qsl_strategies, "warmup_count", changed_warmup)
    with pytest.raises(ValueError, match="fingerprint"):
        qsl_strategies.resolve_strategy("sma-cross-v1")


@pytest.mark.parametrize("text", [" " * 2000 + "x", "x" + " " * 2000, "\t" * 2000 + "x"])
def test_hypothesis_raw_length_limit_cannot_be_bypassed_by_padding(text):
    with pytest.raises(ValueError, match="hypothesis"):
        definition(hypothesis=text)


def test_hypothesis_raw_length_limit_is_inclusive():
    assert len(definition(hypothesis=" " * 1999 + "x").hypothesis) == 2000


def _review_materialized_manifest_inputs(mode, monkeypatch):
    original = QuantExperimentManifest.from_phase_datasets
    captured = {}

    def capture(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(QuantExperimentManifest, "from_phase_datasets", capture)
    test_every_research_phase_uses_same_snapshot_and_common_seed_even_for_flat(mode)
    return original, captured["args"], captured["kwargs"]


@pytest.mark.parametrize("mode", ["strict", "retrospective"])
def test_review_manifest_factory_rejects_a_different_instrument(mode, monkeypatch):
    factory, (value, phases, snapshot), kwargs = _review_materialized_manifest_inputs(
        mode, monkeypatch,
    )
    with pytest.raises(ValueError, match="instrument|market"):
        factory(replace(value, instrument_id=UUID(int=99999)), phases, snapshot, **kwargs)


@pytest.mark.parametrize("mutation", [
    "duplicate_steps", "missing_steps", "missing_outcomes", "late_cutoff", "broken_seed",
])
def test_review_strict_manifest_rejects_corrupt_materialized_phase(monkeypatch, mutation):
    factory, (value, phases, snapshot), kwargs = _review_materialized_manifest_inputs(
        "strict", monkeypatch,
    )
    first = phases[0]
    data = first.dataset
    if mutation == "duplicate_steps":
        bad = replace(data, steps=data.steps * 2)
    elif mutation == "missing_steps":
        bad = replace(data, steps=())
    elif mutation == "missing_outcomes":
        bad = replace(data, outcome_bars=())
    elif mutation == "late_cutoff":
        bad = replace(data, outcome_cutoffs=((data.outcome_bars[0].revision_id, snapshot.at),))
    else:
        step = data.steps[0]
        bad = replace(data, steps=(replace(step, input_bars=(step.signal_bar,)),))
    with pytest.raises(ValueError, match="incomplete|duplicated|cutoff|seed prefix"):
        factory(value, (replace(first, dataset=bad), *phases[1:]), snapshot, **kwargs)


def test_review_strict_single_close_after_seed_cannot_reseed():
    data = seeded("strict")
    snapshot = seed_fixture("strict")[2]
    binding = PhaseSeedBinding(
        0, "training", PhaseWindow(data.fold.test_start, data.fold.test_end),
        data.warmup_binding.expected_bar_starts, data.expected_bar_starts,
    )
    validate_quant_phase_dataset(data, binding, snapshot)
    following = data.steps[1]
    wrong = replace(following, input_bars=(*data.warmup, following.signal_bar))
    with pytest.raises(ValueError, match="one seed prefix"):
        validate_quant_phase_dataset(
            replace(data, steps=(data.steps[0], wrong, *data.steps[2:])), binding, snapshot,
        )
