"""Pure versioned companion transitions. Persistence and operator authority are caller-owned."""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from hmac import compare_digest
from types import MappingProxyType

from .interpretation import ProposalBinding, ValidatedInterpretation

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HASH = re.compile(r"[a-f0-9]{64}")
_FIELDS = MappingProxyType({
    "MARKET": ("market", "Welchen Markt oder welches Instrument möchtest du untersuchen?"),
    "TRIGGER": ("trigger", "Welches Ereignis soll die Beobachtung auslösen?"),
    "SIGNAL": ("signal", "Woran erkennst du das vermutete Signal?"),
    "OBSERVATION_WINDOW": ("observationWindow", "Welchen Zeitraum willst du beobachten?"),
    "TARGET": ("target", "Welche messbare Veränderung erwartest du?"),
    "MEASUREMENT_HORIZON": ("measurementHorizon", "Nach wie vielen Handelstagen misst du sie?"),
    "BENCHMARK": ("benchmark", "Womit soll das Ergebnis verglichen werden?"),
    "EVALUATION_CRITERION": (
        "evaluationCriterion", "Welches Ergebnis würde die Vermutung stützen?",
    ),
})
PrivacyCheck = Callable[[str], bool]


class DialogueError(ValueError):
    """Only stable, content-free failure codes leave this boundary."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Confirmation:
    value: str
    decision_id: str
    idea_version: int


@dataclass(frozen=True, slots=True)
class ExpertBinding:
    definition_id: str
    definition_version: int
    definition_hash: str
    fields: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Dialogue:
    dialogue_id: str
    idea_version: int
    version: int = 1
    original_id: str | None = None
    turn_id: str | None = None
    proposal_json: str | None = field(default=None, repr=False)
    confirmed_fields: tuple[tuple[str, Confirmation], ...] = field(default=(), repr=False)
    confirmed_assumptions: tuple[tuple[str, Confirmation], ...] = field(default=(), repr=False)
    local_fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    requested_fields: tuple[str, ...] = ()
    clarification: str | None = field(default=None, repr=False)
    last_decision_id: str | None = None
    expert: ExpertBinding | None = None

    @property
    def expert_fields(self) -> dict[str, str]:
        return {} if self.expert is None else dict(self.expert.fields)

    @property
    def proposal(self) -> dict | None:
        return json.loads(self.proposal_json) if self.proposal_json is not None else None

    @property
    def binding(self) -> ProposalBinding:
        if self.original_id is None or self.turn_id is None:
            raise DialogueError("DIALOGUE_HAS_NO_TURN")
        return ProposalBinding(self.original_id, self.turn_id, self.idea_version, self.version)

    @property
    def confirmed(self) -> dict[str, str]:
        return {name: confirmation.value for name, confirmation in self.confirmed_fields}

    def record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: Mapping) -> "Dialogue":
        fields = tuple((key, Confirmation(**value)) for key, value in record["confirmed_fields"])
        assumptions = tuple(
            (key, Confirmation(**value)) for key, value in record["confirmed_assumptions"]
        )
        expert = record.get("expert")
        return cls(**dict(record) | {
            "confirmed_fields": fields,
            "confirmed_assumptions": assumptions,
            "local_fields": tuple((key, value) for key, value in record["local_fields"]),
            "requested_fields": tuple(record["requested_fields"]),
            "expert": None if expert is None else ExpertBinding(**expert | {
                "fields": tuple((key, value) for key, value in expert["fields"]),
            }),
        })


@dataclass(frozen=True, slots=True)
class LocalQuestion:
    question_id: str
    field: str
    question: str


def _id(value: str) -> None:
    if type(value) is not str or not _ID.fullmatch(value):
        raise DialogueError("INVALID_DIALOGUE_REQUEST")


def _check_versions(state: Dialogue, idea: int, dialogue: int) -> None:
    if (type(idea) is not int or type(dialogue) is not int
        or idea != state.idea_version or dialogue != state.version):
        raise DialogueError("VERSION_CONFLICT")


def _check_text(value: str, maximum: int, privacy_check: PrivacyCheck) -> None:
    if (type(value) is not str or not value.strip() or not 1 <= len(value) <= maximum
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise DialogueError("INVALID_DIALOGUE_REQUEST")
    try:
        permitted = privacy_check(value) is True
    except Exception:
        permitted = False
    if not permitted:
        raise DialogueError("MODEL_CONTEXT_UNCLASSIFIED")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _proposal(
    state: Dialogue, proposal_id: str, interpretation: dict, *, origin: str,
) -> Dialogue:
    _id(proposal_id)
    binding = state.binding
    hypothesis = interpretation["hypothesis"]
    conflicts = [
        {"field": name, "question": _FIELDS[name][1]}
        for name, previous in (state.confirmed | state.expert_fields).items()
        if hypothesis[_FIELDS[name][0]] not in (None, previous)
    ]
    proposal = {
        "proposalId": proposal_id, "originalId": binding.original_id,
        "turnId": binding.turn_id, "ideaVersion": binding.idea_version,
        "dialogueVersion": binding.dialogue_version + 1, "origin": origin,
        "interpretation": interpretation, "conflicts": conflicts,
    }
    proposal["proposalHash"] = sha256(_canonical(proposal).encode("utf-8")).hexdigest()
    return replace(state, version=state.version + 1, proposal_json=_canonical(proposal))


def submit_original(
    state: Dialogue, *, expected_idea_version: int, expected_dialogue_version: int,
    turn_id: str, original_id: str, text: str, privacy_check: PrivacyCheck,
    expert: ExpertBinding | None = None,
) -> Dialogue:
    """Authorize a new protected original reference without duplicating original text."""
    _check_versions(state, expected_idea_version, expected_dialogue_version)
    _id(turn_id)
    _id(original_id)
    if turn_id == state.turn_id or original_id == state.original_id:
        raise DialogueError("INVALID_DIALOGUE_REQUEST")
    _check_text(text, 4096, privacy_check)
    if expert is not None:
        _id(expert.definition_id)
        if (
            type(expert.definition_version) is not int or expert.definition_version < 1
            or not isinstance(expert.definition_hash, str)
            or not _HASH.fullmatch(expert.definition_hash)
            or len(dict(expert.fields)) != len(expert.fields)
            or not dict(expert.fields).keys() <= _FIELDS.keys()
            or (state.expert is not None and state.expert != expert)
            or any(key in state.confirmed and state.confirmed[key] != value
                   for key, value in expert.fields)
        ):
            raise DialogueError("INVALID_EXPERT_BINDING")
        for _, value in expert.fields:
            _check_text(value, 240, privacy_check)
    return replace(
        state, version=state.version + 1, original_id=original_id, turn_id=turn_id,
        proposal_json=None, local_fields=(), requested_fields=(), clarification=None,
        expert=expert or state.expert,
    )


def propose(
    state: Dialogue, *, expected_idea_version: int, expected_dialogue_version: int,
    proposal_id: str, interpretation: ValidatedInterpretation,
) -> Dialogue:
    """Keep interpretation as an unconfirmed proposal bound to the latest turn."""
    _check_versions(state, expected_idea_version, expected_dialogue_version)
    if interpretation.binding != state.binding:
        raise DialogueError("VERSION_CONFLICT")
    if state.proposal is not None:
        raise DialogueError("VERSION_CONFLICT")
    return _proposal(
        state, proposal_id, json.loads(interpretation.content), origin="MODEL_INTERPRETATION",
    )


def local_questions(state: Dialogue) -> tuple[LocalQuestion, ...]:
    if state.original_id is None:
        return ()
    staged = dict(state.local_fields)
    proposal = state.proposal
    conflicts = {conflict["field"] for conflict in proposal["conflicts"]} if proposal else set()
    if state.requested_fields:
        needed = set(state.requested_fields)
    else:
        needed = set(_FIELDS) - state.confirmed.keys() | conflicts
    return tuple(
        LocalQuestion(
            sha256(_canonical([state.dialogue_id, state.version, name]).encode()).hexdigest(),
            name, question,
        )
        for name, (_, question) in _FIELDS.items()
        if name in needed and name not in staged
    )


def answer_local(
    state: Dialogue, *, expected_idea_version: int, expected_dialogue_version: int,
    question_id: str, field: str, value: str, turn_id: str, proposal_id: str,
    privacy_check: PrivacyCheck,
) -> Dialogue:
    """Advance structured clarification without granting model access or confirming fields."""
    _check_versions(state, expected_idea_version, expected_dialogue_version)
    _id(turn_id)
    current_questions = local_questions(state)
    if turn_id == state.turn_id or not any(
        question.question_id == question_id and question.field == field
        for question in current_questions
    ):
        raise DialogueError("VERSION_CONFLICT")
    _check_text(value, 240, privacy_check)
    if field in state.expert_fields and state.expert_fields[field] != value:
        raise DialogueError("EXPERT_FIELD_CONFLICT")
    staged = dict(state.local_fields) | {field: value}
    values = state.confirmed | staged
    proposal = state.proposal
    targeted = state.requested_fields
    if not targeted and proposal is not None and proposal["conflicts"]:
        targeted = tuple(question.field for question in current_questions)
    next_state = replace(
        state, turn_id=turn_id, local_fields=tuple(staged.items()), requested_fields=targeted,
    )
    interpretation = {
        "schemaVersion": "V1",
        "hypothesis": {
            "statement": "Strukturierte lokale Klärung der Forschungsidee",
            **{slot: values.get(name) for name, (slot, _) in _FIELDS.items()},
        },
        "requiredDataTypes": [], "openQuestions": [
            {"field": question.field, "question": question.question}
            for question in local_questions(next_state)
        ],
        "assumptions": [], "unsupportedAspects": [], "confidence": "UNKNOWN",
        "proposedTestability": None,
    }
    return _proposal(next_state, proposal_id, interpretation, origin="LOCAL_CLARIFICATION")


def _bound_proposal(state: Dialogue, proposal_id: str, proposal_hash: str) -> dict:
    proposal = state.proposal
    if (
        proposal is None or proposal["proposalId"] != proposal_id
        or type(proposal_hash) is not str or not _HASH.fullmatch(proposal_hash)
        or not compare_digest(proposal["proposalHash"], proposal_hash)
        or proposal["ideaVersion"] != state.idea_version
        or proposal["dialogueVersion"] != state.version
    ):
        raise DialogueError("VERSION_CONFLICT")
    return proposal


def confirm(
    state: Dialogue, *, expected_idea_version: int, expected_dialogue_version: int,
    proposal_id: str, proposal_hash: str, decision_id: str, confirmed_fields: Mapping[str, str],
    confirmed_assumption_ids: tuple[str, ...],
) -> Dialogue:
    """Bind only explicitly selected values from the current proposal."""
    _check_versions(state, expected_idea_version, expected_dialogue_version)
    _id(decision_id)
    proposal = _bound_proposal(state, proposal_id, proposal_hash)
    hypothesis = proposal["interpretation"]["hypothesis"]
    assumptions = {row["id"]: row["statement"] for row in proposal["interpretation"]["assumptions"]}
    if (
        not 1 <= len(confirmed_fields) <= len(_FIELDS)
        or any(name not in _FIELDS or value is None or hypothesis[_FIELDS[name][0]] != value
               for name, value in confirmed_fields.items())
        or len(set(confirmed_assumption_ids)) != len(confirmed_assumption_ids)
        or not set(confirmed_assumption_ids) <= assumptions.keys()
        or any(name in state.expert_fields and value != state.expert_fields[name]
               for name, value in confirmed_fields.items())
    ):
        raise DialogueError("INVALID_HYPOTHESIS_CONFIRMATION")
    idea_version = state.idea_version + 1
    fields = dict(state.confirmed_fields)
    kept_assumptions = dict(state.confirmed_assumptions)
    for name, value in confirmed_fields.items():
        if name not in fields or fields[name].value != value:
            fields[name] = Confirmation(value, decision_id, idea_version)
    for assumption_id in confirmed_assumption_ids:
        value = assumptions[assumption_id]
        if assumption_id not in kept_assumptions or kept_assumptions[assumption_id].value != value:
            kept_assumptions[assumption_id] = Confirmation(value, decision_id, idea_version)
    if len(kept_assumptions) > 20:
        raise DialogueError("INVALID_HYPOTHESIS_CONFIRMATION")
    return replace(
        state, idea_version=idea_version, version=state.version + 1, proposal_json=None,
        confirmed_fields=tuple(fields.items()),
        confirmed_assumptions=tuple(kept_assumptions.items()),
        local_fields=tuple((name, value) for name, value in state.local_fields
                           if name not in confirmed_fields), requested_fields=(),
        last_decision_id=decision_id, clarification=None,
    )


def request_changes(
    state: Dialogue, *, expected_idea_version: int, expected_dialogue_version: int,
    proposal_id: str, proposal_hash: str, decision_id: str, requested_fields: tuple[str, ...],
    clarification: str, privacy_check: PrivacyCheck,
) -> Dialogue:
    _check_versions(state, expected_idea_version, expected_dialogue_version)
    _id(decision_id)
    _bound_proposal(state, proposal_id, proposal_hash)
    if (not requested_fields or len(set(requested_fields)) != len(requested_fields)
        or not set(requested_fields) <= _FIELDS.keys()):
        raise DialogueError("INVALID_DIALOGUE_REQUEST")
    _check_text(clarification, 320, privacy_check)
    return replace(
        state, version=state.version + 1, proposal_json=None, last_decision_id=decision_id,
        requested_fields=requested_fields, clarification=clarification, local_fields=tuple(
            (name, value) for name, value in state.local_fields if name not in requested_fields
        ),
    )
