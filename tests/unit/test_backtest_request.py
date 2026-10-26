"""Protocol semantics beyond individual JSON field shapes."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.backtesting.request import canonical_request, parse_request
from packages.research.models import content_fingerprint
from packages.research.registry import definitions

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def valid_request():
    root = Path(__file__).resolve().parents[2]
    value = json.loads((root / "contracts/examples/v1/backtest-request.valid.json").read_text())
    value["instrumentId"] = str(definitions()[0][0].instrument_id)
    return value


def test_decimal_padding_and_utc_fraction_representations_canonicalize_before_idempotency():
    base = valid_request()
    padded = deepcopy(base)
    padded.update(startCash="10000.00", feeUsd="1.000", slippageBps="5.0")
    padded["folds"][0]["trainingEnd"] = "2026-10-22T00:00:00.000000Z"
    assert canonical_request(base, NOW) == canonical_request(padded, NOW)
    assert content_fingerprint(canonical_request(base, NOW)) == content_fingerprint(
        canonical_request(padded, NOW)
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"startCash": "1e4"},
        {"startCash": "NaN"},
        {"feeUsd": "-0"},
        {"parameters": {"fast": True, "slow": 3}},
        {"codeVersion": "a" * 40},
    ],
)
def test_invalid_protocol_values(patch):
    with pytest.raises(ValueError):
        parse_request({**valid_request(), **patch}, NOW)


def test_true_time_comparison_and_future_or_overlapping_folds():
    request = valid_request()
    request["folds"][0]["testStart"] = "2026-10-22T00:00:00.1Z"
    parse_request(request, NOW)
    request["folds"].append(deepcopy(request["folds"][0]))
    with pytest.raises(ValueError):
        parse_request(request, NOW)
    request["folds"] = [request["folds"][0]]
    request["folds"][0]["testEnd"] = "2026-10-27T00:00:00Z"
    with pytest.raises(ValueError):
        parse_request(request, NOW)
