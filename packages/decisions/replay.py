"""Local, time-frozen replay of immutable decision inputs.

No decision adapter, broker port, provider factory, or network client is accepted.
Cached responses must already be complete domain ``DecisionResult`` objects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from packages.decisions.baseline import (
    BaselineConfig,
    DeterministicBaselineDecisionEngine,
    _admissible,
    _canonical,
    input_hash,
)
from packages.decisions.mock import MockDecisionEngine
from packages.domain.models import Choice, DecisionResult, MarketState, utc_time

Source = Literal["baseline", "mock", "cached"]


@dataclass(frozen=True, slots=True)
class ReplayCase:
    case_id: str
    state: MarketState
    evaluation_at: datetime

    def __post_init__(self) -> None:
        if not self.case_id or not isinstance(self.state, MarketState):
            raise ValueError("typed replay case and identity required")
        object.__setattr__(self, "evaluation_at", utc_time(self.evaluation_at))
        s, at = self.state, self.evaluation_at
        if any(
            time > at
            for time in (
                s.as_of,
                s.available_at,
                s.event_time,
                s.received_at,
                s.bar.start_at,
                s.bar.end_at,
                s.bar.event_time,
                s.bar.received_at,
                s.bar.available_at,
                s.evidence.available_at,
                s.portfolio.as_of,
            )
        ):
            raise ValueError("future replay evidence")


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    version: str
    rules: BaselineConfig
    expected_model_id: str | None = None
    shadow_minimum_evidence_quality: Decimal | None = None
    shadow_maximum_signal_conflict: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.version or not isinstance(self.rules, BaselineConfig):
            raise ValueError("versioned replay configuration required")
        if self.expected_model_id is not None and (
            not isinstance(self.expected_model_id, str) or not self.expected_model_id.strip()
        ):
            raise ValueError("expected model identity must be nonempty")
        if (self.shadow_minimum_evidence_quality is None) != (
            self.shadow_maximum_signal_conflict is None
        ):
            raise ValueError("shadow thresholds must be paired")
        if self.shadow_minimum_evidence_quality is not None and (
            not Decimal("0.70") <= self.shadow_minimum_evidence_quality <= 1
            or not 0 <= self.shadow_maximum_signal_conflict <= Decimal("0.25")
        ):
            raise ValueError("unsafe shadow replay thresholds")

    @property
    def digest(self) -> str:
        return _hash(_canonical(self))


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """Captured typed response; its binding is verified at replay time."""

    result: DecisionResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, DecisionResult):
            raise ValueError("complete typed cached result required")


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    case_id: str
    source: Source
    evaluation_at: datetime
    result: DecisionResult
    semantic_hash: str
    response_hash: str


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    total: int
    schema_failures: int
    failure_counts: tuple[tuple[str, int], ...]
    abstention_rate: Decimal
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    reported_api_units: Decimal | None
    reported_estimated_cost: Decimal | None
    reported_input_tokens: int | None = None
    reported_output_tokens: int | None = None
    calibration: None = None  # No labeled outcome corpus is supplied.


@dataclass(frozen=True, slots=True)
class ReplayReport:
    corpus_hash: str
    config_hash: str
    records: tuple[ReplayRecord, ...]
    metrics: ReplayMetrics


@dataclass(frozen=True, slots=True)
class ReplayDifference:
    case_id: str
    left_hash: str | None
    right_hash: str | None
    changed_fields: tuple[str, ...]
    technical_changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionCoverage:
    config_versions: tuple[str, ...]
    model_ids: tuple[str, ...]
    prompt_versions: tuple[str, ...]
    question_versions: tuple[str, ...]
    input_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    left_corpus_hash: str
    right_corpus_hash: str
    same_corpus: bool
    left_config_hash: str
    right_config_hash: str
    left_coverage: tuple[str, ...]
    right_coverage: tuple[str, ...]
    left_versions: VersionCoverage
    right_versions: VersionCoverage
    differences: tuple[ReplayDifference, ...]


class _FrozenClock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at


class _StableIds:
    def new_id(self, namespace: str, seed: str | None = None) -> str:
        return hashlib.sha256(f"{namespace}:{seed}".encode()).hexdigest()


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _validated_cached(
    case: ReplayCase, config: ReplayConfig, cached: CachedResponse
) -> DecisionResult:
    result = cached.result
    if (
        result.input_hash != input_hash(case.state, config.rules, config.version)
        or result.market_state_id != case.state.id
        or result.trace_id != case.state.trace_id
        or result.config_version != config.version
        or result.prompt_version != config.rules.prompt_version
        or result.question_version != config.rules.question_version
        or result.model_id
        != ((config.expected_model_id or config.rules.version) if result.typed_answers else None)
        or not (
            case.evaluation_at
            < result.valid_until
            <= case.evaluation_at + timedelta(seconds=config.rules.decision_ttl_seconds)
        )
    ):
        raise ValueError("cached response binding or expiry mismatch")
    if result.choice is not Choice.ABSTAIN:
        distribution = result.distribution
        if (
            not _admissible(case.state, case.evaluation_at, config.rules)
            or result.confidence is None
            or result.confidence < config.rules.minimum_confidence
            or distribution is None
            or any(
                probability >= distribution[result.choice.value]
                for direction, probability in distribution.items()
                if direction != result.choice.value
            )
        ):
            raise ValueError("cached response unsafe for replay input or rules")
    if result.typed_answers is not None and not _admissible(
        case.state, case.evaluation_at, config.rules
    ):
        raise ValueError("cached typed response requires admitted replay input")
    if result.reason_code == "JEV_SHADOW":
        if (
            config.shadow_minimum_evidence_quality is None
            or result.typed_answers is None
            or result.evidence_quality < config.shadow_minimum_evidence_quality
            or result.signal_conflict > config.shadow_maximum_signal_conflict
            or result.regime == "UNCERTAIN"
            or min(
                result.confidence,
                result.typed_answers.regime.confidence,
                result.typed_answers.evidence_quality.confidence,
            )
            < config.rules.minimum_confidence
        ):
            raise ValueError("cached shadow response fails bound eligibility")
    return result


_TECHNICAL_FIELDS = frozenset(
    {"id", "latency_ms", "api_units", "estimated_cost", "input_tokens", "output_tokens"}
)


def _semantic_payload(result: DecisionResult) -> dict[str, object]:
    return {key: value for key, value in _canonical(result).items() if key not in _TECHNICAL_FIELDS}


def replay(
    cases: tuple[ReplayCase, ...],
    config: ReplayConfig,
    *,
    source: Source = "baseline",
    failure: str | None = None,
    cached: dict[str, CachedResponse] | None = None,
) -> ReplayReport:
    """Replay ordered cases with local baseline/mock rules or captured responses."""
    if source not in ("baseline", "mock", "cached"):
        raise ValueError("unsupported replay source")
    if not isinstance(config, ReplayConfig) or not isinstance(cases, tuple):
        raise ValueError("versioned configuration and tuple corpus required")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("duplicate replay case")
    if source != "mock" and failure is not None:
        raise ValueError("failure injection belongs to mock source")
    if source != "cached" and cached is not None:
        raise ValueError("cached responses belong to cached source")
    if source == "cached" and (cached is None or set(cached) != {c.case_id for c in cases}):
        raise ValueError("cached response coverage mismatch")
    records = []
    for case in cases:
        if not isinstance(case, ReplayCase):
            raise ValueError("typed replay case required")
        if source == "cached":
            result = _validated_cached(case, config, cached[case.case_id])  # type: ignore[index]
        else:
            engine_type = (
                MockDecisionEngine if source == "mock" else DeterministicBaselineDecisionEngine
            )
            kwargs = {
                "clock": _FrozenClock(case.evaluation_at),
                "ids": _StableIds(),
                "config": config.rules,
            }
            if source == "mock":
                kwargs["failure"] = failure
            result = engine_type(**kwargs).decide(case.state, config.version)
        records.append(
            ReplayRecord(
                case.case_id,
                source,
                case.evaluation_at,
                result,
                _hash(_semantic_payload(result)),
                _hash(_canonical(result)),
            )
        )
    corpus_hash = _hash(
        [
            {
                "case_id": c.case_id,
                "state": _canonical(c.state),
                "evaluation_at": _canonical(c.evaluation_at),
            }
            for c in cases
        ]
    )
    return ReplayReport(corpus_hash, config.digest, tuple(records), _metrics(records))


def _percentile(values: list[int], numerator: int, denominator: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[(len(ordered) * numerator + denominator - 1) // denominator - 1]


def _metrics(records: list[ReplayRecord]) -> ReplayMetrics:
    total = len(records)
    failures: dict[str, int] = {}
    for record in records:
        if record.result.reason_code.startswith("ABSTAIN_"):
            failures[record.result.reason_code] = failures.get(record.result.reason_code, 0) + 1
    units = [r.result.api_units for r in records if r.result.api_units is not None]
    costs = [r.result.estimated_cost for r in records if r.result.estimated_cost is not None]
    input_counts = [r.result.input_tokens for r in records if r.result.input_tokens is not None]
    output_counts = [r.result.output_tokens for r in records if r.result.output_tokens is not None]
    with localcontext() as context:
        context.prec = 80
        rate = (
            Decimal(sum(r.result.choice is Choice.ABSTAIN for r in records)) / Decimal(total)
            if total
            else Decimal("0")
        )
        usage = sum(units, Decimal("0")) if units else None
        cost = sum(costs, Decimal("0")) if costs else None
    return ReplayMetrics(
        total,
        failures.get("ABSTAIN_INVALID_RESPONSE", 0),
        tuple(sorted(failures.items())),
        rate,
        _percentile([r.result.latency_ms for r in records], 50, 100),
        _percentile([r.result.latency_ms for r in records], 95, 100),
        usage,
        cost,
        sum(input_counts) if input_counts else None,
        sum(output_counts) if output_counts else None,
    )


def compare(left: ReplayReport, right: ReplayReport) -> ReplayComparison:
    """Expose corpus identity, version coverage, and semantic/technical differences."""
    lmap = {record.case_id: record for record in left.records}
    rmap = {record.case_id: record for record in right.records}
    differences = []
    for case_id in sorted(lmap.keys() | rmap.keys()):
        left_record, right_record = lmap.get(case_id), rmap.get(case_id)
        if (
            left_record is not None
            and right_record is not None
            and left_record.response_hash == right_record.response_hash
        ):
            continue
        lv = _canonical(left_record.result) if left_record else {}
        rv = _canonical(right_record.result) if right_record else {}
        differences.append(
            ReplayDifference(
                case_id,
                left_record.semantic_hash if left_record else None,
                right_record.semantic_hash if right_record else None,
                tuple(
                    sorted(
                        key
                        for key in lv.keys() | rv.keys()
                        if key not in _TECHNICAL_FIELDS and lv.get(key) != rv.get(key)
                    )
                ),
                tuple(
                    sorted(
                        key
                        for key in lv.keys() | rv.keys()
                        if key in _TECHNICAL_FIELDS and lv.get(key) != rv.get(key)
                    )
                ),
            )
        )
    return ReplayComparison(
        left.corpus_hash,
        right.corpus_hash,
        left.corpus_hash == right.corpus_hash,
        left.config_hash,
        right.config_hash,
        tuple(sorted(lmap)),
        tuple(sorted(rmap)),
        _version_coverage(left),
        _version_coverage(right),
        tuple(differences),
    )


def _version_coverage(report: ReplayReport) -> VersionCoverage:
    results = [record.result for record in report.records]
    return VersionCoverage(
        tuple(sorted({result.config_version for result in results})),
        tuple(sorted({result.model_id for result in results if result.model_id is not None})),
        tuple(sorted({result.prompt_version for result in results})),
        tuple(sorted({result.question_version for result in results})),
        tuple(sorted({result.input_hash for result in results})),
    )
