from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from packages.agent_research.budget import BudgetDenied, TokenPrices, Tokens
from services.llm_gateway.probe import PROBE_PROJECTION
from services.llm_gateway.provider_http import Capability, provider_request_bytes
from services.llm_gateway.provider_wire import Provider
from services.llm_gateway.smoke_budget import (
    ProviderSmokeBudgetPolicyV1,
    SmokeEvidence,
    SmokeOperatorLimit,
    SmokeTariff,
    load_smoke_budget,
)

NOW = datetime(2026, 11, 14, 23, 59, tzinfo=UTC)
EXPIRY = NOW + timedelta(hours=1)
CAPABILITY = Capability(
    Provider.OPENAI,
    "reviewed-model-v1",
    "smoke-profile",
    1,
    1,
    1,
    "provider-probe-v1",
    "Run the fixed synthetic validation.",
    '{"type":"object"}',
    32,
    5,
)
EVIDENCE = SmokeEvidence(
    provider=Provider.OPENAI,
    model_id=CAPABILITY.model,
    response_model_id=CAPABILITY.model,
    api_version="v1",
    profile_generation=1,
    model_generation=1,
    capability_generation=1,
    credential_generation=1,
    revision=1,
    expires_at=EXPIRY,
    source_sha256="a" * 64,
    profile_id=CAPABILITY.profile_id,
    model_input_tokens=250_000,
    model_bound_includes_overhead=True,
    minimum_output_tokens=16,
    output_limit_includes_reasoning=True,
    reasoning_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    fixture_only=True,
)
TARIFF = SmokeTariff(
    TokenPrices(1, "USD", Decimal("2"), Decimal("12"), True),
    "b" * 64,
    EXPIRY,
    Decimal("2"),
    Decimal("12"),
    None,
    None,
    None,
    Decimal(0),
    ("default",),
    True,
)
LIMIT = SmokeOperatorLimit(1, EXPIRY)


def quote(*, evidence=EVIDENCE, tariff=TARIFF, limit=LIMIT, now=NOW, offline_fixture=True):
    return ProviderSmokeBudgetPolicyV1.quote(
        CAPABILITY,
        PROBE_PROJECTION,
        evidence=evidence,
        tariff=tariff,
        limit=limit,
        now=now,
        offline_fixture=offline_fixture,
    )


def test_smoke_reserve_prefers_exact_request_proof_then_full_model_ceiling():
    fallback = quote()
    assert fallback.tokens == Tokens(250_000, 32)
    assert fallback.input_bound_kind == "MODEL"
    assert fallback.max_cost == Decimal("0.500384")
    wire = provider_request_bytes(CAPABILITY, PROBE_PROJECTION)
    proof = replace(
        EVIDENCE,
        request_input_tokens=1024,
        request_sha256=sha256(wire).hexdigest(),
        request_size=len(wire),
        request_bound_method="TOKENIZER_WITH_MAX_OVERHEAD",
    )
    small = quote(evidence=proof)
    assert small.input_bound_kind == "REQUEST"
    assert small.tokens == Tokens(1024, 32)
    assert small.max_cost == Decimal("0.002432")
    mismatched = quote(evidence=replace(proof, request_size=len(wire) + 1))
    assert mismatched.tokens == fallback.tokens and mismatched.input_bound_kind == "MODEL"
    with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
        quote(evidence=replace(proof, request_sha256="d" * 64, model_bound_includes_overhead=False))


def test_smoke_cost_checks_reasoning_and_cache_pricing_and_ledger_envelope():
    evidence = replace(
        EVIDENCE,
        reasoning_tokens=24,
        cache_read_tokens=200,
        cache_read_is_subset=True,
        cache_write_tokens=44,
    )
    tariff = replace(
        TARIFF,
        reasoning=Decimal("15"),
        cache_read=Decimal("1"),
        cache_write=Decimal("4"),
        fixed_fee=Decimal("0.01"),
    )
    with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
        quote(evidence=evidence, tariff=tariff)
    widened = replace(
        tariff, ledger_prices=replace(tariff.ledger_prices, input_per_million=Decimal("2.1"))
    )
    result = quote(evidence=evidence, tariff=widened)
    assert result.tokens == Tokens(250_044, 32)
    assert result.component_cost_bound == Decimal("0.510632")
    assert result.component_cost_bound <= result.max_cost
    for fault in (
        replace(widened, cache_write=None),
        replace(widened, reasoning=None),
        replace(widened, all_reachable_tiers_covered=False),
    ):
        with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
            quote(evidence=evidence, tariff=fault)


def test_smoke_operator_limit_expiry_fixture_and_reapproval_fail_closed():
    original = quote()
    for alternate in (
        quote(limit=replace(LIMIT, revision=2)),
        quote(tariff=replace(TARIFF, ledger_prices=replace(TARIFF.ledger_prices, version=2))),
        quote(now=NOW + timedelta(minutes=2)),
    ):
        with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
            original.require_current(alternate)
    for invalid in (
        {"offline_fixture": False},
        {"evidence": replace(EVIDENCE, response_model_id="other")},
        {"evidence": replace(EVIDENCE, expires_at=NOW)},
        {"evidence": replace(EVIDENCE, reasoning_tokens=None)},
        {"evidence": replace(EVIDENCE, minimum_output_tokens=257)},
        {"tariff": replace(TARIFF, expires_at=NOW)},
        {"limit": replace(LIMIT, expires_at=NOW)},
    ):
        with pytest.raises(BudgetDenied, match="BUDGET_POLICY_UNAVAILABLE"):
            quote(**invalid)
    expensive = replace(
        TARIFF,
        input=Decimal("4"),
        ledger_prices=replace(TARIFF.ledger_prices, input_per_million=Decimal("4")),
    )
    with pytest.raises(BudgetDenied, match="BUDGET_EXHAUSTED"):
        quote(tariff=expensive)
    assert quote(
        tariff=expensive, limit=replace(LIMIT, amount=Decimal("1.10"))
    ).max_cost == Decimal("1.000384")


def test_smoke_budget_descriptor_strictly_parses_decimal_and_limits():
    import json

    doc = json.loads(
        json.dumps(
            dict(
                version=1,
                policy_revision=1,
                output_tokens=32,
                timeout_seconds=5,
                evidence=asdict(EVIDENCE),
                tariff=asdict(TARIFF),
                limit=asdict(LIMIT),
            ),
            default=str,
        )
    )
    assert (
        load_smoke_budget(doc).quote(
            CAPABILITY,
            PROBE_PROJECTION,
            NOW,
            offline_fixture=True,
        )
        == quote()
    )
    for invalid in (
        doc | {"output_tokens": True},
        doc | {"limit": doc["limit"] | {"amount": 0.65}},
        doc | {"tariff": doc["tariff"] | {"fixed_fee": "NaN"}},
        doc | {"version": True},
    ):
        with pytest.raises(BudgetDenied):
            load_smoke_budget(invalid)
