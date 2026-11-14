"""Durable Research provider authority, serialized with send and publish decisions."""

import json
import re
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .runs import RunConflict

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_PROVIDERS = {"OPENAI", "ANTHROPIC"}
_DATA_CLASSES = {
    "OPERATOR_RESEARCH_EXCERPT", "CONFIRMED_RESEARCH_FIELDS", "HISTORICAL_REVISION",
    "PERSISTED_RESEARCH_RESULT",
}
_MAX_REVISION = 9007199254740991


class ProviderPurpose(StrEnum):
    DIALOGUE = "DIALOGUE"
    DISCOVERY = "DISCOVERY"
    PROBE = "PROBE"


class ProviderProfileState(StrEnum):
    STAGED = "STAGED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    REVOKED = "REVOKED"


def _revision(value: object) -> bool:
    return type(value) is int and 1 <= value <= _MAX_REVISION


def _hash(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _denied() -> RunConflict:
    return RunConflict("PROVIDER_AUTHORITY_UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class ProviderBinding:
    profile_id: str
    profile_generation: int
    provider: str
    model_id: str
    response_model_id: str
    model_generation: int
    capability_generation: int
    credential_generation: int
    credential_version: int
    credential_digest: str
    api_version: str
    rights_generation: int
    policy_generation: int
    prompt_sha256: str
    schema_sha256: str
    evidence_revision: int
    tariff_revision: int
    evidence_expires_at: datetime
    data_classes: tuple[str, ...]
    envelope_version: str
    max_request_bytes: int
    max_response_bytes: int
    timeout_seconds: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost: Decimal
    discovery_policy_sha256: str | None = None

    def __post_init__(self):
        if (
            self.provider not in _PROVIDERS
            or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in (
                self.profile_id, self.model_id, self.response_model_id, self.api_version,
                self.envelope_version,
            ))
            or any(not _revision(value) for value in (
                self.profile_generation, self.model_generation, self.capability_generation,
                self.credential_generation, self.credential_version, self.rights_generation,
                self.policy_generation, self.evidence_revision, self.tariff_revision,
                self.max_request_bytes, self.max_response_bytes, self.timeout_seconds,
                self.max_input_tokens, self.max_output_tokens,
            ))
            or not all(map(_hash, (self.credential_digest, self.prompt_sha256, self.schema_sha256)))
            or not isinstance(self.evidence_expires_at, datetime)
            or self.evidence_expires_at.utcoffset() != UTC.utcoffset(None)
            or not isinstance(self.data_classes, tuple)
            or any(
                not isinstance(item, str) or item not in _DATA_CLASSES for item in self.data_classes
            )
            or self.data_classes != tuple(sorted(set(self.data_classes)))
            or self.max_request_bytes > 65536 or self.max_response_bytes > 262144
            or self.timeout_seconds > 60
            or self.max_input_tokens > 2**31 - 1 or self.max_output_tokens > 65536
            or not isinstance(self.max_cost, Decimal) or not self.max_cost.is_finite()
            or not 0 <= self.max_cost < Decimal("1000000")
            or self.max_cost.as_tuple().exponent < -18
            or (
                self.discovery_policy_sha256 is not None
                and not _hash(self.discovery_policy_sha256)
            )
        ):
            raise _denied()

    def record(self) -> dict:
        fields = asdict(self)
        if self.discovery_policy_sha256 is None:
            del fields["discovery_policy_sha256"]
        return fields | {
            "data_classes": list(self.data_classes),
            "evidence_expires_at": self.evidence_expires_at.isoformat(),
            "max_cost": format(self.max_cost, "f"),
        }

    @classmethod
    def from_record(cls, record: dict) -> "ProviderBinding":
        try:
            return cls(**(record | {
                "data_classes": tuple(record["data_classes"]),
                "evidence_expires_at": datetime.fromisoformat(record["evidence_expires_at"]),
                "max_cost": Decimal(record["max_cost"]),
            }))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise _denied() from None

    @property
    def digest(self) -> str:
        return sha256(self.json().encode("ascii")).hexdigest()

    def json(self) -> str:
        return json.dumps(self.record(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ProviderAuthority:
    epoch: int
    state: ProviderProfileState
    binding: ProviderBinding
    dialogue: bool
    discovery: bool
    smoke_attempt_id: UUID | None


def authority_hash(binding: ProviderBinding, epoch: int, purpose: ProviderPurpose) -> str:
    if not _revision(epoch) or type(purpose) is not ProviderPurpose:
        raise _denied()
    return sha256(f"{binding.digest}:{epoch}:{purpose.value}".encode("ascii")).hexdigest()


class ProviderAuthorityStore:
    """Acquire the provider row first. Hold it until the caller's transaction commits."""

    def __init__(
        self, engine: Engine, *,
        check_evidence: Callable[[object, ProviderBinding], bool],
    ):
        self._engine = engine
        self._check_evidence = check_evidence

    def _transaction(self, scope):
        return self._engine.begin() if scope is None else nullcontext(scope)

    @staticmethod
    def _credential(scope, provider):
        row = scope.execute(text("""
            SELECT version,generation,digest,blocked
            FROM agent_research.provider_credential_authority
            WHERE provider=:provider FOR UPDATE
        """), {"provider": provider}).one_or_none()
        if row is None:
            raise _denied()
        return row

    @staticmethod
    def _params(binding):
        return {
            "provider": binding.provider, "profile": binding.profile_id,
            "generation": binding.profile_generation, "digest": binding.digest,
        }

    @classmethod
    def _current(cls, scope, binding):
        return scope.execute(text("""
            SELECT a.*,b.digest FROM agent_research.provider_authority a
            JOIN agent_research.provider_bindings b USING (provider,profile_id,generation)
            WHERE a.provider=:provider AND a.profile_id=:profile
            FOR UPDATE OF a
        """), cls._params(binding)).one_or_none()

    def _validate_current_evidence(self, scope, binding, credential):
        if (
            credential.blocked
            or (credential.version, credential.generation, credential.digest) != (
                binding.credential_version, binding.credential_generation,
                binding.credential_digest,
            )
            or scope.execute(text("SELECT clock_timestamp()")).scalar_one()
            >= binding.evidence_expires_at
            or self._check_evidence(scope, binding) is not True
        ):
            raise _denied()

    @staticmethod
    def _view(row, binding):
        return ProviderAuthority(
            row.epoch, ProviderProfileState(row.state), binding,
            row.dialogue, row.discovery, row.smoke_attempt_id,
        )

    def attest_credential(
        self, provider: str, generation: int, version: int, digest: str | None, *,
        expected_version: int, scope=None,
    ):
        """Store redacted Setup authority and revoke all prior bindings atomically."""
        if (
            provider not in _PROVIDERS
            or not all(map(_revision, (generation, version, expected_version)))
            or (digest is not None and not _hash(digest))
        ):
            raise _denied()
        with self._transaction(scope) as transaction:
            current = self._credential(transaction, provider)
            if (current.version, current.generation, current.digest) == (
                version, generation, digest,
            ):
                # A blocked current version may never be re-attested to undo a deny-first change.
                if current.blocked and digest is not None:
                    raise _denied()
                return
            if (
                current.version != expected_version or version != expected_version + 1
                or generation != (current.generation or 0) + int(digest is not None)
            ):
                raise _denied()
            transaction.execute(text("""
                UPDATE agent_research.provider_credential_authority
                SET version=:version,generation=:generation,digest=:digest,
                  blocked=(CAST(:digest AS text) IS NULL)
                WHERE provider=:provider
            """), dict(provider=provider, version=version, generation=generation, digest=digest))
            self._revoke_provider(transaction, provider)

    @staticmethod
    def _revoke_provider(transaction, provider):
        transaction.execute(text("""
            UPDATE agent_research.provider_authority
            SET epoch=epoch+1,state='REVOKED',dialogue=false,discovery=false
            WHERE provider=:provider AND state<>'REVOKED'
        """), {"provider": provider})

    def block_credential(self, provider: str, expected_version: int, *, scope=None):
        """Deny all dispatch before Setup mutates or removes the current local generation."""
        if provider not in _PROVIDERS or not _revision(expected_version):
            raise _denied()
        with self._transaction(scope) as transaction:
            current = self._credential(transaction, provider)
            if current.version != expected_version:
                raise _denied()
            transaction.execute(text("""
                UPDATE agent_research.provider_credential_authority
                SET blocked=true WHERE provider=:provider
            """), {"provider": provider})
            self._revoke_provider(transaction, provider)

    def stage(
        self, binding: ProviderBinding, expected_epoch: int, *, scope=None,
    ) -> ProviderAuthority:
        with self._transaction(scope) as transaction:
            credential = self._credential(transaction, binding.provider)
            self._validate_current_evidence(transaction, binding, credential)
            row = self._current(transaction, binding)
            if (
                expected_epoch != (row.epoch if row else 0)
                or binding.profile_generation != (row.generation + 1 if row else 1)
            ):
                raise _denied()
            params = self._params(binding) | {
                "binding": binding.json(), "epoch": expected_epoch + 1,
            }
            transaction.execute(text("""
                INSERT INTO agent_research.provider_bindings
                  (provider,profile_id,generation,digest,binding)
                VALUES (:provider,:profile,:generation,:digest,CAST(:binding AS jsonb))
            """), params)
            created = transaction.execute(text("""
                INSERT INTO agent_research.provider_authority
                  (provider,profile_id,generation,epoch,state)
                VALUES (:provider,:profile,:generation,:epoch,'STAGED')
                ON CONFLICT (provider,profile_id) DO UPDATE SET generation=EXCLUDED.generation,
                  epoch=EXCLUDED.epoch,state='STAGED',dialogue=false,discovery=false,
                  smoke_attempt_id=NULL
                RETURNING *
            """), params).one()
            return self._view(created, binding)

    def _lock_exact(self, scope, binding, epoch, *, check_evidence=True):
        credential = self._credential(scope, binding.provider)
        row = self._current(scope, binding)
        if (
            not _revision(epoch) or row is None or row.epoch != epoch
            or row.digest != binding.digest
        ):
            raise _denied()
        if check_evidence:
            self._validate_current_evidence(scope, binding, credential)
        return row

    def require(self, binding, epoch, purpose: ProviderPurpose, *, scope) -> ProviderAuthority:
        """For PROBE the caller must also atomically consume its separate one-use grant."""
        if scope is None or type(purpose) is not ProviderPurpose:
            raise _denied()
        row = self._lock_exact(scope, binding, epoch)
        allowed = {
            ProviderPurpose.PROBE: row.state == ProviderProfileState.STAGED,
            ProviderPurpose.DIALOGUE: row.state == ProviderProfileState.ACTIVE and row.dialogue,
            ProviderPurpose.DISCOVERY: row.state == ProviderProfileState.ACTIVE and row.discovery,
        }
        if not allowed[purpose]:
            raise _denied()
        return self._view(row, binding)

    def load_active(self, provider: str, profile_id: str, purpose: ProviderPurpose, *, scope):
        row = scope.execute(text("""
            SELECT a.epoch,b.binding FROM agent_research.provider_authority a
            JOIN agent_research.provider_bindings b USING (provider,profile_id,generation)
            WHERE a.provider=:provider AND a.profile_id=:profile
        """), {"provider": provider, "profile": profile_id}).one_or_none()
        if row is None or purpose is ProviderPurpose.PROBE:
            raise _denied()
        binding = ProviderBinding.from_record(row.binding)
        if (binding.provider, binding.profile_id) != (provider, profile_id):
            raise _denied()
        return self.require(binding, row.epoch, purpose, scope=scope)

    def _transition(self, scope, binding, state, *, dialogue=False, discovery=False, smoke=None):
        row = scope.execute(text("""
            UPDATE agent_research.provider_authority
            SET epoch=epoch+1,state=:state,dialogue=:dialogue,discovery=:discovery,
              smoke_attempt_id=:smoke
            WHERE provider=:provider AND profile_id=:profile RETURNING *
        """), self._params(binding) | {
            "state": state.value, "dialogue": dialogue, "discovery": discovery, "smoke": smoke,
        }).one()
        return self._view(row, binding)

    def mark_verified(
        self, binding, epoch, smoke_attempt_id: UUID, *, scope=None,
    ) -> ProviderAuthority:
        """Verify only a settled successful smoke attempt bound to this exact STAGED epoch."""
        if type(smoke_attempt_id) is not UUID:
            raise _denied()
        with self._transaction(scope) as transaction:
            self.require(binding, epoch, ProviderPurpose.PROBE, scope=transaction)
            proven = transaction.execute(text("""
                SELECT 1 FROM agent_research.model_attempts a
                JOIN agent_research.runs r USING (run_id)
                WHERE a.attempt_id=:attempt AND r.kind='PROVIDER_PROBE' AND r.state='COMPLETED'
                  AND a.status='COMPLETED' AND a.confirmed_cost IS NOT NULL
                  AND a.reported_input IS NOT NULL AND a.reported_output IS NOT NULL
                  AND NOT a.exceeded_bound AND a.pricing_version=:tariff
                  AND a.evidence_revision=:evidence AND a.authority_hash=:authority
            """), {
                "attempt": smoke_attempt_id, "tariff": binding.tariff_revision,
                "evidence": binding.evidence_revision,
                "authority": authority_hash(binding, epoch, ProviderPurpose.PROBE),
            }).scalar_one_or_none()
            if proven != 1:
                raise _denied()
            return self._transition(
                transaction, binding, ProviderProfileState.VERIFIED, smoke=smoke_attempt_id,
            )

    def set_purpose(
        self, binding, epoch, purpose: ProviderPurpose, enabled: bool, *, scope=None,
    ) -> ProviderAuthority:
        if (
            type(purpose) is not ProviderPurpose or purpose is ProviderPurpose.PROBE
            or type(enabled) is not bool
        ):
            raise _denied()
        with self._transaction(scope) as transaction:
            row = self._lock_exact(transaction, binding, epoch, check_evidence=enabled)
            if row.state not in (ProviderProfileState.VERIFIED, ProviderProfileState.ACTIVE):
                raise _denied()
            dialogue = enabled if purpose is ProviderPurpose.DIALOGUE else row.dialogue
            discovery = enabled if purpose is ProviderPurpose.DISCOVERY else row.discovery
            if (dialogue, discovery) == (row.dialogue, row.discovery):
                return self._view(row, binding)
            return self._transition(
                transaction, binding, ProviderProfileState.ACTIVE if dialogue or discovery
                else ProviderProfileState.VERIFIED,
                dialogue=dialogue, discovery=discovery, smoke=row.smoke_attempt_id,
            )

    def revoke(self, binding, epoch, *, scope=None) -> ProviderAuthority:
        with self._transaction(scope) as transaction:
            row = self._lock_exact(transaction, binding, epoch, check_evidence=False)
            if row.state == ProviderProfileState.REVOKED:
                return self._view(row, binding)
            return self._transition(transaction, binding, ProviderProfileState.REVOKED)

    def disable(self, binding, epoch, *, scope=None) -> ProviderAuthority:
        with self._transaction(scope) as transaction:
            row = self._lock_exact(transaction, binding, epoch, check_evidence=False)
            if row.state == ProviderProfileState.DISABLED:
                return self._view(row, binding)
            if row.state == ProviderProfileState.REVOKED:
                raise _denied()
            return self._transition(transaction, binding, ProviderProfileState.DISABLED)
