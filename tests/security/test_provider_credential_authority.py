from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_api.auth import LocalAuth
from services.llm_gateway.provider_wire import Provider
from services.runtime.provider_credential_authority import (
    PATH,
    SIGNATURE,
    CredentialAuthorityClient,
    register_credential_authority,
)
from services.setup.llm_credentials import CredentialState, CredentialStatus
from services.setup.research_app import GRANT_HEADER, create_app


def test_metadata_only_channel_authenticates_both_sides():
    store = Mock()
    key = bytes(range(32))
    app = FastAPI()
    register_credential_authority(app, store, key)
    with TestClient(app) as http:
        assert http.post(PATH, headers={SIGNATURE: "a" * 64}, json={
            "provider": "OPENAI", "expected_version": 1,
        }).status_code == 403
        store.block_credential.assert_not_called()
        setup = CredentialAuthorityClient(http, key)
        setup.block(Provider.OPENAI, 1)
        setup.attest(Provider.OPENAI, SimpleNamespace(version=2, generation=1), "a" * 64, 1)
    store.block_credential.assert_called_once_with(provider="OPENAI", expected_version=1)
    store.attest_credential.assert_called_once_with(
        provider="OPENAI", expected_version=1, generation=1, version=2, digest="a" * 64,
    )


def test_setup_waits_for_durable_block_before_mutating():
    from datetime import UTC, datetime

    old = CredentialStatus(2, CredentialState.STORED, 1, datetime.now(UTC))
    new = CredentialStatus(3, CredentialState.REVOKED, 1, datetime.now(UTC))
    storage = Mock()
    storage.status.return_value = old
    storage.revoke.return_value = new
    storage.authority_metadata.return_value = (new, None)
    authority = Mock()
    authority.block.side_effect = RuntimeError("unavailable")
    origin = "http://127.0.0.1:8000"
    app = create_app(storage, LocalAuth(origin), credential_authority=authority)
    url = "/api/v1/integrations/research-setup/providers/OPENAI/revoke"
    with TestClient(app, base_url=origin) as http:
        def revoke():
            grant = app.state.issue_grant("OPENAI", "revoke")
            return http.post(
                url, headers={"Origin": origin, GRANT_HEADER: grant},
                json={"expectedCredentialVersion": 2, "credentialGeneration": 1},
            )

        assert revoke().status_code == 503
        storage.revoke.assert_not_called()
        authority.block.side_effect = None
        assert revoke().status_code == 200
    storage.revoke.assert_called_once_with(Provider.OPENAI, expected_version=2, generation=1)
    authority.attest.assert_called_once_with(Provider.OPENAI, new, None, 2)
