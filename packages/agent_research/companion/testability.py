"""Deterministic assessment of confirmed hypotheses using trusted Quant bindings."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from packages.backtesting.experiment import PROTOCOL_VERSION
from packages.backtesting.models import fingerprint
from packages.backtesting.strategies import (
    CATALOG_VERSION,
    RESEARCH_MODES,
    canonical_parameters,
    resolve_strategy,
)

SIGNATURE_VERSION = "agent-hypothesis-signature-v1"
MAPPING_VERSION = "agent-testability-mapping-v1"
type Parameters = tuple[tuple[str, int | str], ...]


class Classification(StrEnum):
    EXACTLY_TESTABLE = "EXACTLY_TESTABLE"
    TESTABLE_WITH_DECLARED_APPROXIMATION = "TESTABLE_WITH_DECLARED_APPROXIMATION"
    REQUIRES_NEW_STRATEGY = "REQUIRES_NEW_STRATEGY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNSUPPORTED = "UNSUPPORTED"


class Reason(StrEnum):
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    OUTSIDE_RESEARCH_SCOPE = "OUTSIDE_RESEARCH_SCOPE"
    NO_EXACT_MAPPING = "NO_EXACT_MAPPING"
    MAPPING_UNAVAILABLE = "MAPPING_UNAVAILABLE"
    DATA_NOT_VERIFIED = "DATA_NOT_VERIFIED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_QUALITY_INSUFFICIENT = "DATA_QUALITY_INSUFFICIENT"
    DATA_RIGHTS_UNAVAILABLE = "DATA_RIGHTS_UNAVAILABLE"
    STRICT_PIT_UNVERIFIED = "STRICT_PIT_UNVERIFIED"
    APPROXIMATION_ACCEPTANCE_REQUIRED = "APPROXIMATION_ACCEPTANCE_REQUIRED"
    EXACT_MAPPING_VERIFIED = "EXACT_MAPPING_VERIFIED"
    APPROXIMATION_VERIFIED = "APPROXIMATION_VERIFIED"


class Option(StrEnum):
    CLARIFY_HYPOTHESIS = "CLARIFY_HYPOTHESIS"
    RESEARCH_DIFFERENT_QUESTION = "RESEARCH_DIFFERENT_QUESTION"
    DEVELOP_MAPPING = "DEVELOP_MAPPING"
    RESOLVE_DATA_RESTRICTIONS = "RESOLVE_DATA_RESTRICTIONS"
    REVIEW_APPROXIMATION = "REVIEW_APPROXIMATION"
    PREPARE_VALIDATION = "PREPARE_VALIDATION"


@dataclass(frozen=True, slots=True)
class HypothesisIdentity:
    market: str | None
    trigger: str | None
    signal: str | None
    observation_window: str | None
    target: str | None
    measurement_horizon: str | None
    benchmark: str | None
    evaluation_criterion: str | None
    constraints: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_document(cls, hypothesis: dict) -> "HypothesisIdentity":
        return cls(
            hypothesis["market"], hypothesis["trigger"], hypothesis["signal"],
            hypothesis["observationWindow"], hypothesis["target"], hypothesis["measurementHorizon"],
            hypothesis["benchmark"], hypothesis["evaluationCriterion"],
        )

    def fields(self) -> tuple[tuple[str, str | None], ...]:
        return (
            ("MARKET", self.market),
            ("TRIGGER", self.trigger),
            ("SIGNAL", self.signal),
            ("OBSERVATION_WINDOW", self.observation_window),
            ("TARGET", self.target),
            ("MEASUREMENT_HORIZON", self.measurement_horizon),
            ("BENCHMARK", self.benchmark),
            ("EVALUATION_CRITERION", self.evaluation_criterion),
        )


def _normalized(value: str | None) -> str | None:
    return None if value is None else unicodedata.normalize("NFC", value).strip() or None


def hypothesis_signature(hypothesis: HypothesisIdentity) -> str | None:
    """Ignore presentation and evidence revisions, never infer semantic equivalence."""
    fields = tuple((name, _normalized(value)) for name, value in hypothesis.fields())
    constraints = tuple(
        (_normalized(name), _normalized(value)) for name, value in hypothesis.constraints
    )
    if any(value is None for _, value in fields):
        return None
    if any(not name or not value for name, value in constraints) or len(
        {name for name, _ in constraints}
    ) != len(constraints):
        raise ValueError("constraints require unique nonempty names and values")
    return fingerprint((SIGNATURE_VERSION, fields, sorted(constraints)))


@dataclass(frozen=True, slots=True)
class StrategyMapping:
    """A trusted semantic mapping, never constructed from a model recommendation."""

    hypothesis_signature: str
    strategy_id: str
    strategy_version: str
    strategy_fingerprint: str
    parameters: Parameters
    catalog_version: str
    protocol_version: str
    research_mode: str

    @property
    def binding(self) -> str:
        return fingerprint((MAPPING_VERSION, self))


def ema_crossover_mapping(
    hypothesis: HypothesisIdentity, parameters: Parameters, research_mode: str
) -> StrategyMapping | None:
    """Map the closed QSL EMA comparison, after explicit operator confirmation."""
    signature = hypothesis_signature(hypothesis)
    if signature is None or hypothesis.observation_window is None:
        return None
    try:
        strategy = resolve_strategy("ema-cross-v1", require_execution=True)
        parameters = canonical_parameters(strategy.strategy_id, parameters)
        start, end = hypothesis.observation_window.split("/")
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    values = dict(parameters)
    supported_constraints = {
        "currency": strategy.currency,
        "feed": strategy.feed,
        "adjustment": strategy.adjustment,
        "interval": strategy.interval,
    }
    if (
        hypothesis.market not in strategy.symbols
        or hypothesis.trigger != "EMA crossover"
        or hypothesis.signal != f"EMA({values['fast']}) above EMA({values['slow']})"
        or hypothesis.target != "net return"
        or hypothesis.measurement_horizon != "entire evaluation window"
        or hypothesis.benchmark != "buy-hold-v1"
        or hypothesis.evaluation_criterion != "return greater than benchmark"
        or start_date >= end_date
        or (start_date.isoformat(), end_date.isoformat()) != (start, end)
        or research_mode not in strategy.research_modes
        or any(supported_constraints.get(key) != value for key, value in hypothesis.constraints)
    ):
        return None
    return StrategyMapping(
        signature,
        strategy.strategy_id,
        strategy.version,
        strategy.implementation_fingerprint,
        parameters,
        CATALOG_VERSION,
        PROTOCOL_VERSION,
        research_mode,
    )


@dataclass(frozen=True, slots=True)
class DataSuitability:
    mapping_binding: str
    available: bool
    quality_sufficient: bool
    rights_available: bool
    strict_pit_verified: bool


@dataclass(frozen=True, slots=True)
class Approximation:
    idea_id: str
    idea_version: int
    original_signature: str
    substitute: HypothesisIdentity
    mapping: StrategyMapping
    difference: str
    limitations: tuple[str, ...]

    @property
    def binding(self) -> str:
        return fingerprint((MAPPING_VERSION, self))


@dataclass(frozen=True, slots=True)
class Assessment:
    classification: Classification | None
    reasons: tuple[Reason, ...]
    options: tuple[Option, ...]
    signature: str | None
    missing_fields: tuple[str, ...] = ()
    mapping: StrategyMapping | None = None
    approximation: Approximation | None = None
    approximation_accepted: bool = False
    limitations: tuple[str, ...] = ()


def _mapping_available(mapping: StrategyMapping, research_mode: str) -> bool:
    if (mapping.catalog_version, mapping.protocol_version, mapping.research_mode) != (
        CATALOG_VERSION,
        PROTOCOL_VERSION,
        research_mode,
    ):
        return False
    try:
        strategy = resolve_strategy(
            mapping.strategy_id,
            mapping.strategy_version,
            mapping.strategy_fingerprint,
            require_execution=True,
        )
        return (
            research_mode in strategy.research_modes
            and canonical_parameters(
                mapping.strategy_id,
                mapping.parameters,
            )
            == mapping.parameters
        )
    except ValueError:
        return False


def _data_reasons(mapping: StrategyMapping, data: DataSuitability | None) -> tuple[Reason, ...]:
    if data is None or data.mapping_binding != mapping.binding:
        return (Reason.DATA_NOT_VERIFIED,)
    checks = (
        (data.available, Reason.DATA_UNAVAILABLE),
        (data.quality_sufficient, Reason.DATA_QUALITY_INSUFFICIENT),
        (data.rights_available, Reason.DATA_RIGHTS_UNAVAILABLE),
        (
            mapping.research_mode != "STRICT_PIT" or data.strict_pit_verified,
            Reason.STRICT_PIT_UNVERIFIED,
        ),
    )
    return tuple(reason for passed, reason in checks if not passed)


def preliminary_testability(hypothesis: HypothesisIdentity) -> dict | None:
    """Project catalog fit without treating unverified data as suitable."""
    if hypothesis_signature(hypothesis) is None:
        return None
    mapping = None
    signal = re.fullmatch(r"EMA\(([0-9]{1,3})\) above EMA\(([0-9]{1,3})\)", hypothesis.signal or "")
    if signal is not None:
        parameters = (("fast", int(signal[1])), ("slow", int(signal[2])))
        for mode in RESEARCH_MODES:
            mapping = ema_crossover_mapping(hypothesis, parameters, mode)
            if mapping is not None:
                break
    assessment = assess_testability(
        hypothesis,
        research_mode=mapping.research_mode if mapping is not None else RESEARCH_MODES[0],
        in_scope=True,
        mapping=mapping,
    )
    return {
        "classification": assessment.classification,
        "catalogVersion": CATALOG_VERSION,
        "mappingVersion": MAPPING_VERSION,
        "reasons": list(assessment.reasons),
        "limitations": list(assessment.limitations),
        "options": list(assessment.options),
    }


def assess_testability(
    hypothesis: HypothesisIdentity,
    *,
    research_mode: str,
    in_scope: bool,
    mapping: StrategyMapping | None = None,
    data: DataSuitability | None = None,
    approximation: Approximation | None = None,
    idea_binding: tuple[str, int] | None = None,
    accepted_approximation_binding: str | None = None,
    required_data_missing: bool = False,
) -> Assessment:
    """Classify only the confirmed semantics. Acceptance never authorizes an experiment."""
    signature = hypothesis_signature(hypothesis)
    if signature is None:
        return Assessment(
            None,
            (Reason.CLARIFICATION_REQUIRED,),
            (Option.CLARIFY_HYPOTHESIS,),
            None,
            missing_fields=tuple(
                name for name, value in hypothesis.fields() if not _normalized(value)
            ),
        )
    if not in_scope:
        return Assessment(
            Classification.UNSUPPORTED,
            (Reason.OUTSIDE_RESEARCH_SCOPE,),
            (Option.RESEARCH_DIFFERENT_QUESTION,),
            signature,
        )
    no_mapping = Reason.NO_EXACT_MAPPING
    if mapping is not None and mapping.hypothesis_signature == signature:
        if _mapping_available(mapping, research_mode):
            reasons = _data_reasons(mapping, data)
            return Assessment(
                Classification.INSUFFICIENT_DATA if reasons else Classification.EXACTLY_TESTABLE,
                reasons or (Reason.EXACT_MAPPING_VERIFIED,),
                (Option.RESOLVE_DATA_RESTRICTIONS if reasons else Option.PREPARE_VALIDATION,),
                signature,
                mapping=mapping,
            )
        no_mapping = Reason.MAPPING_UNAVAILABLE
    if (
        approximation is not None
        and idea_binding == (approximation.idea_id, approximation.idea_version)
        and approximation.original_signature == signature
    ):
        substitute_signature = hypothesis_signature(approximation.substitute)
        if (
            substitute_signature is not None
            and substitute_signature != signature
            and substitute_signature == approximation.mapping.hypothesis_signature
            and approximation.difference.strip()
            and approximation.limitations
            and all(item.strip() for item in approximation.limitations)
            and _mapping_available(approximation.mapping, research_mode)
        ):
            reasons = _data_reasons(approximation.mapping, data)
            accepted = accepted_approximation_binding == approximation.binding
            if not accepted:
                reasons += (Reason.APPROXIMATION_ACCEPTANCE_REQUIRED,)
            blocked_data = any(
                reason != Reason.APPROXIMATION_ACCEPTANCE_REQUIRED for reason in reasons
            )
            if blocked_data:
                classification = Classification.INSUFFICIENT_DATA
                option = Option.RESOLVE_DATA_RESTRICTIONS
            else:
                classification = Classification.TESTABLE_WITH_DECLARED_APPROXIMATION
                option = Option.PREPARE_VALIDATION if accepted else Option.REVIEW_APPROXIMATION
            return Assessment(
                classification,
                reasons or (Reason.APPROXIMATION_VERIFIED,),
                (option,),
                signature,
                mapping=approximation.mapping,
                approximation=approximation,
                approximation_accepted=accepted,
                limitations=approximation.limitations,
            )
    reasons = (no_mapping, Reason.DATA_UNAVAILABLE) if required_data_missing else (no_mapping,)
    return Assessment(
        Classification.REQUIRES_NEW_STRATEGY,
        reasons,
        (Option.DEVELOP_MAPPING,),
        signature,
    )
