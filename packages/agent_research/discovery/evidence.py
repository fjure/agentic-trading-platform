"""Deterministic discovery triggers over committed, immutable evidence references."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from packages.domain.models import utc_string, utc_time
from packages.research.models import content_fingerprint

EVIDENCE_VERSION = "agent-discovery-evidence-v1"
GROUP_VERSION = "agent-discovery-group-v1"
EVALUATION_VERSION = "agent-discovery-evaluation-v1"
MAX_GROUP_ITEMS = 16
MAX_COMMIT_BUCKET = 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HASH = re.compile(r"[a-f0-9]{64}")


class EvidenceKind(StrEnum):
    MARKET_REVISION = "MARKET_REVISION"
    RESEARCH_FINDING = "RESEARCH_FINDING"
    QUANT_RESULT = "QUANT_RESULT"
    DATA_QUALITY = "DATA_QUALITY"


class Readiness(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class Rights(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"


class TriggerReason(StrEnum):
    NEW_EVIDENCE = "NEW_EVIDENCE"
    CHANGED_EVIDENCE = "CHANGED_EVIDENCE"
    CONTRADICTORY_FINDING = "CONTRADICTORY_FINDING"
    UNCHANGED_EVIDENCE = "UNCHANGED_EVIDENCE"
    NON_MATERIAL_CHANGE = "NON_MATERIAL_CHANGE"
    SOURCE_OUT_OF_SCOPE = "SOURCE_OUT_OF_SCOPE"
    RETRACTED_EVIDENCE = "RETRACTED_EVIDENCE"
    RIGHTS_UNAVAILABLE = "RIGHTS_UNAVAILABLE"
    PROVIDER_RIGHTS_UNAVAILABLE = "PROVIDER_RIGHTS_UNAVAILABLE"
    EVIDENCE_NOT_READY = "EVIDENCE_NOT_READY"
    EVIDENCE_TIME_UNCLEAR = "EVIDENCE_TIME_UNCLEAR"
    STALE_EVIDENCE = "STALE_EVIDENCE"


_ELIGIBLE = {
    TriggerReason.NEW_EVIDENCE,
    TriggerReason.CHANGED_EVIDENCE,
    TriggerReason.CONTRADICTORY_FINDING,
}


def _identifier(value: str) -> None:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid discovery reference")


def _version(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError("invalid discovery version")


def _optional_time(value: str | None) -> datetime | None:
    return None if value is None else utc_time(datetime.fromisoformat(value))


@dataclass(frozen=True, order=True, slots=True)
class EvidencePosition:
    """Ordered source key in a commit-serialized stream, never a wall-clock watermark."""

    commit_version: int
    kind: EvidenceKind
    source_id: str

    def __post_init__(self) -> None:
        _version(self.commit_version)
        if not isinstance(self.kind, EvidenceKind):
            raise ValueError("unregistered discovery evidence kind")
        _identifier(self.source_id)


@dataclass(frozen=True, slots=True)
class EvidenceRevision:
    position: EvidencePosition
    revision: int
    instrument_id: str
    observed_at: datetime | None
    window_start: datetime | None
    window_end: datetime | None
    content_hash: str
    material_change: bool
    contradicts_prior: bool = False

    def __post_init__(self) -> None:
        _version(self.revision)
        _identifier(self.instrument_id)
        if (
            not isinstance(self.position, EvidencePosition)
            or type(self.content_hash) is not str
            or not _HASH.fullmatch(self.content_hash)
            or type(self.material_change) is not bool
            or type(self.contradicts_prior) is not bool
        ):
            raise ValueError("invalid discovery revision")
        if self.contradicts_prior and self.position.kind not in {
            EvidenceKind.RESEARCH_FINDING,
            EvidenceKind.QUANT_RESULT,
        }:
            raise ValueError("only persisted findings can contradict prior findings")
        for value in (self.observed_at, self.window_start, self.window_end):
            if value is not None:
                utc_time(value)

    def immutable_binding(self) -> dict:
        return {
            "schemaVersion": EVIDENCE_VERSION,
            "kind": self.position.kind.value,
            "sourceId": self.position.source_id,
            "revision": self.revision,
            "instrumentId": self.instrument_id,
            "observedAt": None if self.observed_at is None else utc_string(self.observed_at),
            "windowStart": None if self.window_start is None else utc_string(self.window_start),
            "windowEnd": None if self.window_end is None else utc_string(self.window_end),
            "contentHash": self.content_hash,
        }

    def record(self) -> dict:
        return self.immutable_binding() | {
            "commitVersion": self.position.commit_version,
            "materialChange": self.material_change,
            "contradictsPrior": self.contradicts_prior,
        }

    @classmethod
    def from_record(cls, value: dict) -> "EvidenceRevision":
        position = EvidencePosition(
            value["commitVersion"],
            EvidenceKind(value["kind"]),
            value["sourceId"],
        )
        result = cls(
            position,
            value["revision"],
            value["instrumentId"],
            _optional_time(value["observedAt"]),
            _optional_time(value["windowStart"]),
            _optional_time(value["windowEnd"]),
            value["contentHash"],
            value["materialChange"],
            value["contradictsPrior"],
        )
        if value != result.record():
            raise ValueError("noncanonical discovery evidence record")
        return result


@dataclass(frozen=True, slots=True)
class EvidenceAccess:
    """Current authoritative gates, reevaluated before dispatch and publication."""

    readiness: Readiness
    rights: Rights
    provider_rights: Rights
    retracted: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.readiness, Readiness)
            or not isinstance(self.rights, Rights)
            or not isinstance(self.provider_rights, Rights)
            or type(self.retracted) is not bool
        ):
            raise ValueError("invalid discovery source gate")


@dataclass(frozen=True, slots=True)
class TriggerPolicy:
    rule_version: str
    allowlist_version: str
    instruments: frozenset[str]
    max_age: timedelta
    kinds: frozenset[EvidenceKind] = frozenset(EvidenceKind)

    def __post_init__(self) -> None:
        _identifier(self.rule_version)
        _identifier(self.allowlist_version)
        if (
            type(self.instruments) is not frozenset
            or type(self.kinds) is not frozenset
            or not isinstance(self.max_age, timedelta)
            or not timedelta() < self.max_age <= timedelta(days=366)
            or any(not isinstance(kind, EvidenceKind) for kind in self.kinds)
        ):
            raise ValueError("invalid discovery trigger policy")
        for instrument in self.instruments:
            _identifier(instrument)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    reason: TriggerReason

    @property
    def eligible(self) -> bool:
        return self.reason in _ELIGIBLE


def evaluate_evidence(
    revision: EvidenceRevision,
    access: EvidenceAccess,
    policy: TriggerPolicy,
    *,
    now: datetime,
    previous: EvidenceRevision | None = None,
) -> TriggerDecision:
    """Use source authority and immutable comparison rather than model judgments."""
    now = utc_time(now)
    if (
        revision.position.kind not in policy.kinds
        or revision.instrument_id not in policy.instruments
    ):
        return TriggerDecision(TriggerReason.SOURCE_OUT_OF_SCOPE)
    if access.retracted:
        return TriggerDecision(TriggerReason.RETRACTED_EVIDENCE)
    if access.rights is not Rights.ALLOWED:
        return TriggerDecision(TriggerReason.RIGHTS_UNAVAILABLE)
    if access.provider_rights is not Rights.ALLOWED:
        return TriggerDecision(TriggerReason.PROVIDER_RIGHTS_UNAVAILABLE)
    if access.readiness is not Readiness.READY:
        return TriggerDecision(TriggerReason.EVIDENCE_NOT_READY)
    if (
        revision.observed_at is None
        or revision.window_start is None
        or revision.window_end is None
        or not revision.window_start < revision.window_end <= revision.observed_at <= now
    ):
        return TriggerDecision(TriggerReason.EVIDENCE_TIME_UNCLEAR)
    if now - revision.observed_at > policy.max_age:
        return TriggerDecision(TriggerReason.STALE_EVIDENCE)
    if previous is not None:
        if previous.immutable_binding() == revision.immutable_binding():
            return TriggerDecision(TriggerReason.UNCHANGED_EVIDENCE)
        if (
            previous.position.kind != revision.position.kind
            or previous.instrument_id != revision.instrument_id
            or previous.position >= revision.position
        ):
            raise ValueError("invalid prior evidence binding")
        if previous.content_hash == revision.content_hash and (
            previous.window_start,
            previous.window_end,
        ) == (revision.window_start, revision.window_end):
            return TriggerDecision(TriggerReason.UNCHANGED_EVIDENCE)
    if not revision.material_change:
        return TriggerDecision(TriggerReason.NON_MATERIAL_CHANGE)
    if revision.contradicts_prior and previous is not None:
        return TriggerDecision(TriggerReason.CONTRADICTORY_FINDING)
    return TriggerDecision(
        TriggerReason.NEW_EVIDENCE if previous is None else TriggerReason.CHANGED_EVIDENCE,
    )


def decision_fingerprint(
    revision: EvidenceRevision,
    decision: TriggerDecision,
    policy: TriggerPolicy,
) -> str:
    return content_fingerprint(
        (
            "agent-discovery-decision-v1",
            revision.immutable_binding(),
            policy.rule_version,
            policy.allowlist_version,
            decision.reason,
        )
    )


@dataclass(frozen=True, slots=True)
class EvidenceGroup:
    items: tuple[EvidenceRevision, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not 1 <= len(self.items) <= MAX_GROUP_ITEMS:
            raise ValueError("invalid discovery group size")
        positions = tuple(item.position for item in self.items)
        if (
            len({(pos.commit_version, pos.kind) for pos in positions}) != 1
            or len(set(positions)) != len(positions)
            or tuple(sorted(positions)) != positions
        ):
            raise ValueError("group must contain unique ordered evidence from one commit and kind")

    @property
    def fingerprint(self) -> str:
        return content_fingerprint(
            (GROUP_VERSION, [item.immutable_binding() for item in self.items])
        )

    def record(self) -> dict:
        return {"schemaVersion": GROUP_VERSION, "items": [item.record() for item in self.items]}

    @classmethod
    def from_record(cls, value: dict) -> "EvidenceGroup":
        result = cls(tuple(EvidenceRevision.from_record(item) for item in value["items"]))
        if value != result.record():
            raise ValueError("noncanonical discovery group record")
        return result


def group_evidence(
    bucket: Sequence[EvidenceRevision],
    *,
    max_items: int = MAX_GROUP_ITEMS,
) -> tuple[EvidenceGroup, ...]:
    """Chunk a sealed commit/kind bucket independently of source query page boundaries."""
    if type(max_items) is not int or not 1 <= max_items <= MAX_GROUP_ITEMS:
        raise ValueError("invalid discovery group limit")
    if len(bucket) > MAX_COMMIT_BUCKET:
        raise ValueError("unbounded discovery source bucket")
    ordered = sorted(bucket, key=lambda item: item.position)
    positions = tuple(item.position for item in ordered)
    if (
        len(set(positions)) != len(positions)
        or len({(position.commit_version, position.kind) for position in positions}) > 1
    ):
        raise ValueError("bucket must contain one commit and kind without repeated positions")
    return tuple(
        EvidenceGroup(tuple(ordered[index : index + max_items]))
        for index in range(0, len(ordered), max_items)
    )


@dataclass(frozen=True, slots=True)
class EvaluationBinding:
    rule_version: str
    allowlist_version: str
    agent_id: str
    agent_version: int
    prompt_version: str
    provider: str
    profile_id: str
    profile_generation: int
    model_id: str
    model_generation: int
    output_schema: str
    reanalysis_id: str | None = None
    envelope_hash: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.rule_version,
            self.allowlist_version,
            self.agent_id,
            self.prompt_version,
            self.profile_id,
            self.model_id,
            self.output_schema,
        ):
            _identifier(value)
        for version in (self.agent_version, self.profile_generation, self.model_generation):
            _version(version)
        if self.provider not in {"OPENAI", "ANTHROPIC"}:
            raise ValueError("unregistered discovery provider")
        if self.reanalysis_id is not None:
            _identifier(self.reanalysis_id)
        if self.envelope_hash is not None and not _HASH.fullmatch(self.envelope_hash):
            raise ValueError("invalid discovery envelope binding")

    def permits_reanalysis(self, previous: "EvaluationBinding") -> bool:
        """The ID comes from an audited approval, never from a model proposal."""
        return self.reanalysis_id not in (None, previous.reanalysis_id) and (
            self.prompt_version,
            self.provider,
            self.model_id,
            self.model_generation,
        ) != (
            previous.prompt_version,
            previous.provider,
            previous.model_id,
            previous.model_generation,
        )


def evaluation_fingerprint(group: EvidenceGroup, binding: EvaluationBinding) -> str:
    return content_fingerprint((EVALUATION_VERSION, group.fingerprint, binding))
