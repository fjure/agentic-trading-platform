"""Fail-closed TypeSafe Jev adapter for explicitly enabled shadow evaluation.

The vendor payload stays inside this module. The adapter has no broker or order port.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path
from threading import Lock
from types import MappingProxyType

import httpx

from packages.decisions.baseline import (
    INPUT_FIELDS,
    RESULT_FIELDS,
    BaselineConfig,
    _admissible,
    _canonical,
    input_hash,
)
from packages.decisions.replay import ReplayConfig
from packages.domain.models import (
    Capability,
    Choice,
    ChoiceAnswer,
    DecisionCapabilities,
    DecisionResult,
    MarketState,
    NoulAnswer,
    ScoreAnswer,
    TypedAnswers,
    decimal_value,
    utc_time,
)
from packages.domain.ports import Clock, IdGenerator

_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "agents" / "jev.yaml"
_MODEL = "jev-1.13.0"
_REGIME_ID = "regime"
_QUALITY_ID = "evidence_quality"
_CONFLICT_ID = "signal_conflict"
_DIRECTION_ID = "shadow_direction"
_IDS = frozenset({_REGIME_ID, _QUALITY_ID, _CONFLICT_ID, _DIRECTION_ID})
_REQUIRED_IDS = _IDS - {_DIRECTION_ID}
_REGIMES = frozenset({"TREND", "RANGE", "UNCERTAIN"})
_DIRECTIONS = frozenset({"LONG", "FLAT", "ABSTAIN"})
_MAX_BODY_BYTES = 64 * 1024
_MAX_USAGE_RECORDS = 256
_MAX_TOKEN_COUNT = 9_007_199_254_740_991
_FRACTION_QUANTUM = Decimal("0.000000000000000001")


def _fixed_keys(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid provider schema")
    return value


def _pairs_unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate provider field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonfinite provider number")


def _fraction(value: object) -> Decimal:
    if type(value) is int:
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise ValueError("provider number required")
    result = decimal_value(value)
    if not Decimal("0") <= result <= Decimal("1"):
        raise ValueError("provider fraction outside bounds")
    return result


def _distribution(value: object, expected: frozenset[str]) -> dict[str, Decimal]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("provider distribution options differ")
    result = {key: _fraction(number) for key, number in value.items()}
    if sum(result.values(), Decimal("0")) != 1:
        raise ValueError("provider distribution is not normalized")
    return result


def _choice(value: object, expected: frozenset[str], version: str) -> ChoiceAnswer:
    data = _fixed_keys(value, frozenset({"type", "choice", "probabilities", "confidence"}))
    if data["type"] != "choice" or data["choice"] not in expected:
        raise ValueError("invalid provider choice")
    distribution = _distribution(data["probabilities"], expected)
    maximum = max(distribution.values())
    if [key for key, probability in distribution.items() if probability == maximum] != [
        data["choice"]
    ]:
        raise ValueError("ambiguous or inconsistent provider choice")
    return ChoiceAnswer(version, data["choice"], _fraction(data["confidence"]), distribution)


def _score(value: object, labels: tuple[str, ...], version: str) -> ScoreAnswer:
    data = _fixed_keys(value, frozenset({"type", "score", "legend", "probabilities", "confidence"}))
    legend = {str(index): label for index, label in enumerate(labels)}
    if data["type"] != "score" or data["legend"] != legend:
        raise ValueError("invalid provider score legend")
    distribution = _distribution(data["probabilities"], frozenset(legend))
    given = data["score"]
    if type(given) is int:
        given = Decimal(given)
    if not isinstance(given, Decimal) or not given.is_finite():
        raise ValueError("invalid provider score")
    given = decimal_value(given)
    weighted = sum(
        (
            Decimal(index) * probability
            for index, probability in ((key, distribution[key]) for key in legend)
        ),
        Decimal("0"),
    )
    if not 0 <= given <= 9 or abs(given - weighted) > Decimal("0.000001"):
        raise ValueError("provider score differs from probabilities")
    return ScoreAnswer(version, weighted, legend, distribution, _fraction(data["confidence"]))


def _noul(value: object, version: str) -> NoulAnswer:
    data = _fixed_keys(value, frozenset({"type", "noul"}))
    if data["type"] != "noul":
        raise ValueError("invalid provider noul")
    return NoulAnswer(version, _fraction(data["noul"]))


def _freeze_questions(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, dict) or set(raw) not in (_REQUIRED_IDS, _IDS):
        raise ValueError("fixed atomic questions required")
    frozen: dict[str, object] = {}
    for question_id, question in raw.items():
        if not isinstance(question, Mapping) or not isinstance(question.get("instructions"), str):
            raise ValueError("invalid atomic question")
        if not question["instructions"].strip() or set(question) != {
            "type",
            "instructions",
            "criteria",
        }:
            raise ValueError("invalid atomic question fields")
        expected_type = (
            "score"
            if question_id == _QUALITY_ID
            else "noul"
            if question_id == _CONFLICT_ID
            else "choice"
        )
        if question["type"] != expected_type:
            raise ValueError("atomic question type changed")
        criteria = question["criteria"]
        if question_id == _QUALITY_ID:
            if (
                not isinstance(criteria, (list, tuple))
                or len(criteria) != 10
                or any(not isinstance(label, str) or not label.strip() for label in criteria)
            ):
                raise ValueError("score requires ten ordered levels")
            criteria = tuple(criteria)
        else:
            expected = (
                {"true", "false"}
                if question_id == _CONFLICT_ID
                else _REGIMES
                if question_id == _REGIME_ID
                else _DIRECTIONS
            )
            if (
                not isinstance(criteria, Mapping)
                or set(criteria) != expected
                or any(
                    not isinstance(label, str) or not label.strip() for label in criteria.values()
                )
            ):
                raise ValueError("atomic criteria changed")
            criteria = MappingProxyType(dict(criteria))
        frozen[question_id] = MappingProxyType(
            {
                "type": question["type"],
                "instructions": question["instructions"],
                "criteria": criteria,
            }
        )
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class JevConfig:
    version: str
    model: str
    prompt_version: str
    question_version: str
    enabled: bool
    mode: str
    max_state_age_seconds: int
    decision_ttl_seconds: int
    minimum_confidence: Decimal
    minimum_evidence_quality: Decimal
    maximum_signal_conflict: Decimal
    questions: Mapping[str, object]

    def __post_init__(self) -> None:
        if (
            self.model != _MODEL
            or self.mode != "SHADOW"
            or type(self.enabled) is not bool
            or any(
                not isinstance(value, str) or not value.strip()
                for value in (self.version, self.prompt_version, self.question_version)
            )
        ):
            raise ValueError("unsafe Jev configuration")
        if (
            type(self.max_state_age_seconds) is not int
            or not 0 < self.max_state_age_seconds <= 120
            or type(self.decision_ttl_seconds) is not int
            or not 0 < self.decision_ttl_seconds <= 5
        ):
            raise ValueError("unsafe Jev timing")
        for name in ("minimum_confidence", "minimum_evidence_quality", "maximum_signal_conflict"):
            value = decimal_value(getattr(self, name))
            if name == "maximum_signal_conflict" and not 0 <= value <= Decimal("0.25"):
                raise ValueError("unsafe Jev threshold")
            if name != "maximum_signal_conflict" and not Decimal("0.70") <= value <= 1:
                raise ValueError("unsafe Jev threshold")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "questions", _freeze_questions(dict(self.questions)))

    @property
    def rules(self) -> BaselineConfig:
        return BaselineConfig(
            version=self.model,
            question_version=self.question_version,
            prompt_version=self.prompt_version,
            max_state_age_seconds=self.max_state_age_seconds,
            decision_ttl_seconds=self.decision_ttl_seconds,
            minimum_confidence=self.minimum_confidence,
        )

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            _canonical(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def replay_config(self, caller_config_version: str) -> ReplayConfig:
        """Bind ReplayCache to every question and policy byte through the version."""
        if not isinstance(caller_config_version, str) or not caller_config_version.strip():
            raise ValueError("configuration version required")
        return ReplayConfig(
            f"{caller_config_version}:{self.digest}",
            self.rules,
            expected_model_id=self.model,
            shadow_minimum_evidence_quality=self.minimum_evidence_quality,
            shadow_maximum_signal_conflict=self.maximum_signal_conflict,
        )


def load_config() -> JevConfig:
    """Read the checked-in JSON subset of YAML with no YAML tags or external paths."""
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"), object_pairs_hook=_pairs_unique)
    _fixed_keys(raw, frozenset(JevConfig.__dataclass_fields__))
    return JevConfig(**raw)


@dataclass(frozen=True, slots=True)
class JevUsage:
    input_tokens: int
    output_tokens: int


class JevDecisionEngine:
    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        api_key: str | None = None,
        config: JevConfig | None = None,
        transport: httpx.BaseTransport | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.clock = clock
        self.ids = ids
        self.config = config if config is not None else load_config()
        if not isinstance(self.config, JevConfig):
            raise ValueError("immutable Jev configuration required")
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("only local MockTransport injection is supported")
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key.strip()
            or "\r" in api_key
            or "\n" in api_key
        ):
            raise ValueError("invalid injected Jev credential")
        self._api_key = api_key
        self._transport = transport
        self._monotonic_ns = monotonic_ns
        self._usage: OrderedDict[str, JevUsage] = OrderedDict()
        self._usage_lock = Lock()

    def capabilities(self) -> DecisionCapabilities:
        available = (
            Capability.CONFIRMED
            if self.config.enabled and self._api_key
            else Capability.UNAVAILABLE
        )
        return DecisionCapabilities(
            "jev-shadow", tuple(sorted(INPUT_FIELDS)), tuple(sorted(RESULT_FIELDS)), available
        )

    def usage_for(self, decision_id: str) -> JevUsage | None:
        """Return bounded, normalized token counts without retaining the vendor body."""
        with self._usage_lock:
            return self._usage.get(decision_id)

    def decide(self, market_state: MarketState | None, config_version: str) -> DecisionResult:
        started = self._monotonic_ns()
        captured_at = utc_time(self.clock.now())
        if not isinstance(config_version, str) or not config_version.strip():
            raise ValueError("configuration version required")
        state = market_state if isinstance(market_state, MarketState) else None
        replay_config = self.config.replay_config(config_version)
        digest = input_hash(state, replay_config.rules, replay_config.version)
        expiry = captured_at + timedelta(seconds=self.config.decision_ttl_seconds)
        if state is not None:
            max_age = timedelta(seconds=self.config.max_state_age_seconds)
            expiry = min(
                expiry,
                state.available_at + max_age,
                state.bar.end_at + max_age,
                state.portfolio.as_of + max_age,
            )
        base = dict(
            id=self.ids.new_id("decision", digest),
            market_state_id=state.id if state else "missing-market-state",
            prompt_version=self.config.prompt_version,
            question_version=self.config.question_version,
            config_version=replay_config.version,
            input_hash=digest,
            valid_until=expiry,
            trace_id=state.trace_id if state else "0" * 32,
        )

        def fallback(reason: str) -> DecisionResult:
            latency = max(0, (self._monotonic_ns() - started) // 1_000_000)
            return DecisionResult(
                choice=Choice.ABSTAIN, model_id=None, reason_code=reason, latency_ms=latency, **base
            )

        if not self.capabilities().supports(INPUT_FIELDS, RESULT_FIELDS):
            return fallback("ABSTAIN_UNAVAILABLE")
        if state is None or not _admissible(state, captured_at, self.config.rules):
            return fallback("ABSTAIN_MISSING_EVIDENCE")
        questions = _canonical(self.config.questions)
        try:
            with httpx.Client(
                transport=self._transport,
                verify=True,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(5.0),
            ) as client:
                with client.stream(
                    "POST",
                    _ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    json={
                        "model": self.config.model,
                        "state": _canonical(state),
                        "questions": questions,
                    },
                ) as response:
                    if response.status_code != 200:
                        return fallback("ABSTAIN_UNAVAILABLE")
                    chunks: list[bytes] = []
                    size = 0
                    stream = (
                        [response.content] if response.is_stream_consumed else response.iter_raw()
                    )
                    for chunk in stream:
                        size += len(chunk)
                        if size > _MAX_BODY_BYTES:
                            return fallback("ABSTAIN_INVALID_RESPONSE")
                        chunks.append(chunk)
            if self._monotonic_ns() - started >= 5_000_000_000 or self.clock.now() >= expiry:
                return fallback("ABSTAIN_UNAVAILABLE")
            raw = json.loads(
                b"".join(chunks),
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_reject_constant,
                object_pairs_hook=_pairs_unique,
            )
            data = _fixed_keys(raw, frozenset({"model", "answers", "usage"}))
            if data["model"] != self.config.model:
                raise ValueError("provider model differs")
            answers = data["answers"]
            if not isinstance(answers, dict) or set(answers) != set(self.config.questions):
                raise ValueError("provider answer IDs differ")
            version = self.config.question_version
            regime = _choice(answers[_REGIME_ID], _REGIMES, version)
            labels = self.config.questions[_QUALITY_ID]["criteria"]
            quality = _score(answers[_QUALITY_ID], labels, version)
            conflict = _noul(answers[_CONFLICT_ID], version)
            direction = (
                _choice(answers[_DIRECTION_ID], _DIRECTIONS, version)
                if _DIRECTION_ID in self.config.questions
                else None
            )
            usage = _fixed_keys(data["usage"], frozenset({"input_tokens", "output_tokens"}))
            if any(
                type(value) is not int or not 0 <= value <= _MAX_TOKEN_COUNT
                for value in usage.values()
            ):
                raise ValueError("invalid provider usage")
            typed = TypedAnswers(regime, quality, conflict, direction)
            confidence = (
                direction.confidence if direction else min(regime.confidence, quality.confidence)
            )
            distribution = (
                dict(direction.distribution)
                if direction
                else {"LONG": Decimal("0"), "FLAT": Decimal("0"), "ABSTAIN": Decimal("1")}
            )
            if (
                regime.confidence < self.config.minimum_confidence
                or quality.confidence < self.config.minimum_confidence
                or confidence < self.config.minimum_confidence
            ):
                reason = "ABSTAIN_LOW_CONFIDENCE"
            elif conflict.noul > self.config.maximum_signal_conflict:
                reason = "ABSTAIN_SIGNAL_CONFLICT"
            elif regime.choice == "UNCERTAIN":
                reason = "ABSTAIN_UNCERTAIN_REGIME"
            else:
                reason = "JEV_SHADOW"
            with localcontext() as context:
                context.prec = 80
                quality_fraction = (quality.score / Decimal("9")).quantize(
                    _FRACTION_QUANTUM, rounding=ROUND_DOWN
                )
            if quality_fraction < self.config.minimum_evidence_quality:
                reason = "ABSTAIN_LOW_EVIDENCE_QUALITY"
            result = DecisionResult(
                choice=Choice.ABSTAIN,
                model_id=data["model"],
                reason_code=reason,
                latency_ms=max(0, (self._monotonic_ns() - started) // 1_000_000),
                confidence=confidence,
                distribution=distribution,
                typed_answers=typed,
                api_units=Decimal(usage["input_tokens"]),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                estimated_cost=None,
                regime=regime.choice,
                evidence_quality=quality_fraction,
                signal_conflict=conflict.noul,
                shadow_choice=Choice(direction.choice) if direction else None,
                **base,
            )
            finished_at = utc_time(self.clock.now())
            if self._monotonic_ns() - started >= 5_000_000_000 or finished_at >= expiry:
                return fallback("ABSTAIN_UNAVAILABLE")
            if not _admissible(state, finished_at, self.config.rules):
                return fallback("ABSTAIN_MISSING_EVIDENCE")
            with self._usage_lock:
                self._usage[result.id] = JevUsage(usage["input_tokens"], usage["output_tokens"])
                while len(self._usage) > _MAX_USAGE_RECORDS:
                    self._usage.popitem(last=False)
            return result
        except httpx.HTTPError:
            return fallback("ABSTAIN_UNAVAILABLE")
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return fallback("ABSTAIN_INVALID_RESPONSE")
