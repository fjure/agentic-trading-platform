"""Closed canonical backtest wire validators, separate from Strict readiness DTOs.

The checked-in contract is executable at this boundary: every exported component
validates its complete JSON shape, including conditionals and nested references.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from pydantic import RootModel, model_validator

FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=(ValueError, TypeError))
def utc_timestamp(value):
    if not isinstance(value, str):
        return True  # JSON Schema formats constrain strings, not nullable branches.
    if not value.endswith("Z"):
        return False
    return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset().total_seconds() == 0


@lru_cache(maxsize=1)
def schema():
    return json.loads(
        (
            Path(__file__).resolve().parents[2] / "contracts/schemas/v1/backtesting.schema.json"
        ).read_text(encoding="utf-8")
    )


@lru_cache(maxsize=100)
def validator(component):
    if component not in schema()["$defs"]:
        raise ValueError("unknown canonical backtest component")
    return Draft202012Validator(
        {**schema(), "$ref": "#/$defs/" + component}, format_checker=FORMATS
    )


class CanonicalBacktestDTO(RootModel[Any]):
    component: ClassVar[str]

    @model_validator(mode="after")
    def closed_shape(self):
        try:
            validator(self.component).validate(self.root)
        except ValidationError:
            raise ValueError("invalid canonical backtest payload") from None
        _legacy_semantics(self.root)
        return self


def _legacy_semantics(value):
    """Cross-field rules also apply to protocols nested in responses/reports."""
    from services.control_api.backtest_dto import (
        BacktestFold,
        BacktestRequest,
        BacktestSmaParameters,
    )

    fields = set(BacktestRequest.model_fields)
    fold_fields = set(BacktestFold.model_fields)

    def walk(item):
        if isinstance(item, list):
            for nested in item:
                walk(nested)
        elif isinstance(item, dict):
            if "strategyId" in item and fields <= item.keys():
                BacktestRequest.model_validate({key: item[key] for key in fields})
            if "trainingStart" in item and fold_fields <= item.keys():
                BacktestFold.model_validate({key: item[key] for key in fold_fields})
            if "fast" in item and "slow" in item:
                BacktestSmaParameters.model_validate({key: item[key] for key in ("fast", "slow")})
            for nested in item.values():
                walk(nested)

    walk(value)


COMPONENT_MODELS = {
    name: type(name, (CanonicalBacktestDTO,), {"component": name, "__module__": __name__})
    for name in schema()["$defs"]
}
globals().update(COMPONENT_MODELS)
BacktestCreateRequest = COMPONENT_MODELS["BacktestRequest"]
