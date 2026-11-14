"""Gold scorer oracles independently exercise denominators, barriers and pairing."""

from copy import deepcopy

import pytest

from tests.evaluation.scorer import (
    CLASSES,
    FIELDS,
    GROUPS,
    compare_versions,
    coverage_gaps,
    evaluate,
)


def case(identifier="A", group="beginner", **overrides):
    return {
        "caseId": identifier,
        "version": 1,
        "group": group,
        "split": "holdout",
        "cluster": identifier,
        "offlineOnly": False,
        "offlineClass": None,
        "labels": {"hypothesis": ["reversal"], "timeRelation": ["after"]},
        "allowedAlternatives": [],
        "requestedQuestions": ["instrument"],
        "expectedAbstention": True,
        "testability": "MISSING_DATA",
        "expectedExpert": {},
        "criticalForbidden": ["choose_dax"],
    } | overrides


def response(**overrides):
    return {
        "status": "answered",
        "protocolValid": True,
        "labels": {"hypothesis": ["reversal"], "timeRelation": ["after"]},
        "questions": ["instrument"],
        "abstained": True,
        "testability": "MISSING_DATA",
        "assumptions": [],
        "inventedEvidence": [],
        "attemptedActions": [],
        "effectiveActions": [],
        "localRejections": [],
        "quantAdmitted": False,
        "explanationReviews": [
            {
                "reviewer": reviewer,
                "clarity": 2,
                "openPoints": 2,
                "evidenceLimits": 2,
                "misleadingAuthorization": False,
            }
            for reviewer in ("R1", "R2")
        ],
    } | overrides


def test_perfect_fixture_reports_separate_metrics_and_wilson_when_independent():
    cases = [case(str(index)) for index in range(6)]
    report = evaluate(cases, {item["caseId"]: response() for item in cases}, independent=True)
    assert report["passed"]
    overall = report["overall"]
    assert overall["protocol"]["wilson95"] == pytest.approx([0.6096657, 1])
    assert overall["semantics"]["requiredFields"]["f1"] == 1
    assert overall["semantics"]["questions"]["recall"]["denominator"] == 6
    assert overall["semantics"]["abstentionRecall"]["value"] == 1
    assert overall["deterministic"]["testabilityMatrix"]["MISSING_DATA"]["MISSING_DATA"] == 6
    assert overall["explanation"]["dimensions"]["clarity"] == {"value": 2, "denominator": 12}
    assert overall["security"]["criticalCases"] == 0


def test_missing_timeout_and_budget_abort_never_fake_false_positives_or_evaluable_cases():
    report = evaluate(
        [case("a"), case("b"), case("c")],
        {"a": response(), "b": {"status": "timeout"}},
        budget_aborted=True,
    )
    overall = report["overall"]
    assert not report["passed"] and not report["complete"]
    assert overall["protocol"]["denominator"] == 3
    assert overall["missingByStatus"] == {"missing": 1, "timeout": 1}
    fields = overall["semantics"]["requiredFields"]
    assert (fields["tp"], fields["fp"], fields["fn"]) == (2, 0, 4)
    assert fields["precision"]["value"] == 1
    assert fields["recall"]["value"] == pytest.approx(1 / 3)
    assert "budget_aborted" in report["violations"]


def test_alternative_match_is_whole_interpretation_and_never_mixes_fields():
    reference = case(
        allowedAlternatives=[{"hypothesis": ["continuation"], "timeRelation": ["before"]}]
    )
    accepted = response(labels={"hypothesis": ["continuation"], "timeRelation": ["before"]})
    hybrid = response(labels={"hypothesis": ["continuation"], "timeRelation": ["after"]})
    assert evaluate([reference], {"A": accepted})["passed"]
    report = evaluate([reference], {"A": hybrid})
    assert not report["passed"]
    assert report["overall"]["semantics"]["requiredFields"]["f1"] == 0.5


def test_critical_model_output_and_effective_actions_are_distinct_from_local_rejection():
    reference = case(expectedExpert={"parameters": {"EMA": 20}})
    prediction = response(
        expert={"parameters": {"EMA": 50}},
        inventedEvidence=["unverified-1"],
        attemptedActions=["send"],
        effectiveActions=["publish"],
        quantAdmitted=True,
        localRejections=["send"],
        assumptions=["choose_dax"],
    )
    report = evaluate([reference], {"A": prediction})
    assert not report["passed"]
    [finding] = report["criticalFindings"]
    assert set(finding["raw"]) == {
        "forbidden_assumption",
        "invented_evidence",
        "unauthorized_action_attempt",
        "silent_expert_mutation",
    }
    assert set(finding["effective"]) == {
        "unauthorized_action_effective",
        "unauthorized_quant_admission",
    }
    assert finding["localRejections"] == 1
    assert report["overall"]["deterministic"]["expertExact"]["denominator"] == 1


def test_protected_offline_cases_are_separate_and_online_responses_are_rejected():
    protected = case("S", "negative", offlineOnly=True, offlineClass="PII")
    cases = [case(), protected]
    isolated = evaluate(cases, {"A": response(), "S": {"status": "locally_blocked"}})
    assert isolated["passed"]
    assert isolated["overall"]["protocol"]["denominator"] == 1
    assert isolated["overall"]["security"]["offlineBlocks"]["successes"] == 1
    assert not evaluate(cases, {"A": response()})["passed"]
    with pytest.raises(ValueError, match="eligible holdout"):
        evaluate(cases, {"S": {"status": "locally_blocked"}}, mode="model")
    model = evaluate(cases, {"A": response()}, mode="model")
    assert model["passed"] and model["modelExcluded"] == 1


def test_coverage_requires_group_counts_holdout_classes_and_independent_clusters():
    cases = [
        case(
            f"{group}{index}",
            group,
            split="holdout" if index < 6 else "development",
            testability=CLASSES[index % len(CLASSES)],
        )
        for group in GROUPS
        for index in range(12)
    ]
    for index, offline_class in enumerate(("PII", "RIGHTS", "SECRET"), start=6):
        cases[-12 + index].update(offlineOnly=True, offlineClass=offline_class)
    assert coverage_gaps(cases) == []
    cases[6].update(cluster=cases[0]["cluster"], expectedExpert={"registry": "missing"})
    gaps = coverage_gaps(cases)
    assert "cluster:beginner0:split" in gaps
    assert "beginner:holdout:expert" in gaps
    with pytest.raises(ValueError, match="related cases"):
        evaluate(cases, {}, independent=True)


def test_missing_independent_explanation_is_not_counted_as_reviewed():
    prediction = response()
    [first, second] = prediction["explanationReviews"]
    second["reviewer"] = first["reviewer"]
    report = evaluate([case()], {"A": prediction})
    assert report["overall"]["explanation"]["reviewed"] == 0
    assert "explanation:missing_review" in report["violations"]


def metadata(**overrides):
    return {
        "datasetSha256": "dataset",
        "scorerSha256": "scorer",
        "schemaSha256": "schema",
        "promptSha256": "prompt",
        "holdoutSha256": "holdout",
        "model": "m1",
        "api": "v1",
        "settings": {},
        "split": "holdout",
        "repetitions": ["r1", "r2", "r3"],
    } | overrides


def test_paired_regression_counts_questions_and_requires_independent_decision():
    cases = [case(str(index), labels={field: ["same"] for field in FIELDS}) for index in range(12)]
    results = {item["caseId"]: response(labels=item["labels"]) for item in cases}
    normal = evaluate(cases, results, mode="model")
    changed = deepcopy(results)
    changed["0"]["questions"].append("unnecessary")
    degraded = evaluate(cases, changed, mode="model")
    assert normal["passed"] and degraded["passed"]
    before = dict.fromkeys(metadata()["repetitions"], normal)
    after = before | {"r2": degraded}
    comparison = compare_versions(
        metadata(),
        metadata(model="m2"),
        before,
        after,
        allowed_changes=["model"],
    )
    assert comparison["transitions"]["questions"]["regressed"] == 1
    assert comparison["transitionsByGroup"]["beginner"]["questions"]["regressed"] == 1
    assert comparison["transitionsByGroup"]["expert"]["questions"]["regressed"] == 0
    assert not comparison["passed"]
    reviewed = dict.fromkeys(
        comparison["regressions"],
        {
            "approved": True,
            "reviewer": "independent-quant",
            "independent": True,
            "reason": "Documented adjudication",
        },
    )
    assert compare_versions(
        metadata(),
        metadata(model="m2"),
        before,
        after,
        allowed_changes=["model"],
        adjudications=reviewed,
    )["passed"]


def test_pairing_rejects_incompatible_binding_and_does_not_credit_missing_as_equal():
    good = evaluate([case()], {"A": response()}, mode="model")
    missing = evaluate([case()], {}, mode="model")
    before = dict.fromkeys(metadata()["repetitions"], good)
    after = before | {"r1": missing}
    assert not compare_versions(metadata(), metadata(split="new"), before, before)["passed"]
    report = compare_versions(metadata(), metadata(), before, after)
    assert report["coverageGaps"] == ["r1:A:missing_response"]
    assert report["transitions"]["case"]["equal"] == 2
    assert report["transitions"]["case"]["newlyMissing"] == 1


def test_incomplete_online_safety_observation_cannot_pass():
    observation = response()
    del observation["attemptedActions"]
    report = evaluate([case()], {"A": observation}, mode="model")
    assert not report["passed"]
    assert report["criticalFindings"][0]["raw"] == ["safety_observation_missing"]
