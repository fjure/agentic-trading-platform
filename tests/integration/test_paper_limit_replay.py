"""APW-06 pure replay: frozen corpus, unchanged decisions, no broker/SQL ports."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal

import pytest

from packages.decisions.baseline import DeterministicBaselineDecisionEngine, _canonical
from packages.decisions.mock import MockDecisionEngine
from packages.decisions.replay import compare, replay
from packages.domain.models import Choice, Target
from services.worker.paper_limit import PaperLimitPolicy
from services.worker.planner import PlannerConfig, plan_proposal
from tests.integration.test_replay import CONFIG, NOW, corpus, state
from tests.unit.test_decision import FixedClock, SeededIds
from tests.unit.test_paper_limit_planner import inputs

PLANNER = PlannerConfig("apw06-paper-limit-replay.v1", Decimal("999"))


def _hash(value):
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _plan(source, decision, proof):
    return plan_proposal(decision=decision, market_state=source, config=PLANNER,
                         clock=FixedClock(NOW), ids=SeededIds(),
                         paper_inputs=proof, require_paper=True)


def evaluation_summary():
    cases = corpus()
    baseline = replay(cases, CONFIG)
    mock = replay(cases, CONFIG, source="mock")
    decisions_before = _hash(tuple(record.result for record in baseline.records))
    states_before = _hash(tuple(case.state for case in cases))
    planned, proof_hashes = [], []
    for case, record in zip(cases, baseline.records, strict=True):
        proof = inputs(case.state)
        proof_hashes.append((case.case_id, proof.digest()))
        proposal = _plan(case.state, record.result, proof)
        if proposal is not None:
            assert proposal.limit_price is not None
            assert proposal.quantity*proposal.limit_price*Decimal("1.01") <= proof.risk_budget
            assert proposal.quantity*proposal.limit_price*Decimal("1.01") <= proof.remaining_cash
            assert proposal.quantity*proposal.limit_price <= proof.equity*Decimal("0.10")
            assert timedelta() < proposal.valid_until-NOW <= timedelta(seconds=30)
            assert proposal.quantity.as_tuple().exponent >= -9
            assert proposal.limit_price % Decimal("0.01") == 0
            assert f"PAPER_INPUT_SHA256:{proof.digest()}" in proposal.rationale_codes
        planned.append(proposal)
    coverage = compare(baseline, mock)
    return {
        "scope": "synthetic-pure-no-broker-no-sql",
        "clock": NOW.isoformat(),
        "corpus_hash": baseline.corpus_hash,
        "decision_config_hash": baseline.config_hash,
        "planner_config_hash": _hash(PLANNER),
        "policy_hash": _hash(PaperLimitPolicy()),
        "planning_inputs_hash": _hash(proof_hashes),
        "proposal_outputs_hash": _hash(planned),
        "decision_outputs_hash": decisions_before,
        "version_coverage": asdict(coverage.left_versions),
        "cases": len(cases),
        "decision_schema_failures": baseline.metrics.schema_failures,
        "decision_abstentions": sum(r.result.choice is Choice.ABSTAIN for r in baseline.records),
        "baseline_mock_differences": len(coverage.differences),
        "limit_proposals": sum(proposal is not None for proposal in planned),
        "no_proposal": sum(proposal is None for proposal in planned),
        "unchanged_decisions": decisions_before == _hash(tuple(
            record.result for record in baseline.records)),
        "unchanged_states": states_before == _hash(tuple(case.state for case in cases)),
        "real_provider_latency": None,
        "real_provider_cost": None,
        "real_provider_calibration": None,
        "reported_api_units": baseline.metrics.reported_api_units,
        "reported_estimated_cost": baseline.metrics.reported_estimated_cost,
    }


def test_three_repeat_runs_preserve_frozen_corpus_and_decision_distributions():
    summaries = [evaluation_summary() for _ in range(3)]
    assert summaries[0] == summaries[1] == summaries[2]
    report = summaries[0]
    assert report["corpus_hash"] == (
        "09aaaed9ab608bf4e340adfd5af6d7173bb3725e090cf7f2fb9a58d4fe4229fe"
    )
    assert report["decision_config_hash"] == (
        "6139dff1d15c12a0c850842fc90c14985194fc4e1e8fda19f9ff52bcbcff2816"
    )
    assert report["decision_outputs_hash"] == (
        "830b69844bf5085d05337dee2cd5bcf63b0186d7eea8adf40630fd8d13a342cf"
    )
    assert report["planner_config_hash"] == (
        "d4ff06796b435f4bac7408b392c0caa23d51a7f8c96592c59d2fd5d1915e84fc"
    )
    assert report["policy_hash"] == (
        "169b74661ee9b16dab581b3d71ad7deac1d25178e3ea1ba3a2dd5fa872a79b9e"
    )
    assert report["planning_inputs_hash"] == (
        "38777183f3a504d9ca78f2896c2ca902f9867348156da095fae0d38728fc7e7a"
    )
    assert report["proposal_outputs_hash"] == (
        "760a6227f13131539c3ab5741cbff351f413a1ed324f6912da1b6f147c1555f5"
    )
    assert report["cases"] == 100
    assert report["decision_schema_failures"] == report["baseline_mock_differences"] == 0
    assert report["decision_abstentions"] == report["no_proposal"] == 30
    assert report["limit_proposals"] == 70
    assert report["unchanged_decisions"] and report["unchanged_states"]
    coverage = report["version_coverage"]
    assert coverage["config_versions"] == ("rules.v1",)
    assert coverage["model_ids"] == ("neutral-baseline.v1",)
    assert coverage["prompt_versions"] == ("no-prompt.v1",)
    assert coverage["question_versions"] == ("neutral-questions.v1",)
    assert len(set(coverage["input_hashes"])) == 100
    assert report["real_provider_latency"] is report["real_provider_cost"] is None
    assert report["real_provider_calibration"] is report["reported_api_units"] is None


def test_real_local_engines_match_prior_replay_before_and_after_planning():
    cases = corpus()
    baseline_report = replay(cases, CONFIG)
    mock_report = replay(cases, CONFIG, source="mock")
    for engine_type, report in ((DeterministicBaselineDecisionEngine, baseline_report),
                                (MockDecisionEngine, mock_report)):
        for case, record in zip(cases, report.records, strict=True):
            engine = engine_type(clock=FixedClock(case.evaluation_at), ids=SeededIds(),
                                 config=CONFIG.rules)
            original = engine.decide(case.state, CONFIG.version)
            assert original == record.result
            before = _hash(original)
            _plan(case.state, original, inputs(case.state))
            repeated = engine.decide(case.state, CONFIG.version)
            assert repeated == original and _hash(original) == before
            assert repeated.choice == record.result.choice
            assert repeated.distribution == record.result.distribution


@pytest.mark.parametrize("failure,schema_failures", [
    ("timeout", 0), ("invalid_response", 100), ("missing_response", 100),
])
def test_mock_schema_or_provider_failure_never_creates_proposal(failure, schema_failures):
    cases = corpus()
    failed = replay(cases, CONFIG, source="mock", failure=failure)
    assert failed.metrics.schema_failures == schema_failures
    assert all(record.result.choice is Choice.ABSTAIN for record in failed.records)
    assert all(_plan(case.state, record.result, inputs(case.state)) is None
               for case, record in zip(cases, failed.records, strict=True))
    assert replay(cases, CONFIG).metrics.abstention_rate == Decimal("0.3")


def test_low_confidence_stale_or_missing_paper_proof_fail_closed_on_all_admitted_cases():
    cases = corpus()
    report = replay(cases, CONFIG)
    admitted = 0
    for case, record in zip(cases, report.records, strict=True):
        if record.result.choice is Choice.ABSTAIN:
            continue
        admitted += 1
        assert _plan(case.state, replace(record.result, confidence=Decimal("0.69")),
                     inputs(case.state)) is None
        assert _plan(case.state, record.result, inputs(
            case.state, quote_provider_time=NOW-timedelta(seconds=30),
        )) is None
        assert _plan(case.state, record.result, None) is None
    assert admitted == 70


def test_positive_sell_fixture_and_flat_without_holdings_use_original_decision():
    flat = state()
    flat = replace(flat, features={**flat.features, "previous_close": Decimal("102")})
    engine = DeterministicBaselineDecisionEngine(clock=FixedClock(NOW), ids=SeededIds(),
                                               config=CONFIG.rules)
    decision = engine.decide(flat, CONFIG.version)
    assert decision.choice is Choice.FLAT
    assert _plan(flat, decision, inputs(flat)) is None
    held = replace(flat, position=replace(flat.position, quantity=Decimal("3.25")),
                   portfolio=replace(flat.portfolio, cash=Decimal("10000"),
                                     equity=Decimal("10328.25"), exposure=Decimal("328.25")))
    closing = engine.decide(held, CONFIG.version)
    before = _hash(closing)
    proof = inputs(held, instrument_exposure=Decimal("328.25"),
                   open_exit_quantity=Decimal("0.25"))
    proposal = _plan(held, closing, proof)
    assert proposal is not None and proposal.target is Target.FLAT
    assert proposal.quantity == Decimal("3.000000000")
    assert proposal.quantity+proof.open_exit_quantity <= held.position.quantity
    assert proposal.limit_price <= proof.bid
    assert proposal.limit_price % Decimal("0.01") == 0
    assert proposal.valid_until <= NOW+timedelta(seconds=30)
    assert _hash(closing) == before and closing.choice is Choice.FLAT
