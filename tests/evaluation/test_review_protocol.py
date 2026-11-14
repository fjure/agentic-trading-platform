from copy import deepcopy

import pytest

from .catalog import FIXTURES, _read_json
from .review_protocol import annotation_sha256, quant_checks, validate_review


def test_review_handoff_rejects_unisolated_and_conflicting_annotations():
    doc = _read_json(FIXTURES / "development.v1.json")
    case = next(row for row in doc["cases"] if not row["expectedExpert"])
    case = case | {"split": "holdout"}
    holdout = "a" * 64

    def report(profile, context):
        return {
            "version": 1,
            "holdoutSha256": holdout,
            "identity": {
                "provider": "fixture",
                "model": "review-double",
                "modelVersion": "v1",
                "profile": profile,
                "contextSha256": context * 64,
                "promptVersion": "v1",
                "promptSha256": "a" * 64,
            },
            "fixturesOnly": True,
            "isolated": dict.fromkeys(
                (
                    "noTargetResponses",
                    "noDevelopmentHistory",
                    "noPromptOrScorerWrites",
                    "noExperimentStarts",
                    "noBrokerRights",
                    "noUncontrolledTools",
                    "isolatedContext",
                ),
                True,
            ),
            "decisions": [
                {
                    "caseId": case["caseId"],
                    "annotationSha256": annotation_sha256(case),
                    "decision": "accepted",
                    "limitations": [],
                    "quantChecks": quant_checks(case),
                }
            ],
        }

    primary, secondary = report("review-one", "b"), report("review-two", "c")

    def validate(other):
        return validate_review(
            holdout,
            [case],
            primary,
            other,
            target_provider="test",
            target_model="candidate",
        )

    result = validate(secondary)
    assert result["fixturesOnly"] is True
    assert "DIFFERENT_PROVIDER_REVIEW" in result["independence"]
    for field, value in (
        ("annotationSha256", "d" * 64),
        ("decision", "disputed"),
        ("quantChecks", {"result": "guaranteed return"}),
    ):
        changed = deepcopy(secondary)
        changed["decisions"][0][field] = value
        with pytest.raises(ValueError):
            validate(changed)
    secondary["isolated"]["noDevelopmentHistory"] = False
    with pytest.raises(ValueError):
        validate(secondary)
