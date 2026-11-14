"""Immutable, deny-by-default cost quotes for one synthetic provider smoke attempt."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from hashlib import sha256

from packages.agent_research.budget import (
    BudgetAmount,
    BudgetDenied,
    TokenPrices,
    Tokens,
    reserve_model,
)

from .provider_http import Capability, provider_request_bytes
from .provider_wire import Provider

DEFAULT_SMOKE_LIMIT = Decimal("0.65")
_QUANTUM = Decimal("0.000000000000000001")
_COMPONENTS = ("input", "output", "reasoning", "cache_read", "cache_write")
_HASH = re.compile(r"[a-f0-9]{64}")


def _deny(code: str = "BUDGET_POLICY_UNAVAILABLE"):
    raise BudgetDenied(code)


def _positive(value) -> bool:
    return type(value) is int and 1 <= value <= 2**31 - 1


def _hash(value) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _current(expires_at: datetime, now: datetime, revision: int) -> bool:
    return (
        _positive(revision)
        and isinstance(expires_at, datetime)
        and expires_at.utcoffset() == timedelta(0)
        and expires_at > now
    )


@dataclass(frozen=True, slots=True)
class SmokeEvidence:
    """Reviewed, current evidence for the billable envelope of exactly one model revision."""

    provider: Provider
    model_id: str
    response_model_id: str
    api_version: str
    profile_generation: int
    model_generation: int
    capability_generation: int
    credential_generation: int
    revision: int
    expires_at: datetime
    source_sha256: str
    profile_id: str
    model_input_tokens: int | None
    request_input_tokens: int | None = None
    request_sha256: str | None = None
    request_size: int | None = None
    request_bound_method: str | None = None
    model_bound_includes_overhead: bool = False
    minimum_output_tokens: int | None = None
    output_limit_includes_reasoning: bool = False
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_read_is_subset: bool = False
    cache_write_is_subset: bool = False
    fixture_only: bool = False


@dataclass(frozen=True, slots=True)
class SmokeTariff:
    """Reviewed maximum prices across all reachable tiers, in USD per million tokens."""

    ledger_prices: TokenPrices
    source_sha256: str
    expires_at: datetime
    # None explicitly denotes the absence of a price for that component.
    input: Decimal | None
    output: Decimal | None
    reasoning: Decimal | None
    cache_read: Decimal | None
    cache_write: Decimal | None
    fixed_fee: Decimal
    tiers: tuple[str, ...]
    all_reachable_tiers_covered: bool = False


@dataclass(frozen=True, slots=True)
class SmokeOperatorLimit:
    revision: int
    expires_at: datetime
    amount: Decimal = DEFAULT_SMOKE_LIMIT


@dataclass(frozen=True, slots=True)
class SmokeQuote:
    tokens: Tokens
    max_cost: Decimal
    commitment: str
    expires_at: datetime
    input_bound_kind: str
    ledger_prices: TokenPrices
    wire_sha256: str
    wire_size: int
    component_bounds: tuple[int, ...]
    component_cost_bound: Decimal

    def require_current(self, current: "SmokeQuote") -> None:
        if current != self:
            _deny()


def _component_bounds(capability, evidence, wire_sha256, wire_size):
    if (
        _positive(evidence.request_input_tokens)
        and evidence.request_bound_method in {"PROVIDER_HARD_BOUND", "TOKENIZER_WITH_MAX_OVERHEAD"}
        and evidence.request_sha256 == wire_sha256
        and evidence.request_size == wire_size
    ):
        input_tokens, kind = evidence.request_input_tokens, "REQUEST"
    elif _positive(evidence.model_input_tokens) and evidence.model_bound_includes_overhead is True:
        input_tokens, kind = evidence.model_input_tokens, "MODEL"
    else:
        _deny()
    reasoning = evidence.reasoning_tokens
    cache_read, cache_write = evidence.cache_read_tokens, evidence.cache_write_tokens
    if (
        any(
            type(value) is not int or not 0 <= value <= 2**31 - 1
            for value in (reasoning, cache_read, cache_write)
        )
        or type(evidence.output_limit_includes_reasoning) is not bool
        or type(evidence.cache_read_is_subset) is not bool
        or type(evidence.cache_write_is_subset) is not bool
        or (
            cache_read > 0
            and evidence.cache_read_is_subset != (evidence.provider is Provider.OPENAI)
        )
        or evidence.output_limit_includes_reasoning
        and reasoning > capability.max_output_tokens
        or evidence.cache_read_is_subset
        and cache_read > input_tokens
        or evidence.cache_write_is_subset
        and cache_write > input_tokens
    ):
        _deny()
    output = capability.max_output_tokens
    # Reasoning in the output cap is already priced as output. Extra reasoning
    # always requires a separately proven ceiling and contributes to ledger tokens.
    aggregate_output = output if evidence.output_limit_includes_reasoning else output + reasoning
    if aggregate_output > 256:
        _deny()
    aggregate_input = input_tokens
    if not evidence.cache_read_is_subset:
        aggregate_input += cache_read
    if not evidence.cache_write_is_subset:
        aggregate_input += cache_write
    return (
        Tokens(aggregate_input, aggregate_output),
        kind,
        (input_tokens, output, reasoning, cache_read, cache_write),
    )


def _component_cost(bounds, evidence, tariff):
    if (
        not tariff.all_reachable_tiers_covered
        or type(tariff.all_reachable_tiers_covered) is not bool
        or not tariff.tiers
        or len(set(tariff.tiers)) != len(tariff.tiers)
        or ("default" if evidence.provider is Provider.OPENAI else "standard") not in tariff.tiers
    ):
        _deny()
    for name, bound in zip(_COMPONENTS, bounds, strict=True):
        rate = getattr(tariff, name)
        if rate is None and bound:
            _deny()
        if rate is not None:
            BudgetAmount("USD", rate)
    BudgetAmount("USD", tariff.fixed_fee)
    base, output, reasoning, read, write = bounds
    with localcontext() as context:
        context.prec = 80
        input_cost = base * tariff.input
        # Subset tokens replace base input at a higher price. Never credit a cache
        # discount and conservatively allow overlap of the two declared maxima.
        if read:
            input_cost += read * (
                max(Decimal(0), tariff.cache_read - tariff.input)
                if evidence.cache_read_is_subset
                else tariff.cache_read
            )
        if write:
            input_cost += write * (
                max(Decimal(0), tariff.cache_write - tariff.input)
                if evidence.cache_write_is_subset
                else tariff.cache_write
            )
        total = input_cost + output * tariff.output
        if reasoning:
            total += reasoning * (
                max(Decimal(0), tariff.reasoning - tariff.output)
                if evidence.output_limit_includes_reasoning
                else tariff.reasoning
            )
        bound = (total / 1_000_000 + tariff.fixed_fee).quantize(_QUANTUM, rounding=ROUND_CEILING)
    BudgetAmount("USD", bound)
    return bound


class ProviderSmokeBudgetPolicyV1:
    @staticmethod
    def quote(
        capability: Capability,
        projection: bytes,
        *,
        evidence: SmokeEvidence | None,
        tariff: SmokeTariff | None,
        limit: SmokeOperatorLimit | None,
        now: datetime,
        policy_revision: int = 1,
        offline_fixture: bool = False,
    ) -> SmokeQuote:
        if (
            not isinstance(now, datetime)
            or now.utcoffset() != timedelta(0)
            or not _positive(policy_revision)
            or not isinstance(capability, Capability)
            or type(capability.provider) is not Provider
            or not isinstance(evidence, SmokeEvidence)
            or not isinstance(tariff, SmokeTariff)
            or not isinstance(limit, SmokeOperatorLimit)
            or not isinstance(tariff.ledger_prices, TokenPrices)
            or type(evidence.fixture_only) is not bool
            or type(offline_fixture) is not bool
            or evidence.fixture_only != offline_fixture
            or not _current(evidence.expires_at, now, evidence.revision)
            or not _current(tariff.expires_at, now, tariff.ledger_prices.version)
            or not _current(limit.expires_at, now, limit.revision)
            or not all(_hash(value) for value in (evidence.source_sha256, tariff.source_sha256))
            or not _positive(evidence.capability_generation)
            or tariff.ledger_prices.currency != "USD"
            or not tariff.ledger_prices.bounds_unconfirmed_cost
            or not _positive(capability.max_output_tokens)
            or capability.max_output_tokens > 256
            or not _positive(evidence.minimum_output_tokens)
            or evidence.minimum_output_tokens > capability.max_output_tokens
            or not 0 < capability.timeout_seconds <= 15
        ):
            _deny()
        if (
            evidence.provider is not capability.provider
            or (evidence.model_id, evidence.response_model_id)
            != (capability.model, capability.model)
            or evidence.api_version
            != ("v1" if capability.provider is Provider.OPENAI else "2026-10-21")
            or (
                evidence.profile_id,
                evidence.profile_generation,
                evidence.model_generation,
                evidence.credential_generation,
            )
            != (
                capability.profile_id,
                capability.profile_generation,
                capability.model_generation,
                capability.credential_generation,
            )
        ):
            _deny()
        BudgetAmount("USD", limit.amount)
        wire = provider_request_bytes(capability, projection)
        wire_sha256 = sha256(wire).hexdigest()
        tokens, kind, bounds = _component_bounds(capability, evidence, wire_sha256, len(wire))
        component_cost = _component_cost(bounds, evidence, tariff)
        maximum = reserve_model(
            tariff.ledger_prices,
            tokens,
            tokens.input + tokens.output,
        ).bound.cost
        if maximum < component_cost:
            _deny()
        if limit.amount <= 0 or maximum > limit.amount:
            _deny("BUDGET_EXHAUSTED")
        commitment = sha256(
            json.dumps(
                {
                    "v": 1,
                    "policyRevision": policy_revision,
                    "wire": wire_sha256,
                    "wireSize": len(wire),
                    "capability": asdict(capability),
                    "evidence": asdict(evidence),
                    "tariff": asdict(tariff),
                    "limit": asdict(limit),
                    "period": now.astimezone(UTC).date().isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        return SmokeQuote(
            tokens,
            maximum,
            commitment,
            min(evidence.expires_at, tariff.expires_at, limit.expires_at),
            kind,
            tariff.ledger_prices,
            wire_sha256,
            len(wire),
            bounds,
            component_cost,
        )


@dataclass(frozen=True, slots=True)
class SmokeBudgetInputs:
    evidence: SmokeEvidence
    tariff: SmokeTariff
    limit: SmokeOperatorLimit
    output_tokens: int
    timeout_seconds: int
    policy_revision: int

    def quote(
        self,
        capability: Capability,
        projection: bytes,
        now: datetime,
        *,
        offline_fixture: bool = False,
    ) -> SmokeQuote:
        if (
            capability.max_output_tokens != self.output_tokens
            or capability.timeout_seconds != self.timeout_seconds
        ):
            _deny()
        return ProviderSmokeBudgetPolicyV1.quote(
            capability,
            projection,
            evidence=self.evidence,
            tariff=self.tariff,
            limit=self.limit,
            now=now,
            policy_revision=self.policy_revision,
            offline_fixture=offline_fixture,
        )


def _decimal(value):
    if type(value) is not str or re.fullmatch(r"[0-9]{1,20}(?:\.[0-9]{1,18})?", value) is None:
        _deny()
    return Decimal(value)


def _timestamp(value):
    if type(value) is not str:
        _deny()
    result = datetime.fromisoformat(value)
    if result.utcoffset() != timedelta(0):
        _deny()
    return result


def load_smoke_budget(document: dict) -> SmokeBudgetInputs:
    """Parse the private, explicit V1 descriptor without coercing numbers or booleans."""
    try:
        if (
            type(document) is not dict
            or set(document)
            != {
                "version",
                "evidence",
                "tariff",
                "limit",
                "output_tokens",
                "timeout_seconds",
                "policy_revision",
            }
            or type(document["version"]) is not int
            or document["version"] != 1
        ):
            _deny()
        evidence, tariff, limit = (document[key] for key in ("evidence", "tariff", "limit"))
        if not all(type(value) is dict for value in (evidence, tariff, limit)):
            _deny()
        evidence, tariff, limit = (value.copy() for value in (evidence, tariff, limit))
        evidence["provider"] = Provider(evidence["provider"])
        evidence["expires_at"] = _timestamp(evidence["expires_at"])
        tariff["expires_at"] = _timestamp(tariff["expires_at"])
        limit["expires_at"] = _timestamp(limit["expires_at"])
        if "amount" in limit:
            limit["amount"] = _decimal(limit["amount"])
        for key in _COMPONENTS:
            if tariff[key] is not None:
                tariff[key] = _decimal(tariff[key])
        tariff["fixed_fee"] = _decimal(tariff["fixed_fee"])
        if type(tariff["tiers"]) is not list or any(
            type(tier) is not str or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", tier) is None
            for tier in tariff["tiers"]
        ):
            _deny()
        tariff["tiers"] = tuple(tariff["tiers"])
        prices = tariff["ledger_prices"].copy()
        for key in ("input_per_million", "output_per_million"):
            prices[key] = _decimal(prices[key])
        tariff["ledger_prices"] = TokenPrices(**prices)
        output, timeout, policy_revision = (
            document[key] for key in ("output_tokens", "timeout_seconds", "policy_revision")
        )
        if (
            not all(_positive(value) for value in (output, timeout, policy_revision))
            or output > 256
            or timeout > 15
        ):
            _deny()
        return SmokeBudgetInputs(
            SmokeEvidence(**evidence),
            SmokeTariff(**tariff),
            SmokeOperatorLimit(**limit),
            output,
            timeout,
            policy_revision,
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        _deny()
