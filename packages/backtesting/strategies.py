"""Closed deterministic Long/Flat benchmarks. Inputs contain closes only."""

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from enum import StrEnum

from packages.domain.models import decimal_value

from .models import METRICS_VERSION, SIMULATOR_VERSION, fingerprint, validate_parameters


class Target(StrEnum):
    FLAT = "FLAT"
    LONG = "LONG"


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    version: str = "1"


REGISTRY = tuple(StrategyDefinition(name) for name in ("flat-v1", "buy-hold-v1", "sma-cross-v1"))


def warmup_count(strategy_id: str, parameters: tuple[tuple[str, int], ...]) -> int:
    parameters = canonical_parameters(strategy_id, parameters)
    definition = resolve_strategy(strategy_id)
    if definition.warmup_rule == "MAX_SLOW_MINUS_ONE":
        return dict(parameters)["slow"] - 1
    if definition.warmup_rule == "MAX_RSI_PERIOD":
        return dict(parameters)["period"]
    return 0


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


CATALOG_VERSION = "quant-strategy-catalog-v1"
QUANT_WARMUP_VERSION = "quant-common-seed-v1"
FITTING_STATUS = "NOT_TRAINABLE"
RESEARCH_MODES = ("STRICT_PIT", "RETROSPECTIVE_EXPLORATORY")


class StrategyVersionUnavailable(ValueError):
    code = "VERSION_UNAVAILABLE"


@dataclass(frozen=True)
class ParameterDefinition:
    name: str
    value_type: str
    minimum: str
    maximum: str
    maximum_fractional_digits: int = 0


@dataclass(frozen=True)
class QuantStrategyDefinition:
    strategy_id: str
    display_name: str
    description: str
    category: str
    parameter_definitions: tuple[ParameterDefinition, ...]
    warmup_rule: str
    signal_version: str
    implementation_fingerprint: str
    version: str = "1"
    schema_version: str = "quant-lab-v1"
    symbols: tuple[str, ...] = ("SPY", "QQQ")
    currency: str = "USD"
    adjustment: str = "raw"
    feed: str = "IEX"
    interval: str = "15Min"
    input_type: str = "CLOSE"
    target_states: tuple[str, ...] = ("LONG", "FLAT")
    warmup_version: str = QUANT_WARMUP_VERSION
    research_modes: tuple[str, ...] = RESEARCH_MODES
    fitting_status: str = FITTING_STATUS
    limitations: tuple[str, ...] = (
        "PRICE_ONLY", "DIVIDENDS_EXCLUDED", "IEX_PARTIAL_MARKET", "SURVIVORSHIP_BIAS",
        "NO_TRADING_AUTHORIZATION",
    )

    @property
    def execution_available(self):
        """QSL02 registers recursive metadata; QSL04 supplies their machines."""
        return self.strategy_id in ("flat-v1", "buy-hold-v1", "sma-cross-v1")


def canonical_decimal(value, *, places, maximum):
    """A bounded wire decimal, normalized without ambient Decimal rounding."""
    if type(value) is not str or len(value) > 64 or not re.fullmatch(
        rf"(?:0|[1-9][0-9]*)(?:\.[0-9]{{1,{places}}})?", value
    ):
        raise ValueError("finite bounded decimal string required")
    parsed = Decimal(value)
    if not 0 <= parsed <= maximum:
        raise ValueError("decimal outside parameter bounds")
    return value.rstrip("0").rstrip(".") if "." in value else value


def canonical_parameters(strategy_id, parameters):
    """Closed QSL parameters; legacy request normalization remains untouched."""
    if isinstance(parameters, Mapping):
        pairs = tuple(parameters.items())
    elif type(parameters) is tuple and all(
        type(item) is tuple and len(item) == 2 for item in parameters
    ):
        pairs = parameters
    else:
        raise ValueError("parameter mapping or immutable pairs required")
    if any(type(k) is not str for k, _ in pairs) or len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate or invalid parameter key")
    keys = tuple(sorted(k for k, _ in pairs))
    values = dict(pairs)
    definition = resolve_strategy(strategy_id)
    if keys != tuple(sorted(p.name for p in definition.parameter_definitions)):
        raise ValueError("exact registered parameters required")
    for spec in definition.parameter_definitions:
        value = values[spec.name]
        if spec.value_type == "INTEGER":
            if type(value) is not int or not int(spec.minimum) <= value <= int(spec.maximum):
                raise ValueError("integer parameter outside registered bounds")
        else:
            values[spec.name] = canonical_decimal(
                value, places=spec.maximum_fractional_digits, maximum=Decimal(spec.maximum)
            )
    if definition.category == "TREND":
        _validate_cross(values)
    elif definition.category == "MEAN_REVERSION":
        _validate_rsi(values)
    return tuple((k, values[k]) for k in keys)


def _validate_cross(values):
    if values["fast"] >= values["slow"]:
        raise ValueError("cross requires fast < slow")


def _validate_rsi(values):
    if Decimal(values["entry"]) >= Decimal(values["exit"]):
        raise ValueError("RSI requires entry < exit")


def _implementation_fingerprint(strategy_id, signal_version, definition):
    # Normalized reviewed source artifacts plus dependent method versions. QSL04
    # adds the recursive signal artifact before the first executable QSL release.
    functions = (canonical_decimal, canonical_parameters, warmup_count)
    if definition.category == "TREND":
        functions += (_validate_cross,)
    elif definition.category == "MEAN_REVERSION":
        functions += (_validate_rsi,)
    if definition.execution_available:
        functions += (decide, validate_parameters)
    artifacts = tuple(
        inspect.getsource(fn).replace("\r\n", "\n").strip()
        for fn in functions
    )
    metadata = tuple(
        (name, getattr(definition, name)) for name in definition.__dataclass_fields__
        if name != "implementation_fingerprint"
    )
    return fingerprint((
        strategy_id, "1", signal_version, QUANT_WARMUP_VERSION,
        SIMULATOR_VERSION, METRICS_VERSION, metadata, artifacts,
    ))


def _quant_register():
    cross = (
        ParameterDefinition("fast", "INTEGER", "2", "99"),
        ParameterDefinition("slow", "INTEGER", "3", "100"),
    )
    rows = (
        ("flat-v1", "Flat", "Cash reference; always FLAT.", "REFERENCE", (), "NONE"),
        ("buy-hold-v1", "Buy and hold", "Long reference.", "REFERENCE", (), "NONE"),
        ("sma-cross-v1", "SMA crossover", "Close-only SMA crossover.", "TREND", cross,
         "MAX_SLOW_MINUS_ONE"),
        ("ema-cross-v1", "EMA crossover", "Finite common-seed EMA crossover.", "TREND", cross,
         "MAX_SLOW_MINUS_ONE"),
        ("rsi-mean-reversion-v1", "RSI mean reversion", "Wilder RSI with target hysteresis.",
         "MEAN_REVERSION", (
             ParameterDefinition("entry", "DECIMAL_STRING", "0", "100", 4),
             ParameterDefinition("exit", "DECIMAL_STRING", "0", "100", 4),
             ParameterDefinition("period", "INTEGER", "2", "50"),
         ), "MAX_RSI_PERIOD"),
    )
    result = []
    for strategy_id, name, description, category, params, rule in rows:
        signal_version = strategy_id + "-signal-v1"
        definition = QuantStrategyDefinition(
            strategy_id, name, description, category, params, rule, signal_version,
            "",
        )
        if strategy_id in ("ema-cross-v1", "rsi-mean-reversion-v1"):
            definition = replace(
                definition, limitations=definition.limitations + ("FINITE_COMMON_SEED",)
            )
        definition = replace(definition, implementation_fingerprint=_implementation_fingerprint(
            strategy_id, signal_version, definition
        ))
        result.append(definition)
    return tuple(result)


QUANT_REGISTRY = _quant_register()

# Reviewed QSL02 source identities. Recursive entries bind their definition/seed
# primitives only and cannot execute yet. QSL04 must replace those two source
# bindings with the completed machines before any QSL execution is published.
_SOURCE_PINS = (
    ("flat-v1", "5dbe03072ae6f2dcb17f15ec4a3381dd9dcb1870500ce0c7fc20a0f4f3a6758b"),
    ("buy-hold-v1", "fd43e95c4a589d7c83a1abe80bdcfc15d6efcb0569cd5b815320a3a5b1953628"),
    ("sma-cross-v1", "59d73195ce055f9aa0885b5f4b234b4f6dec8bc6648d0c8a0a50858d194f6b94"),
    ("ema-cross-v1", "5846427c3bf3edc315180ab425834a73f5a4f62c859abe56894a27a63f6e3cf0"),
    ("rsi-mean-reversion-v1", "aa80a5f3df41012ab971ca02ee9b55e4b7486568cda8a4f4cc088bb0f3ab4d6b"),
)


def resolve_strategy(
    strategy_id, version="1", expected_fingerprint=None, *, require_execution=False
):
    for definition in QUANT_REGISTRY:
        if (definition.strategy_id, definition.version) == (strategy_id, version):
            if require_execution and not definition.execution_available:
                raise StrategyVersionUnavailable("VERSION_UNAVAILABLE")
            try:
                actual = _implementation_fingerprint(
                    strategy_id, definition.signal_version, definition
                )
            except (TypeError, OSError) as error:
                raise ValueError("strategy implementation fingerprint unavailable") from error
            if actual != dict(_SOURCE_PINS)[strategy_id] or (
                actual != definition.implementation_fingerprint
            ) or (
                expected_fingerprint is not None and expected_fingerprint != actual
            ):
                raise ValueError("strategy implementation fingerprint mismatch")
            return definition
    raise StrategyVersionUnavailable("VERSION_UNAVAILABLE")
