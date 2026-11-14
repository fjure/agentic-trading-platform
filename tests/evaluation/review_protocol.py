"""Deterministic validation of a two-stage, tool-free Quant review handoff."""

import json
import re
from hashlib import sha256

from packages.backtesting.experiment import SearchSpace, enumerate_candidates
from packages.backtesting.strategies import RESEARCH_MODES, resolve_strategy

_SHA = re.compile(r"[a-f0-9]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_FIELDS = {"hypothesis", "event", "timeRelation", "conditions", "instrument", "missingInformation"}
_CLASSES = {"EXACT", "APPROXIMATION", "MISSING_STRATEGY", "MISSING_DATA", "UNSUPPORTED"}
_REVIEW_FIELDS = {"version", "holdoutSha256", "identity", "isolated", "fixturesOnly", "decisions"}
_ID_FIELDS = {
    "provider",
    "model",
    "modelVersion",
    "profile",
    "contextSha256",
    "promptVersion",
    "promptSha256",
}
_ISOLATED = {
    "noTargetResponses",
    "noDevelopmentHistory",
    "noPromptOrScorerWrites",
    "noExperimentStarts",
    "noBrokerRights",
    "noUncontrolledTools",
    "isolatedContext",
}
_DECISION_FIELDS = {"caseId", "annotationSha256", "decision", "limitations", "quantChecks"}
_LIMITATIONS = {"SEMANTIC_JUDGMENT", "DATA_AVAILABILITY_UNVERIFIED", "NO_HUMAN_REVIEW"}
_ANNOTATION = {
    "labels",
    "allowedAlternatives",
    "requestedQuestions",
    "expectedAbstention",
    "testability",
    "expectedExpert",
    "criticalForbidden",
    "dataRequirements",
    "rationale",
}


def _invalid():
    return ValueError("EVALUATION_REVIEW_INVALID")


def canonical_review_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _digest(value) -> str:
    return sha256(canonical_review_bytes(value)).hexdigest()


def _matches(pattern, value) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _texts(value) -> bool:
    return (
        type(value) is list
        and all(isinstance(item, str) and item.strip() and len(item) <= 2048 for item in value)
        and len(value) == len(set(value))
    )


def _labels(value) -> bool:
    return (
        type(value) is dict
        and value.keys() == _FIELDS
        and all(_texts(values) for values in value.values())
    )


def annotation_sha256(case: dict) -> str:
    return _digest(
        {
            "caseId": case["caseId"],
            "version": case["version"],
            **{key: case[key] for key in _ANNOTATION},
        }
    )


def _values(source: str, integer: bool) -> list:
    vector = (
        source[1:-1].split(",") if source.startswith("[") and source.endswith("]") else [source]
    )
    if any(not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", item) for item in vector):
        raise _invalid()
    if integer and any("." in item for item in vector):
        raise _invalid()
    return [int(item) for item in vector] if integer else vector


def _parameters(expert: dict, definition) -> str:
    reference = expert.get("parameters")
    if reference is None:
        return "not_applicable"
    if definition is None:
        return "unverified"
    try:
        pairs = [segment.split("=", 1) for segment in reference.split(";")]
        if any(len(pair) != 2 for pair in pairs):
            return "invalid"
        entries = dict(pairs)
        parameter_types = {item.name: item.value_type for item in definition.parameter_definitions}
        if len(entries) != len(pairs) or entries.keys() != parameter_types.keys():
            return "invalid"
        space = SearchSpace.from_mapping(
            {
                "strategyId": definition.strategy_id,
                "version": definition.version,
                "parameterValues": {
                    name: _values(value, parameter_types[name] == "INTEGER")
                    for name, value in entries.items()
                },
            }
        )
        enumerate_candidates((space,))
    except ValueError:
        return "invalid"
    return "valid"


def quant_checks(case: dict) -> dict:
    """Check normalized reference bindings with the authoritative Quant registry."""
    expert = case["expectedExpert"]
    if type(expert) is not dict or not expert.keys() <= {
        "registry",
        "parameters",
        "mode",
        "validation",
    }:
        raise _invalid()
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 2048
        for value in expert.values()
    ):
        raise _invalid()
    registry = expert.get("registry")
    definition = None
    if registry:
        strategy, _, version = registry.partition("@")
        if version:
            try:
                definition = resolve_strategy(strategy, version, require_execution=True)
            except ValueError:
                pass
    mode = expert.get("mode")
    checks = {
        "registry": ("valid" if definition is not None else "invalid")
        if registry
        else "not_applicable",
        "parameters": _parameters(expert, definition),
        "mode": ("valid" if mode in RESEARCH_MODES else "invalid") if mode else "not_applicable",
        "quantitativeResult": "not_claimed",
    }
    if case["testability"] == "EXACT" and any(
        checks[field] != "valid" for field in ("registry", "parameters", "mode")
    ):
        raise _invalid()
    return checks


def _case_reference(case: dict) -> tuple[str, dict]:
    if (
        type(case) is not dict
        or not _ANNOTATION <= case.keys()
        or not _matches(_IDENTIFIER, case.get("caseId"))
        or case.get("split") != "holdout"
        or type(case.get("version")) is not int
        or case["version"] < 1
        or case["testability"] not in _CLASSES
        or not _labels(case["labels"])
        or type(case["allowedAlternatives"]) is not list
        or not all(_labels(item) for item in case["allowedAlternatives"])
        or case["expectedAbstention"] is not None
        and type(case["expectedAbstention"]) is not bool
        or not all(
            _texts(case[key])
            for key in (
                "requestedQuestions",
                "criticalForbidden",
                "dataRequirements",
            )
        )
        or not _texts([case["rationale"]])
        or (case["testability"] == "EXACT" and case["expectedAbstention"] is True)
    ):
        raise _invalid()
    return annotation_sha256(case), quant_checks(case)


def _identity(identity: dict) -> dict:
    if (
        type(identity) is not dict
        or identity.keys() != _ID_FIELDS
        or any(
            not _matches(_SHA if name.endswith("Sha256") else _IDENTIFIER, value)
            for name, value in identity.items()
        )
    ):
        raise _invalid()
    return dict(identity)


def _report(report: dict, holdout: str, checked: dict) -> tuple[dict, set[str]]:
    if (
        type(report) is not dict
        or report.keys() != _REVIEW_FIELDS
        or type(report["version"]) is not int
        or report["version"] != 1
        or report["holdoutSha256"] != holdout
        or type(report["fixturesOnly"]) is not bool
        or type(report["isolated"]) is not dict
        or report["isolated"].keys() != _ISOLATED
        or any(value is not True for value in report["isolated"].values())
        or type(report["decisions"]) is not list
        or len(report["decisions"]) != len(checked)
    ):
        raise _invalid()
    identity = _identity(report["identity"])
    seen = set()
    limitations = {"NO_HUMAN_REVIEW"}
    for item in report["decisions"]:
        if type(item) is not dict or item.keys() != _DECISION_FIELDS:
            raise _invalid()
        case_id = item["caseId"]
        if (
            not _matches(_IDENTIFIER, case_id)
            or case_id in seen
            or case_id not in checked
            or item["decision"] != "accepted"
            or (item["annotationSha256"], item["quantChecks"]) != checked[case_id]
            or not _texts(item["limitations"])
            or not set(item["limitations"]) <= _LIMITATIONS
        ):
            raise _invalid()
        seen.add(case_id)
        limitations.update(item["limitations"])
    return identity, limitations


def validate_review(
    holdout_sha256: str,
    cases: list[dict],
    primary: dict,
    secondary: dict,
    *,
    target_provider: str,
    target_model: str,
) -> dict:
    """Validate both reports and return hashes, identities and code-only limitations.

    Isolation fields are evidence claims made by the isolated reviewer deployments.
    The independent custodian must establish the origin and trust the execution context.
    """
    try:
        if (
            not _matches(_SHA, holdout_sha256)
            or not _matches(_IDENTIFIER, target_provider)
            or not _matches(_IDENTIFIER, target_model)
            or type(cases) is not list
            or not 1 <= len(cases) <= 1000
        ):
            raise _invalid()
        checked = {case["caseId"]: _case_reference(case) for case in cases}
        if len(checked) != len(cases):
            raise _invalid()
        first, first_limits = _report(primary, holdout_sha256, checked)
        second, second_limits = _report(secondary, holdout_sha256, checked)
        if (
            first["contextSha256"] == second["contextSha256"]
            or first["profile"] == second["profile"]
            or primary["fixturesOnly"] != secondary["fixturesOnly"]
        ):
            raise _invalid()
        providers = {first["provider"], second["provider"]}
        models = {first["model"], second["model"]}
        limitations = first_limits | second_limits | {"SEMANTIC_JUDGMENT"}
        if target_provider not in providers:
            level = "DIFFERENT_PROVIDER_REVIEW"
        elif target_model not in models:
            level = "DIFFERENT_MODEL_REVIEW"
        else:
            level = "SAME_MODEL_SEPARATE_CONTEXT"
            limitations.add("TARGET_MODEL_SHARED")
        if len(providers) == len(models) == 1:
            limitations.add("REVIEW_MODEL_SHARED")
        return {
            "version": 1,
            "primary": first,
            "secondary": second,
            "independence": sorted({"SEPARATE_CONTEXT", "DETERMINISTICALLY_VERIFIED", level}),
            "limitations": sorted(limitations),
            "reviewSha256": _digest([primary, secondary]),
            "caseCount": len(checked),
            "fixturesOnly": primary["fixturesOnly"],
        }
    except (KeyError, TypeError, RecursionError):
        raise _invalid() from None
