"""Private credential lifecycle solely for the offline LLM test harness."""

import json
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from uuid import UUID, uuid4

from services.llm_gateway.provider_wire import Provider

_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_CANARY = re.compile(r"offline-canary-[A-Za-z0-9_-]{32}")
_MAX_GENERATIONS = 32
_PROBE_LIFETIME = timedelta(seconds=60)
_STORAGE_VERSION = 1


class CredentialDenied(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CredentialState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    STORED = "STORED"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    provider: Provider
    credential_generation: int
    profile_id: str
    profile_generation: int
    model_generation: int

    def __post_init__(self):
        versions = (self.credential_generation, self.profile_generation, self.model_generation)
        if (
            not isinstance(self.provider, Provider)
            or not isinstance(self.profile_id, str) or not _ID.fullmatch(self.profile_id)
            or any(type(version) is not int or version < 1 for version in versions)
        ):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    version: int
    state: CredentialState
    generation: int | None
    updated_at: datetime
    locally_revoked_at: datetime | None = None
    provider_revocation_state: str = "NOT_CONFIRMED"


@dataclass(frozen=True, slots=True)
class ProbeRef:
    probe_id: UUID
    binding: CredentialBinding
    expires_at: datetime


@dataclass(slots=True)
class _Generation:
    key: str | None = field(repr=False)
    state: CredentialState = CredentialState.STORED
    probe: ProbeRef | None = None
    probe_claimed: bool = False
    verified: ProbeRef | None = None
    revoked_at: datetime | None = None


@dataclass(slots=True)
class _ProviderState:
    version: int = 1
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    generations: dict[int, _Generation] = field(default_factory=dict, repr=False)


def _serialize(providers: dict[Provider, _ProviderState]) -> str:
    def encode(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return value.hex
        raise TypeError("invalid credential state")

    return json.dumps(
        {provider: asdict(state) for provider, state in providers.items()},
        default=encode, separators=(",", ":"),
    )


def _deserialize(payload: str) -> dict[Provider, _ProviderState]:
    def probe(value):
        if value is None:
            return None
        binding = value["binding"]
        binding["provider"] = Provider(binding["provider"])
        return ProbeRef(
            UUID(hex=value["probe_id"]), CredentialBinding(**binding),
            datetime.fromisoformat(value["expires_at"]),
        )

    providers = json.loads(payload)
    if providers.keys() != set(Provider):
        raise ValueError("invalid credential state")
    result = {}
    for name, item in providers.items():
        provider = Provider(name)
        if (
            type(item["version"]) is not int or item["version"] < 1
            or len(item["generations"]) > _MAX_GENERATIONS
        ):
            raise ValueError("invalid credential state")
        generations = {}
        for generation, entry in item["generations"].items():
            number = int(generation)
            state = CredentialState(entry["state"])
            key = entry["key"]
            if (
                state is CredentialState.NOT_CONFIGURED or type(entry["probe_claimed"]) is not bool
                or (state is CredentialState.REVOKED and key is not None)
                or (state is not CredentialState.REVOKED and (
                    not isinstance(key, str) or not _CANARY.fullmatch(key)
                ))
            ):
                raise ValueError("invalid credential state")
            pending, verified = probe(entry["probe"]), probe(entry["verified"])
            if any(
                ref is not None and (ref.binding.provider, ref.binding.credential_generation)
                != (provider, number)
                for ref in (pending, verified)
            ):
                raise ValueError("invalid credential state")
            generations[number] = _Generation(
                key=key, state=state, probe=pending, probe_claimed=entry["probe_claimed"],
                verified=verified,
                revoked_at=(
                    datetime.fromisoformat(entry["revoked_at"]) if entry["revoked_at"] else None
                ),
            )
        if generations.keys() != set(range(1, len(generations) + 1)):
            raise ValueError("invalid credential state")
        result[provider] = _ProviderState(
            item["version"], datetime.fromisoformat(item["updated_at"]), generations,
        )
    return result


def _check_private_path(path: Path, *, existing: bool = True) -> None:
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or path.parent.resolve() != path.parent:
        raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")
    if os.name == "posix" and (parent.st_uid != os.geteuid() or parent.st_mode & 0o077):
        raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")
    if existing:
        file = path.lstat()
        if not stat.S_ISREG(file.st_mode) or file.st_nlink != 1:
            raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")
        if os.name == "posix" and (file.st_uid != os.geteuid() or file.st_mode & 0o077):
            raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")


class SyntheticCredentialStore:
    """Only generates synthetic canaries. No input path for production keys."""

    def __init__(self, path: Path | None = None):
        self._providers = {provider: _ProviderState() for provider in Provider}
        self._lock = Lock()
        self._path = None if path is None else Path(path).absolute()
        if self._path is not None:
            with self._transaction():
                pass

    @classmethod
    def provision(cls, path: Path) -> "SyntheticCredentialStore":
        """Create the private synthetic store once; reopening never creates missing data."""
        path = Path(path).absolute()
        try:
            _check_private_path(path, existing=False)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(path, flags, 0o600))
            with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as connection:
                connection.execute("PRAGMA synchronous=EXTRA")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("""
                        CREATE TABLE credential_state (
                            singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                            payload TEXT NOT NULL
                        ) STRICT
                    """)
                    connection.execute(
                        "INSERT INTO credential_state VALUES (1,?)",
                        (_serialize({provider: _ProviderState() for provider in Provider}),),
                    )
                    connection.execute(f"PRAGMA user_version={_STORAGE_VERSION}")
        except (OSError, sqlite3.Error):
            raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE") from None
        return cls(path)

    @contextmanager
    def _transaction(self):
        if self._path is None:
            with self._lock:
                yield self._providers
            return
        try:
            _check_private_path(self._path)
            uri = self._path.as_uri() + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
                connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 262144)
                connection.execute("PRAGMA synchronous=EXTRA")
                connection.execute("PRAGMA secure_delete=ON")
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    if connection.execute("PRAGMA user_version").fetchone() != (_STORAGE_VERSION,):
                        raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")
                    row = connection.execute(
                        "SELECT payload FROM credential_state WHERE singleton=1",
                    ).fetchone()
                    if row is None:
                        raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE")
                    try:
                        providers = _deserialize(row[0])
                    except (AttributeError, KeyError, TypeError, ValueError):
                        raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE") from None
                    yield providers
                    updated = _serialize(providers)
                    if updated != row[0]:
                        connection.execute(
                            "UPDATE credential_state SET payload=? WHERE singleton=1", (updated,),
                        )
        except (OSError, sqlite3.Error):
            raise CredentialDenied("CREDENTIAL_STORAGE_UNAVAILABLE") from None

    @staticmethod
    def _provider(providers: dict[Provider, _ProviderState], provider: Provider) -> _ProviderState:
        if not isinstance(provider, Provider):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        return providers[provider]

    @staticmethod
    def _check_version(state: _ProviderState, expected_version: int) -> None:
        if type(expected_version) is not int or expected_version != state.version:
            raise CredentialDenied("VERSION_CONFLICT")

    @staticmethod
    def _record(state: _ProviderState) -> None:
        state.version += 1
        state.updated_at = datetime.now(UTC)

    @staticmethod
    def _status(state: _ProviderState, generation: int | None) -> CredentialStatus:
        if generation is None:
            return CredentialStatus(state.version, CredentialState.NOT_CONFIGURED, None,
                                    state.updated_at)
        entry = state.generations.get(generation)
        if entry is None:
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        return CredentialStatus(state.version, entry.state, generation, state.updated_at,
                                entry.revoked_at)

    def status(self, provider: Provider, generation: int | None = None) -> CredentialStatus:
        with self._transaction() as providers:
            state = self._provider(providers, provider)
            if generation is None:
                generation = max(state.generations, default=None)
            elif type(generation) is not int or generation < 1:
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            return self._status(state, generation)

    @staticmethod
    def _create(state: _ProviderState) -> CredentialStatus:
        generation = len(state.generations) + 1
        if generation > _MAX_GENERATIONS:
            raise CredentialDenied("BUDGET_EXHAUSTED")
        state.generations[generation] = _Generation(f"offline-canary-{token_urlsafe(24)}")
        SyntheticCredentialStore._record(state)
        return SyntheticCredentialStore._status(state, generation)

    def save(self, provider: Provider, *, expected_version: int) -> CredentialStatus:
        with self._transaction() as providers:
            state = self._provider(providers, provider)
            self._check_version(state, expected_version)
            if state.generations:
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            return self._create(state)

    def rotate(
        self, provider: Provider, *, expected_version: int, expected_generation: int,
    ) -> CredentialStatus:
        with self._transaction() as providers:
            state = self._provider(providers, provider)
            self._check_version(state, expected_version)
            if (
                type(expected_generation) is not int or not state.generations
                or expected_generation != max(state.generations)
            ):
                raise CredentialDenied("VERSION_CONFLICT")
            return self._create(state)

    def _lookup(
        self, providers: dict[Provider, _ProviderState], binding: CredentialBinding,
    ) -> tuple[_ProviderState, _Generation]:
        if not isinstance(binding, CredentialBinding):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        state = self._provider(providers, binding.provider)
        entry = state.generations.get(binding.credential_generation)
        if entry is None:
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        if entry.state is CredentialState.REVOKED:
            raise CredentialDenied("CREDENTIAL_GENERATION_REVOKED")
        return state, entry

    def begin_probe(self, binding: CredentialBinding, *, expected_version: int) -> ProbeRef:
        with self._transaction() as providers:
            state, entry = self._lookup(providers, binding)
            self._check_version(state, expected_version)
            now = datetime.now(UTC)
            if (
                entry.state not in {CredentialState.STORED, CredentialState.VERIFIED}
                or (entry.probe is not None and entry.probe.expires_at > now)
            ):
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            entry.probe = ProbeRef(uuid4(), binding, now + _PROBE_LIFETIME)
            entry.probe_claimed = False
            entry.verified = None
            entry.state = CredentialState.STORED
            self._record(state)
            return entry.probe

    def take_probe(self, probe: ProbeRef) -> str:
        """Only the offline gateway double should consume this one-shot probe key."""
        if not isinstance(probe, ProbeRef):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        with self._transaction() as providers:
            _, entry = self._lookup(providers, probe.binding)
            if (
                entry.probe != probe or probe.expires_at <= datetime.now(UTC)
                or entry.probe_claimed or entry.key is None
            ):
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            entry.probe_claimed = True
            return entry.key

    def record_probe(self, probe: ProbeRef, *, passed: bool) -> CredentialStatus:
        if not isinstance(probe, ProbeRef):
            raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
        with self._transaction() as providers:
            state, entry = self._lookup(providers, probe.binding)
            if (
                type(passed) is not bool or entry.probe != probe
                or probe.expires_at <= datetime.now(UTC) or not entry.probe_claimed
            ):
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            entry.probe = None
            entry.probe_claimed = False
            if passed:
                entry.verified = probe
                entry.state = CredentialState.VERIFIED
            self._record(state)
            return self._status(state, probe.binding.credential_generation)

    def activate(
        self, binding: CredentialBinding, *, expected_version: int, verified_probe_id: UUID,
    ) -> CredentialStatus:
        with self._transaction() as providers:
            state, entry = self._lookup(providers, binding)
            self._check_version(state, expected_version)
            if (
                entry.state is not CredentialState.VERIFIED or entry.verified is None
                or entry.verified.binding != binding or entry.verified.probe_id != verified_probe_id
            ):
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            for previous in state.generations.values():
                if previous.state is CredentialState.ACTIVE:
                    previous.state = CredentialState.STORED
                    previous.verified = None
            entry.state = CredentialState.ACTIVE
            self._record(state)
            return self._status(state, binding.credential_generation)

    def read_active(self, binding: CredentialBinding) -> str:
        with self._transaction() as providers:
            _, entry = self._lookup(providers, binding)
            if (
                entry.state is not CredentialState.ACTIVE or entry.verified is None
                or entry.verified.binding != binding or entry.key is None
            ):
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            return entry.key

    def revoke(
        self, provider: Provider, *, expected_version: int, generation: int,
    ) -> CredentialStatus:
        with self._transaction() as providers:
            state = self._provider(providers, provider)
            self._check_version(state, expected_version)
            if type(generation) is not int or generation not in state.generations:
                raise CredentialDenied("CREDENTIAL_REQUEST_DENIED")
            entry = state.generations[generation]
            if entry.state is CredentialState.REVOKED:
                raise CredentialDenied("CREDENTIAL_GENERATION_REVOKED")
            entry.key = None
            entry.probe = None
            entry.probe_claimed = False
            entry.verified = None
            entry.state = CredentialState.REVOKED
            entry.revoked_at = datetime.now(UTC)
            self._record(state)
            return self._status(state, generation)
