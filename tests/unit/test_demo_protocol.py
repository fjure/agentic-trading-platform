"""The private protocol never exposes provisioning or object dispatch."""

from fastapi.testclient import TestClient

from services.demo.app import create_app


class Coordinator:
    def __init__(self):
        self.calls = []

    def list_runs(self):
        self.calls.append("list")
        return {"items": [], "nextCursor": None}

    def get_run(self, **kwargs):
        self.calls.append(kwargs)
        return {"synthetic": True}

    def snapshot(self, **kwargs):
        raise RuntimeError("synthetic-private-diagnostic")

    def control(self, **kwargs):
        self.calls.append(kwargs)
        return {"simulationOnly": True}

    def close(self):
        pass


def test_internal_protocol_rejects_privileged_or_extra_arguments():
    coordinator = Coordinator()
    with TestClient(create_app(coordinator)) as client:
        response = client.post("/internal/demo", json={"operation": "list_runs"})
        assert response.status_code == 200 and response.json()["items"] == []
        for payload in (
            {"operation": "provision_run", "run_id": "lab_runtime"},
            {"operation": "get_run", "run_id": "test", "database": "lab_runtime"},
            {"operation": "__dict__"},
            {"operation": "history", "run_id": "test", "resource": "orders", "host": "external"},
        ):
            result = client.post("/internal/demo", json=payload)
            assert result.status_code == 400
        assert coordinator.calls == ["list"]
        result = client.post(
            "/internal/demo", json={"operation": "snapshot", "run_id": "test", "trace": "a" * 32}
        )
        assert result.status_code == 503
        assert "synthetic-private-diagnostic" not in result.text
        assert client.post("/internal/demo", content="{" + " " * 17000).status_code == 400
