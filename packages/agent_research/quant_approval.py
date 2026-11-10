"""Immutable binding for a local operator's one-shot Quant approval."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Self

from jsonschema import Draft202012Validator

from packages.domain.models import utc_time

_CONTRACT = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/agent-research.schema.json"


class ApprovalDenied(ValueError):
    """Stable rejection without echoing untrusted input or policy contents."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1)
def _binding_validator() -> Draft202012Validator:
    schema = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    return Draft202012Validator(
        schema | {"$ref": "#/$defs/AgentQuantApprovalBindingV1"},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


@dataclass(frozen=True, slots=True, init=False)
class ApprovalBinding:
    """Closed immutable binding, with the repository's sorted ASCII JSON identity.

    The owner must resolve and rebuild the current binding from authoritative
    state under the same transaction as the one-shot Quant admission. Neither
    schema validation nor a matching digest authenticates supplied facts.
    """

    _canonical_text: str = field(repr=False)

    def __init__(self, document: dict):
        try:
            if type(document) is not dict or not _binding_validator().is_valid(document):
                raise ValueError
            utc_time(datetime.fromisoformat(document["expiresAt"]))
            policy_ids = [policy["policyId"] for policy in document["activePolicies"]]
            if len(set(policy_ids)) != len(policy_ids):
                raise ValueError
            # JSON Schema accepts mathematically integral floats. Approval
            # revisions and counts use exact integer transport values.
            if any(type(document[key]) is not int for key in (
                "runVersion", "ideaVersion", "hypothesisVersion", "definitionVersion",
            )):
                raise ValueError
            approximation = document["approximation"]
            revisions = [policy["revision"] for policy in document["activePolicies"]]
            if approximation is not None:
                revisions.append(approximation["revision"])
            if any(type(revision) is not int for revision in revisions):
                raise ValueError
            budget = document["resourceBudget"]
            if any(type(value) is float for value in budget.values()):
                raise ValueError
            canonical = json.dumps(
                document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
            )
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise ApprovalDenied("INVALID_APPROVAL_BINDING") from None
        object.__setattr__(self, "_canonical_text", canonical)

    @classmethod
    def parse(cls, document: dict) -> Self:
        return cls(document)

    @property
    def canonical_text(self) -> str:
        return self._canonical_text

    @property
    def binding_hash(self) -> str:
        return sha256(self._canonical_text.encode("ascii")).hexdigest()

    @property
    def expires_at(self) -> datetime:
        return datetime.fromisoformat(self.to_document()["expiresAt"])

    def to_document(self) -> dict:
        return json.loads(self._canonical_text)

    def require_current(
        self, current_binding: Self, *, expected_hash: str, operator_id: str, now: datetime,
    ) -> None:
        stored = self.to_document()
        if stored["operatorId"] != operator_id:
            raise ApprovalDenied("APPROVAL_REQUIRED")
        try:
            expired = utc_time(now) >= self.expires_at
        except ValueError:
            raise ApprovalDenied("APPROVAL_STALE") from None
        if expired:
            raise ApprovalDenied("APPROVAL_EXPIRED")
        if (
            not isinstance(current_binding, ApprovalBinding)
            or type(expected_hash) is not str
            or len(expected_hash) != 64
            or not expected_hash.isascii()
            or not compare_digest(expected_hash, self.binding_hash)
            or not compare_digest(self.canonical_text, current_binding.canonical_text)
        ):
            raise ApprovalDenied("APPROVAL_STALE")
        budget = stored["resourceBudget"]
        if not budget["calendarConfirmed"] or any(budget[key] is None for key in (
            "snapshotRows", "simulationBarSteps", "publicationBytes",
        )):
            raise ApprovalDenied("APPROVAL_STALE")
        if not budget["withinLimits"]:
            raise ApprovalDenied("QUANT_CAPACITY_EXHAUSTED")
