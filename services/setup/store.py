"""Owner-only atomic storage; public status uses metadata only, never credential reads."""

from __future__ import annotations

import json
import os
import stat
import unicodedata
from pathlib import Path
from uuid import UUID, uuid4

MAX_BODY = 16 * 1024
PROVIDERS = frozenset({"alpaca", "jev"})
FILENAME = "credentials.json"


class SetupInvalid(ValueError):
    def __init__(self):
        super().__init__("INVALID_REQUEST")


class SetupUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("TEMPORARILY_UNAVAILABLE")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SetupInvalid()
        result[key] = value
    return result


def validate(provider: str, value: object) -> dict[str, str]:
    """Return the fixed private wire shape. Validation errors never include input."""
    if provider not in PROVIDERS or not isinstance(value, dict):
        raise SetupInvalid()
    expected = {"apiKey", "apiSecret", "expectedAccountId", "paperOnly"}
    if set(value) != (expected if provider == "alpaca" else {"apiKey"}):
        raise SetupInvalid()
    fields = ("apiKey", "apiSecret") if provider == "alpaca" else ("apiKey",)
    for field in fields:
        item = value[field]
        if (not isinstance(item, str) or not 1 <= len(item) <= 512
                or any(unicodedata.category(char).startswith("C") for char in item)
                or item != item.strip()):
            raise SetupInvalid()
    result = {"api_key": value["apiKey"]}
    if provider == "alpaca":
        account = value["expectedAccountId"]
        try:
            canonical = str(UUID(account)) if isinstance(account, str) else None
        except (ValueError, AttributeError):
            raise SetupInvalid() from None
        if account != canonical or canonical is None or value["paperOnly"] is not True:
            raise SetupInvalid()
        result.update(api_secret=value["apiSecret"], expected_account_id=account)
    return result


def parse_body(provider: str, body: bytes) -> dict:
    if not body or len(body) > MAX_BODY:
        raise SetupInvalid()
    try:
        value = json.loads(body, object_pairs_hook=_pairs)
        validate(provider, value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SetupInvalid() from None


class ProviderStore:
    def __init__(self, root: Path = Path("/run/provider"), *, owner_uid: int = 10001):
        # Injection exists for synthetic filesystem tests, never from HTTP or environment.
        self._root = root
        self._uid = owner_uid

    def _private(self, info, *, directory=False):
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not valid_type or info.st_uid != self._uid
                or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                or (not directory and (info.st_nlink != 1 or not 0 < info.st_size <= MAX_BODY))):
            raise SetupUnavailable()

    def _directory(self, provider: str) -> int:
        if provider not in PROVIDERS or os.name != "posix" or not self._root.is_absolute():
            raise SetupUnavailable()
        # Resolve every component through no-follow directory descriptors. Path.resolve
        # would follow symlinks before they can be inspected.
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in (*self._root.parts[1:], provider):
                if component in {".", ".."}:
                    raise SetupUnavailable()
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            self._private(os.fstat(descriptor), directory=True)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _exists(self, directory: int) -> bool:
        try:
            info = os.stat(FILENAME, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        self._private(info)
        return True

    def status(self) -> dict:
        result = {}
        try:
            for provider in sorted(PROVIDERS):
                directory = self._directory(provider)
                try:
                    configured = self._exists(directory)
                finally:
                    os.close(directory)
                result[provider] = {
                    "status": "CONFIGURED" if configured else "UNCONFIGURED",
                    "restartRequired": configured,
                }
            return result
        except Exception:
            raise SetupUnavailable() from None

    def save(self, provider: str, value: dict) -> dict:
        private = validate(provider, value)
        payload = json.dumps(private, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        # Escaped JSON can be larger than the public Unicode request.
        if len(payload) > MAX_BODY:
            raise SetupInvalid()
        directory = None
        temporary = "." + uuid4().hex + ".tmp"
        created = False
        try:
            directory = self._directory(provider)
            self._exists(directory)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            created = True
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(payload)
                    stream.flush()
                self._private(os.fstat(descriptor))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._private(os.fstat(directory), directory=True)
            self._exists(directory)
            os.replace(temporary, FILENAME, src_dir_fd=directory, dst_dir_fd=directory)
            created = False
            os.fsync(directory)
        except Exception:
            raise SetupUnavailable() from None
        finally:
            if directory is not None:
                try:
                    if created:
                        os.unlink(temporary, dir_fd=directory)
                finally:
                    os.close(directory)
        return {"status": "CONFIGURED", "restartRequired": True}
