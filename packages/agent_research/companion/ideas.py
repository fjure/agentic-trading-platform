"""Immutable ResearchIdea versions and their permitted lifecycle actions."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from jsonschema import Draft202012Validator

from .testability import HypothesisIdentity, preliminary_testability


class IdeaStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    PROPOSED = "PROPOSED"
    REVIEWED = "REVIEWED"
    EXPERIMENT_DRAFTED = "EXPERIMENT_DRAFTED"
    TESTED = "TESTED"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"
    REJECTED = "REJECTED"


class IdeaOrigin(StrEnum):
    USER = "USER"
    AGENT = "AGENT"


class IdeaAction(StrEnum):
    CREATE = "CREATE"
    PUBLISH = "PUBLISH"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    REVISE = "REVISE"
    CONFIRM_HYPOTHESIS = "CONFIRM_HYPOTHESIS"
    VALIDATE_DRAFT = "VALIDATE_DRAFT"
    LINK_EXPERIMENT = "LINK_EXPERIMENT"
    COMPLETE_REPORT = "COMPLETE_REPORT"
    BLOCK = "BLOCK"
    MARK_UNSUPPORTED = "MARK_UNSUPPORTED"
    RECHECK = "RECHECK"


class IdeaDenied(ValueError):
    """Redacted lifecycle refusal suitable for application error mapping."""


@dataclass(frozen=True, slots=True)
class IdeaVersion:
    idea_id: str
    version: int
    status: IdeaStatus
    _content: bytes = field(repr=False)

    def to_document(self) -> dict:
        return json.loads(self._content)


_ASSESSABLE = frozenset({
    IdeaStatus.DISCOVERED, IdeaStatus.PROPOSED, IdeaStatus.REVIEWED, IdeaStatus.EXPERIMENT_DRAFTED,
})
_RECHECKABLE = frozenset({
    IdeaStatus.BLOCKED, IdeaStatus.UNSUPPORTED, IdeaStatus.REJECTED, IdeaStatus.TESTED,
})
_TRANSITIONS = {
    IdeaAction.PUBLISH: (frozenset({IdeaStatus.DISCOVERED}), IdeaStatus.PROPOSED),
    IdeaAction.REVIEW: (frozenset({IdeaStatus.PROPOSED}), IdeaStatus.REVIEWED),
    IdeaAction.REJECT: (frozenset({
        IdeaStatus.PROPOSED, IdeaStatus.REVIEWED, IdeaStatus.EXPERIMENT_DRAFTED,
        IdeaStatus.BLOCKED, IdeaStatus.UNSUPPORTED,
    }), IdeaStatus.REJECTED),
    IdeaAction.REVISE: (frozenset({IdeaStatus.PROPOSED}), IdeaStatus.PROPOSED),
    IdeaAction.CONFIRM_HYPOTHESIS: (frozenset({IdeaStatus.PROPOSED}), IdeaStatus.PROPOSED),
    IdeaAction.VALIDATE_DRAFT: (frozenset({IdeaStatus.REVIEWED}), IdeaStatus.EXPERIMENT_DRAFTED),
    IdeaAction.LINK_EXPERIMENT: (
        frozenset({IdeaStatus.EXPERIMENT_DRAFTED}), IdeaStatus.EXPERIMENT_DRAFTED,
    ),
    IdeaAction.COMPLETE_REPORT: (frozenset({IdeaStatus.EXPERIMENT_DRAFTED}), IdeaStatus.TESTED),
    IdeaAction.BLOCK: (_ASSESSABLE, IdeaStatus.BLOCKED),
    IdeaAction.MARK_UNSUPPORTED: (_ASSESSABLE, IdeaStatus.UNSUPPORTED),
}
_ASSESSMENT_FIELDS = frozenset({"testability", "openQuestions", "uncertainties"})
_MUTABLE_INPUT = frozenset({"title", "description", "hypothesis"})
_ALLOWED_CHANGES = {
    IdeaAction.REVISE: _MUTABLE_INPUT,
    IdeaAction.CONFIRM_HYPOTHESIS: _MUTABLE_INPUT | {
        "dialogueId", "interpretation", "assumptions", "openQuestions", "uncertainties",
    },
    IdeaAction.VALIDATE_DRAFT: _ASSESSMENT_FIELDS | {"experimentDraftIds"},
    IdeaAction.LINK_EXPERIMENT: frozenset({"experimentIds"}),
    IdeaAction.BLOCK: _ASSESSMENT_FIELDS,
    IdeaAction.MARK_UNSUPPORTED: _ASSESSMENT_FIELDS,
    IdeaAction.RECHECK: _MUTABLE_INPUT | {
        "evidence", "instrumentIds", "observationStart", "observationEnd", "relatedIdeaIds",
        "runId", "sourceId", "discovery",
    },
}
_USER_INTAKE = frozenset({"title", "description", "originalText", "hypothesis", "dialogueId"})
_AGENT_INTAKE = frozenset({
    "title", "description", "sourceId", "hypothesis", "dialogueId", "runId", "openQuestions",
    "uncertainties", "instrumentIds", "evidence", "relatedIdeaIds", "observationStart",
    "observationEnd", "discovery",
})


def _initial_status(origin: IdeaOrigin) -> IdeaStatus:
    return IdeaStatus.DISCOVERED if origin is IdeaOrigin.AGENT else IdeaStatus.PROPOSED


def _timestamp(value: datetime) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise IdeaDenied("INVALID_IDEA_TIME")
    return value.isoformat().replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _evidence_keys(items: list[dict]) -> set[tuple[str, str, int]]:
    return {(item["kind"], item["evidenceId"], item["revision"]) for item in items}


class IdeaLifecycle:
    """Validate append candidates. The store must lock/CAS before appending.

    The local policy screens content before storage. The authoritative reference
    gate must resolve action, actor/decision, sources, draft and completed report
    in the same transaction where applicable. Client/model assertions are not proofs.
    """

    def __init__(
        self,
        contract: Path,
        *,
        permits: Callable[[dict], bool],
        verifies: Callable[[IdeaAction, dict | None, dict], bool],
    ):
        schema = json.loads(contract.read_text(encoding="utf-8"))
        self._validator = Draft202012Validator(
            schema | {"$ref": "#/$defs/AgentIdeaVersionV1"},
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        self._permits = permits
        self._verifies = verifies

    def create(
        self, content: Mapping, *, idea_id: str, initiator_id: str, origin: IdeaOrigin,
        created_at: datetime,
    ) -> IdeaVersion:
        try:
            origin = IdeaOrigin(origin)
        except ValueError:
            raise IdeaDenied("INVALID_IDEA") from None
        allowed = _USER_INTAKE if origin is IdeaOrigin.USER else _AGENT_INTAKE
        if content.keys() - allowed:
            raise IdeaDenied("INVALID_IDEA")
        data = {
            "hypothesis": None, "openQuestions": [], "assumptions": [], "uncertainties": [],
            "instrumentIds": [], "evidence": [], "testability": None, "relatedIdeaIds": [],
            **content,
            "id": idea_id, "initiatorId": initiator_id, "origin": origin,
            "version": 1, "previousVersion": None, "status": _initial_status(origin),
            "createdAt": _timestamp(created_at), "experimentDraftIds": [], "experimentIds": [],
        }
        if "description" not in data and type(data.get("originalText")) is str:
            data["description"] = data["originalText"][:1000]
        return self._accept(IdeaAction.CREATE, None, data)

    def revise(
        self, current: IdeaVersion, *, expected_version: int, action: IdeaAction,
        decision_id: str, created_at: datetime, changes: Mapping | None = None,
    ) -> IdeaVersion:
        prior = current.to_document()
        if type(expected_version) is not int or prior["version"] != expected_version:
            raise IdeaDenied("STALE_IDEA_VERSION")
        try:
            action = IdeaAction(action)
        except ValueError:
            raise IdeaDenied("INVALID_IDEA_TRANSITION") from None
        status = IdeaStatus(prior["status"])
        origin = IdeaOrigin(prior["origin"])
        if action is IdeaAction.RECHECK and status in _RECHECKABLE:
            target = _initial_status(origin)
        elif action in _TRANSITIONS and status in _TRANSITIONS[action][0]:
            target = _TRANSITIONS[action][1]
        else:
            raise IdeaDenied("INVALID_IDEA_TRANSITION")

        changes = dict(changes or {})
        if changes.keys() - _ALLOWED_CHANGES.get(action, frozenset()):
            raise IdeaDenied("INVALID_IDEA_CHANGE")
        data = prior | changes | {
            "version": expected_version + 1, "previousVersion": expected_version, "status": target,
            "decisionId": decision_id, "createdAt": _timestamp(created_at),
        }
        if _instant(data["createdAt"]) < _instant(prior["createdAt"]):
            raise IdeaDenied("INVALID_IDEA_TIME")
        if action is IdeaAction.RECHECK or (
            action is IdeaAction.REVISE and data["hypothesis"] != prior["hypothesis"]
        ):
            data.update(testability=None, assumptions=[], openQuestions=[], uncertainties=[])
            data.pop("interpretation", None)
        if action is IdeaAction.RECHECK:
            data.update(experimentDraftIds=[], experimentIds=[])
        if action is IdeaAction.CONFIRM_HYPOTHESIS:
            if changes.get("hypothesis") is None or "assumptions" not in changes:
                raise IdeaDenied("INVALID_IDEA_CHANGE")
            data["testability"] = None
        return self._accept(action, prior, data)

    def restore(self, document: Mapping) -> IdeaVersion:
        """Rehydrate a protected persisted version without re-running mutable policies."""
        return self._freeze(dict(document))

    def _accept(self, action: IdeaAction, prior: dict | None, data: dict) -> IdeaVersion:
        version = self._freeze(data)
        if action is IdeaAction.CONFIRM_HYPOTHESIS:
            hypothesis = HypothesisIdentity.from_document(data["hypothesis"])
            assessment = preliminary_testability(hypothesis)
            if assessment is not None:
                data["testability"] = assessment
                version = self._freeze(data)
        classification = (data["testability"] or {}).get("classification")
        if action is IdeaAction.VALIDATE_DRAFT and (
            not data["experimentDraftIds"]
            or classification not in {"EXACTLY_TESTABLE", "TESTABLE_WITH_DECLARED_APPROXIMATION"}
        ):
            raise IdeaDenied("VALIDATED_DRAFT_REQUIRED")
        if action is IdeaAction.LINK_EXPERIMENT and (
            not set(prior["experimentIds"]) < set(data["experimentIds"])
        ):
            raise IdeaDenied("LINKED_EXPERIMENT_REQUIRED")
        if action is IdeaAction.COMPLETE_REPORT and not data["experimentIds"]:
            raise IdeaDenied("LINKED_EXPERIMENT_REQUIRED")
        if action is IdeaAction.RECHECK and prior["status"] == IdeaStatus.REJECTED and not (
            _evidence_keys(data["evidence"]) - _evidence_keys(prior["evidence"])
        ):
            raise IdeaDenied("NEW_EVIDENCE_REQUIRED")
        if action is IdeaAction.MARK_UNSUPPORTED and (
            classification not in {"UNSUPPORTED", "REQUIRES_NEW_STRATEGY"}
        ):
            raise IdeaDenied("UNSUPPORTED_ASSESSMENT_REQUIRED")
        try:
            permitted = self._permits(version.to_document()) is True
            verified = permitted and self._verifies(action, prior, version.to_document()) is True
        except Exception:
            raise IdeaDenied("IDEA_POLICY_DENIED") from None
        if not permitted:
            raise IdeaDenied("IDEA_POLICY_DENIED")
        if not verified:
            raise IdeaDenied("IDEA_REFERENCE_DENIED")
        return version

    def _freeze(self, data: dict) -> IdeaVersion:
        try:
            if not self._validator.is_valid(data):
                raise ValueError
            origin, status = IdeaOrigin(data["origin"]), IdeaStatus(data["status"])
            version = data["version"]
            if type(version) is not int or data["previousVersion"] != (version - 1 or None):
                raise ValueError
            if version == 1 and status is not _initial_status(origin):
                raise ValueError
            if version > 1 and "decisionId" not in data:
                raise ValueError
            if not ({"originalText", "sourceId"} & data.keys()):
                raise ValueError
            if origin is IdeaOrigin.USER and (
                status is IdeaStatus.DISCOVERED or "discovery" in data
            ):
                raise ValueError
            if ("observationStart" in data) != ("observationEnd" in data):
                raise ValueError
            if "observationStart" in data and (
                _instant(data["observationStart"]) >= _instant(data["observationEnd"])
            ):
                raise ValueError
            if data["id"] in data["relatedIdeaIds"]:
                raise ValueError
            if len(_evidence_keys(data["evidence"])) != len(data["evidence"]):
                raise ValueError
            for key in ("instrumentIds", "experimentIds", "experimentDraftIds", "relatedIdeaIds"):
                if len(set(data[key])) != len(data[key]):
                    raise ValueError
            content = json.dumps(
                data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode()
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise IdeaDenied("INVALID_IDEA") from None
        return IdeaVersion(data["id"], version, status, content)
