"""Versioned, deterministic decision from admitted neutral market observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from enum import Enum

from packages.domain.models import (
    Capability,
    Choice,
    ChoiceAnswer,
    DecisionCapabilities,
    DecisionResult,
    MarketState,
    NoulAnswer,
    Quality,
    Reconciliation,
    ScoreAnswer,
    TypedAnswers,
    decimal_string,
    decimal_value,
    utc_string,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator

INPUT_FIELDS = frozenset({"market_state.v1"})
RESULT_FIELDS = frozenset({"choice", "typed_answers", "confidence", "distribution"})


def _canonical(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, Decimal):
        return decimal_string(value)
    if isinstance(value, datetime):
        return utc_string(value)
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("unsupported decision input")


def input_hash(
    market_state: MarketState | None, config: BaselineConfig, config_version: str
) -> str:
    """Hash every domain field, including correction versions and provenance."""
    payload = {"market_state": _canonical(market_state), "rules": _canonical(config),
               "config_version": config_version}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    version: str = "neutral-baseline.v1"
    question_version: str = "neutral-questions.v1"
    prompt_version: str = "no-prompt.v1"
    max_state_age_seconds: int = 120
    decision_ttl_seconds: int = 60
    move_threshold: Decimal = Decimal("0.002")
    max_spread_fraction: Decimal = Decimal("0.01")
    minimum_confidence: Decimal = Decimal("0.7")

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in
               (self.version, self.question_version, self.prompt_version)):
            raise ValueError("configuration versions required")
        if any(type(value) is not int or value <= 0 for value in
               (self.max_state_age_seconds, self.decision_ttl_seconds)):
            raise ValueError("positive lifetimes required")
        for name in ("move_threshold", "max_spread_fraction", "minimum_confidence"):
            value = decimal_value(getattr(self, name))
            if not Decimal("0") < value <= Decimal("1"):
                raise ValueError("rule fractions must be in (0, 1]")
            object.__setattr__(self, name, value)


class DeterministicBaselineDecisionEngine:
    def __init__(self, *, clock: Clock, ids: IdGenerator,
                 config: BaselineConfig | None = None,
                 capability: DecisionCapabilities | None = None) -> None:
        self.clock = clock
        self.ids = ids
        self.config = config or BaselineConfig()
        self._capability = capability or DecisionCapabilities(
            "neutral-baseline", tuple(sorted(INPUT_FIELDS)), tuple(sorted(RESULT_FIELDS)),
            Capability.CONFIRMED,
        )

    def capabilities(self) -> DecisionCapabilities:
        return self._capability

    def decide(self, market_state: MarketState | None, config_version: str) -> DecisionResult:
        now = utc_time(self.clock.now())
        if not isinstance(config_version, str) or not config_version:
            raise ValueError("configuration version required")
        if not isinstance(market_state, MarketState):
            market_state = None
        digest = input_hash(market_state, self.config, config_version)
        state_id = (
            market_state.id if isinstance(market_state, MarketState) else "missing-market-state"
        )
        trace_id = market_state.trace_id if isinstance(market_state, MarketState) else "0" * 32
        valid_until = now + timedelta(seconds=self.config.decision_ttl_seconds)
        base = dict(
            id=self.ids.new_id("decision", digest), market_state_id=state_id,
            model_id=None, prompt_version=self.config.prompt_version,
            question_version=self.config.question_version,
            config_version=config_version, input_hash=digest, latency_ms=0,
            valid_until=valid_until, trace_id=trace_id,
        )
        if not self._capability.supports(INPUT_FIELDS, RESULT_FIELDS):
            return DecisionResult(choice=Choice.ABSTAIN, reason_code="ABSTAIN_UNAVAILABLE", **base)
        if not isinstance(market_state, MarketState) or not _admissible(
            market_state, now, self.config
        ):
            return DecisionResult(choice=Choice.ABSTAIN,
                                  reason_code="ABSTAIN_MISSING_EVIDENCE", **base)
        previous = market_state.features["previous_close"]
        last = market_state.features["last_close"]
        with localcontext() as context:
            context.prec = 80
            movement = (last - previous) / previous
        if movement >= self.config.move_threshold:
            choice, confidence, regime = Choice.LONG, Decimal("0.8"), "TREND"
        elif movement <= -self.config.move_threshold:
            choice, confidence, regime = Choice.FLAT, Decimal("0.8"), "TREND"
        else:
            choice, confidence, regime = Choice.ABSTAIN, Decimal("0.6"), "UNCERTAIN"
        if confidence < self.config.minimum_confidence:
            choice = Choice.ABSTAIN
        distribution = {
            "LONG": Decimal("0.8") if choice is Choice.LONG else Decimal("0.1"),
            "FLAT": Decimal("0.8") if choice is Choice.FLAT else Decimal("0.1"),
            "ABSTAIN": Decimal("0.8") if choice is Choice.ABSTAIN else Decimal("0.1"),
        }
        regime_distribution = (
            {"TREND": Decimal("0.8"), "RANGE": Decimal("0.1"),
             "UNCERTAIN": Decimal("0.1")}
            if regime == "TREND" else
            {"TREND": Decimal("0.1"), "RANGE": Decimal("0.1"),
             "UNCERTAIN": Decimal("0.8")}
        )
        answers = TypedAnswers(
            regime=ChoiceAnswer(self.config.question_version, regime,
                                Decimal("0.8"), regime_distribution),
            evidence_quality=ScoreAnswer(
                self.config.question_version, Decimal("9"),
                {"0": "invalid", "9": "admitted"},
                {"0": Decimal("0"), "9": Decimal("1")}, Decimal("1")),
            signal_conflict=NoulAnswer(self.config.question_version, Decimal("0")),
        )
        return DecisionResult(
            choice=choice, model_id=self.config.version,
            reason_code=(
                "BASELINE_SIGNAL" if choice is not Choice.ABSTAIN else "ABSTAIN_LOW_CONFIDENCE"
            ),
            confidence=confidence, distribution=distribution, typed_answers=answers,
            regime=regime, evidence_quality=Decimal("1"),
            signal_conflict=Decimal("0"), **{k: v for k, v in base.items() if k != "model_id"},
        )


def _admissible(state: MarketState, now: datetime, config: BaselineConfig) -> bool:
    if any(item is not Quality.VALID for item in
           (state.quality, state.bar.quality, state.evidence.quality, state.portfolio.quality)):
        return False
    if state.evidence.conflicts or state.portfolio.reconciliation is not Reconciliation.CURRENT:
        return False
    if (state.as_of > now or state.available_at > now or state.bar.available_at > now
            or state.evidence.available_at > now or state.portfolio.as_of > now):
        return False
    if now - state.available_at > timedelta(seconds=config.max_state_age_seconds):
        return False
    if now - state.bar.end_at > timedelta(seconds=config.max_state_age_seconds):
        return False
    if now - state.portfolio.as_of > timedelta(seconds=config.max_state_age_seconds):
        return False
    features = state.features
    if not {"previous_close", "last_close", "window_volume"} <= features.keys():
        return False
    previous, last, volume = (features[key] for key in
                              ("previous_close", "last_close", "window_volume"))
    if previous <= 0 or last <= 0 or volume <= 0 or last != state.bar.close:
        return False
    with localcontext() as context:
        context.prec = 80
        if state.spread > last * config.max_spread_fraction:
            return False
    return True
