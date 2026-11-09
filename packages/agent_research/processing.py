"""Private, one-shot consent ledger and in-memory send projections."""

import hmac
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from secrets import token_bytes
from threading import Lock, Timer
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection

from .budget import ModelOutcome, Tokens
from .model_attempts import ModelAttemptStore
from .runs import Claim, RunConflict
from .runs import _audit as _audit_run

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}")
_COMMITMENT = re.compile(r"[a-f0-9]{64}")
_CLASSES = {"OPERATOR_RESEARCH_EXCERPT", "CONFIRMED_RESEARCH_FIELDS"}
_PURPOSE = "HYPOTHESIS_INTERPRETATION_V1"


def _invalid() -> RunConflict:
    return RunConflict("PROCESSING_GRANT_INVALID")


@dataclass(frozen=True, slots=True)
class ProcessingBinding:
    run_id: UUID
    actor: str
    dialogue_id: str
    dialogue_version: int
    turn_id: str
    turn_version: int
    provider: str
    profile_id: str
    profile_generation: int
    model_id: str
    model_generation: int
    credential_generation: int
    policy_generation: int
    envelope_version: str
    data_classes: tuple[str, ...]
    step_id: str

    def __post_init__(self) -> None:
        identities = (
            self.actor, self.dialogue_id, self.turn_id, self.profile_id, self.model_id,
            self.envelope_version, self.step_id,
        )
        versions = (
            self.dialogue_version, self.turn_version, self.profile_generation,
            self.model_generation, self.credential_generation, self.policy_generation,
        )
        if (
            not isinstance(self.run_id, UUID)
            or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in identities)
            or any(type(value) is not int or value < 1 for value in versions)
            or self.provider not in {"OPENAI", "ANTHROPIC"}
            or not isinstance(self.data_classes, tuple)
            or len(set(self.data_classes)) != len(self.data_classes)
            or not set(self.data_classes) <= _CLASSES
            or "OPERATOR_RESEARCH_EXCERPT" not in self.data_classes
        ):
            raise _invalid()

    def record(self) -> dict:
        return asdict(self) | {
            "run_id": str(self.run_id), "data_classes": list(self.data_classes),
            "purpose": _PURPOSE,
        }

    def model_record(self) -> dict:
        return {
            "provider": self.provider, "profile_id": self.profile_id,
            "profile_generation": self.profile_generation, "model_id": self.model_id,
            "model_generation": self.model_generation,
        }


@dataclass(frozen=True, slots=True)
class Preview:
    preview_id: UUID
    commitment: str
    binding: ProcessingBinding
    expires_at: datetime


@dataclass(frozen=True, slots=True, repr=False)
class _Projection:
    preview: Preview
    session: str
    content: bytes
    timer: Timer


class ProjectionVault:
    """A private process-local vault. Neither sessions nor payloads leave it for storage."""

    def __init__(self) -> None:
        self._key = token_bytes(32)
        self._entries: dict[UUID, _Projection] = {}
        self._lock = Lock()

    def _commitment(self, preview_id: UUID, binding: ProcessingBinding, content: bytes) -> str:
        metadata = json.dumps(binding.record(), sort_keys=True, separators=(",", ":")).encode()
        payload = preview_id.bytes + metadata + b"\x00" + content
        return hmac.digest(self._key, payload, "sha256").hex()

    def issue(
        self, binding: ProcessingBinding, *, session: str, content: bytes, deadline: datetime,
    ) -> Preview:
        now = datetime.now(UTC)
        if (
            not isinstance(content, bytes) or not 1 <= len(content) <= 16384
            or not isinstance(session, str) or not 1 <= len(session) <= 256
            or deadline.tzinfo is None or deadline <= now
        ):
            raise _invalid()
        preview_id = uuid4()
        expiry = min(deadline.astimezone(UTC), now + timedelta(minutes=10))
        commitment = self._commitment(preview_id, binding, content)
        preview = Preview(preview_id, commitment, binding, expiry)
        remaining = max(0, (expiry - datetime.now(UTC)).total_seconds())
        timer = Timer(remaining, self.discard, (preview_id,))
        timer.daemon = True
        with self._lock:
            if len(self._entries) >= 128:
                raise RunConflict("BUDGET_EXHAUSTED")
            self._entries[preview_id] = _Projection(preview, session, content, timer)
            try:
                timer.start()
            except RuntimeError:
                self._entries.pop(preview_id)
                raise
        return preview

    def _entry(self, preview_id: UUID) -> _Projection:
        with self._lock:
            entry = self._entries.get(preview_id)
            if entry is None or entry.preview.expires_at <= datetime.now(UTC):
                if entry is not None:
                    self._entries.pop(preview_id)
                    entry.timer.cancel()
                raise _invalid()
            return entry

    def read(self, preview: Preview, *, session: str) -> bytes:
        entry = self._entry(preview.preview_id)
        if entry.session != session or entry.preview != preview:
            raise _invalid()
        return entry.content

    def _dispatch_content(self, preview_id: UUID, binding: ProcessingBinding, digest: str) -> bytes:
        entry = self._entry(preview_id)
        if entry.preview.binding != binding or not hmac.compare_digest(
            self._commitment(preview_id, binding, entry.content), digest,
        ):
            raise _invalid()
        return entry.content

    def _available(self, preview_id: UUID) -> bool:
        try:
            self._entry(preview_id)
        except RunConflict:
            return False
        return True

    def discard(self, preview_id: UUID) -> None:
        with self._lock:
            entry = self._entries.pop(preview_id, None)
            if entry is not None:
                entry.timer.cancel()


@dataclass(frozen=True, slots=True)
class ProcessingReceipt:
    receipt_id: UUID
    state: str
    revision: int
    expires_at: datetime
    attempt_id: UUID | None
    result_ref: str | None


@dataclass(frozen=True, slots=True, repr=False)
class AuthorizedProjection:
    attempt_id: UUID
    commitment: str
    content: bytes


def _receipt(row) -> ProcessingReceipt:
    return ProcessingReceipt(
        row.receipt_id, row.state, row.revision, row.expires_at, row.attempt_id, row.result_ref,
    )


def _audit(connection: Connection, receipt_id: UUID) -> None:
    connection.execute(text("""
        INSERT INTO agent_research.processing_events (receipt_id,revision,state)
        SELECT receipt_id,revision,state FROM agent_research.processing_grants WHERE receipt_id=:id
    """), {"id": receipt_id})


def _lock_claim(connection: Connection, claim: Claim) -> dict:
    held = connection.execute(text("""
        SELECT agent_binding FROM agent_research.runs
        WHERE run_id=:run_id AND owner=:owner AND fence=:fence AND version=:version
          AND kind='DIALOGUE' AND state='RUNNING' AND lease_until>clock_timestamp()
          AND deadline_at>clock_timestamp() FOR UPDATE
    """), {"run_id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version}).scalar_one_or_none()
    if held is None:
        raise RunConflict("LEASE_LOST")
    return held


class ProcessingStore:
    """The Control API must authenticate callers before invoking operator methods."""

    def __init__(self, engine: Engine, vault: ProjectionVault):
        self.engine = engine
        self.vault = vault

    def _current_receipt(self, connection: Connection, row) -> ProcessingReceipt:
        if row.state == "AUTHORIZED" and (
            not row.live or not self.vault._available(row.preview_id)
        ):
            row = connection.execute(text("""
                UPDATE agent_research.processing_grants SET state='UNAVAILABLE',
                    revision=revision+1 WHERE receipt_id=:id RETURNING *
            """), {"id": row.receipt_id}).one()
            _audit(connection, row.receipt_id)
        return _receipt(row)

    def confirm(
        self, preview_id: UUID, commitment: str, binding: ProcessingBinding, *, session: str,
        key: str, check_current: Callable[[ReadOnlyScopedConnection, ProcessingBinding], None],
    ) -> ProcessingReceipt:
        """Bind the current server-produced preview; only exact replay can survive its loss."""
        if not session or not _KEY.fullmatch(key) or not _COMMITMENT.fullmatch(commitment):
            raise _invalid()
        request = json.dumps([
            str(preview_id), commitment, binding.record(),
        ], sort_keys=True, separators=(",", ":"))
        params = {
            "receipt_id": uuid4(), "run_id": binding.run_id, "actor": binding.actor,
            "preview_id": preview_id, "commitment": commitment,
            "key_hash": sha256(key.encode()).hexdigest(), "binding": json.dumps(binding.record()),
            "request_hash": sha256(request.encode()).hexdigest(),
        }
        with self.engine.begin() as connection:
            run = connection.execute(text("""
                SELECT state, deadline_at>clock_timestamp() AS live
                FROM agent_research.runs WHERE run_id=:run_id AND actor=:actor
                  AND kind='DIALOGUE' FOR UPDATE
            """), params).one_or_none()
            if run is None:
                raise _invalid()
            previous = connection.execute(text("""
                SELECT *,expires_at>clock_timestamp() AS live
                FROM agent_research.processing_grants
                WHERE run_id=:run_id AND actor=:actor AND key_hash=:key_hash
                FOR UPDATE
            """), params).one_or_none()
            if previous is not None:
                if previous.request_hash != params["request_hash"]:
                    raise RunConflict("IDEMPOTENCY_CONFLICT")
                return self._current_receipt(connection, previous)
            if not run.live or run.state != "WAITING_FOR_PROCESSING_DECISION":
                raise _invalid()
            entry = self.vault._entry(preview_id)
            if (
                entry.session != session or entry.preview.binding != binding
                or not hmac.compare_digest(entry.preview.commitment, commitment)
            ):
                raise _invalid()
            check_current(ReadOnlyScopedConnection(connection), binding)
            params["expires_at"] = entry.preview.expires_at
            created = connection.execute(text("""
                INSERT INTO agent_research.processing_grants (
                    receipt_id,run_id,preview_id,actor,key_hash,request_hash,commitment,binding,
                    created_at,expires_at,state
                ) SELECT :receipt_id,run_id,:preview_id,:actor,:key_hash,:request_hash,:commitment,
                    CAST(:binding AS jsonb),statement_timestamp(),:expires_at,'AUTHORIZED'
                  FROM agent_research.runs WHERE run_id=:run_id
                    AND :expires_at>statement_timestamp() AND :expires_at<=deadline_at
                    AND :expires_at<=statement_timestamp()+interval '10 minutes'
                ON CONFLICT (preview_id) DO NOTHING RETURNING *
            """), params).one_or_none()
            if created is None:
                raise _invalid()
            connection.execute(text("""
                UPDATE agent_research.runs SET state='QUEUED',version=version+1,
                    updated_at=clock_timestamp() WHERE run_id=:run_id
            """), params)
            _audit(connection, created.receipt_id)
            _audit_run(connection, binding.run_id)
            return _receipt(created)

    def status(self, receipt_id: UUID, *, actor: str) -> ProcessingReceipt:
        with self.engine.begin() as connection:
            params = {"id": receipt_id, "actor": actor}
            row = connection.execute(text("""
                SELECT *, expires_at>clock_timestamp() AS live
                FROM agent_research.processing_grants WHERE receipt_id=:id AND actor=:actor
                FOR UPDATE
            """), params).one_or_none()
            if row is None:
                raise RunConflict("PROCESSING_GRANT_REQUIRED")
            return self._current_receipt(connection, row)

    def revoke(self, receipt_id: UUID, *, actor: str, expected_revision: int) -> ProcessingReceipt:
        if type(expected_revision) is not int or expected_revision < 1:
            raise _invalid()
        with self.engine.begin() as connection:
            params = {"id": receipt_id, "actor": actor, "revision": expected_revision}
            changed = connection.execute(text("""
                UPDATE agent_research.processing_grants SET state='REVOKED',revision=revision+1
                WHERE receipt_id=:id AND actor=:actor AND revision=:revision AND state<>'REVOKED'
                RETURNING *
            """), params).one_or_none()
            if changed is None:
                changed = connection.execute(text("""
                    SELECT * FROM agent_research.processing_grants WHERE receipt_id=:id
                      AND actor=:actor AND revision=:revision+1 AND state='REVOKED'
                """), params).one_or_none()
                if changed is None:
                    raise RunConflict("VERSION_CONFLICT")
            else:
                _audit(connection, receipt_id)
            receipt = _receipt(changed)
        self.vault.discard(changed.preview_id)
        return receipt

    def authorize(
        self, claim: Claim, receipt_id: UUID, attempt_id: UUID, binding: ProcessingBinding,
        *, check_policy: Callable[[ReadOnlyScopedConnection, ProcessingBinding], None],
    ) -> AuthorizedProjection:
        """Consume consent and authorize the reserved model attempt in one transaction."""
        with self.engine.begin() as connection:
            agent = _lock_claim(connection, claim)
            grant = connection.execute(text("""
                SELECT *,expires_at>clock_timestamp() AS live
                FROM agent_research.processing_grants WHERE receipt_id=:id AND run_id=:run_id
                FOR UPDATE
            """), {"id": receipt_id, "run_id": claim.run_id}).one_or_none()
            if grant is None:
                raise RunConflict("PROCESSING_GRANT_REQUIRED")
            if grant.state == "REVOKED":
                raise RunConflict("PROCESSING_GRANT_REVOKED")
            if (
                grant.state != "AUTHORIZED" or not grant.live or grant.binding != binding.record()
                or agent["model_binding"] != binding.model_record()
            ):
                raise _invalid()
            attempt_step = connection.execute(text("""
                SELECT step_id FROM agent_research.model_attempts
                WHERE attempt_id=:attempt AND run_id=:run_id
            """), {"attempt": attempt_id, "run_id": claim.run_id}).scalar_one_or_none()
            if attempt_step != binding.step_id:
                raise _invalid()
            content = self.vault._dispatch_content(grant.preview_id, binding, grant.commitment)
            check_policy(ReadOnlyScopedConnection(connection), binding)
            ModelAttemptStore.authorize_in_transaction(
                connection, claim, attempt_id, grant.commitment, dialogue_authorized=True,
            )
            changed = connection.execute(text("""
                UPDATE agent_research.processing_grants SET state='DISPATCHED',revision=revision+1,
                    attempt_id=:attempt,dispatch_fence=:fence,dispatched_at=clock_timestamp()
                WHERE receipt_id=:id AND expires_at>clock_timestamp() RETURNING receipt_id
            """), {"id": receipt_id, "attempt": attempt_id,
                    "fence": claim.fence}).scalar_one_or_none()
            if changed is None:
                raise _invalid()
            _audit(connection, receipt_id)
        self.vault.discard(grant.preview_id)
        return AuthorizedProjection(attempt_id, grant.commitment, content)

    def accept_result(
        self, claim: Claim, receipt_id: UUID, attempt_id: UUID, result_ref: str,
        *, binding: ProcessingBinding,
        check_policy: Callable[[ReadOnlyScopedConnection, ProcessingBinding], None],
        persist_result: Callable[[ScopedConnection], None],
        reported_tokens: Tokens | None = None, confirmed_cost: Decimal | None = None,
    ) -> ProcessingReceipt:
        """Fence publication and persist the independently validated result in the same commit."""
        if not _ID.fullmatch(result_ref):
            raise _invalid()
        with self.engine.begin() as connection:
            _lock_claim(connection, claim)
            params = {"id": receipt_id, "run_id": claim.run_id, "attempt": attempt_id,
                      "fence": claim.fence, "result": result_ref}
            changed = connection.execute(text("""
                UPDATE agent_research.processing_grants SET state='ACCEPTED',revision=revision+1,
                    result_ref=:result
                WHERE receipt_id=:id AND run_id=:run_id AND attempt_id=:attempt
                  AND dispatch_fence=:fence AND state='DISPATCHED'
                RETURNING *
            """), params).one_or_none()
            if changed is None:
                state = connection.execute(text("""
                    SELECT state FROM agent_research.processing_grants
                    WHERE receipt_id=:id AND run_id=:run_id AND attempt_id=:attempt
                """), params).scalar_one_or_none()
                if state == "REVOKED":
                    raise RunConflict("PROCESSING_GRANT_REVOKED")
                raise _invalid()
            if changed.binding != binding.record():
                raise _invalid()
            check_policy(ReadOnlyScopedConnection(connection), binding)
            ModelAttemptStore.settle_in_transaction(
                connection, claim, attempt_id, ModelOutcome.COMPLETED, result_ref=result_ref,
                reported_tokens=reported_tokens, confirmed_cost=confirmed_cost,
                dialogue_authorized=True,
            )
            persist_result(ScopedConnection(connection))
            _lock_claim(connection, claim)
            _audit(connection, receipt_id)
            return _receipt(changed)
