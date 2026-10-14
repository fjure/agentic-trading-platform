"""PTV-15: local Jev Shadow evaluation over the frozen replay corpus."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from packages.decisions.jev import JevDecisionEngine, load_config
from packages.decisions.replay import CachedResponse, replay
from packages.domain.models import Choice
from tests.contracts.test_jev import _response
from tests.integration.test_replay import NOW, corpus
from tests.unit.test_decision import FixedClock, SeededIds

_CALLER_VERSION = "shadow-evaluation.v1"
_SYNTHETIC_KEY = "synthetic-credential-never-real"
_SUITABILITY_LABEL_VERSION = "synthetic-input-suitability.v1"
# Ten fixed labels repeat across corpus.v1: stale, conflict, incomplete, then seven admitted.
# These label input suitability for the local fixture, never profit or provider correctness.
_SUITABILITY_LABELS = {
    f"corpus.v1-{index:03}": int(("0001111111" * 10)[index]) for index in range(100)
}
_SUITABILITY_LABEL_HASH = hashlib.sha256(
    json.dumps(
        {"version": _SUITABILITY_LABEL_VERSION, "labels": _SUITABILITY_LABELS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def _capture(fault: str | None = None):
    """Capture typed decisions through an in-memory provider and replay them locally."""
    cases = corpus()
    config = replace(load_config(), enabled=True)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if fault == "status429":
            return httpx.Response(429)
        if fault == "malformed":
            return httpx.Response(200, content=b"{")
        body = copy.deepcopy(_response())
        if fault == "missingquestion":
            body["answers"].pop("regime")
        elif fault == "lowconfidence":
            body["answers"]["shadow_direction"]["confidence"] = 0.1
        elif fault == "lowquality":
            quality = body["answers"]["evidence_quality"]
            quality["probabilities"]["0"] = 0.8
            quality["probabilities"]["9"] = 0.2
            quality["score"] = 1.8
        elif fault == "conflict":
            body["answers"]["signal_conflict"]["noul"] = 0.9
        return httpx.Response(200, json=body)

    engine = JevDecisionEngine(
        clock=FixedClock(NOW),
        ids=SeededIds(),
        api_key=_SYNTHETIC_KEY,
        config=config,
        transport=httpx.MockTransport(handler),
    )
    captured = {
        case.case_id: CachedResponse(engine.decide(case.state, _CALLER_VERSION))
        for case in cases
    }
    report = replay(
        cases,
        config.replay_config(_CALLER_VERSION),
        source="cached",
        cached=captured,
    )
    return report, len(calls)


def evaluation_summary() -> dict[str, object]:
    """Return JSON-compatible synthetic evidence; real provider calibration is unknown."""
    runs = [_capture() for _ in range(3)]
    reports = [report for report, _ in runs]
    first = reports[0]
    baseline = replay(corpus(), replace(load_config(), enabled=True).replay_config(_CALLER_VERSION))
    eligible = [record for record in first.records if record.result.reason_code == "JEV_SHADOW"]
    baseline_by_id = {record.case_id: record.result for record in baseline.records}
    if set(_SUITABILITY_LABELS) != {record.case_id for record in first.records}:
        raise ValueError("synthetic suitability labels do not cover the frozen corpus")
    confidences = [record.result.confidence for record in eligible]
    if not confidences or any(confidence is None for confidence in confidences):
        raise ValueError("eligible shadow responses require confidence")
    labels = [Decimal(_SUITABILITY_LABELS[record.case_id]) for record in eligible]
    count = Decimal(len(eligible))
    mean_confidence = sum(confidences, Decimal("0")) / count
    observed_suitability = sum(labels, Decimal("0")) / count
    brier = sum(
        ((confidence - label) ** 2 for confidence, label in zip(confidences, labels, strict=True)),
        Decimal("0"),
    ) / count
    return {
        "scope": "synthetic-local-mock-transport",
        "corpus_hash": first.corpus_hash,
        "config_hash": first.config_hash,
        "cases": first.metrics.total,
        "provider_requests_per_run": [count for _, count in runs],
        "stable_semantic_hashes": all(
            [record.semantic_hash for record in report.records]
            == [record.semantic_hash for record in first.records]
            for report in reports[1:]
        ),
        "actual_abstentions": sum(
            record.result.choice is Choice.ABSTAIN for record in first.records
        ),
        "preadmission_abstentions": sum(
            record.result.reason_code == "ABSTAIN_MISSING_EVIDENCE"
            for record in first.records
        ),
        "eligible_healthy": len(eligible),
        "synthetic_suitability_label_version": _SUITABILITY_LABEL_VERSION,
        "synthetic_suitability_label_hash": _SUITABILITY_LABEL_HASH,
        "synthetic_suitability_eligible_coverage": f"{len(eligible)}/{len(first.records)}",
        "synthetic_suitability_excluded": len(first.records) - len(eligible),
        "synthetic_suitability_brier": str(brier),
        "synthetic_suitability_mean_confidence": str(mean_confidence),
        "synthetic_suitability_observed_rate": str(observed_suitability),
        "synthetic_suitability_calibration_gap": str(abs(mean_confidence - observed_suitability)),
        "healthy_shadow_long": sum(
            record.result.shadow_choice is Choice.LONG for record in eligible
        ),
        "projected_shadow_baseline_alignment": sum(
            record.result.shadow_choice is baseline_by_id[record.case_id].choice
            for record in eligible
        ),
        "schema_failures": first.metrics.schema_failures,
        "abstention_rate": str(first.metrics.abstention_rate),
        "latency_p95_ms": first.metrics.latency_p95_ms,
        "reported_input_tokens": first.metrics.reported_input_tokens,
        "reported_output_tokens": first.metrics.reported_output_tokens,
        "reported_estimated_cost": None,
        "real_provider_calibration": None,
    }


def test_jev_shadow_100_case_local_evaluation() -> None:
    summary = evaluation_summary()
    assert summary["cases"] == 100
    assert summary["provider_requests_per_run"] == [70, 70, 70]
    assert summary["stable_semantic_hashes"] is True
    assert summary["actual_abstentions"] == 100
    assert summary["preadmission_abstentions"] == 30
    assert summary["eligible_healthy"] == summary["healthy_shadow_long"] == 70
    assert summary["synthetic_suitability_label_version"] == "synthetic-input-suitability.v1"
    assert summary["synthetic_suitability_label_hash"] == (
        "42d81c01cc31e7e9e8705809604a8ffcd358c9d46efe54781224f59544724a3c"
    )
    assert summary["synthetic_suitability_eligible_coverage"] == "70/100"
    assert summary["synthetic_suitability_excluded"] == 30
    assert summary["synthetic_suitability_brier"] == "0.04"
    assert summary["synthetic_suitability_mean_confidence"] == "0.8"
    assert summary["synthetic_suitability_observed_rate"] == "1"
    assert summary["synthetic_suitability_calibration_gap"] == "0.2"
    assert summary["projected_shadow_baseline_alignment"] == 70
    assert summary["schema_failures"] == 0
    assert summary["abstention_rate"] == "1"
    assert summary["latency_p95_ms"] <= 5000
    assert summary["reported_input_tokens"] == 7000
    assert summary["reported_output_tokens"] == 700
    assert summary["reported_estimated_cost"] is summary["real_provider_calibration"] is None


@pytest.mark.parametrize(
    "fault,healthy_reason",
    [
        ("timeout", "ABSTAIN_UNAVAILABLE"),
        ("status429", "ABSTAIN_UNAVAILABLE"),
        ("malformed", "ABSTAIN_INVALID_RESPONSE"),
        ("missingquestion", "ABSTAIN_INVALID_RESPONSE"),
        ("lowconfidence", "ABSTAIN_LOW_CONFIDENCE"),
        ("lowquality", "ABSTAIN_LOW_EVIDENCE_QUALITY"),
        ("conflict", "ABSTAIN_SIGNAL_CONFLICT"),
    ],
)
def test_faults_never_create_eligible_shadow(fault: str, healthy_reason: str) -> None:
    report, requests = _capture(fault)
    reasons = [record.result.reason_code for record in report.records]
    assert requests == 70
    assert all(record.result.choice is Choice.ABSTAIN for record in report.records)
    assert reasons.count("ABSTAIN_MISSING_EVIDENCE") == 30
    assert reasons.count(healthy_reason) == 70
    assert "JEV_SHADOW" not in reasons
    expected_schema_failures = 70 if healthy_reason == "ABSTAIN_INVALID_RESPONSE" else 0
    assert report.metrics.schema_failures == expected_schema_failures
