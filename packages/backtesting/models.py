"""Immutable experiment protocol and canonical evidence identities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from packages.domain.models import decimal_value, utc_time
from packages.research.models import content_fingerprint

SIMULATOR_VERSION = "backtest-simulator-v1"
METRICS_VERSION = "backtest-metrics-v1"
WARMUP_VERSION = "regular-bars-slow-minus-one-v1"


class BacktestBlocked(ValueError):
    def __init__(self, code: str, reference: str | None = None) -> None:
        self.code = code
        self.reference = reference
        super().__init__(code)


class BacktestBudgetExceeded(ValueError):
    code = "BUDGET_EXHAUSTED"


def fingerprint(value: object) -> str:
    return content_fingerprint(value)


def label(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
        raise ValueError("invalid reference")
    if "://" in value:
        raise ValueError("URLs are not references")


def digest(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("invalid SHA256 fingerprint")


@dataclass(frozen=True)
class Fold:
    training_start: datetime
    training_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        for value in (self.training_start, self.training_end, self.test_start, self.test_end):
            utc_time(value)
        if not self.training_start < self.training_end <= self.test_start < self.test_end:
            raise ValueError("training must precede a nonempty test window")


def validate_parameters(strategy_id: str, parameters: tuple[tuple[str, int], ...]) -> None:
    if type(parameters) is not tuple or any(type(item) is not tuple for item in parameters):
        raise ValueError("immutable parameter pairs required")
    if strategy_id in ("flat-v1", "buy-hold-v1"):
        if parameters:
            raise ValueError("strategy takes no parameters")
    elif strategy_id == "sma-cross-v1":
        if len(parameters) != 2 or tuple(key for key, _ in parameters) != ("fast", "slow"):
            raise ValueError("canonical fast/slow parameters required")
        fast, slow = (value for _, value in parameters)
        if type(fast) is not int or type(slow) is not int or not 2 <= fast < slow <= 100:
            raise ValueError("SMA requires 2 <= fast < slow <= 100")
    else:
        raise ValueError("unregistered strategy")


@dataclass(frozen=True)
class ExperimentConfig:
    instrument_id: UUID
    symbol: str
    strategy_id: str
    parameters: tuple[tuple[str, int], ...]
    start_cash: Decimal
    fee_usd: Decimal
    slippage_bps: Decimal
    quantity_step: Decimal
    folds: tuple[Fold, ...]
    strategy_version: str = "1"
    feed: str = "iex"
    interval: str = "15Min"
    adjustment: str = "raw"
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, UUID) or self.symbol not in ("SPY", "QQQ"):
            raise ValueError("only one SPY or QQQ instrument is supported")
        if (self.strategy_version, self.feed, self.interval, self.adjustment, self.currency) != (
            "1",
            "iex",
            "15Min",
            "raw",
            "USD",
        ):
            raise ValueError("unsupported experiment version or market")
        validate_parameters(self.strategy_id, self.parameters)
        for value in (self.start_cash, self.fee_usd, self.slippage_bps, self.quantity_step):
            if not isinstance(value, Decimal):
                raise ValueError("Decimal values required")
            decimal_value(value)
        if not 0 < self.start_cash <= Decimal("1000000000"):
            raise ValueError("invalid starting cash")
        if not 0 <= self.fee_usd <= 1000 or not 0 <= self.slippage_bps <= 100:
            raise ValueError("invalid costs")
        if self.quantity_step != Decimal("0.000001"):
            raise ValueError("unsupported quantity raster")
        if type(self.folds) is not tuple or not 1 <= len(self.folds) <= 12:
            raise ValueError("one to twelve immutable folds required")
        if any(not isinstance(fold, Fold) for fold in self.folds):
            raise ValueError("typed folds required")
        if any(
            left.test_end > right.test_start
            for left, right in zip(self.folds, self.folds[1:], strict=False)
        ):
            raise ValueError("test windows must be chronological and disjoint")
        if self.folds[-1].test_end - min(f.training_start for f in self.folds) > timedelta(
            days=366
        ):
            raise ValueError("experiment exceeds 366 days")

    @property
    def input_hash(self) -> str:
        return fingerprint(self)


@dataclass(frozen=True, order=True)
class DataReference:
    revision_id: UUID
    fingerprint: str
    cutoff: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, UUID):
            raise ValueError("revision UUID required")
        digest(self.fingerprint)
        utc_time(self.cutoff)


@dataclass(frozen=True)
class WarmupBinding:
    warmup_start: datetime
    warmup_end: datetime
    expected_bar_starts: tuple[datetime, ...]
    rule_version: str = WARMUP_VERSION

    def __post_init__(self) -> None:
        utc_time(self.warmup_start)
        utc_time(self.warmup_end)
        if self.rule_version != WARMUP_VERSION or self.warmup_start > self.warmup_end:
            raise ValueError("invalid warmup binding")
        if type(self.expected_bar_starts) is not tuple:
            raise ValueError("immutable bar identities required")
        for start in self.expected_bar_starts:
            utc_time(start)
            if not self.warmup_start <= start < self.warmup_end:
                raise ValueError("warmup identity outside binding")
        if tuple(sorted(set(self.expected_bar_starts))) != self.expected_bar_starts:
            raise ValueError("warmup identities must be unique and chronological")
        expected_start = (
            self.expected_bar_starts[0] if self.expected_bar_starts else self.warmup_end
        )
        if self.warmup_start != expected_start:
            raise ValueError("warmup start must match its first expected identity")


@dataclass(frozen=True)
class Manifest:
    config: ExperimentConfig
    snapshot_version: int
    snapshot_at: datetime
    decision_data: tuple[DataReference, ...]
    outcome_data: tuple[DataReference, ...]
    calendar_revisions: tuple[str, ...]
    quality_revisions: tuple[str, ...]
    coverage_revisions: tuple[str, ...]
    warmups: tuple[WarmupBinding, ...]
    code_version: str
    simulator_version: str = SIMULATOR_VERSION
    metrics_version: str = METRICS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.config, ExperimentConfig):
            raise ValueError("typed config required")
        if type(self.snapshot_version) is not int or self.snapshot_version < 1:
            raise ValueError("committed snapshot required")
        utc_time(self.snapshot_at)
        if self.config.folds[-1].test_end > self.snapshot_at:
            raise ValueError("future test window")
        if (self.simulator_version, self.metrics_version) != (SIMULATOR_VERSION, METRICS_VERSION):
            raise ValueError("unsupported model versions")
        label(self.code_version)
        for refs in (self.decision_data, self.outcome_data):
            if type(refs) is not tuple or any(not isinstance(r, DataReference) for r in refs):
                raise ValueError("immutable typed evidence required")
            if tuple(sorted(set(refs))) != refs:
                raise ValueError("data references must be sorted and unique")
        for refs in (self.calendar_revisions, self.quality_revisions, self.coverage_revisions):
            if type(refs) is not tuple or not refs or tuple(sorted(set(refs))) != refs:
                raise ValueError("sorted revision evidence required")
            for ref in refs:
                label(ref)
        if type(self.warmups) is not tuple or len(self.warmups) != len(self.config.folds):
            raise ValueError("one warmup binding per fold required")
        required = dict(self.config.parameters).get("slow", 1) - 1
        for fold, binding in zip(self.config.folds, self.warmups, strict=True):
            if not isinstance(binding, WarmupBinding):
                raise ValueError("typed warmup required")
            if binding.warmup_end != fold.test_start or binding.warmup_start < fold.training_start:
                raise ValueError("warmup must be bound within training")
            if len(binding.expected_bar_starts) != required:
                raise ValueError("wrong warmup bar count")

    @property
    def manifest_hash(self) -> str:
        return fingerprint(self)
