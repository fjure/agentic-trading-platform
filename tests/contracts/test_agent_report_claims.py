import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from packages.agent_research.reporting.claims import InterpretationDenied, interpret, metric_claims
from packages.agent_research.reporting.service import ReportDenied, ResearchReports, _phase_report
from packages.agent_research.reporting.store import ReportStoreError, _validator
from services.llm_gateway.content_policy import safe_result_content

MANIFEST = "a" * 64
SELECTED = "b" * 64
BENCHMARK = "c" * 64
RUN = "12345678-1234-4321-9234-123456789abc"
BENCHMARK_RUN = "12345678-1234-4321-9234-123456789abd"


def result(phase="holdout", *, candidate=SELECTED, role="SEARCH_CANDIDATE", fold=None,
           net_return="-0.125000000000"):
    return {
        "batchId": "batch-1", "manifestHash": MANIFEST, "candidateId": candidate, "role": role,
        "runId": RUN if role == "SEARCH_CANDIDATE" else BENCHMARK_RUN,
        "phase": phase, "fold": fold, "evaluationStatus": "COMPLETE", "reasonCodes": [],
        "metrics": {
            "netReturn": {"status": "VALID", "value": net_return,
                          "methodVersion": "quant-metrics-v1", "frequency": "PHASE"},
            "sharpe": {"status": "N/A", "value": None},
            "fees": {"status": "VALID", "value": "1.2000"},
            "activity": {"fillCount": {"status": "VALID", "value": 3}},
        },
    }


def proposal(**fields):
    return json.dumps([fields]).encode()


def test_claims_are_bound_to_native_fields_and_preserve_decimal_values():
    net_return, fees, fills = metric_claims(result(), MANIFEST)
    assert (net_return.value, fees.value, fills.value) == ("-0.125", "1.2", "3")
    assert net_return.reference == {
        "batchId": "batch-1", "manifestHash": MANIFEST, "fieldPath": "/metrics/netReturn/value",
        "phase": "HOLDOUT", "subject": "CANDIDATE", "subjectId": RUN, "unit": "RATIO",
    }
    assert fees.reference["unit"] == "MONEY" and fills.reference["unit"] == "COUNT"
    with pytest.raises(ValueError, match="REPORT_RESULT_BINDING_MISMATCH"):
        metric_claims(result(), "e" * 64)


def test_direction_and_benchmark_are_checked_using_only_bound_values():
    candidate = metric_claims(result(), MANIFEST)[0]
    benchmark = metric_claims(result(
        candidate=BENCHMARK, role="REFERENCE_BUY_HOLD", net_return="0.01",
    ), MANIFEST)[0]
    claims = candidate, benchmark
    direction = interpret(proposal(claimId=candidate.claim_id, predicate="NEGATIVE"), claims)
    assert direction[0]["statement"] == "Net return was negative in the bound phase."
    comparison = interpret(proposal(claimId=candidate.claim_id, predicate="BELOW",
                                    benchmarkClaimId=benchmark.claim_id), claims)
    assert comparison[0]["supportingClaimIds"] == [candidate.claim_id, benchmark.claim_id]
    with pytest.raises(InterpretationDenied, match="REPORT_OUTPUT_PREDICATE_MISMATCH"):
        interpret(proposal(claimId=candidate.claim_id, predicate="ABOVE",
                           benchmarkClaimId=benchmark.claim_id), claims)
    other_phase = metric_claims(result(
        "validation", candidate=BENCHMARK, role="REFERENCE_BUY_HOLD", fold=0,
    ), MANIFEST)[0]
    with pytest.raises(InterpretationDenied, match="REPORT_OUTPUT_NOT_COMPARABLE"):
        interpret(proposal(claimId=candidate.claim_id, predicate="EQUAL",
                           benchmarkClaimId=other_phase.claim_id), (candidate, other_phase))


def test_similar_results_from_different_folds_are_not_comparable():
    left = metric_claims(result("test", fold=0), MANIFEST)[0]
    right = metric_claims(result(
        "test", fold=1, candidate=BENCHMARK, role="REFERENCE_BUY_HOLD",
    ), MANIFEST)[0]
    with pytest.raises(InterpretationDenied, match="REPORT_OUTPUT_NOT_COMPARABLE"):
        interpret(proposal(claimId=left.claim_id, predicate="EQUAL",
                           benchmarkClaimId=right.claim_id), (left, right))


@pytest.mark.parametrize("extra", [
    {"statement": "<img src=x>"}, {"value": "0.99"}, {"tool": "start"},
])
def test_untrusted_content_never_forms_a_report_statement(extra):
    candidate = metric_claims(result(), MANIFEST)[0]
    with pytest.raises(InterpretationDenied, match="REPORT_OUTPUT_SHAPE_INVALID"):
        interpret(proposal(claimId=candidate.claim_id, predicate="NEGATIVE", **extra), (candidate,))
    with pytest.raises(InterpretationDenied, match="REPORT_OUTPUT_REFERENCE_INVALID"):
        interpret(proposal(claimId="absent-sharpe", predicate="POSITIVE"), (candidate,))


def test_phase_projection_follows_persisted_selection_and_keeps_unavailable_holdout_empty():
    final = {"status": "SELECTED", "selectedCandidateId": SELECTED}
    rows = [result(), result(candidate="d" * 64), result("validation", fold=0)]
    phase, claims = _phase_report(rows, final, {}, MANIFEST, "holdout")
    assert phase["status"] == "AVAILABLE"
    assert {claim.reference["subjectId"] for claim in claims} == {RUN}
    no_selection = {"status": "NO_ELIGIBLE_CANDIDATE", "selectedCandidateId": None}
    phase, claims = _phase_report(rows, no_selection, {}, MANIFEST, "holdout")
    assert phase == {"status": "NOT_SELECTED", "metrics": [], "limitations": []}
    assert claims == ()


def _quant_example(name):
    path = Path(__file__).resolve().parents[2] / "contracts/examples/v1"
    return json.loads((path / f"quant-lab-{name}.valid.json").read_text(encoding="utf-8"))


def test_report_falls_back_to_schema_valid_facts_when_model_uses_unknown_metric():
    manifest = _quant_example("manifest-reference")
    final = _quant_example("selection-final")
    batch_id = "12345678-1234-4321-9234-123456789abc"
    row = result(candidate=final["selectedCandidateId"])
    row.update(batchId=batch_id, manifestHash=manifest["manifestHash"], reliability=None)
    evidence = SimpleNamespace(
        batch_id=batch_id, definition_id="12345678-1234-4321-9234-123456789abe",
        definition_hash="1" * 64,
        manifest_hash=manifest["manifestHash"], manifest=manifest, report_id="native-report",
        report_hash="2" * 64, selections=(final,), results=(row,),
        definition=_quant_example("experiment-request"),
        history=_quant_example("history-no-registered-prior-use"),
    )
    source = Mock()
    source.load.return_value = evidence
    source.idea_references.return_value = ()
    store = Mock()
    store.get.return_value = None
    store.authorize.return_value = {
        "approvalId": "approval", "createdAt": "2026-01-12T15:00:00Z",
        "manifestHash": evidence.manifest_hash,
        "idea": {"id": "idea", "version": 2, "title": "Historical EMA experiment",
                 "description": "How did the defined strategy perform?",
                 "hypothesis": {"statement": "The strategy may differ from the benchmark."},
                 "assumptions": [], "evidence": []},
        "binding": {"experimentId": evidence.definition_id,
                    "definitionHash": evidence.definition_hash,
                    "hypothesisVersion": 2, "definitionVersion": 1},
    }
    store.publish.side_effect = lambda document, **_: document
    service = ResearchReports(source, store, permits=safe_result_content)
    report = service.publish(batch_id, actor="operator", proposal=proposal(
        claimId="absent-sharpe", predicate="POSITIVE",
    ))
    assert not list(_validator().iter_errors(report))
    assert report["summary"]["interpretationStatus"] == "FACTS_ONLY"
    assert report["selection"] == final and report["interpretations"] == []
    assert {"field": "interpretation", "reason": "INTERPRETATION_REJECTED"} in report["unknowns"]
    assert report["phases"]["holdout"]["metrics"][0]["value"] == "-0.125"
    assert store.publish.call_args.kwargs["rejection_code"] == "REPORT_OUTPUT_REFERENCE_INVALID"

    evidence.definition = evidence.definition | {"hypothesis": "<script>unsafe</script>"}
    with pytest.raises(ReportDenied, match="REPORT_CONTEXT_DENIED"):
        service.publish(batch_id, actor="operator")
    evidence.definition = _quant_example("experiment-request")

    store.get.side_effect = [None, report]
    store.publish.side_effect = ReportStoreError("REPORT_VERSION_CONFLICT")
    assert service.publish(batch_id, actor="operator") == report
    store.get.side_effect = None

    store.get.return_value = report
    source.load.reset_mock()
    store.publish.reset_mock()
    assert service.publish(batch_id, actor="operator", proposal=b"ignored") == report
    source.load.assert_not_called()
    store.publish.assert_not_called()

    store.authorize.return_value = None
    with pytest.raises(ReportDenied, match="REPORT_SCOPE_DENIED"):
        service.publish(batch_id, actor="foreign-operator")
