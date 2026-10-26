"""Strict public request to immutable experiment protocol; no supplied manifest fields."""

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from packages.backtesting.models import ExperimentConfig, Fold
from packages.domain.models import utc_time
from packages.research.models import _canonical
from packages.research.registry import definitions

FIELDS = {
    "instrumentId",
    "strategyId",
    "version",
    "parameters",
    "startCash",
    "feeUsd",
    "slippageBps",
    "quantityStep",
    "folds",
}
FOLD_FIELDS = ("trainingStart", "trainingEnd", "testStart", "testEnd")


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError("UTC timestamp required")
    return utc_time(datetime.fromisoformat(value.replace("Z", "+00:00")))


def parse_request(request, now):
    if type(request) is not dict or set(request) != FIELDS:
        raise ValueError("strict request required")
    if not isinstance(request["instrumentId"], str):
        raise ValueError("canonical UUID required")
    instrument = UUID(request["instrumentId"])
    if str(instrument) != request["instrumentId"]:
        raise ValueError("canonical UUID required")
    known = {i.instrument_id: m.symbol for i, m in definitions()}
    if instrument not in known:
        raise ValueError("unsupported instrument")
    if request["version"] != "1" or type(request["parameters"]) is not dict:
        raise ValueError("unsupported strategy version")
    decimals = []
    for field in ("startCash", "feeUsd", "slippageBps", "quantityStep"):
        value = request[field]
        if (
            not isinstance(value, str)
            or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,18})?", value)
            or len(value) > 60
        ):
            raise ValueError("canonical nonnegative Decimal required")
        if "." in value and len(value.partition(".")[2]) > 18:
            raise ValueError("Decimal scale exceeded")
        decimals.append(Decimal(value))
    if type(request["folds"]) is not list or not 1 <= len(request["folds"]) <= 12:
        raise ValueError("bounded folds required")
    folds = []
    for fold in request["folds"]:
        if type(fold) is not dict or set(fold) != set(FOLD_FIELDS):
            raise ValueError("strict fold required")
        folds.append(Fold(*(parse_time(fold[field]) for field in FOLD_FIELDS)))
    config = ExperimentConfig(
        instrument,
        known[instrument],
        request["strategyId"],
        tuple(sorted(request["parameters"].items())),
        *decimals,
        tuple(folds),
        strategy_version=request["version"],
    )
    if config.folds[-1].test_end > utc_time(now):
        raise ValueError("future test window")
    return config


def canonical_request(request, now):
    config = parse_request(request, now)
    return {
        **request,
        **{
            name: _canonical(getattr(config, field))
            for name, field in (
                ("startCash", "start_cash"),
                ("feeUsd", "fee_usd"),
                ("slippageBps", "slippage_bps"),
                ("quantityStep", "quantity_step"),
            )
        },
        "folds": [
            dict(zip(FOLD_FIELDS, _canonical(fold).values(), strict=True)) for fold in config.folds
        ],
    }
