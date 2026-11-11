"""Actor-scoped append-only persistence for companion ideas and dialogue."""

import json
import re
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from hmac import compare_digest, digest

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection

from .testability import HypothesisIdentity, hypothesis_signature

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


class CompanionConflict(RuntimeError):
    """Redacted conflict in the companion repository."""


@dataclass(frozen=True, slots=True)
class DialogueRevision:
    version: int
    idea_version: int
    document: dict


@dataclass(frozen=True, slots=True)
class DialogueChange:
    document: dict
    event: str
    next_idea: dict | None = None
    original: tuple[str, str, str] | None = None


def _identity(*values: str) -> None:
    if any(not isinstance(value, str) or not _ID.fullmatch(value) for value in values):
        raise CompanionConflict("INVALID_COMPANION_REFERENCE")


def _encode(document: dict) -> str:
    if type(document) is not dict:
        raise CompanionConflict("INVALID_COMPANION_DOCUMENT")
    try:
        return json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        raise CompanionConflict("INVALID_COMPANION_DOCUMENT") from None


def _lock_idea(connection, idea_id: str) -> None:
    isolation = connection.execute(text("SELECT current_setting('transaction_isolation')"))
    if isolation.scalar_one() != "read committed":
        raise CompanionConflict("COMPANION_TRANSACTION_UNAVAILABLE")
    connection.execute(text("""
        SELECT pg_advisory_xact_lock(hashtextextended('companion-idea:' || :idea,0))
    """), {"idea": idea_id})


def _idea(connection, actor: str, idea_id: str, version: int | None = None):
    result = connection.execute(text("""
        SELECT v.version,v.document FROM agent_research.companion_idea_versions v
        JOIN agent_research.companion_ideas i USING (idea_id)
        WHERE i.actor=:actor AND i.idea_id=:idea
          AND (CAST(:version AS bigint) IS NULL OR v.version=:version)
        ORDER BY v.version DESC LIMIT 1
    """), {"actor": actor, "idea": idea_id, "version": version}).one_or_none()
    if result is None:
        raise CompanionConflict("COMPANION_NOT_FOUND")
    return result


def _insert_idea(connection, idea_id: str, version: int, document: dict) -> None:
    if document.get("id") != idea_id or document.get("version") != version:
        raise CompanionConflict("INVALID_COMPANION_DOCUMENT")
    hypothesis = document.get("hypothesis")
    signature = None if hypothesis is None else hypothesis_signature(
        HypothesisIdentity.from_document(hypothesis),
    )
    connection.execute(text("""
        INSERT INTO agent_research.companion_idea_versions
          (idea_id,version,previous_version,document,hypothesis_signature)
        VALUES (:idea,:version,:previous,CAST(:document AS jsonb),:signature)
    """), {"idea": idea_id, "version": version, "previous": version - 1 or None,
            "document": _encode(document), "signature": signature})


class CompanionStore:
    """The trusted caller supplies authenticated actors and domain-validated transitions."""

    def __init__(self, engine: Engine, *, command_key: bytes):
        if type(command_key) is not bytes or len(command_key) < 32:
            raise ValueError("invalid companion command authentication configuration")
        self._engine = engine
        self._key = command_key

    def create(
        self, actor: str, idea_id: str, dialogue_id: str, dialogue: dict, *,
        key: str, command: bytes, make_idea: Callable[[], dict],
        scope: ScopedConnection | None = None, successor: dict | None = None,
    ) -> dict:
        _identity(actor, idea_id, dialogue_id, key)
        if type(command) is not bytes or len(command) > 65536:
            raise CompanionConflict("INVALID_COMPANION_COMMAND")
        key_hash = digest(self._key, b"companion-create-key-v1\0" + key.encode(), "sha256").hex()
        command_hash = digest(self._key, b"companion-create-v1\0" + command, "sha256").hex()
        try:
            with nullcontext(scope) if scope is not None else self._engine.begin() as connection:
                _lock_idea(connection, f"create:{actor}:{key_hash}")
                replay = connection.execute(text("""
                    SELECT idea_id,command_hash FROM agent_research.companion_ideas
                    WHERE actor=:actor AND command_key=:key
                """), {"actor": actor, "key": key_hash}).one_or_none()
                if replay is not None:
                    if not compare_digest(replay.command_hash, command_hash):
                        raise CompanionConflict("IDEMPOTENCY_CONFLICT")
                    return _idea(connection, actor, replay.idea_id, 1).document
                idea = make_idea()
                connection.execute(text("""
                    INSERT INTO agent_research.companion_ideas
                      (idea_id,actor,command_key,command_hash)
                    VALUES (:idea,:actor,:key,:hash)
                """), {"idea": idea_id, "actor": actor, "key": key_hash, "hash": command_hash})
                _insert_idea(connection, idea_id, 1, idea)
                if successor is not None:
                    _insert_idea(connection, idea_id, 2, successor)
                connection.execute(text("""
                    INSERT INTO agent_research.companion_dialogues (dialogue_id,idea_id)
                    VALUES (:dialogue,:idea)
                """), {"dialogue": dialogue_id, "idea": idea_id})
                connection.execute(text("""
                    INSERT INTO agent_research.companion_dialogue_versions
                      (dialogue_id,idea_id,version,idea_version,document)
                    VALUES (:dialogue,:idea,1,:idea_version,CAST(:document AS jsonb))
                """), {"dialogue": dialogue_id, "idea": idea_id,
                        "idea_version": 1 if successor is None else 2,
                        "document": _encode(dialogue)})
                return _idea(connection, actor, idea_id, 1).document
        except IntegrityError:
            raise CompanionConflict("COMPANION_ID_CONFLICT") from None

    def read_idea(self, actor: str, idea_id: str, version: int | None = None) -> dict:
        _identity(actor, idea_id)
        with self._engine.connect() as connection:
            return _idea(connection, actor, idea_id, version).document

    def exact_repetitions(
        self, actor: str, idea_id: str, version: int,
    ) -> tuple[tuple[str, int], ...]:
        """Find prior exact structured bindings visible to the same authenticated actor."""
        _identity(actor, idea_id)
        with self._engine.connect() as connection:
            _idea(connection, actor, idea_id, version)
            rows = connection.execute(text("""
                SELECT DISTINCT ON (prior.idea_id) prior.idea_id,prior.version
                FROM agent_research.companion_idea_versions selected
                JOIN agent_research.companion_idea_versions prior USING (hypothesis_signature)
                JOIN agent_research.companion_ideas i ON i.idea_id=prior.idea_id
                WHERE selected.idea_id=:idea AND selected.version=:version AND i.actor=:actor
                  AND (prior.created_at,prior.idea_id,prior.version)
                    < (selected.created_at,selected.idea_id,selected.version)
                ORDER BY prior.idea_id,prior.version DESC LIMIT 200
            """), {"actor": actor, "idea": idea_id, "version": version})
            return tuple((row.idea_id, row.version) for row in rows)

    def read_dialogue(
        self, actor: str, idea_id: str, dialogue_id: str, version: int | None = None,
    ) -> DialogueRevision:
        _identity(actor, idea_id, dialogue_id)
        with self._engine.connect() as connection:
            _idea(connection, actor, idea_id)
            return self._dialogue(connection, idea_id, dialogue_id, version)

    def read_bound_dialogue(self, actor: str, idea_id: str, dialogue_id: str) -> DialogueRevision:
        _identity(actor, idea_id, dialogue_id)
        with self._engine.begin() as connection:
            _lock_idea(connection, idea_id)
            idea = _idea(connection, actor, idea_id)
            dialogue = self._dialogue(connection, idea_id, dialogue_id)
            if dialogue.idea_version != idea.version:
                raise CompanionConflict("STALE_IDEA_VERSION")
            return dialogue

    def check_current_turn(
        self, scope: ReadOnlyScopedConnection, actor: str, idea_id: str, dialogue_id: str, *,
        idea_version: int, dialogue_version: int, turn_id: str, original_id: str,
    ) -> None:
        _identity(actor, idea_id, dialogue_id, turn_id, original_id)
        _lock_idea(scope, idea_id)
        idea = _idea(scope, actor, idea_id)
        dialogue = self._dialogue(scope, idea_id, dialogue_id)
        if (
            idea.version != idea_version or dialogue.idea_version != idea_version
            or dialogue.version != dialogue_version or dialogue.document["turn_id"] != turn_id
            or dialogue.document["original_id"] != original_id
        ):
            raise CompanionConflict("STALE_DIALOGUE_VERSION")
        origin = scope.execute(text("""
            SELECT 1 FROM agent_research.companion_originals
            WHERE dialogue_id=:dialogue AND original_id=:original AND turn_id=:turn
        """), {"dialogue": dialogue_id, "original": original_id, "turn": turn_id})
        if origin.scalar_one_or_none() is None:
            raise CompanionConflict("STALE_DIALOGUE_VERSION")

    def read_original(
        self, actor: str, idea_id: str, dialogue_id: str, original_id: str,
        *, turn_id: str | None = None,
    ) -> str:
        _identity(actor, idea_id, dialogue_id, original_id)
        with self._engine.connect() as connection:
            _idea(connection, actor, idea_id)
            result = connection.execute(text("""
                SELECT o.original_text FROM agent_research.companion_originals o
                JOIN agent_research.companion_dialogues d USING (dialogue_id)
                WHERE d.idea_id=:idea AND o.dialogue_id=:dialogue AND o.original_id=:original
                  AND (CAST(:turn AS text) IS NULL OR o.turn_id=:turn)
            """), {"idea": idea_id, "dialogue": dialogue_id,
                    "original": original_id, "turn": turn_id})
            original = result.scalar_one_or_none()
            if original is None:
                raise CompanionConflict("COMPANION_NOT_FOUND")
            return original

    def change_dialogue(
        self, actor: str, idea_id: str, dialogue_id: str, *,
        expected_idea_version: int, expected_dialogue_version: int, key: str, command: bytes,
        change: Callable[[dict, DialogueRevision], DialogueChange],
        scope: ScopedConnection | None = None,
    ) -> DialogueRevision:
        _identity(actor, idea_id, dialogue_id, key)
        if not isinstance(command, bytes) or len(command) > 65536:
            raise CompanionConflict("INVALID_COMPANION_COMMAND")
        request = json.dumps(
            [actor, idea_id, dialogue_id, expected_idea_version, expected_dialogue_version],
            separators=(",", ":"),
        ).encode()
        key_hash = digest(self._key, b"companion-key-v1\0" + key.encode(), "sha256").hex()
        command_hash = digest(
            self._key, b"companion-command-v1\0" + request + b"\0" + command, "sha256",
        ).hex()
        with nullcontext(scope) if scope is not None else self._engine.begin() as connection:
            _lock_idea(connection, idea_id)
            current_idea = _idea(connection, actor, idea_id)
            previous = connection.execute(text("""
                SELECT version,idea_version,document,command_hash
                FROM agent_research.companion_dialogue_versions
                WHERE idea_id=:idea AND dialogue_id=:dialogue AND command_key=:key
            """), {"idea": idea_id, "dialogue": dialogue_id, "key": key_hash}).one_or_none()
            if previous is not None:
                if not compare_digest(previous.command_hash, command_hash):
                    raise CompanionConflict("IDEMPOTENCY_CONFLICT")
                return DialogueRevision(previous.version, previous.idea_version, previous.document)
            dialogue = self._dialogue(connection, idea_id, dialogue_id)
            if current_idea.version != expected_idea_version:
                raise CompanionConflict("STALE_IDEA_VERSION")
            if dialogue.version != expected_dialogue_version:
                raise CompanionConflict("STALE_DIALOGUE_VERSION")
            result = change(current_idea.document, dialogue)
            next_idea_version = current_idea.version
            if result.next_idea is not None:
                next_idea_version += 1
                _insert_idea(connection, idea_id, next_idea_version, result.next_idea)
            version = dialogue.version + 1
            stored = connection.execute(text("""
                INSERT INTO agent_research.companion_dialogue_versions
                  (dialogue_id,idea_id,version,previous_version,idea_version,document,
                   command_key,command_hash)
                VALUES (:dialogue,:idea,:version,:previous,:idea_version,CAST(:document AS jsonb),
                        :key,:hash)
                RETURNING document
            """), {"dialogue": dialogue_id, "idea": idea_id, "version": version,
                    "previous": dialogue.version, "idea_version": next_idea_version,
                    "document": _encode(result.document), "key": key_hash,
                    "hash": command_hash}).scalar_one()
            connection.execute(text("""
                INSERT INTO agent_research.companion_events (dialogue_id,version,code)
                VALUES (:dialogue,:version,:code)
            """), {"dialogue": dialogue_id, "version": version, "code": result.event})
            if result.original is not None:
                original_id, turn_id, original_text = result.original
                inserted = connection.execute(text("""
                    INSERT INTO agent_research.companion_originals
                      (dialogue_id,version,original_id,turn_id,original_text)
                    VALUES (:dialogue,:version,:original,:turn,:content)
                    ON CONFLICT DO NOTHING RETURNING original_id
                """), {"dialogue": dialogue_id, "version": version, "original": original_id,
                        "turn": turn_id, "content": original_text})
                if inserted.scalar_one_or_none() is None:
                    raise CompanionConflict("COMPANION_ID_CONFLICT")
            return DialogueRevision(version, next_idea_version, stored)

    @staticmethod
    def _dialogue(
        connection, idea_id: str, dialogue_id: str, version: int | None = None,
    ) -> DialogueRevision:
        row = connection.execute(text("""
            SELECT version,idea_version,document FROM agent_research.companion_dialogue_versions
            WHERE idea_id=:idea AND dialogue_id=:dialogue
              AND (CAST(:version AS bigint) IS NULL OR version=:version)
            ORDER BY version DESC LIMIT 1
        """), {"idea": idea_id, "dialogue": dialogue_id, "version": version}).one_or_none()
        if row is None:
            raise CompanionConflict("COMPANION_NOT_FOUND")
        return DialogueRevision(*row)
