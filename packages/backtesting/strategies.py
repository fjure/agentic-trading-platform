"""Closed deterministic Long/Flat benchmarks. Inputs contain closes only."""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum

from packages.domain.models import decimal_value

from .models import validate_parameters


class Target(StrEnum):
    FLAT = "FLAT"
    LONG = "LONG"


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    version: str = "1"


REGISTRY = tuple(StrategyDefinition(name) for name in ("flat-v1", "buy-hold-v1", "sma-cross-v1"))


def warmup_count(strategy_id: str, parameters: tuple[tuple[str, int], ...]) -> int:
    validate_parameters(strategy_id, parameters)
    return dict(parameters)["slow"] - 1 if strategy_id == "sma-cross-v1" else 0


def decide(
    strategy_id: str, parameters: tuple[tuple[str, int], ...], closes: tuple[Decimal, ...]
) -> Target:
    validate_parameters(strategy_id, parameters)
    if type(closes) is not tuple or any(
        not isinstance(c, Decimal) or decimal_value(c) <= 0 for c in closes
    ):
        raise ValueError("immutable positive closes required")
    if not closes:
        raise ValueError("completed signal bar required")
    if strategy_id == "flat-v1":
        return Target.FLAT
    if strategy_id == "buy-hold-v1":
        return Target.LONG
    fast, slow = (value for _, value in parameters)
    if len(closes) < slow:
        raise ValueError("missing warmup")
    with localcontext() as ctx:
        ctx.prec = 38
        return (
            Target.LONG if sum(closes[-fast:]) / fast > sum(closes[-slow:]) / slow else Target.FLAT
        )
