"""Synthetic-only gateway process smoke for a private Docker test network."""

import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock
from uuid import uuid4

import httpx
import uvicorn
from fastapi.testclient import TestClient

from services.llm_gateway.app import GatewayDispatchClient, create_gateway_app
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.offline_gateway import AuthorizedDispatch, DispatchRequest, OfflineGateway
from services.llm_gateway.provider_http import Capability, OfflineProviderHttp
from services.llm_gateway.provider_wire import Provider, ResultState
from services.llm_gateway.supervisor_rpc import (
    ADMISSION_PATH,
    AdmissionAuthentication,
    AdmissionClient,
    create_admission_app,
)
from services.setup.llm_credentials import CredentialBinding, SyntheticCredentialStore

_AUTH = AdmissionAuthentication(bytes(range(32)))
_PROJECTION = b'{"excerpt":"synthetic research","confirmedContext":{}}'
_PROFILE = Capability(
    Provider.OPENAI, "fixture-model", "fixture-profile", 1, 1, 1, "fixture-v1", "fixed",
    json.dumps({"type": "object", "properties": {"hypothesis": {"type": "string"}},
                "required": ["hypothesis"], "additionalProperties": False}),
    128, 2,
)


def serve_supervisor() -> None:
    consumed = set()
    lock = Lock()

    def consume(request):
        with lock:
            if request.attempt_id in consumed or request.projection_sha256 != sha256(
                _PROJECTION
            ).hexdigest():
                return None
            consumed.add(request.attempt_id)
        return AuthorizedDispatch(request, _PROFILE, _PROJECTION)

    uvicorn.run(create_admission_app(_AUTH, consume), host="0.0.0.0", port=8080,
                access_log=False, log_level="error")


def _wait_for_supervisor(client: httpx.Client) -> None:
    until = time.monotonic() + 10
    while time.monotonic() < until:
        try:
            if client.get(ADMISSION_PATH).status_code == 405:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("synthetic supervisor unavailable")


def gateway_probe() -> None:
    with tempfile.TemporaryDirectory() as scratch, httpx.Client(
        base_url="http://apf03-supervisor:8080", trust_env=False, timeout=1,
    ) as client:
        _wait_for_supervisor(client)
        root = Path(scratch)
        credentials = SyntheticCredentialStore.provision(root / "credentials.sqlite")
        binding = CredentialBinding(_PROFILE.provider, 1, _PROFILE.profile_id, 1, 1)
        saved = credentials.save(binding.provider, expected_version=1)
        probe = credentials.begin_probe(binding, expected_version=saved.version)
        credentials.take_probe(probe)
        verified = credentials.record_probe(probe, passed=True)
        active = credentials.activate(
            binding, expected_version=verified.version, verified_probe_id=probe.probe_id,
        )
        calls = []

        def provider(request):
            assert request.url == httpx.URL("https://api.openai.com/v1/responses")
            assert request.headers["authorization"].startswith("Bearer offline-canary-")
            assert json.loads(request.content)["input"][0]["content"] == _PROJECTION.decode()
            calls.append(request.url)
            return httpx.Response(200, json={
                "model": _PROFILE.model, "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 5}, "output": [{
                    "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": '{"hypothesis":"synthetic"}'}],
                }],
            })

        journal = DispatchJournal.provision(root / "dispatch.sqlite")
        gateway = OfflineGateway(
            journal, OfflineProviderHttp(httpx.MockTransport(provider)),
            AdmissionClient(client, _AUTH).authorize, credentials=credentials,
            capabilities=(_PROFILE,),
        )

        def request():
            return DispatchRequest(
                uuid4(), "a" * 64, sha256(_PROJECTION).hexdigest(), _PROFILE.provider,
                _PROFILE.model, _PROFILE.profile_id, 1, 1, 1, _PROFILE.envelope_version,
                datetime.now(UTC) + timedelta(seconds=20),
            )

        with TestClient(create_gateway_app(gateway, _AUTH)) as gateway_http:
            dispatch = GatewayDispatchClient(gateway_http, _AUTH)
            first = request()
            assert dispatch.dispatch(first).state is ResultState.ACCEPTED
            assert dispatch.dispatch(first).state is ResultState.OUTCOME_UNKNOWN
            credentials.revoke(binding.provider, expected_version=active.version, generation=1)
            assert dispatch.dispatch(request()).state is ResultState.DISPATCH_DENIED
        assert len(calls) == 1
    print("PASS: private HTTP admission, exact fixture dispatch, replay and revoked generation")


if __name__ == "__main__":
    {"supervisor": serve_supervisor, "gateway": gateway_probe}[sys.argv[1]]()
