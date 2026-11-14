"""Deterministic custodian for a versioned, encrypted synthetic holdout."""

import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .catalog import _parse_json
from .custody import attestation_message

_RELEASE_ARTIFACTS = ("holdout.v1.json", "split-manifest.v1.json")
_ARTIFACTS = (
    *_RELEASE_ARTIFACTS, "primary-review.json", "secondary-review.json", "development.v1.json",
)
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_ARTIFACT = 2**21
_DB_NAME = "custody.sqlite"


class CustodyDenied(RuntimeError):
    """Report a custody failure without revealing private inputs."""


def _private_path(path: Path, directory: bool = False) -> None:
    path = path.absolute()
    info = path.lstat()
    if (
        path.resolve() != path or info.st_uid != os.geteuid()
        or (directory and (
            not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
        ))
        or (not directory and (
            not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ))
    ):
        raise CustodyDenied("CUSTODY_PRIVATE_STORAGE_REQUIRED")


def _read_key(path: Path) -> bytes:
    _private_path(path.parent, directory=True)
    _private_path(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise CustodyDenied("CUSTODY_KEY_INVALID")
    return key


def _encode(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")


def _aad(revision: str, holdout_hash: str, manifest_hash: str) -> bytes:
    return b"atl-custodian-v1\0" + _encode({
        "revision": revision, "holdoutSha256": holdout_hash, "manifestSha256": manifest_hash,
    })


def _validate_artifacts(artifacts: dict[str, bytes]) -> tuple[str, str]:
    if (
        type(artifacts) is not dict or set(artifacts) != set(_ARTIFACTS)
        or any(type(data) is not bytes or not 1 <= len(data) <= _MAX_ARTIFACT
               for data in artifacts.values())
    ):
        raise CustodyDenied("CUSTODY_ARTIFACT_INVALID")
    holdout_hash, manifest_hash = (sha256(artifacts[key]).hexdigest() for key in _RELEASE_ARTIFACTS)
    try:
        holdout, manifest = (_parse_json(artifacts[key]) for key in _RELEASE_ARTIFACTS)
        rows, identities = holdout["cases"], manifest["cases"]
        if (
            type(holdout["version"]) is not int or holdout["version"] != 1
            or type(manifest["version"]) is not int or manifest["version"] != 1
            or manifest["holdoutSha256"] != holdout_hash
            or manifest["developmentSha256"] != sha256(artifacts["development.v1.json"]).hexdigest()
            or type(rows) is not list or not rows or type(identities) is not list
            or any(case["split"] != "holdout" for case in rows)
        ):
            raise ValueError
        fields = ("caseId", "version", "split", "group", "cluster", "offlineOnly")
        selected = [{key: case[key] for key in fields} for case in rows]
        published = [row for row in identities if row["split"] == "holdout"]
        if (
            len({case["caseId"] for case in selected}) != len(selected)
            or sorted(selected, key=lambda case: case["caseId"])
            != sorted(published, key=lambda case: case["caseId"])
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        raise CustodyDenied("CUSTODY_ARTIFACT_INVALID") from None
    return holdout_hash, manifest_hash


class Custodian:
    """Encrypt with an externally provisioned key and enforce one release per revision."""

    def __init__(
        self, directory: Path, *, development_uid: int, reviewer_uid: int,
        encryption_key_file: Path, signer_key_file: Path, release_ledger: Path | None = None,
        _provision: bool = False,
    ):
        if os.name != "posix" or (
            any(type(uid) is not int or uid <= 0 for uid in (development_uid, reviewer_uid))
            or os.geteuid() == 0 or len({os.geteuid(), development_uid, reviewer_uid}) != 3
        ):
            raise CustodyDenied("CUSTODY_ISOLATION_REQUIRED")
        self._path = directory.absolute() / _DB_NAME
        self._release_ledger = (release_ledger or directory).absolute()
        try:
            _private_path(directory, directory=True)
            _private_path(self._release_ledger, directory=True)
            signer = _read_key(signer_key_file)
            encryption = _read_key(encryption_key_file)
            if signer == encryption:
                raise CustodyDenied("CUSTODY_KEY_INVALID")
            self._signer = Ed25519PrivateKey.from_private_bytes(signer)
            self._cipher = AESGCM(encryption)
            if _provision:
                self._initialize()
            with self._connection():
                pass
        except (OSError, sqlite3.Error, ValueError):
            raise CustodyDenied("CUSTODY_STORE_UNAVAILABLE") from None

    @classmethod
    def provision(
        cls, directory: Path, *, development_uid: int, reviewer_uid: int,
        encryption_key_file: Path, signer_key_file: Path, release_ledger: Path | None = None,
    ) -> "Custodian":
        return cls(
            directory, development_uid=development_uid, reviewer_uid=reviewer_uid,
            encryption_key_file=encryption_key_file, signer_key_file=signer_key_file,
            release_ledger=release_ledger, _provision=True,
        )

    def _initialize(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        os.close(os.open(self._path, flags, 0o600))
        with closing(sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True)) as conn:
            conn.execute("PRAGMA synchronous=EXTRA")
            conn.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE revisions (
                    id TEXT PRIMARY KEY,
                    holdout_sha TEXT NOT NULL UNIQUE,
                    manifest_sha TEXT NOT NULL,
                    nonce BLOB NOT NULL,
                    ciphertext BLOB NOT NULL,
                    released_at TEXT,
                    attestation_sha TEXT
                ) STRICT;
                PRAGMA user_version=1;
                COMMIT;
            """)

    @contextmanager
    def _connection(self):
        try:
            _private_path(self._path.parent, directory=True)
            _private_path(self._path)
            for suffix in ("-journal", "-wal", "-shm"):
                try:
                    _private_path(self._path.with_name(self._path.name + suffix))
                except FileNotFoundError:
                    pass
            with closing(sqlite3.connect(
                self._path.as_uri() + "?mode=rw", uri=True, timeout=5,
            )) as conn:
                conn.execute("PRAGMA synchronous=EXTRA")
                if conn.execute("PRAGMA user_version").fetchone() != (1,):
                    raise CustodyDenied("CUSTODY_STORE_UNAVAILABLE")
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    yield conn
        except (OSError, sqlite3.Error):
            raise CustodyDenied("CUSTODY_STORE_UNAVAILABLE") from None

    def seal(self, revision: str, artifacts: dict[str, bytes]) -> tuple[str, str]:
        if type(revision) is not str or not _REVISION.fullmatch(revision):
            raise CustodyDenied("CUSTODY_REVISION_INVALID")
        holdout_hash, manifest_hash = _validate_artifacts(artifacts)
        try:
            raw = _encode({key: artifacts[key].decode("utf-8") for key in _ARTIFACTS})
        except ValueError:
            raise CustodyDenied("CUSTODY_ARTIFACT_INVALID") from None
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, raw, _aad(revision, holdout_hash, manifest_hash))
        with self._connection() as conn:
            inserted = conn.execute(
                """INSERT OR IGNORE INTO revisions(id,holdout_sha,manifest_sha,nonce,ciphertext)
                   VALUES(?,?,?,?,?)""", (revision, holdout_hash, manifest_hash, nonce, ciphertext),
            )
            if inserted.rowcount != 1:
                raise CustodyDenied("CUSTODY_REVISION_EXISTS")
        return holdout_hash, manifest_hash

    def _burn(self, holdout_hash: str) -> None:
        # This ledger is restored independently of the encrypted payload database.
        try:
            _private_path(self._release_ledger, directory=True)
            flags = os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY
            marker = self._release_ledger / f"released-{holdout_hash}"
            with os.fdopen(os.open(marker, flags, 0o600), "wb") as marker_file:
                os.fsync(marker_file.fileno())
            directory = os.open(self._release_ledger, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            raise CustodyDenied("CUSTODY_RELEASE_UNAVAILABLE") from None
        except OSError:
            raise CustodyDenied("CUSTODY_STORE_UNAVAILABLE") from None

    def audit(self, revision: str) -> dict | None:
        """Return only verification hashes and timestamps."""
        with self._connection() as conn:
            row = conn.execute(
                """SELECT holdout_sha,manifest_sha,released_at,attestation_sha
                   FROM revisions WHERE id=?""", (revision,),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(
            ("holdoutSha256", "manifestSha256", "releasedAt", "attestationSha256"),
            row, strict=True,
        ))

    def release(
        self, revision: str, *, verify: Callable[[dict[str, bytes]], bytes],
    ) -> dict[str, bytes]:
        """Release bytes after verification and commit; a crashed delivery stays consumed."""
        if type(revision) is not str or not _REVISION.fullmatch(revision):
            raise CustodyDenied("CUSTODY_REVISION_INVALID")
        with self._connection() as conn:
            record = conn.execute(
                """SELECT holdout_sha,manifest_sha,nonce,ciphertext FROM revisions
                   WHERE id=? AND released_at IS NULL""", (revision,),
            ).fetchone()
            if record is None:
                raise CustodyDenied("CUSTODY_RELEASE_UNAVAILABLE")
            holdout_hash, manifest_hash, nonce, ciphertext = record
            try:
                plaintext = self._cipher.decrypt(
                    nonce, ciphertext, _aad(revision, holdout_hash, manifest_hash),
                )
                decoded = _parse_json(plaintext)
                artifacts = {key: decoded[key].encode("utf-8") for key in _ARTIFACTS}
                if _validate_artifacts(artifacts) != (holdout_hash, manifest_hash):
                    raise ValueError
                document = _parse_json(verify(artifacts.copy()))
                if (
                    document["holdoutSha256"] != holdout_hash
                    or document["manifestSha256"] != manifest_hash
                ):
                    raise ValueError
                attestation = _encode(document)
                signature = self._signer.sign(attestation_message(document))
            except (InvalidTag, ValueError, KeyError, TypeError, AttributeError, RecursionError):
                raise CustodyDenied("CUSTODY_RELEASE_UNVERIFIED") from None
            self._burn(holdout_hash)
            conn.execute(
                "UPDATE revisions SET released_at=?,attestation_sha=? WHERE id=?",
                (datetime.now(UTC).isoformat(), sha256(attestation).hexdigest(), revision),
            )
        return {key: artifacts[key] for key in _RELEASE_ARTIFACTS} | {
            "reviewer-attestation.json": attestation,
            "reviewer-attestation.sig": signature,
        }
