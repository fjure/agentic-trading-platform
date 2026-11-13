"""Local setup ingress never passes credential bodies to the canonical API."""

from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from services.control_api.auth import LocalAuth
from services.setup.llm_credentials import CredentialState, CredentialStatus
from services.setup.research_app import GRANT_HEADER, create_app

ORIGIN = "http://127.0.0.1:8000"
PATH = "/api/v1/integrations/research-setup/providers/OPENAI"


def test_setup_origin_and_one_use_grant():
    from datetime import UTC, datetime

    authority = LocalAuth(ORIGIN)
    storage = Mock()
    storage.save.return_value = CredentialStatus(
        2, CredentialState.STORED, 1, datetime.now(UTC),
    )
    body = {"expectedCredentialVersion": 1, "credential": "synthetic-setup-canary"}
    app = create_app(storage, authority)
    with TestClient(app, base_url=ORIGIN) as client:
        grant = app.state.issue_grant("OPENAI", "save")
        headers = {"Origin": ORIGIN, GRANT_HEADER: grant}
        assert client.put(PATH, json=body).status_code == 403
        assert client.put(
            PATH.replace("OPENAI", "ANTHROPIC"), json=body, headers=headers,
        ).status_code == 403
        assert client.put(
            PATH, json=body, headers=headers | {"Origin": "http://127.0.0.1:9999"},
        ).status_code == 403
        result = client.put(PATH, json=body, headers=headers)
        assert result.status_code == 200
        assert result.json()["credentialGeneration"] == 1
        assert body["credential"] not in result.text
        assert client.put(PATH, json=body, headers=headers).status_code == 403
        storage.save.assert_called_once()


def test_bridge_refuses_malformed_setup_path_without_canonical_api(tmp_path):
    from services.runtime.api import create_bridge

    app = create_bridge(SimpleNamespace(origin=ORIGIN), tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.put(PATH + "/unexpected", json={}).status_code == 404
        assert client.put(PATH, content=b"a" * 8193).status_code == 413
