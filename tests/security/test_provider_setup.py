"""Offline setup tests. Every credential below is a synthetic canary, never a provider key."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from services.setup.app import create_app
from services.setup.client import FixedSetupClient
from services.setup.store import ProviderStore, SetupInvalid, SetupUnavailable, parse_body, validate

CANARY = "synthetic-setup-canary-never-a-real-provider-key"
ACCOUNT = "11111111-2222-4333-8444-555555555555"
ALPACA = {"apiKey": CANARY, "apiSecret": CANARY + "-secret",
          "expectedAccountId": ACCOUNT, "paperOnly": True}
JEV = {"apiKey": CANARY + "-jev"}
SAVED = {"status": "CONFIGURED", "restartRequired": True}


@pytest.mark.parametrize("provider,value", [("alpaca", ALPACA), ("jev", JEV)])
def test_fixed_private_shape(provider, value):
    fields = validate(provider, value)
    assert set(fields) == ({"api_key", "api_secret", "expected_account_id"}
                           if provider == "alpaca" else {"api_key"})


@pytest.mark.parametrize("value", [None, [], {}, {"apiKey": ""}, {"apiKey": 42},
    {"apiKey": "x" * 513}, {"apiKey": CANARY + "\n"}, {"apiKey": " x"},
    {"apiKey": "x\x00y"}, {"apiKey": "x\x7fy"}, {"apiKey": "x\u202ey"},
    {"apiKey": CANARY, "baseUrl": "https://invalid.example"}])
def test_validation_redacts_invalid_jev(value):
    with pytest.raises(SetupInvalid) as error:
        validate("jev", value)
    assert str(error.value) == "INVALID_REQUEST"


@pytest.mark.parametrize("update", [{"paperOnly": False}, {"paperOnly": 1},
    {"expectedAccountId": ACCOUNT.upper() + " "}, {"expectedAccountId": "garbage"},
    {"expectedAccountId": []}, {"apiSecret": CANARY + "\t"}, {"baseUrl": "x"}])
def test_alpaca_validation_is_paper_only_and_uuid_canonical(update):
    with pytest.raises(SetupInvalid):
        validate("alpaca", ALPACA | update)


@pytest.fixture
def internal():
    storage = Mock(spec=ProviderStore)
    storage.status.return_value = {
        provider: {"status": "UNCONFIGURED", "restartRequired": False}
        for provider in ("alpaca", "jev")
    }
    storage.save.return_value = SAVED
    return TestClient(create_app(storage)), storage


@pytest.mark.parametrize("provider,value", [("alpaca", ALPACA), ("jev", JEV)])
def test_http_save_response_and_no_body_logs(internal, caplog, capsys, provider, value):
    client, storage = internal
    response = client.put("/integrations/" + provider, json=value)
    assert response.status_code == 200 and response.json() == SAVED
    assert response.headers["cache-control"] == "no-store"
    storage.save.assert_called_once_with(provider, value)
    output = capsys.readouterr()
    assert CANARY not in response.text + caplog.text + output.out + output.err


@pytest.mark.parametrize("body", [b"", b"{", b"[]", b'"' + CANARY.encode() + b'"',
    b'{"apiKey":"first","apiKey":"' + CANARY.encode() + b'"}',
    b'{"apiKey":NaN}', b'{"apiKey":"' + b"x" * 513 + b'"}',
    b" " * (16384 + 1), b"[" * 1100])
def test_http_malformed_duplicate_huge_and_nonfinite_rejected(internal, body):
    client, storage = internal
    response = client.put("/integrations/jev", content=body,
                          headers={"content-type": "application/json"})
    assert response.status_code == 400 and response.json() == {"code": "INVALID_REQUEST"}
    storage.save.assert_not_called()


def test_streamed_body_limit_without_content_length(internal):
    client, storage = internal
    response = client.put("/integrations/jev", content=iter([b" " * 10000] * 2),
                          headers={"content-type": "application/json"})
    assert response.status_code == 400
    storage.save.assert_not_called()


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json",
    "/integrations/alpaca", "/integrations/jev", "/integrations/jev/credentials.json"])
def test_no_credential_reads_or_discovery_docs(internal, path):
    client, _ = internal
    response = client.get(path)
    assert response.status_code in {404, 405}
    assert CANARY not in response.text


def test_store_exception_body_not_exposed(internal):
    client, storage = internal
    storage.save.side_effect = RuntimeError(CANARY)
    response = client.put("/integrations/jev", json=JEV)
    assert response.status_code == 400
    assert CANARY not in response.text


def test_status_never_contains_credentials(internal):
    client, storage = internal
    response = client.get("/integrations")
    assert response.status_code == 200
    assert response.json() == storage.status.return_value
    assert CANARY not in response.text


def test_parse_errors_do_not_echo():
    with pytest.raises(SetupInvalid) as error:
        parse_body("jev", b'{"apiKey":"' + CANARY.encode() + b'","apiKey":null}')
    assert str(error.value) == "INVALID_REQUEST"


@pytest.fixture
def volume(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX no-follow/uid/mode volume checks run in the Linux container")
    for provider in ("alpaca", "jev"):
        (tmp_path / provider).mkdir(mode=0o700)
    return ProviderStore(tmp_path, owner_uid=os.getuid()), tmp_path


def test_real_volume_atomic_replace_metadata_only_status(volume, monkeypatch):
    store, root = volume
    assert all(entry["status"] == "UNCONFIGURED" for entry in store.status().values())
    for provider, value in (("alpaca", ALPACA), ("jev", JEV)):
        assert store.save(provider, value) == SAVED
        target = root / provider / "credentials.json"
        original_inode = target.stat().st_ino
        assert json.loads(target.read_text()) == validate(provider, value)
        assert target.stat().st_mode & 0o777 == 0o600
        assert target.stat().st_uid == os.getuid() and target.stat().st_nlink == 1
        store.save(provider, value)
        assert target.stat().st_ino != original_inode
        assert list(target.parent.iterdir()) == [target]
    def no_read(*_args, **_kwargs):
        raise AssertionError("Status must not read persisted secrets")
    monkeypatch.setattr(Path, "read_text", no_read)
    assert all(entry == SAVED for entry in store.status().values())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode", "directory", "empty"])
def test_unsafe_existing_target_is_neither_read_nor_replaced(volume, kind):
    store, root = volume
    target = root / "jev" / "credentials.json"
    protected = root / "protected"
    protected.write_text(CANARY)
    protected.chmod(0o600)
    if kind == "symlink":
        target.symlink_to(protected)
    elif kind == "hardlink":
        os.link(protected, target)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    else:
        target.write_text(CANARY if kind == "mode" else "")
        target.chmod(0o644 if kind == "mode" else 0o600)
    with pytest.raises(SetupUnavailable):
        store.save("jev", JEV)
    with pytest.raises(SetupUnavailable):
        store.status()
    assert protected.read_text() == CANARY
    assert not list(target.parent.glob(".*.tmp"))


@pytest.mark.parametrize("kind", ["mode", "symlink", "owner"])
def test_unsafe_directory_fail_closed(volume, kind):
    store, root = volume
    directory = root / "jev"
    if kind == "mode":
        directory.chmod(0o750)
    elif kind == "symlink":
        elsewhere = root / "other"
        directory.rename(elsewhere)
        directory.symlink_to(elsewhere, target_is_directory=True)
    else:
        store = ProviderStore(root, owner_uid=os.getuid() + 1)
    with pytest.raises(SetupUnavailable):
        store.save("jev", JEV)
    assert not list(directory.glob("*.json"))


def test_failure_before_replace_preserves_previous_volume(volume, monkeypatch):
    store, root = volume
    store.save("jev", JEV)
    old = (root / "jev" / "credentials.json").read_bytes()
    def fail(*_args, **_kwargs):
        raise OSError(CANARY)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(SetupUnavailable) as error:
        store.save("jev", {"apiKey": "synthetic-replacement"})
    assert str(error.value) == "TEMPORARILY_UNAVAILABLE"
    assert (root / "jev" / "credentials.json").read_bytes() == old
    assert not list((root / "jev").glob(".*.tmp"))


def test_client_uses_fixed_internal_url_and_refuses_redirect(monkeypatch):
    calls = []
    real_client = httpx.Client
    def transport(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://invalid.example/"})
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(transport), **kwargs)
    monkeypatch.setattr(httpx, "Client", factory)
    with pytest.raises(SetupUnavailable):
        FixedSetupClient().save_jev(JEV)
    assert len(calls) == 1
    assert str(calls[0].url) == "http://provider-setup:8011/integrations/jev"


def test_client_rejects_untrusted_credential_response():
    client = FixedSetupClient()
    client._request = Mock(return_value=SAVED | {"apiKey": CANARY})
    with pytest.raises(SetupUnavailable) as error:
        client.save_jev(JEV)
    assert CANARY not in str(error.value)
