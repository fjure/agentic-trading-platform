"""Closed, deterministic publication boundary for offline Research Discovery."""

import json
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256

from jsonschema import Draft202012Validator

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HASH = re.compile(r"[a-f0-9]{64}")
_SLOTS = (
    "market", "trigger", "signal", "observationWindow", "target", "measurementHorizon",
    "benchmark", "evaluationCriterion",
)
_EFFORT = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "UNKNOWN": 3}
_MAX_PAYLOAD = 65536


class EvidenceKind(StrEnum):
    MARKET_REVISION = "MARKET_REVISION"
    RESEARCH_FINDING = "RESEARCH_FINDING"
    QUANT_RESULT = "QUANT_RESULT"
    DATA_QUALITY = "DATA_QUALITY"


class PublicationDenied(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _closed(properties: dict) -> dict:
    return {
        "type": "object", "properties": properties, "required": list(properties),
        "additionalProperties": False,
    }


def _text(maximum: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


_INSTANT = {"type": "string", "format": "date-time", "pattern": "Z$"}
_PROPOSAL_SCHEMA = _closed({
    "title": _text(160),
    "hypothesis": _closed({
        "statement": _text(1000),
        **{name: {"anyOf": [_text(240), {"type": "null"}]} for name in _SLOTS},
    }),
    "direction": {"type": "string", "enum": ["INCREASE", "DECREASE", "RELATIONSHIP"]},
    "instrumentId": {"type": "string", "pattern": f"^{_ID.pattern}$"},
    "observationStart": _INSTANT, "observationEnd": _INSTANT,
    "proof": {
        "type": "array", "minItems": 1, "maxItems": 8, "items": _closed({
            "kind": {"type": "string", "enum": [kind.value for kind in EvidenceKind]},
            "evidenceId": {"type": "string", "pattern": f"^{_ID.pattern}$"},
            "revision": {"type": "integer", "minimum": 1, "maximum": 2**63 - 1},
            "quote": _text(240),
        }),
    },
})
PUBLICATION_SCHEMA = _closed({
    "ideas": {"type": "array", "maxItems": 16, "items": _PROPOSAL_SCHEMA},
})
_VALIDATOR = Draft202012Validator(
    _PROPOSAL_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER,
)


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise PublicationDenied("INVALID_PUBLICATION_TIME")
    return value.astimezone(UTC)


def _wire_time(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    kind: EvidenceKind
    evidence_id: str
    revision: int
    fingerprint: str
    instrument_id: str
    observed_at: datetime
    observation_start: datetime
    observation_end: datetime
    quotes: tuple[str, ...] = field(repr=False)
    market: str | None = None
    rights_allowed: bool = False
    quality_ok: bool = False
    retracted: bool = False
    material_change: bool = False
    quality_findings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, int]:
        return self.kind.value, self.evidence_id, self.revision

    def reference(self) -> dict:
        return {
            "kind": self.kind.value, "evidenceId": self.evidence_id, "revision": self.revision,
            "observedAt": _wire_time(self.observed_at),
            "qualityFindings": list(self.quality_findings),
        }


@dataclass(frozen=True, slots=True)
class PublicationAssessment:
    """Server-side mapping and effort verdict, independent of the model response."""

    testable: bool = False
    estimated_effort: str = "UNKNOWN"
    effort_reason: str = "Aufwand erst nach der fachlichen Prüfung bestimmbar."


@dataclass(frozen=True, slots=True)
class KnownPublication:
    idea_id: str
    hypothesis_key: str
    comparison_key: tuple[str, ...]
    observation_start: datetime
    observation_end: datetime
    evidence_keys: frozenset[tuple[str, str, int]]


@dataclass(frozen=True, slots=True)
class PublicationCandidate:
    hypothesis_key: str
    publication_key: str
    comparison_key: tuple[str, ...]
    evidence: tuple[EvidenceClaim, ...]
    observation_start: datetime
    observation_end: datetime
    priority_key: tuple[int, int, int, timedelta, str]
    _content: bytes = field(repr=False)

    def idea_content(self, *, run_id: str) -> dict:
        """Return only APF-04 agent intake fields. No provider text or quotes are added."""
        if type(run_id) is not str or not _ID.fullmatch(run_id):
            raise PublicationDenied("INVALID_PUBLICATION_RUN")
        return json.loads(self._content) | {"runId": run_id}


@dataclass(frozen=True, slots=True)
class PublicationResult:
    candidates: tuple[PublicationCandidate, ...]
    denials: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _unique_fields(items):
    value = dict(items)
    if len(value) != len(items):
        raise ValueError("duplicate member")
    return value


def _no_constant(_value):
    raise ValueError("invalid constant")


def _normalize(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _approved_prose(value: str, *, no_numbers: bool = True) -> bool:
    return (
        type(value) is str and bool(value.strip())
        and not any(0xD800 <= ord(char) <= 0xDFFF or unicodedata.category(char) == "Cf"
                    for char in value)
        and (not no_numbers or not any(char.isnumeric() for char in value))
    )


def _source_map(evidence: Iterable[EvidenceClaim]) -> dict:
    result = {}
    for item in evidence:
        if (
            not isinstance(item, EvidenceClaim) or not isinstance(item.kind, EvidenceKind)
            or not all(type(value) is str and _ID.fullmatch(value)
                       for value in (item.evidence_id, item.instrument_id))
            or type(item.fingerprint) is not str or not _HASH.fullmatch(item.fingerprint)
            or type(item.revision) is not int or item.revision < 1
            or any(type(value) is not bool for value in (
                item.rights_allowed, item.quality_ok, item.retracted, item.material_change,
            ))
            or (item.market is not None and not _approved_prose(item.market))
            or not 1 <= len(item.quotes) <= 8
            or not all(_approved_prose(quote, no_numbers=False) and len(quote) <= 240
                       for quote in item.quotes)
            or _utc(item.observation_start) >= _utc(item.observation_end)
            or _utc(item.observed_at) < _utc(item.observation_end)
            or len(item.quality_findings) > 20 or len(item.limitations) > 20
            or not all(_approved_prose(prose, no_numbers=False) and len(prose) <= 240
                       for prose in (*item.quality_findings, *item.limitations))
        ):
            raise PublicationDenied("INVALID_BOUND_EVIDENCE")
        if item.key in result:
            raise PublicationDenied("INVALID_BOUND_EVIDENCE")
        result[item.key] = item
    if len(result) > 32:
        raise PublicationDenied("INVALID_BOUND_EVIDENCE")
    return result


def _match_proof(row: dict, allowed: dict, now: datetime) -> tuple[EvidenceClaim, ...]:
    selected = {}
    for proof in row["proof"]:
        revision = proof["revision"]
        key = proof["kind"], proof["evidenceId"], revision
        item = allowed.get(key)
        if type(revision) is not int or key in selected or item is None:
            raise PublicationDenied("EVIDENCE_REFERENCE_DENIED")
        if item.rights_allowed is not True or item.retracted or item.quality_ok is not True:
            raise PublicationDenied("EVIDENCE_NO_LONGER_ALLOWED")
        if proof["quote"] not in item.quotes:
            raise PublicationDenied("EVIDENCE_QUOTE_UNVERIFIED")
        if (
            row["instrumentId"] != item.instrument_id or _utc(item.observed_at) > now
            or (item.market is not None and _normalize(row["hypothesis"]["market"])
                != _normalize(item.market))
        ):
            raise PublicationDenied("EVIDENCE_REFERENCE_DENIED")
        selected[key] = item
    return tuple(selected[key] for key in sorted(selected))


def _similar(
    left: tuple[str, ...], right: tuple[str, ...], start: datetime, end: datetime,
    other_start: datetime, other_end: datetime,
) -> bool:
    return (
        left[:2] == right[:2] and start < other_end and other_start < end
        and sum(bool(a) and a == b for a, b in zip(left[2:], right[2:], strict=True)) >= 3
    )


def _related(
    known: Iterable[KnownPublication], hypothesis_key: str, comparison_key: tuple[str, ...],
    evidence: tuple[EvidenceClaim, ...], start: datetime, end: datetime,
) -> tuple[str, ...]:
    related = set()
    for item in known:
        if hypothesis_key == item.hypothesis_key:
            if not any(claim.material_change and claim.key not in item.evidence_keys
                       for claim in evidence):
                raise PublicationDenied("DUPLICATE_RESEARCH_IDEA")
            related.add(item.idea_id)
        elif _similar(
            comparison_key, item.comparison_key, start, end,
            item.observation_start, item.observation_end,
        ):
            raise PublicationDenied("SIMILAR_IDEA_REVIEW_REQUIRED")
    return tuple(sorted(related))[:20]


def _candidate(
    row: dict, allowed: dict, *, now: datetime, priority_rule_version: str,
    freshness_age: timedelta, known: tuple[KnownPublication, ...], permits: Callable[[dict], bool],
    assess: Callable[[dict], PublicationAssessment] | None,
) -> PublicationCandidate:
    if not _VALIDATOR.is_valid(row):
        raise PublicationDenied("INVALID_RESEARCH_IDEA")
    hypothesis = row["hypothesis"]
    if (
        not row["title"].endswith("?") or not hypothesis["statement"].endswith("?")
        or not all(_approved_prose(value) for value in (row["title"], *hypothesis.values())
                   if value is not None)
        or not all(_normalize(hypothesis[name]) for name in ("market", "signal", "target"))
    ):
        raise PublicationDenied("UNVERIFIED_RESEARCH_CLAIM")
    selected = _match_proof(row, allowed, now)
    start = _utc(datetime.fromisoformat(row["observationStart"]))
    end = _utc(datetime.fromisoformat(row["observationEnd"]))
    if start >= end or end > now or not all(
        _utc(item.observation_start) <= start and end <= _utc(item.observation_end)
        for item in selected
    ):
        raise PublicationDenied("EVIDENCE_WINDOW_UNVERIFIED")
    comparison = (
        row["instrumentId"], row["direction"], _normalize(hypothesis["market"]),
        _normalize(hypothesis["trigger"]), _normalize(hypothesis["signal"]),
        _normalize(hypothesis["target"]), _normalize(hypothesis["measurementHorizon"]),
    )
    hypothesis_key = _fingerprint([*comparison, *(_normalize(hypothesis[name]) for name in (
        "observationWindow", "benchmark", "evaluationCriterion",
    ))])
    related = _related(known, hypothesis_key, comparison, selected, start, end)
    publication_key = _fingerprint([
        hypothesis_key, _wire_time(start), _wire_time(end),
        [(*item.key, item.fingerprint) for item in selected],
    ])
    assessment = PublicationAssessment()
    try:
        if permits(json.loads(_canonical(row))) is not True:
            raise PublicationDenied("DISCOVERY_CONTENT_DENIED")
        if assess is not None:
            assessment = assess(json.loads(_canonical(row)))
    except PublicationDenied:
        raise
    except Exception:
        raise PublicationDenied("DISCOVERY_CONTENT_DENIED") from None
    if (
        not isinstance(assessment, PublicationAssessment)
        or assessment.estimated_effort not in _EFFORT
        or not _approved_prose(assessment.effort_reason, no_numbers=False)
        or len(assessment.effort_reason) > 320
    ):
        raise PublicationDenied("INVALID_DISCOVERY_POLICY")
    latest = max(_utc(item.observed_at) for item in selected)
    freshness = "CURRENT" if now - latest <= freshness_age else "STALE"
    complete = assessment.testable is True and all(hypothesis[name] is not None for name in _SLOTS)
    limitations = list(dict.fromkeys(line for item in selected for line in item.limitations))
    if not complete:
        limitations.insert(0, "Die Testbarkeit muss fachlich geprüft werden.")
    content = {
        "title": row["title"], "description": hypothesis["statement"],
        "hypothesis": hypothesis, "sourceId": publication_key,
        "instrumentIds": [row["instrumentId"]], "evidence": [item.reference() for item in selected],
        "relatedIdeaIds": list(related), "observationStart": _wire_time(start),
        "observationEnd": _wire_time(end), "discovery": {
            "priorityRuleVersion": priority_rule_version,
            "priorityReason": ("Testbar und durch aktuelle Evidenz gebunden."
                               if complete and freshness == "CURRENT"
                               else "Forschungshinweis mit ausgewiesenen Einschränkungen."),
            "latestEvidenceAt": _wire_time(latest), "freshnessEvaluatedAt": _wire_time(now),
            "freshness": freshness, "estimatedEffort": assessment.estimated_effort,
            "effortReason": assessment.effort_reason, "limitations": limitations[:20],
        },
    }
    priority = (
        int(not complete or freshness != "CURRENT"), int(bool(related)),
        _EFFORT[assessment.estimated_effort], now - latest, publication_key,
    )
    return PublicationCandidate(
        hypothesis_key, publication_key, comparison, selected, start, end, priority,
        _canonical(content),
    )


def validate_publication(
    payload: bytes, *, evidence: Iterable[EvidenceClaim], now: datetime, priority_rule_version: str,
    max_ideas: int, permits: Callable[[dict], bool], known: tuple[KnownPublication, ...] = (),
    assess: Callable[[dict], PublicationAssessment] | None = None,
    freshness_age: timedelta = timedelta(days=7),
) -> PublicationResult:
    """Validate proposals against the bounded authoritative evidence in the publication commit."""
    now = _utc(now)
    if (
        type(priority_rule_version) is not str or not _ID.fullmatch(priority_rule_version)
        or type(max_ideas) is not int or not 0 <= max_ideas <= 16
        or not isinstance(freshness_age, timedelta) or freshness_age <= timedelta(0)
    ):
        raise PublicationDenied("INVALID_DISCOVERY_POLICY")
    if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_PAYLOAD:
        raise PublicationDenied("INVALID_DISCOVERY_OUTPUT")
    try:
        output = json.loads(payload.decode(), object_pairs_hook=_unique_fields,
                            parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise PublicationDenied("INVALID_DISCOVERY_OUTPUT") from None
    if (type(output) is not dict or output.keys() != {"ideas"}
        or type(output["ideas"]) is not list or len(output["ideas"]) > 16):
        raise PublicationDenied("INVALID_DISCOVERY_OUTPUT")
    allowed = _source_map(evidence)
    candidates, denials = [], []
    for row in output["ideas"]:
        try:
            candidates.append(_candidate(
                row, allowed, now=now, priority_rule_version=priority_rule_version,
                freshness_age=freshness_age, known=known, permits=permits, assess=assess,
            ))
        except PublicationDenied as error:
            denials.append(error.code)
    accepted = []
    seen = set()
    for candidate in sorted(candidates, key=lambda value: (value.priority_key, value._content)):
        if candidate.hypothesis_key in seen:
            denials.append("DUPLICATE_RESEARCH_IDEA")
        elif any(_similar(
            candidate.comparison_key, other.comparison_key,
            candidate.observation_start, candidate.observation_end,
            other.observation_start, other.observation_end,
        ) for other in accepted):
            denials.append("SIMILAR_IDEA_REVIEW_REQUIRED")
        elif len(accepted) >= max_ideas:
            denials.append("PUBLICATION_LIMIT_REACHED")
        else:
            accepted.append(candidate)
            seen.add(candidate.hypothesis_key)
    return PublicationResult(tuple(accepted), tuple(denials))
