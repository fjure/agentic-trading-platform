"""Build and expose the server-owned processing projection in the Research runtime."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from threading import Lock
from uuid import UUID, uuid4

from sqlalchemy import text

from packages.agent_research.companion.dialogue import Dialogue
from packages.agent_research.companion.store import CompanionStore
from packages.agent_research.processing import Preview, ProcessingBinding, ProcessingStore
from packages.agent_research.runs import RunConflict
from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection
from services.llm_gateway.content_policy import safe_result_content, valid_projection
from services.runtime.research_companion import (
    _PROJECTION_FIELDS,
    check_current_processing_turn,
    create_processing_preview,
)

_PURPOSE = "HYPOTHESIS_INTERPRETATION_V1"
_EXCERPT = "OPERATOR_RESEARCH_EXCERPT"
_CONFIRMED = "CONFIRMED_RESEARCH_FIELDS"


@dataclass(frozen=True, slots=True)
class ProcessingAuthority:
    binding: ProcessingBinding
    deadline: datetime
    permits: Callable[[bytes], bool]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise RunConflict("PROCESSING_GRANT_INVALID") from None


def _projection(original: str, dialogue: Dialogue, classes: tuple[str, ...]) -> bytes:
    confirmed = dialogue.confirmed
    context = {
        wire: confirmed[field] for wire, field in _PROJECTION_FIELDS.items()
        if field in confirmed and _CONFIRMED in classes
    }
    value = {"excerpt": original, "confirmedContext": context}
    if not safe_result_content(value):
        raise RunConflict("MODEL_CONTEXT_UNCLASSIFIED")
    try:
        content = _canonical(value)
    except UnicodeError:
        raise RunConflict("MODEL_CONTEXT_UNCLASSIFIED") from None
    if not valid_projection(content, classes) or len(content) > 16384:
        raise RunConflict("MODEL_CONTEXT_UNCLASSIFIED")
    return content


def _versions(dialogue: Dialogue, payload: dict) -> None:
    turn = payload["expectedTurnVersion"]
    revision = payload["expectedDialogueVersion"]
    if (
        type(turn) is not int or turn != 1
        or type(revision) is not int or revision != dialogue.version
    ):
        raise RunConflict("VERSION_CONFLICT")


def _preview_response(preview: Preview, content: bytes) -> dict:
    binding = preview.binding
    return {
        "previewId": str(preview.preview_id),
        "turnId": binding.turn_id,
        "turnVersion": binding.turn_version,
        "dialogueVersion": binding.dialogue_version,
        "provider": binding.provider,
        "profileId": binding.profile_id,
        "profileGeneration": binding.profile_generation,
        "modelId": binding.model_id,
        "modelGeneration": binding.model_generation,
        "envelopeVersion": binding.envelope_version,
        "credentialGeneration": binding.credential_generation,
        "policyGeneration": binding.policy_generation,
        "purpose": _PURPOSE,
        "dataClasses": list(binding.data_classes),
        "expiresAt": _timestamp(preview.expires_at),
        "projectionBinding": preview.commitment,
        "projection": json.loads(content),
    }


def _status(turn_id: str, version: int, state: str) -> dict:
    return {
        "turnId": turn_id, "turnVersion": 1, "dialogueVersion": version,
        "purpose": _PURPOSE, "state": state, "delivery": "NOT_SENT",
        "costState": "NOT_INCURRED",
    }


def _grant_status(row, receipt, *, replay: bool = False) -> dict:
    state = {
        "AUTHORIZED": "SEND_AUTHORIZED", "DISPATCHED": "IN_FLIGHT",
        "ACCEPTED": "PROPOSAL_VALIDATED", "REVOKED": "REVOKED", "UNAVAILABLE": "BLOCKED",
    }["AUTHORIZED" if replay else receipt.state]
    if not replay and receipt.state == "DISPATCHED" and row.attempt_status in {
        "OUTCOME_UNKNOWN", "RESULT_UNAVAILABLE", "UNSENT",
    }:
        state = "BLOCKED"
    binding = row.binding
    result = _status(binding["turn_id"], binding["dialogue_version"], state)
    result["receipt"] = {
        "receiptId": str(row.receipt_id), "turnId": binding["turn_id"],
        "decidedAt": _timestamp(row.created_at), "purpose": _PURPOSE, "state": state,
        "auditId": str(row.receipt_id), "provider": binding["provider"],
        "profileId": binding["profile_id"], "profileGeneration": binding["profile_generation"],
        "policyGeneration": binding["policy_generation"], "projectionBinding": row.commitment,
    }
    if not replay:
        if receipt.attempt_id is not None and row.attempt_status != "UNSENT":
            result["delivery"] = "SENT" if receipt.result_ref is not None else "MAY_HAVE_BEEN_SENT"
            result["costState"] = "KNOWN" if row.confirmed_cost is not None else "UNKNOWN"
        if receipt.state == "ACCEPTED":
            result["proposalId"] = receipt.result_ref
        if receipt.state == "REVOKED":
            result["reasonCode"] = "PROCESSING_GRANT_REVOKED"
        if receipt.state == "UNAVAILABLE":
            result["reasonCode"] = "PROCESSING_PREVIEW_UNAVAILABLE"
        elif state == "BLOCKED":
            result["reasonCode"] = "PROVIDER_UNAVAILABLE"
    return result


class ProcessingProjection:
    """Serve a volatile preview and persist only redacted processing decisions."""

    def __init__(
        self, companion: CompanionStore, processing: ProcessingStore, *, resolve_authority=None,
        notify_cancel=None,
    ):
        self.companion = companion
        self.processing = processing
        self.resolve_authority = resolve_authority
        self.notify_cancel = notify_cancel
        self._previews: dict[str, Preview] = {}
        self._preview_lock = Lock()

    def _authority(self, scope, actor, dialogue, profile_id) -> ProcessingAuthority:
        if self.resolve_authority is None:
            raise RunConflict("MODEL_CONTEXT_DENIED")
        authority = self.resolve_authority(scope, actor, dialogue, profile_id)
        binding = authority.binding
        if (
            (binding.actor, binding.dialogue_id, binding.turn_id, binding.dialogue_version,
             binding.turn_version, binding.profile_id)
            != (actor, dialogue.dialogue_id, dialogue.turn_id, dialogue.version, 1, profile_id)
        ):
            raise RunConflict("PROCESSING_GRANT_INVALID")
        return authority

    @staticmethod
    def _operation(connection, actor, operation, key, dialogue_id, turn_id, payload, session=None):
        key_hash = sha256(_canonical([session, key])).hexdigest()
        fingerprint = sha256(_canonical([dialogue_id, turn_id, payload])).hexdigest()
        params = {
            "actor": actor, "operation": operation, "key_hash": key_hash,
            "fingerprint": fingerprint, "dialogue": dialogue_id, "turn": turn_id,
        }
        lock = ":".join(("processing", actor, dialogue_id))
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:lock, 0))"), {
            "lock": lock,
        })
        prior = connection.execute(text("""
            SELECT request_hash,decision FROM agent_research.processing_operations
            WHERE actor=:actor AND operation=:operation AND key_hash=:key_hash
        """), params).one_or_none()
        if prior is not None and not compare_digest(prior.request_hash, fingerprint):
            raise RunConflict("IDEMPOTENCY_CONFLICT")
        return params, prior

    @staticmethod
    def _record(connection, params, decision=None):
        inserted = connection.execute(text("""
            INSERT INTO agent_research.processing_operations
                (actor,dialogue_id,turn_id,operation,key_hash,request_hash,decision)
            VALUES (:actor,:dialogue,:turn,:operation,:key_hash,:fingerprint,
                    CAST(:decision AS jsonb))
            ON CONFLICT DO NOTHING RETURNING 1
        """), params | {"decision": None if decision is None else _canonical(decision).decode()})
        if inserted.scalar_one_or_none() is None:
            raise RunConflict("IDEMPOTENCY_CONFLICT")

    @staticmethod
    def _grant(connection, actor, dialogue_id, turn_id, receipt_id=None):
        return connection.execute(text("""
            SELECT g.*,a.confirmed_cost,a.status AS attempt_status
            FROM agent_research.processing_grants g
            LEFT JOIN agent_research.model_attempts a ON a.attempt_id=g.attempt_id
            WHERE g.actor=:actor AND g.binding->>'dialogue_id'=:dialogue
              AND g.binding->>'turn_id'=:turn
              AND (CAST(:receipt AS uuid) IS NULL OR g.receipt_id=:receipt)
            ORDER BY g.created_at DESC,g.receipt_id DESC LIMIT 1
        """), {"actor": actor, "dialogue": dialogue_id, "turn": turn_id,
                "receipt": receipt_id}).one_or_none()

    @staticmethod
    def _latest_local(connection, actor, dialogue_id, turn_id):
        return connection.execute(text("""
            SELECT decision FROM agent_research.processing_operations
            WHERE actor=:actor AND dialogue_id=:dialogue AND turn_id=:turn
              AND operation='DECISION' AND decision->>'state'='DECLINED'
            ORDER BY created_at DESC,key_hash DESC LIMIT 1
        """), {"actor": actor, "dialogue": dialogue_id, "turn": turn_id}).scalar_one_or_none()

    def _dialogue(self, actor, dialogue_id, turn_id):
        idea_id, revision = self.companion.read_owned_dialogue(actor, dialogue_id)
        dialogue = Dialogue.from_record(revision.document)
        if dialogue.turn_id != turn_id or dialogue.original_id is None:
            raise RunConflict("RESOURCE_NOT_FOUND")
        return idea_id, dialogue

    def _check_current(self, scope, actor, idea_id, dialogue):
        self.companion.check_current_turn(
            scope, actor, idea_id, dialogue.dialogue_id, idea_version=dialogue.idea_version,
            dialogue_version=dialogue.version, turn_id=dialogue.turn_id,
            original_id=dialogue.original_id,
        )

    def _remember(self, key_hash: str, preview: Preview) -> None:
        with self._preview_lock:
            self._previews = {
                key: value for key, value in self._previews.items()
                if value.expires_at > datetime.now(UTC)
            }
            if len(self._previews) >= 128:
                self._previews.pop(next(iter(self._previews)))
            self._previews[key_hash] = preview

    @staticmethod
    def _run_deadline(scope, binding, deadline):
        run = scope.execute(text("""
            SELECT deadline_at,agent_binding FROM agent_research.runs
            WHERE run_id=:run_id AND actor=:actor AND kind='DIALOGUE'
              AND input_ref=:dialogue AND state='WAITING_FOR_PROCESSING_DECISION'
              AND deadline_at>clock_timestamp()
        """), {"run_id": binding.run_id, "actor": binding.actor,
                "dialogue": binding.dialogue_id}).one_or_none()
        if run is None or run.agent_binding.get("model_binding") != binding.model_record():
            raise RunConflict("PROCESSING_GRANT_INVALID")
        return min(run.deadline_at, deadline)

    def preview(self, actor, session, dialogue_id, turn_id, payload, key) -> dict:
        with self.processing.engine.begin() as connection:
            params, prior = self._operation(
                connection, actor, "PREVIEW", key, dialogue_id, turn_id, payload, session,
            )
            idea_id, dialogue = self._dialogue(actor, dialogue_id, turn_id)
            _versions(dialogue, payload)
            if prior is not None:
                with self._preview_lock:
                    preview = self._previews.get(params["key_hash"])
                if preview is None or preview.expires_at <= datetime.now(UTC):
                    raise RunConflict("PROCESSING_PREVIEW_UNAVAILABLE")
                if connection.execute(text("""
                    SELECT 1 FROM agent_research.processing_grants WHERE preview_id=:preview
                """), {"preview": preview.preview_id}).scalar_one_or_none() is not None:
                    raise RunConflict("PROCESSING_PREVIEW_UNAVAILABLE")
            scope = ReadOnlyScopedConnection(connection)
            authority = self._authority(scope, actor, dialogue, payload["profileId"])
            latest = self._latest_local(connection, actor, dialogue_id, turn_id)
            if latest is not None and latest["dialogueVersion"] == dialogue.version:
                raise RunConflict("PROCESSING_GRANT_INVALID")
            binding = authority.binding
            actual_classes = (_EXCERPT,)
            if _CONFIRMED in binding.data_classes and any(
                field in dialogue.confirmed for field in _PROJECTION_FIELDS.values()
            ):
                actual_classes += (_CONFIRMED,)
            binding = replace(binding, data_classes=actual_classes)
            deadline = self._run_deadline(scope, binding, authority.deadline)
            if prior is not None:
                if preview.binding != binding or preview.expires_at > deadline:
                    raise RunConflict("PROCESSING_PREVIEW_UNAVAILABLE")
                try:
                    content = self.processing.vault.read(preview, session=session)
                except RunConflict:
                    raise RunConflict("PROCESSING_PREVIEW_UNAVAILABLE") from None
                self._check_current(scope, actor, idea_id, dialogue)
                self._require_permission(authority, content)
                return _preview_response(preview, content)

            original = self.companion.read_original(
                actor, idea_id, dialogue_id, dialogue.original_id, turn_id=turn_id,
            )
            content = _projection(original, dialogue, binding.data_classes)
            preview = create_processing_preview(
                self.companion, self.processing.vault, binding, idea_id=idea_id,
                idea_version=dialogue.idea_version, original_id=dialogue.original_id,
                session=session, deadline=deadline, projection=content, permits=authority.permits,
            )
            try:
                self._record(connection, params)
                self._remember(params["key_hash"], preview)
            except Exception:
                self.processing.vault.discard(preview.preview_id)
                raise
            return _preview_response(preview, content)

    @staticmethod
    def _require_permission(authority: ProcessingAuthority, content: bytes) -> None:
        try:
            allowed = authority.permits(content) is True
        except Exception:
            allowed = False
        if not allowed:
            raise RunConflict("MODEL_CONTEXT_DENIED")

    def check_dispatch(self, scope, binding: ProcessingBinding) -> None:
        """Recheck authoritative generations and the exact dialogue turn inside dispatch."""
        idea_id, dialogue = self._dialogue(binding.actor, binding.dialogue_id, binding.turn_id)
        authority = self._authority(scope, binding.actor, dialogue, binding.profile_id)
        if (
            not set(binding.data_classes) <= set(authority.binding.data_classes)
            or replace(authority.binding, data_classes=binding.data_classes) != binding
            or authority.deadline <= datetime.now(UTC)
        ):
            raise RunConflict("PROCESSING_GRANT_INVALID")
        check_current_processing_turn(
            self.companion, scope, binding, idea_id=idea_id, idea_version=dialogue.idea_version,
            original_id=dialogue.original_id,
        )

    def _send(self, connection, actor, session, idea_id, dialogue, payload, key):
        preview_id = _uuid(payload["previewId"])
        with self._preview_lock:
            preview = next(
                (item for item in self._previews.values() if item.preview_id == preview_id), None,
            )
        if preview is None:
            raise RunConflict("PROCESSING_PREVIEW_UNAVAILABLE")
        binding = preview.binding
        if (
            (binding.actor, binding.dialogue_id, binding.turn_id, binding.dialogue_version)
            != (actor, dialogue.dialogue_id, dialogue.turn_id, dialogue.version)
            or not compare_digest(preview.commitment, payload["projectionBinding"])
        ):
            raise RunConflict("PROCESSING_GRANT_INVALID")
        content = self.processing.vault.read(preview, session=session)

        def check_current(scope, submitted):
            check_current_processing_turn(
                self.companion, scope, submitted, idea_id=idea_id,
                idea_version=dialogue.idea_version, original_id=dialogue.original_id,
            )
            authority = self._authority(scope, actor, dialogue, submitted.profile_id)
            if (
                not set(submitted.data_classes) <= set(authority.binding.data_classes)
                or replace(authority.binding, data_classes=submitted.data_classes) != submitted
                or preview.expires_at > authority.deadline
            ):
                raise RunConflict("PROCESSING_GRANT_INVALID")
            self._require_permission(authority, content)

        receipt = self.processing.confirm(
            preview_id, preview.commitment, binding, session=session, key=key,
            check_current=check_current, scope=ScopedConnection(connection),
        )
        row = self._grant(
            connection, actor, dialogue.dialogue_id, dialogue.turn_id, receipt.receipt_id,
        )
        return _grant_status(row, receipt, replay=True)

    def decide(self, actor, session, dialogue_id, turn_id, payload, key) -> dict:
        discard = None
        cancellation = None
        action = payload["action"]
        with self.processing.engine.begin() as connection:
            params, prior = self._operation(
                connection, actor, "DECISION", key, dialogue_id, turn_id, payload,
            )
            if prior is not None:
                return prior.decision
            idea_id, dialogue = self._dialogue(actor, dialogue_id, turn_id)
            _versions(dialogue, payload)
            latest = self._latest_local(connection, actor, dialogue_id, turn_id)
            if action == "SEND":
                if latest is not None and latest["dialogueVersion"] == dialogue.version:
                    raise RunConflict("PROCESSING_GRANT_INVALID")
                response = self._send(connection, actor, session, idea_id, dialogue, payload, key)
            elif action == "DECLINE":
                self._check_current(ReadOnlyScopedConnection(connection), actor, idea_id, dialogue)
                existing = self._grant(connection, actor, dialogue_id, turn_id)
                if existing is not None and existing.state in {"AUTHORIZED", "DISPATCHED"}:
                    raise RunConflict("PROCESSING_GRANT_INVALID")
                response = _status(turn_id, dialogue.version, "DECLINED")
                response["receipt"] = {
                    "receiptId": str(uuid4()), "turnId": turn_id,
                    "decidedAt": _timestamp(datetime.now(UTC)), "purpose": _PURPOSE,
                    "state": "DECLINED",
                }
                response["receipt"]["auditId"] = response["receipt"]["receiptId"]
            elif action == "REVOKE":
                row = self._grant(
                    connection, actor, dialogue_id, turn_id, _uuid(payload["receiptId"]),
                )
                if row is None:
                    raise RunConflict("PROCESSING_GRANT_REQUIRED")
                receipt = self.processing.revoke(
                    row.receipt_id, actor=actor, expected_revision=row.revision,
                    scope=ScopedConnection(connection),
                )
                self._check_current(ReadOnlyScopedConnection(connection), actor, idea_id, dialogue)
                response = _grant_status(row, receipt)
                response["dialogueVersion"] = dialogue.version
                discard = row.preview_id
                if row.attempt_id is not None:
                    cancellation = (row.attempt_id, row.commitment)
            else:
                raise RunConflict("INVALID_REQUEST")
            self._record(connection, params, response)
        if discard is not None:
            self.processing.vault.discard(discard)
        if cancellation is not None and self.notify_cancel is not None:
            try:
                self.notify_cancel(*cancellation)
            except Exception:
                pass  # The durable revocation is authoritative even when cancellation fails.
        return response

    def status(self, actor, dialogue_id, turn_id) -> dict:
        idea_id, revision = self.companion.read_owned_dialogue(actor, dialogue_id)
        current = Dialogue.from_record(revision.document)
        with self.processing.engine.connect() as connection:
            dialogue_version = current.version
            if current.turn_id != turn_id:
                dialogue_version = connection.execute(text("""
                    SELECT max(version) FROM agent_research.companion_dialogue_versions
                    WHERE dialogue_id=:dialogue AND idea_id=:idea AND document->>'turn_id'=:turn
                """), {"dialogue": dialogue_id, "idea": idea_id,
                        "turn": turn_id}).scalar_one()
            if dialogue_version is None:
                raise RunConflict("RESOURCE_NOT_FOUND")
            latest = self._latest_local(connection, actor, dialogue_id, turn_id)
            row = self._grant(connection, actor, dialogue_id, turn_id)
        if latest is not None and (
            row is None or datetime.fromisoformat(latest["receipt"]["decidedAt"]) > row.created_at
        ):
            return latest | {"dialogueVersion": dialogue_version}
        if row is not None:
            receipt = self.processing.status(row.receipt_id, actor=actor)
            return _grant_status(row, receipt) | {"dialogueVersion": dialogue_version}
        with self._preview_lock:
            pending = any(
                preview.binding.actor == actor and preview.binding.dialogue_id == dialogue_id
                and preview.binding.turn_id == turn_id
                and current.turn_id == turn_id
                and preview.binding.dialogue_version == current.version
                and preview.expires_at > datetime.now(UTC)
                for preview in self._previews.values()
            )
        return _status(
            turn_id, dialogue_version, "AWAITING_DECISION" if pending else "LOCAL_CLARIFICATION",
        )
