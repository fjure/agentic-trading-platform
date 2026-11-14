"""Dedicated POSIX credential volume for Research Setup and the provider gateway."""

import json
import os
import re
import stat
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from uuid import uuid4

from services.llm_gateway.provider_wire import Provider

from .llm_credentials import CredentialBinding, CredentialDenied, CredentialState, CredentialStatus

ROOT = Path("/run/research-credentials")
SETUP_UID = 10004
GATEWAY_GID = 10002
_MAX_REVISION = 9007199254740991
_KEY = re.compile(r"[A-Za-z0-9._~+/=-]{1,2048}")
_ALLOCATION = re.compile(r"[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_STORAGE_ERROR = "CREDENTIAL_STORAGE_UNAVAILABLE"
_DENIED = "CREDENTIAL_REQUEST_DENIED"


@dataclass(frozen=True, slots=True)
class _Metadata:
    version: int
    generation: int
    allocation: str | None
    key_hash: str | None
    updated_at: datetime

    def encode(self) -> bytes:
        return json.dumps(
            [
                1, self.version, self.generation, self.allocation, self.key_hash,
                self.updated_at.isoformat(),
            ],
            separators=(",", ":"),
        ).encode("ascii")

    def status(self) -> CredentialStatus:
        stored = self.allocation is not None
        return CredentialStatus(
            self.version, CredentialState.STORED if stored else CredentialState.REVOKED,
            self.generation, self.updated_at, None if stored else self.updated_at,
        )

    @classmethod
    def parse(cls, value: bytes):
        try:
            schema, version, generation, allocation, key_hash, timestamp = json.loads(value)
            updated = datetime.fromisoformat(timestamp)
            if (
                type(schema) is not int or schema != 1
                or type(version) is not int or not 2 <= version <= _MAX_REVISION
                or type(generation) is not int or not 1 <= generation < version
                or (allocation is not None and (
                    not isinstance(allocation, str) or not _ALLOCATION.fullmatch(allocation)
                ))
                or (allocation is None) != (key_hash is None)
                or (key_hash is not None and (
                    not isinstance(key_hash, str) or not _DIGEST.fullmatch(key_hash)
                ))
                or updated.utcoffset() != UTC.utcoffset(None)
            ):
                raise ValueError
            parsed = cls(version, generation, allocation, key_hash, updated)
            if parsed.encode() != value:
                raise ValueError
            return parsed
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise CredentialDenied(_STORAGE_ERROR) from None


class _Volume:
    def __init__(
        self, root: Path = ROOT, *, owner_uid: int = SETUP_UID, reader_gid: int = GATEWAY_GID,
    ):
        self._root, self._owner, self._group = Path(root), owner_uid, reader_gid

    @contextmanager
    def _directory(self, *, write=False):
        if os.name != "posix" or not self._root.is_absolute():
            raise CredentialDenied(_STORAGE_ERROR)
        import fcntl

        directory = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open("/", flags)
            for component in self._root.parts[1:]:
                if component in {".", ".."}:
                    raise CredentialDenied(_STORAGE_ERROR)
                child = os.open(component, flags, dir_fd=directory)
                os.close(directory)
                directory = child
            info = os.fstat(directory)
            if (
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                != (self._owner, self._group, 0o2750)
                or (write and os.geteuid() != self._owner)
            ):
                raise CredentialDenied(_STORAGE_ERROR)
            operation = fcntl.LOCK_EX if write else fcntl.LOCK_SH
            fcntl.flock(directory, operation | fcntl.LOCK_NB)
            yield directory
        except OSError:
            raise CredentialDenied(_STORAGE_ERROR) from None
        finally:
            if directory is not None:
                os.close(directory)

    @staticmethod
    def _provider(provider: Provider) -> str:
        if type(provider) is not Provider:
            raise CredentialDenied(_DENIED)
        return provider.value.lower()

    def _check_file(self, info, limit):
        if (
            not stat.S_ISREG(info.st_mode)
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            != (self._owner, self._group, 0o640)
            or info.st_nlink != 1 or not 0 < info.st_size <= limit
        ):
            raise CredentialDenied(_STORAGE_ERROR)

    def _read(self, directory, filename, limit, *, optional=False) -> bytes | None:
        try:
            descriptor = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
        except FileNotFoundError:
            if optional:
                return None
            raise
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            self._check_file(info, limit)
            value = source.read(limit + 1)
            if len(value) != info.st_size:
                raise CredentialDenied(_STORAGE_ERROR)
        return value

    def _metadata(self, directory, provider: str) -> _Metadata | None:
        data = self._read(directory, f"{provider}.json", 512, optional=True)
        return _Metadata.parse(data) if data is not None else None

    @staticmethod
    def _key_name(provider, allocation):
        return f"{provider}-{allocation}.key"


class ResearchCredentialStore(_Volume):
    """Writer for the setup process. Status projections read metadata alone."""

    def authority_metadata(self, provider: Provider) -> tuple[CredentialStatus, str | None]:
        name = self._provider(provider)
        with self._directory() as directory:
            current = self._metadata(directory, name)
            if current is None:
                return (
                    CredentialStatus(
                        1, CredentialState.NOT_CONFIGURED, None, datetime.fromtimestamp(0, UTC),
                    ),
                    None,
                )
            digest = sha256(current.encode()).hexdigest() if current.allocation else None
            return current.status(), digest

    def status(self, provider: Provider) -> CredentialStatus:
        return self.authority_metadata(provider)[0]

    def save(
        self, provider: Provider, credential: str, *, expected_version: int,
    ) -> CredentialStatus:
        return self._change(provider, expected_version, None, self._validate_key(credential))

    def rotate(
        self, provider: Provider, credential: str, *, expected_version: int,
        expected_generation: int,
    ) -> CredentialStatus:
        return self._change(
            provider, expected_version, self._validate_generation(expected_generation),
            self._validate_key(credential),
        )

    def revoke(
        self, provider: Provider, *, expected_version: int, generation: int,
    ) -> CredentialStatus:
        return self._change(provider, expected_version, self._validate_generation(generation), None)

    @staticmethod
    def _validate_generation(generation: int) -> int:
        if type(generation) is not int or generation < 1:
            raise CredentialDenied(_DENIED)
        return generation

    @staticmethod
    def _validate_key(credential: str) -> bytes:
        if not isinstance(credential, str) or not _KEY.fullmatch(credential):
            raise CredentialDenied(_DENIED)
        return credential.encode("ascii")

    def _change(self, provider, expected_version, generation, credential):
        name = self._provider(provider)
        with self._directory(write=True) as directory:
            current = self._metadata(directory, name)
            version = current.version if current is not None else 1
            if type(expected_version) is not int or expected_version != version:
                raise CredentialDenied("VERSION_CONFLICT")
            if generation != (current.generation if current is not None else None):
                raise CredentialDenied("VERSION_CONFLICT")
            if current is not None and current.allocation is None and credential is None:
                return current.status()
            if version == _MAX_REVISION:
                raise CredentialDenied(_STORAGE_ERROR)
            allocation = uuid4().hex if credential is not None else None
            next_generation = (generation or 0) + int(credential is not None)
            key_hash = sha256(credential).hexdigest() if credential is not None else None
            updated = _Metadata(
                version + 1, next_generation, allocation, key_hash, datetime.now(UTC),
            )
            self._remove_inactive_keys(directory, name, current)
            if credential is not None:
                self._write(directory, self._key_name(name, allocation), credential)
                os.fsync(directory)
            temporary = f".{uuid4().hex}.tmp"
            self._write(directory, temporary, updated.encode())
            try:
                os.replace(temporary, f"{name}.json", src_dir_fd=directory, dst_dir_fd=directory)
            except OSError:
                os.unlink(temporary, dir_fd=directory)
                raise
            os.fsync(directory)
            self._remove_inactive_keys(directory, name, updated)
            return updated.status()

    def _remove_inactive_keys(self, directory, provider, current):
        active = self._key_name(provider, current.allocation) if current else None
        pattern = re.compile(rf"{provider}-[0-9a-f]{{32}}\.key")
        removed = False
        for name in os.listdir(directory):
            if name != active and pattern.fullmatch(name):
                os.unlink(name, dir_fd=directory)
                removed = True
        if removed:
            os.fsync(directory)

    def _write(self, directory, filename, value):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(filename, flags, 0o640, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as target:
                os.fchmod(target.fileno(), 0o640)
                target.write(value)
                target.flush()
                self._check_file(os.fstat(target.fileno()), len(value))
                os.fsync(target.fileno())
        except BaseException:
            os.unlink(filename, dir_fd=directory)
            raise


class ResearchCredentialReader(_Volume):
    """Read the exact currently authorized generation from a gateway-only mount."""

    def __init__(
        self, root: Path = ROOT, *, owner_uid: int = SETUP_UID, reader_gid: int = GATEWAY_GID,
        authorize: Callable[[CredentialBinding, int, str], bool],
    ):
        super().__init__(root, owner_uid=owner_uid, reader_gid=reader_gid)
        self._authorize = authorize

    def read_active(self, binding: CredentialBinding) -> str:
        if type(binding) is not CredentialBinding:
            raise CredentialDenied(_DENIED)
        name = self._provider(binding.provider)
        with self._directory() as directory:
            current = self._metadata(directory, name)
            if current is None or current.generation != binding.credential_generation:
                raise CredentialDenied(_DENIED)
            if current.allocation is None:
                raise CredentialDenied("CREDENTIAL_GENERATION_REVOKED")
            try:
                permitted = self._authorize(
                    binding, current.version, sha256(current.encode()).hexdigest(),
                )
            except Exception:
                raise CredentialDenied(_DENIED) from None
            if permitted is not True:
                raise CredentialDenied(_DENIED)
            key = self._read(directory, self._key_name(name, current.allocation), 2048)
            if not compare_digest(sha256(key).hexdigest(), current.key_hash):
                raise CredentialDenied(_STORAGE_ERROR)
            try:
                value = key.decode("ascii")
            except UnicodeError:
                raise CredentialDenied(_STORAGE_ERROR) from None
            if not _KEY.fullmatch(value):
                raise CredentialDenied(_STORAGE_ERROR)
            return value
