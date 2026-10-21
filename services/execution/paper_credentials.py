"""Execution-only reader for immutable private Alpaca generations.

No active-generation selection or promotion lives here. Production callers use
the fixed read-only mount; root/uid injection exists for synthetic tests only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from services.setup.store import MAX_BODY, ProviderStore, _pairs, generation_filename, validate


class PaperCredentialsUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("CREDENTIALS_MISSING")


@dataclass(frozen=True, slots=True)
class PrivatePaperCredentials:
    generation: int
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    expected_account_id: str = field(repr=False)

    def __post_init__(self):
        try:
            generation_filename(self.generation)
            validate("alpaca", {
                "apiKey": self.api_key, "apiSecret": self.api_secret,
                "expectedAccountId": self.expected_account_id, "paperOnly": True,
            })
        except Exception:
            raise PaperCredentialsUnavailable() from None


class PaperGenerationReader:
    def __init__(self, root: Path = Path("/run/provider"), *, owner_uid: int = 10001):
        self._store = ProviderStore(root, owner_uid=owner_uid)

    def load(self, generation: int) -> PrivatePaperCredentials:
        directory = descriptor = None
        try:
            filename = generation_filename(generation)
            directory = self._store._directory("alpaca")
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            before = os.fstat(descriptor)
            self._store._private(before)
            chunks = bytearray()
            while len(chunks) <= MAX_BODY:
                part = os.read(descriptor, min(4096, MAX_BODY + 1 - len(chunks)))
                if not part:
                    break
                chunks.extend(part)
            after = os.fstat(descriptor)
            self._store._private(after)
            if (len(chunks) != before.st_size or len(chunks) > MAX_BODY
                    or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError()
            value = json.loads(chunks, object_pairs_hook=_pairs)
            if not isinstance(value, dict) or set(value) != {
                "api_key", "api_secret", "expected_account_id"
            }:
                raise ValueError()
            return PrivatePaperCredentials(generation=generation, **value)
        except Exception:
            raise PaperCredentialsUnavailable() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory is not None:
                os.close(directory)
