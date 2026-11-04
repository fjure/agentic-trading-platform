"""QSL-01 independent schema evidence for REQ-QEX-007/008/009 and QCO-002/005.

These are contract checks. They do not prove selection arithmetic, immutable
storage, exposure registration, cursor signatures or runtime authorization.
"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "contracts/examples/v1"
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
MIRROR = json.loads((ROOT / "contracts/schemas/v1/quant-lab.schema.json").read_text("utf-8"))
FIXTURES = json.loads((EXAMPLES / "quant-lab-fixtures.json").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/quant-lab-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)


def validator(component, standalone=False):
    if standalone:
        return Draft202012Validator(
            {**MIRROR, "$ref": "#/$defs/" + component}, format_checker=contract_formats()
        )
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY, format_checker=contract_formats(),
    )


def example(component):
    name = next(name for name, schema in FIXTURES.items() if schema == component)
    return json.loads((EXAMPLES / name).read_text("utf-8"))


def fixture(name):
    return json.loads((EXAMPLES / f"quant-lab-{name}.valid.json").read_text("utf-8"))


def objects(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from objects(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from objects(item, (*path, index))


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_fixtures_validate_and_reject_unknown_keys_at_every_object(name, component, standalone):
    value = json.loads((EXAMPLES / name).read_text("utf-8"))
    check = validator(component, standalone)
    check.validate(value)
    for path, _ in objects(value):
        bad = copy.deepcopy(value)
        target = bad
        for key in path:
            target = target[key]
        target["unverifiedProviderPayload"] = "untrusted-synthetic-canary"
        assert not check.is_valid(bad), (name, path)


def test_every_new_component_is_mirrored_without_drift():
    def rewrite(value):
        if isinstance(value, dict):
            return {
                key: item.replace("#/components/schemas/", "#/$defs/")
                if key == "$ref" else rewrite(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    expected = {
        name: rewrite(schema) for name, schema in OAS["components"]["schemas"].items()
        if name.startswith("QuantLab")
    }
    assert expected
    assert {name: value for name, value in MIRROR["$defs"].items()
            if name.startswith("QuantLab")} == expected


def test_fixture_suite_covers_distinct_research_states_and_required_observations():
    values = [json.loads((EXAMPLES / name).read_text("utf-8")) for name in FIXTURES]
    found = {item for value in values for _, obj in objects(value)
             for item in obj.values() if isinstance(item, str)}
    assert {
        "NO_ELIGIBLE_CANDIDATE", "NOT_EVALUATED_NO_SELECTION", "NO_SELECTED_CANDIDATE",
        "REGISTERED_REUSE", "NO_REGISTERED_PRIOR_USE", "EXTERNAL_KNOWLEDGE_UNKNOWN",
        "IN_SAMPLE_ANALYSIS", "PARAMETER_SELECTION", "WALK_FORWARD_OOS", "FINAL_HOLDOUT",
        "NOT_TRAINABLE", "quant-lab-v1",
    } <= found
    fields = {key for value in values for _, obj in objects(value) for key in obj}
    assert {
        "strategySelectionPolicy", "selectionValues", "selectedCandidateId",
        "investedBarCount", "investedBarRatio", "meanGrossExposureRatio",
        "maxGrossExposureRatio", "buyFillCount", "sellFillCount", "entryCount",
        "exitCount", "endOpenPositionCount", "limitations",
    } <= fields


def test_legacy_components_and_standalone_contracts_remain_frozen():
    # Literal pre-QSL baseline, captured from HEAD before authoring QSL-01.
    # In particular this prevents widening the old strategy unions for EMA/RSI.
    legacy = {name: schema for name, schema in OAS["components"]["schemas"].items()
              if not name.startswith("QuantLab")}
    assert canonical_digest(legacy) == (
        "d309096c85f6c9bcd166bac03d6b3ab8829627fe8a25d93cccf104ab77f88918"
    )
    for name, digest in {
        "backtesting.schema.json": (
            "cfe93de519dfd83efba3a8c92a48c7caf8e2e52fdaa375ddfcf2f9f407e0aa30"
        ),
        "research.schema.json": "c7f4122ef17947ca3d9229ce7f26fc3eafbebed20dac820af42eb40185333b78",
    }.items():
        content = (ROOT / "contracts/schemas/v1" / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest


POLICIES = (
    "MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1",
    "CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1",
)
POLICY = {
    "policyId": POLICIES[0], "maxDrawdown": "0.10", "minValidationSessions": 20,
    "minimumEntryCount": None, "minimumInvestedBarRatio": None,
}


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("policy_id", POLICIES)
def test_selection_policy_explicit_null_minima_and_inclusive_bounds(standalone, policy_id):
    check = validator("QuantLabStrategySelectionPolicy", standalone)
    check.validate({**POLICY, "policyId": policy_id})
    for boundary in ("0", "1", "0.000000000001", "0.999999999999"):
        check.validate({**POLICY, "maxDrawdown": boundary, "minimumInvestedBarRatio": boundary})
    for sessions in (20, 252):
        for count in (0, 100000):
            check.validate({**POLICY, "minValidationSessions": sessions,
                            "minimumEntryCount": count})
    for key in POLICY:
        missing = {name: value for name, value in POLICY.items() if name != key}
        assert not check.is_valid(missing), key


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field,value", [
    ("policyId", "SHARPE"), ("policyId", "strict-pit-v1"), ("policyId", None),
    ("maxDrawdown", None), ("maxDrawdown", 0.1), ("maxDrawdown", True),
    ("minValidationSessions", 19), ("minValidationSessions", 253),
    ("minValidationSessions", True), ("minValidationSessions", "20"),
    ("minValidationSessions", 20.5), ("minValidationSessions", None),
    ("minimumEntryCount", -1), ("minimumEntryCount", 100001),
    ("minimumEntryCount", True), ("minimumEntryCount", "1"), ("minimumEntryCount", 1.5),
    ("minimumInvestedBarRatio", 0), ("minimumInvestedBarRatio", False),
    ("scoreExpression", "return / drawdown"), ("selectionPolicyVersion", "strict-pit-v1"),
])
def test_selection_policy_rejects_invalid_types_bounds_and_unknown_fields(field, value, standalone):
    assert not validator("QuantLabStrategySelectionPolicy", standalone).is_valid(
        {**POLICY, field: value}
    )


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field", ["maxDrawdown", "minimumInvestedBarRatio"])
@pytest.mark.parametrize("value", [
    "NaN", "Infinity", "-Infinity", "1e-3", " 0.1", "0.1 ", ".1", "01", "1.",
    "-0", "-0.1", "1.000000000001", "0.0000000000001",
])
def test_policy_decimal_parameters_are_bounded_finite_strings(field, value, standalone):
    assert not validator("QuantLabStrategySelectionPolicy", standalone).is_valid(
        {**POLICY, field: value}
    )


@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_public_versions_roles_fitting_and_statuses_are_closed(name, component):
    value = json.loads((EXAMPLES / name).read_text("utf-8"))
    check = validator(component)
    for path, target in objects(value):
        for key, mutation in {
            "schemaVersion": "quant-lab-v2", "fittingStatus": "TRAINED_MODEL",
            "status": "UNRECOGNIZED_STATUS", "role": "TRAINED_MODEL",
            "phaseRole": "TRAINED_MODEL",
        }.items():
            if key not in target:
                continue
            bad = copy.deepcopy(value)
            changed = bad
            for step in path:
                changed = changed[step]
            changed[key] = mutation
            assert not check.is_valid(bad), (name, path, key)


@pytest.mark.parametrize("name,component", FIXTURES.items())
def test_scalar_fields_refuse_containers_and_timestamps_require_valid_utc(name, component):
    value = json.loads((EXAMPLES / name).read_text("utf-8"))
    check = validator(component)
    for path, target in objects(value):
        for key, original in target.items():
            if original is None or isinstance(original, (dict, list)):
                continue
            mutations = [[], {}]
            if isinstance(original, str) and original.endswith("Z") and "T" in original:
                mutations += [original[:-1], original[:-1] + "+00:00",
                              "2026-02-30T00:00:00Z"]
            for mutation in mutations:
                bad = copy.deepcopy(value)
                changed = bad
                for step in path:
                    changed = changed[step]
                changed[key] = mutation
                assert not check.is_valid(bad), (name, path, key, mutation)


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("field", [
    "strategySelectionPolicyHash", "definitionHash", "selectionHash",
])
def test_bound_hashes_are_lowercase_sha256_when_present(field, standalone):
    checked = 0
    for filename, component in FIXTURES.items():
        value = json.loads((EXAMPLES / filename).read_text("utf-8"))
        for path, target in objects(value):
            if field not in target or target[field] is None:
                continue
            checked += 1
            for invalid in (None, "a" * 63, "A" * 64, "x" * 64, 1):
                bad = copy.deepcopy(value)
                changed = bad
                for step in path:
                    changed = changed[step]
                changed[field] = invalid
                assert not validator(component, standalone).is_valid(bad), (filename, path, field)
    assert checked, field


@pytest.mark.parametrize("standalone", [False, True])
def test_selection_status_requires_winner_or_explicit_no_winner(standalone):
    check = validator("QuantLabSelection", standalone)
    selected = fixture("selection-selected")
    assert not check.is_valid({**selected, "selectedCandidateId": None})
    assert not check.is_valid({**selected, "eligibleCount": 0})
    assert not check.is_valid({**selected, "scope": "FINAL", "fold": 0})
    assert not check.is_valid({**selected, "scope": "FOLD", "fold": None})
    no_choice = fixture("selection-no-eligible")
    check.validate(no_choice)
    assert not check.is_valid({**no_choice, "selectedCandidateId": selected["selectedCandidateId"]})
    assert not check.is_valid({**no_choice, "eligibleCount": 1})


@pytest.mark.parametrize("standalone", [False, True])
def test_incomplete_search_preserves_complete_candidate_evidence_without_ranking(standalone):
    value = fixture("selection-incomplete")
    check = validator("QuantLabSelection", standalone)
    check.validate(value)
    assert value["candidates"][0]["eligibility"] == "ELIGIBLE"
    assert value["candidates"][0]["medianSelectionValues"] is not None
    assert value["candidates"][1]["eligibility"] == "INCOMPLETE"
    assert all(item["rank"] is None and item["rankingKey"] is None
               for item in value["candidates"])
    ranked = copy.deepcopy(value)
    selected = fixture("selection-selected")["candidates"][0]
    ranked["candidates"][0].update(rank=1, rankingKey=selected["rankingKey"])
    assert not check.is_valid(ranked)


@pytest.mark.parametrize("standalone", [False, True])
def test_selection_gates_cannot_duplicate_one_gate_and_omit_another(standalone):
    value = fixture("selection-selected")
    gates = value["candidates"][0]["validationValues"][0]["gates"]
    assert len({item["gate"] for item in gates}) == 7
    gates[0]["gate"] = gates[1]["gate"]
    assert not validator("QuantLabSelection", standalone).is_valid(value)


@pytest.mark.parametrize("standalone", [False, True])
def test_validation_results_keep_exact_values_and_medians_never_average_counts(standalone):
    value = fixture("result-five-bar-validation")
    check = validator("QuantLabCandidateResult", standalone)
    check.validate(value)
    assert not check.is_valid({**value, "selectionValues": None})
    median = fixture("selection-selected")["candidates"][0]["medianSelectionValues"]
    median_check = validator("QuantLabSelectionMedians", standalone)
    median_check.validate(median)
    assert not median_check.is_valid({**median, "entryCount": 1})
    assert not median_check.is_valid({**median, "completeValidationSessions": 20})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component,name", [
    ("QuantLabExperimentDefinition", "experiment-draft"),
    ("QuantLabManifestReference", "manifest-reference"),
    ("QuantLabSelection", "selection-selected"),
])
def test_immutable_identities_bind_selector_version_and_fingerprint(component, name, standalone):
    value = fixture(name)
    check = validator(component, standalone)
    for key in ("selectorVersion", "selectorFingerprint", "strategySelectionPolicyHash"):
        assert key in value
        assert not check.is_valid({field: item for field, item in value.items() if field != key})
    assert not check.is_valid({**value, "selectorVersion": "latest"})
    assert not check.is_valid({**value, "selectorFingerprint": "A" * 64})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("status", ["N/A", "INCOMPLETE", "INVALID_INPUT"])
def test_metrics_never_replace_missing_evidence_with_zero_or_missing_reason(status, standalone):
    value = fixture("metric-incomplete")
    check = validator("QuantLabMetric", standalone)
    valid = {**value, "status": "VALID", "value": "0.000000000000", "reasonCode": None}
    check.validate(valid)
    assert not check.is_valid({**valid, "value": None})
    assert not check.is_valid({**valid, "reasonCode": "INCOMPLETE_BAR_SERIES"})
    missing = {**value, "status": status}
    check.validate(missing)
    assert not check.is_valid({**missing, "value": "0.000000000000"})
    assert not check.is_valid({**missing, "reasonCode": None})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("invalid", [True, -1, 1.5, "1", "NaN"])
def test_activity_counts_are_exact_nonnegative_integers(invalid, standalone):
    value = fixture("activity-five-bars")["entryCount"]
    assert not validator("QuantLabCountMetric", standalone).is_valid({**value, "value": invalid})


@pytest.mark.parametrize("standalone", [False, True])
def test_selection_values_preserve_more_than_twelve_places_without_display_quantization(standalone):
    value = fixture("selection-values-exact")
    check = validator("QuantLabSelectionValues", standalone)
    check.validate(value)
    assert value["maxDrawdown"] == "0.1000000000004"
    for invalid in (0.1, None, "NaN", "Infinity", "1e-1", "-0", "0.1000000000000"):
        assert not check.is_valid({**value, "maxDrawdown": invalid})


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("component,name", [
    ("QuantLabCandidateResult", "result-five-bar-analysis"),
    ("QuantLabEquityPage", "equity-page"),
    ("QuantLabDrawdownPage", "drawdown-page"),
])
def test_phase_roles_and_fold_nullability_are_coupled(component, name, standalone):
    value = fixture(name)
    check = validator(component, standalone)
    assert not check.is_valid({**value, "researchRole": "FINAL_HOLDOUT"})
    assert not check.is_valid({**value, "fold": None})
    holdout = {**value, "phase": "holdout", "researchRole": "FINAL_HOLDOUT", "fold": None}
    check.validate(holdout)
    assert not check.is_valid({**holdout, "fold": 0})
    assert not check.is_valid({**holdout, "researchRole": "WALK_FORWARD_OOS"})


@pytest.mark.parametrize("standalone", [False, True])
def test_phase_bindings_cannot_claim_holdout_role_on_training(standalone):
    value = fixture("manifest-reference")["phaseBindings"][0]
    check = validator("QuantLabPhaseBinding", standalone)
    check.validate(value)
    assert not check.is_valid({**value, "researchRole": "FINAL_HOLDOUT", "fold": None})


@pytest.mark.parametrize("standalone", [False, True])
def test_succeeded_batch_cannot_be_an_empty_acceptance_receipt(standalone):
    value = fixture("batch-succeeded")
    check = validator("QuantLabBatch", standalone)
    check.validate(value)
    for key, invalid in (("manifest", None), ("reportId", None), ("reportHash", None),
                         ("attemptCount", 0)):
        assert not check.is_valid({**value, key: invalid}), key
    pending = fixture("batch-accepted")
    assert not check.is_valid({**pending, "state": "SUCCEEDED"})


@pytest.mark.parametrize("standalone", [False, True])
def test_historical_overlap_has_provenance_and_no_reuse_is_not_a_freshness_certificate(standalone):
    history = fixture("history-registered-reuse")
    overlap = history["overlaps"][0]
    check = validator("QuantLabExposureOverlap", standalone)
    assert not check.is_valid({**overlap, "priorBatchId": None, "priorRunId": None})
    check.validate({**overlap, "priorBatchId": None,
                    "priorRunId": "11111111-1111-4111-8111-111111111111"})
    fresh = fixture("history-no-registered-prior-use")
    history_check = validator("QuantLabResearchHistory", standalone)
    assert fresh["externalKnowledgeStatus"] == "EXTERNAL_KNOWLEDGE_UNKNOWN"
    assert not history_check.is_valid({**fresh, "externalKnowledgeStatus": "INDEPENDENT"})
    assert not history_check.is_valid({**fresh, "overlapCount": 1})
    assert not history_check.is_valid({**history, "reuseStatus": "NO_REGISTERED_PRIOR_USE"})


@pytest.mark.parametrize("standalone", [False, True])
def test_problem_status_codes_are_consistent_and_details_never_echoed(standalone):
    check = validator("QuantLabProblem", standalone)
    value = fixture("problem-400")
    assert not check.is_valid({**value, "code": "UNAVAILABLE"})
    assert not check.is_valid({**value, "status": 200})
    assert not check.is_valid({**value, "detail": "synthetic private provider response"})
