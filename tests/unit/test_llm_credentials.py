"""Offline, generation-bound LLM credential lifecycle."""

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from threading import Barrier
from uuid import uuid4

import pytest

from services.llm_gateway.provider_wire import Provider
from services.setup.llm_credentials import (
    CredentialBinding,
    CredentialDenied,
    CredentialState,
    SyntheticCredentialStore,
)


def _binding(provider=Provider.OPENAI, generation=1):
    return CredentialBinding(provider, generation, "offline-profile", 1, 1)


def _verify(store, binding, version):
    probe = store.begin_probe(binding, expected_version=version)
    canary = store.take_probe(probe)
    verified = store.record_probe(probe, passed=True)
    assert verified.state is CredentialState.VERIFIED
    return probe, canary, verified


def test_store_probe_and_activate_remain_separate_and_exactly_bound():
    store = SyntheticCredentialStore()
    binding = _binding()
    initial = store.status(binding.provider)
    assert initial.state is CredentialState.NOT_CONFIGURED
    stored = store.save(binding.provider, expected_version=initial.version)
    assert stored.state is CredentialState.STORED and stored.generation == 1
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.read_active(binding)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.activate(binding, expected_version=stored.version, verified_probe_id=uuid4())
    probe, canary, verified = _verify(store, binding, stored.version)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.take_probe(probe)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.activate(
            replace(binding, model_generation=2), expected_version=verified.version,
            verified_probe_id=probe.probe_id,
        )
    active = store.activate(
        binding, expected_version=verified.version, verified_probe_id=probe.probe_id,
    )
    assert active.state is CredentialState.ACTIVE and store.read_active(binding) == canary
    assert canary.startswith("offline-canary-")
    assert all(canary not in repr(record) for record in (asdict(active), probe, store))


def test_failed_or_unclaimed_probe_does_not_verify_generation():
    store = SyntheticCredentialStore()
    binding = _binding()
    stored = store.save(binding.provider, expected_version=1)
    probe = store.begin_probe(binding, expected_version=stored.version)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.record_probe(probe, passed=True)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.take_probe(replace(probe, probe_id=uuid4()))
    store.take_probe(probe)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.take_probe(probe)
    failed = store.record_probe(probe, passed=False)
    assert failed.state is CredentialState.STORED
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.activate(binding, expected_version=failed.version, verified_probe_id=probe.probe_id)


def test_rotation_and_revocation_never_substitute_credential_generations():
    store = SyntheticCredentialStore()
    old = _binding()
    new = replace(old, credential_generation=2)
    first = store.save(old.provider, expected_version=1)
    probe, old_canary, verified = _verify(store, old, first.version)
    active = store.activate(
        old, expected_version=verified.version, verified_probe_id=probe.probe_id,
    )
    rotating = store.rotate(old.provider, expected_version=active.version, expected_generation=1)
    assert rotating.generation == 2 and rotating.state is CredentialState.STORED
    assert store.read_active(old) == old_canary
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.read_active(new)
    probe, new_canary, verified = _verify(store, new, rotating.version)
    active = store.activate(
        new, expected_version=verified.version, verified_probe_id=probe.probe_id,
    )
    assert old_canary != new_canary == store.read_active(new)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        store.read_active(old)
    revoked = store.revoke(old.provider, expected_version=active.version, generation=1)
    assert revoked.state is CredentialState.REVOKED and revoked.locally_revoked_at is not None
    assert revoked.provider_revocation_state == "NOT_CONFIRMED"
    assert store.read_active(new) == new_canary
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_GENERATION_REVOKED$"):
        store.read_active(old)
    assert store.status(Provider.ANTHROPIC).state is CredentialState.NOT_CONFIGURED


def test_concurrent_versioned_rotation_has_one_winner():
    store = SyntheticCredentialStore()
    provider = Provider.ANTHROPIC
    stored = store.save(provider, expected_version=1)
    start = Barrier(2)

    def rotate():
        start.wait(timeout=5)
        try:
            store.rotate(provider, expected_version=stored.version, expected_generation=1)
        except CredentialDenied as error:
            return error.code
        return "ROTATED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: rotate(), range(2))) == ["ROTATED", "VERSION_CONFLICT"]
    assert store.status(provider).generation == 2


def test_private_store_restarts_without_releasing_probe_or_revoked_generation(tmp_path):
    path = tmp_path / "llm-credentials.sqlite"
    store = SyntheticCredentialStore.provision(path)
    binding = _binding()
    stored = store.save(binding.provider, expected_version=1)
    probe = store.begin_probe(binding, expected_version=stored.version)
    canary = SyntheticCredentialStore(path).take_probe(probe)
    reopened = SyntheticCredentialStore(path)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_REQUEST_DENIED$"):
        reopened.take_probe(probe)
    verified = reopened.record_probe(probe, passed=True)
    active = store.activate(
        binding, expected_version=verified.version, verified_probe_id=probe.probe_id,
    )
    assert reopened.read_active(binding) == canary
    revoked = reopened.revoke(binding.provider, expected_version=active.version, generation=1)
    assert SyntheticCredentialStore(path).status(binding.provider) == revoked
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_GENERATION_REVOKED$"):
        store.read_active(binding)
    with closing(sqlite3.connect(path)) as connection:
        payload = connection.execute("SELECT payload FROM credential_state").fetchone()[0]
    if canary in payload or canary in repr(asdict(revoked)):
        pytest.fail("revoked canary remained in current private state or public status")


def test_persistent_rotation_serializes_independent_store_instances(tmp_path):
    path = tmp_path / "llm-credentials.sqlite"
    first = SyntheticCredentialStore.provision(path)
    second = SyntheticCredentialStore(path)
    provider = Provider.ANTHROPIC
    stored = first.save(provider, expected_version=1)
    start = Barrier(2)

    def rotate(store):
        start.wait(timeout=5)
        try:
            store.rotate(provider, expected_version=stored.version, expected_generation=1)
        except CredentialDenied as error:
            return error.code
        return "ROTATED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(rotate, (first, second))) == ["ROTATED", "VERSION_CONFLICT"]
    assert SyntheticCredentialStore(path).status(provider).generation == 2


def test_missing_or_corrupt_credential_store_fails_closed(tmp_path):
    path = tmp_path / "llm-credentials.sqlite"
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_STORAGE_UNAVAILABLE$"):
        SyntheticCredentialStore(path)
    assert not path.exists()
    path.write_bytes(b"invalid")
    for open_store in (SyntheticCredentialStore, SyntheticCredentialStore.provision):
        with pytest.raises(CredentialDenied, match="^CREDENTIAL_STORAGE_UNAVAILABLE$"):
            open_store(path)
    assert path.read_bytes() == b"invalid"


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX mode semantics")
def test_persistent_store_requires_owner_only_file(tmp_path):
    path = tmp_path / "llm-credentials.sqlite"
    store = SyntheticCredentialStore.provision(path)
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(CredentialDenied, match="^CREDENTIAL_STORAGE_UNAVAILABLE$"):
        store.status(Provider.OPENAI)
