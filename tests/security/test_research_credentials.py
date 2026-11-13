"""Synthetic credential generation, reader authorization and private POSIX volume tests."""

import os
from pathlib import Path

import pytest

from services.llm_gateway.provider_wire import Provider
from services.setup.llm_credentials import CredentialBinding, CredentialDenied, CredentialState
from services.setup.research_credentials import ResearchCredentialReader, ResearchCredentialStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires Linux volume semantics")
_PROVIDER = Provider.OPENAI
_FIRST = "synthetic-provider-fixture-first"
_SECOND = "synthetic-provider-fixture-second"


@pytest.fixture
def volume(tmp_path):
    root = tmp_path / "credentials"
    root.mkdir()
    root.chmod(0o2750)
    roles = {"owner_uid": os.getuid(), "reader_gid": os.getgid()}
    return root, ResearchCredentialStore(root, **roles), roles


def binding(generation):
    return CredentialBinding(_PROVIDER, generation, "synthetic-profile", 1, 1)


def test_generation_lifecycle_requires_exact_external_authority(volume, monkeypatch):
    root, store, roles = volume
    assert store.status(_PROVIDER).state is CredentialState.NOT_CONFIGURED
    first = store.save(_PROVIDER, _FIRST, expected_version=1)
    assert (first.state, first.version, first.generation) == (CredentialState.STORED, 2, 1)
    status, digest = store.authority_metadata(_PROVIDER)
    assert status == first
    reader = ResearchCredentialReader(
        root, **roles, authorize=lambda candidate, version, fingerprint:
        (candidate, version, fingerprint) == (binding(1), 2, digest),
    )
    with monkeypatch.context() as patched:
        read = store._read

        def metadata_only(directory, filename, *args, **kwargs):
            assert filename.endswith(".json")
            return read(directory, filename, *args, **kwargs)

        patched.setattr(store, "_read", metadata_only)
        assert store.status(_PROVIDER) == first
    assert reader.read_active(binding(1)) == _FIRST
    assert _FIRST.encode() not in (root / "openai.json").read_bytes()
    second = store.rotate(_PROVIDER, _SECOND, expected_version=2, expected_generation=1)
    assert (second.state, second.version, second.generation) == (CredentialState.STORED, 3, 2)
    assert [path.read_text() for path in root.glob("*.key")] == [_SECOND]
    for generation in (1, 2):
        with pytest.raises(CredentialDenied, match="CREDENTIAL_REQUEST_DENIED"):
            reader.read_active(binding(generation))
    revoked = store.revoke(_PROVIDER, expected_version=3, generation=2)
    assert revoked.state is CredentialState.REVOKED and revoked.locally_revoked_at
    assert not list(root.glob("*.key"))
    assert store.revoke(_PROVIDER, expected_version=4, generation=2) == revoked
    with pytest.raises(CredentialDenied, match="CREDENTIAL_GENERATION_REVOKED"):
        reader.read_active(binding(2))


def test_restore_cannot_reauthorize_previously_bound_key(volume):
    root, store, roles = volume
    store.save(_PROVIDER, _FIRST, expected_version=1)
    backup = {path.name: path.read_bytes() for path in root.iterdir()}
    store.revoke(_PROVIDER, expected_version=2, generation=1)
    store.rotate(_PROVIDER, _SECOND, expected_version=3, expected_generation=1)
    current, digest = store.authority_metadata(_PROVIDER)
    reader = ResearchCredentialReader(
        root, **roles, authorize=lambda candidate, version, fingerprint:
        (candidate, version, fingerprint) == (binding(2), current.version, digest),
    )
    assert reader.read_active(binding(2)) == _SECOND
    for path in root.iterdir():
        path.unlink()
    for name, payload in backup.items():
        path = root / name
        path.write_bytes(payload)
        path.chmod(0o640)
    for generation in (1, 2):
        with pytest.raises(CredentialDenied):
            reader.read_active(binding(generation))


def test_stale_version_bad_key_and_unsafe_metadata_fail_closed(volume):
    root, store, _ = volume
    with pytest.raises(CredentialDenied, match="CREDENTIAL_REQUEST_DENIED"):
        store.save(_PROVIDER, "invalid\nkey", expected_version=1)
    store.save(_PROVIDER, _FIRST, expected_version=1)
    with pytest.raises(CredentialDenied, match="VERSION_CONFLICT"):
        store.rotate(_PROVIDER, _SECOND, expected_version=1, expected_generation=1)
    metadata = root / "openai.json"
    moved = root / "protected"
    metadata.rename(moved)
    metadata.symlink_to(moved)
    with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
        store.revoke(_PROVIDER, expected_version=2, generation=1)
    assert metadata.is_symlink()


def test_write_failure_cannot_select_unpublished_generation(volume, monkeypatch):
    root, store, roles = volume
    first = store.save(_PROVIDER, _FIRST, expected_version=1)
    _, digest = store.authority_metadata(_PROVIDER)
    reader = ResearchCredentialReader(
        root, **roles, authorize=lambda candidate, version, fingerprint:
        (candidate, version, fingerprint) == (binding(1), 2, digest),
    )

    def fail(*args, **kwargs):
        raise OSError

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", fail)
        with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
            store.rotate(_PROVIDER, _SECOND, expected_version=2, expected_generation=1)
    assert store.status(_PROVIDER) == first
    assert reader.read_active(binding(1)) == _FIRST
    second = store.rotate(_PROVIDER, _SECOND, expected_version=2, expected_generation=1)
    assert second.generation == 2 and len(list(root.glob("*.key"))) == 1


def test_corrupt_volume_and_concurrent_write_fail_closed(volume):
    import fcntl

    root, store, roles = volume
    first = store.save(_PROVIDER, _FIRST, expected_version=1)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
            store.rotate(_PROVIDER, _SECOND, expected_version=2, expected_generation=1)
    finally:
        os.close(descriptor)
    assert store.status(_PROVIDER) == first
    key = next(root.glob("*.key"))
    reader = ResearchCredentialReader(root, **roles, authorize=lambda *_: True)
    key.write_text(_SECOND)
    with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
        reader.read_active(binding(1))
    key.write_text(_FIRST)
    key.chmod(0o644)
    with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
        reader.read_active(binding(1))


def test_symlink_parent_rejected(volume):
    root, _, roles = volume
    alias = root.parent / "alias"
    alias.symlink_to(Path(root.parent), target_is_directory=True)
    store = ResearchCredentialStore(alias / root.name, **roles)
    with pytest.raises(CredentialDenied, match="CREDENTIAL_STORAGE_UNAVAILABLE"):
        store.status(_PROVIDER)
