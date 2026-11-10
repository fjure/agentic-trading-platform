"""Validate model interpretations before they become companion proposals."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from jsonschema import Draft202012Validator

_DEFINITION = "#/$defs/ResearchHypothesisInterpretationV1"
_MAX_BYTES = 32768


class InterpretationDenied(ValueError):
    """Redacted refusal to admit a model interpretation."""


def _unique_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate member")
    return value


def _invalid_constant(_value):
    raise ValueError("invalid constant")


@dataclass(frozen=True, slots=True)
class ProposalBinding:
    original_id: str
    turn_id: str
    idea_version: int
    dialogue_version: int


@dataclass(frozen=True, slots=True)
class ValidatedInterpretation:
    binding: ProposalBinding
    content: bytes
    rejected_action: bool

    @property
    def proposal_hash(self) -> str:
        identity = [
            self.binding.original_id, self.binding.turn_id, self.binding.idea_version,
            self.binding.dialogue_version, self.content.decode(),
        ]
        return sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


class InterpretationBoundary:
    """The caller supplies the deployed contract and a local classification policy."""

    def __init__(self, contract: Path, *, permits: Callable[[dict], bool]):
        with contract.open(encoding="utf-8") as source:
            schema = json.load(source)
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema | {"$ref": _DEFINITION})
        self._permits = permits

    def validate(
        self, payload: bytes, *, binding: ProposalBinding, current: ProposalBinding,
        separate_action: object = None,
    ) -> ValidatedInterpretation:
        if binding != current:
            raise InterpretationDenied("STALE_INTERPRETATION")
        if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_BYTES:
            raise InterpretationDenied("INVALID_INTERPRETATION")
        try:
            value = json.loads(
                payload.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
            if not self._validator.is_valid(value):
                raise ValueError("invalid schema")
            for field, key in (("assumptions", "id"), ("openQuestions", "field")):
                identifiers = [item[key] for item in value[field]]
                if len(set(identifiers)) != len(identifiers):
                    raise ValueError("ambiguous interpretation")
            content = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        except (ValueError, RecursionError):
            raise InterpretationDenied("INVALID_INTERPRETATION") from None
        try:
            permitted = self._permits(value) is True
        except Exception:
            permitted = False
        if not permitted:
            raise InterpretationDenied("INTERPRETATION_POLICY_DENIED")
        return ValidatedInterpretation(binding, content, separate_action is not None)
