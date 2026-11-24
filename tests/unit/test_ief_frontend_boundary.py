"""IEF deep-link hosting on the real same-origin bridge, without external I/O."""

import httpx
import pytest
from fastapi.testclient import TestClient

from services.runtime.api import create_bridge
from services.runtime.config import RuntimeConfig


@pytest.mark.parametrize("path", [
    "/onboarding", "/home", "/portfolio", "/portfolio/insights",
    "/strategies", "/strategies/ema-cross-v1?version=1", "/tasks",
    "/tasks/prototype-analysis", "/paper/orders/new", "/activity",
    "/operations?researchIdea=idea_1", "/operations/paper",
])
def test_deep_link_reload_serves_shell_but_cannot_replay_command(tmp_path, path):
    shell = "<html><body>synthetic IEF build</body></html>"
    (tmp_path / "index.html").write_text(shell, encoding="utf-8")
    with TestClient(
        create_bridge(RuntimeConfig(), tmp_path), base_url="http://127.0.0.1:8000"
    ) as client:
        response = client.get(path)
        assert response.status_code == 200 and response.text == shell
        assert response.headers["cache-control"] == "no-store"
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert client.head(path).status_code == 200
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, path, json={"confirm": True}).status_code == 405


def test_api_failure_is_not_replaced_by_successful_spa_document(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("synthetic IEF shell", encoding="utf-8")
    attempted = []

    class OfflineUpstream:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def build_request(self, method, target, **kwargs):
            attempted.append((method, str(target)))
            return httpx.Request(method, target)

        async def send(self, request, **kwargs):
            raise httpx.ConnectError("synthetic offline", request=request)

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", OfflineUpstream)
    with TestClient(
        create_bridge(RuntimeConfig(), tmp_path), base_url="http://127.0.0.1:8000"
    ) as client:
        for path in ("/api/v1/experience/portfolio", "/api/v1/experience/tasks", "/ready"):
            response = client.get(path)
            assert response.status_code == 503
            assert "synthetic IEF shell" not in response.text
        assert client.get("/missing.js").status_code == 404
    assert attempted == [
        ("GET", "http://127.0.0.1:8001/api/v1/experience/portfolio"),
        ("GET", "http://127.0.0.1:8001/api/v1/experience/tasks"),
        ("GET", "http://127.0.0.1:8001/ready"),
    ]
