"""Independent QSL grid identities and whole-search rejection boundaries."""

from dataclasses import replace

import pytest

from packages.backtesting.experiment import SearchSpace, enumerate_candidates
from packages.backtesting.strategies import canonical_parameters, warmup_count

# Independently SHA256-hashed UTF-8 JSON [id,"1",[["fast",f],["slow",s]]].
NINE_IDS = {
    (5, 30): "ecbfe4ef9aef57abe3e36209544e8f9d0beff5008322ec2d0a20b38dcac3deb3",
    (5, 40): "a4d8eee2619eb5c263792d4ed28e3e281e8f7c10c98ae907ded75449e7e32cb0",
    (5, 50): "4e2e31f2f7dc7873ed341a49273fb75dd73235e009012d91fcb72df892c3bab0",
    (10, 30): "79f1731c24ef0dd0b6ccbb21389e2c63523c0863223e21662ade56d9e5b62e01",
    (10, 40): "c5ac14bb10dfefd58005db6f90895ce2690e4887dae6b4d89aaddf3975164bf3",
    (10, 50): "a2ce9fee8875be4dac2a41de7b575c61db8fe6a1db72b300f612484b0d4119af",
    (15, 30): "fc96ca496170617c3898060f7f6b7b3e9cec410b8552c3d2ee5c8e4bd50350df",
    (15, 40): "af1cc9ad38d91a62a32869e11566c0fe677f2065bb49e74ed9d2bf257e2831f9",
    (15, 50): "bfc2c0134641323742af01a51f0d41e840d2b0773c9d82e3dbfd21321976f7a9",
}


def grid(fast=(5, 10, 15), slow=(30, 40, 50), strategy="ema-cross-v1"):
    return SearchSpace(strategy, (("fast", fast), ("slow", slow)))


def test_documented_three_by_three_grid_has_literal_ids_and_stable_order():
    candidates = enumerate_candidates((grid(),))
    assert len(candidates) == 9
    assert {
        (dict(c.parameters)["fast"], dict(c.parameters)["slow"]): c.candidate_id for c in candidates
    } == NINE_IDS
    assert len({c.candidate_id for c in candidates}) == 9
    permuted = SearchSpace("ema-cross-v1", (("slow", (50, 30, 40)), ("fast", (15, 5, 10))))
    assert enumerate_candidates((permuted,)) == candidates
    assert max(c.warmup_count for c in candidates) == 49


def test_strategy_input_order_and_decimal_spelling_are_canonical():
    assert canonical_parameters("ema-cross-v1", {"slow": 30, "fast": 5}) == (
        ("fast", 5),
        ("slow", 30),
    )
    assert canonical_parameters(
        "rsi-mean-reversion-v1", {"period": 2, "exit": "70.0000", "entry": "30.00"}
    ) == (("entry", "30"), ("exit", "70"), ("period", 2))


@pytest.mark.parametrize(
    ("strategy", "parameters", "expected"),
    [
        ("flat-v1", (), 0),
        ("buy-hold-v1", (), 0),
        ("sma-cross-v1", (("fast", 2), ("slow", 100)), 99),
        ("ema-cross-v1", (("fast", 2), ("slow", 50)), 49),
        ("rsi-mean-reversion-v1", (("entry", "30"), ("exit", "70"), ("period", 50)), 50),
    ],
)
def test_definition_resolves_history_requirement(strategy, parameters, expected):
    assert warmup_count(strategy, parameters) == expected


@pytest.mark.parametrize("fast,slow", [((5, 5), (30,)), ((2,), (3, 3)), ((2, 4), (3, 5))])
def test_duplicate_values_and_any_invalid_combination_reject_entire_grid(fast, slow):
    with pytest.raises(ValueError):
        enumerate_candidates((grid(fast, slow),))


def test_duplicates_across_search_spaces_are_not_silently_deduplicated():
    space = grid((5,), (30,))
    with pytest.raises(ValueError):
        enumerate_candidates((space, space))


def test_equivalent_rsi_threshold_spellings_are_duplicate_values():
    with pytest.raises(ValueError):
        enumerate_candidates(
            (
                SearchSpace(
                    "rsi-mean-reversion-v1",
                    (("period", (2,)), ("entry", ("30", "30.0000")), ("exit", ("70",))),
                ),
            )
        )


def test_32_members_are_allowed_and_33_are_rejected_before_enumeration():
    assert len(enumerate_candidates((grid(tuple(range(2, 34)), (100,)),))) == 32
    with pytest.raises(ValueError):
        enumerate_candidates((grid(tuple(range(2, 34)), (100,)), SearchSpace("flat-v1", ())))
    with pytest.raises(ValueError):
        enumerate_candidates((grid(tuple(range(2, 35)), (100,)),))


def test_large_product_rejects_before_constructing_any_candidate(monkeypatch):
    import packages.backtesting.experiment as experiment

    def forbidden(*args, **kwargs):
        pytest.fail("oversized grid reached candidate materialization")

    monkeypatch.setattr(experiment, "CandidateConfiguration", forbidden)
    with pytest.raises(ValueError):
        enumerate_candidates(
            (
                SearchSpace(
                    "rsi-mean-reversion-v1",
                    (
                        ("period", tuple(range(2, 34))),
                        ("entry", tuple(str(n) for n in range(32))),
                        ("exit", tuple(str(n) for n in range(60, 92))),
                    ),
                ),
            )
        )


@pytest.mark.parametrize("invalid", [True, False, None, 2.0, "2", 1, 101])
def test_period_types_and_bounds_are_rejected_before_work(invalid):
    with pytest.raises(ValueError):
        enumerate_candidates((grid((invalid,), (100,)),))


@pytest.mark.parametrize(
    "parameters",
    [
        {"fast": 2, "slow": 3, "program": "anything"},
        (("fast", 2), ("fast", 2), ("slow", 3)),
        {"period": True, "entry": "30", "exit": "70"},
        {"period": 2, "entry": "NaN", "exit": "70"},
        {"period": 2, "entry": "30", "exit": "Infinity"},
        {"period": 2, "entry": "30.00001", "exit": "70"},
        {"period": 2, "entry": 30, "exit": "70"},
        {"period": 2, "entry": "70", "exit": "70"},
    ],
)
def test_unknown_duplicate_nonfinite_and_invalid_parameter_types(parameters):
    strategy = "ema-cross-v1" if "fast" in dict(parameters) else "rsi-mean-reversion-v1"
    with pytest.raises(ValueError):
        canonical_parameters(strategy, parameters)


def test_unknown_strategy_and_version_fail_closed():
    with pytest.raises(ValueError):
        enumerate_candidates((grid(strategy="uploaded-program"),))
    with pytest.raises(ValueError):
        enumerate_candidates((replace(grid(), strategy_version="2"),))
