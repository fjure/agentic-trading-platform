"""Real loopback HTTPS for both fixed provider routes, with no external DNS or egress."""

import asyncio
import json
import ssl
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from services.llm_gateway import provider_http
from services.llm_gateway.app import GatewayDispatchClient, create_provider_gateway_app
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.offline_gateway import AuthorizedDispatch
from services.llm_gateway.provider_http import ProviderHttp
from services.llm_gateway.provider_wire import Provider, ResultState
from services.llm_gateway.supervisor_rpc import AdmissionAuthentication, create_admission_app
from services.runtime.egress import ConnectProxy, EgressDenied, MockRoute, destination
from tests.unit.test_llm_offline_gateway import make_request
from tests.unit.test_llm_provider_http import PROFILE, PROJECTION
from tests.unit.test_llm_provider_wire import response


def _contexts(tmp_path, host):
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(private, hashes.SHA256())
    )
    cert_path, private_path = tmp_path / "fixture.pem", tmp_path / "fixture.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        server.load_cert_chain(cert_path, private_path)
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.load_verify_locations(cafile=cert_path)
    finally:
        private_path.unlink()
        cert_path.unlink()
    return server, client


@pytest.mark.parametrize(("provider", "host", "route", "message_key"), [
    (Provider.OPENAI, "api.openai.com", "/v1/responses", "input"),
    (Provider.ANTHROPIC, "api.anthropic.com", "/v1/messages", "messages"),
])
def test_fixed_provider_protocol_crosses_local_connect_and_verified_tls(
    tmp_path, monkeypatch, provider, host, route, message_key,
):
    server_tls, trust = _contexts(tmp_path, host)
    seen = []
    replies = deque((200, 429, 307, 200))

    async def provider_handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("ascii").strip().split("\r\n")
            headers = dict(line.lower().split(":", 1) for line in lines[1:])
            body = await reader.readexactly(int(headers["content-length"]))
            seen.append((lines[0], json.loads(body), headers))
            payload = response(provider, '{"hypothesis":"synthetic"}')
            status = replies.popleft()
            if not replies:
                payload["model"] = "different-model"
            payload["usage"] = {"input_tokens": 10, "output_tokens": 5}
            if provider is Provider.OPENAI:
                payload["service_tier"] = "default"
                payload["usage"]["input_tokens_details"] = {
                    "cached_tokens": 0, "cache_write_tokens": 0,
                }
            else:
                payload["usage"].update(
                    service_tier="standard", cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                )
            result = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status} Fixture\r\n".encode()
                + b"Content-Type: application/json\r\nContent-Length: "
                + str(len(result)).encode() + b"\r\nConnection: close\r\n\r\n" + result
            )
            await writer.drain()
        finally:
            writer.close()

    async def scenario():
        server = await asyncio.start_server(provider_handler, "127.0.0.1", 0, ssl=server_tls)
        async with server:
            port = server.sockets[0].getsockname()[1]
            proxy = ConnectProxy("research-gateway", mock_route=MockRoute(host, "127.0.0.1", port))
            relay = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
            async with relay:
                relay_port = relay.sockets[0].getsockname()[1]
                monkeypatch.setattr(provider_http, "_PROVIDER_PROXY", f"http://127.0.0.1:{relay_port}")
                monkeypatch.setattr(provider_http, "_provider_tls_context", lambda: trust)
                profile = replace(PROFILE, provider=provider)
                request = replace(make_request(), provider=provider)
                denied = replace(make_request(), provider=provider)

                class FixtureCredentials:
                    def read_active(self, binding):
                        assert binding.provider is provider and binding.credential_generation == 1
                        return "synthetic-wire-fixture"

                def signed_dispatch():
                    path = tmp_path / "dispatch.sqlite"
                    DispatchJournal.provision(path)
                    auth = AdmissionAuthentication(bytes(range(32)))
                    app = create_provider_gateway_app(
                        journal_path=path,
                        auth=auth,
                        credentials=FixtureCredentials(),
                        capabilities=(profile,),
                    )
                    with TestClient(app) as http:
                        client = GatewayDispatchClient(http, auth)
                        assert client.dispatch(denied).state is ResultState.DISPATCH_DENIED
                        assert client.dispatch(request).state is ResultState.ACCEPTED
                        assert client.dispatch(request).state is ResultState.OUTCOME_UNKNOWN
                        for expected in (
                            ResultState.RATE_LIMITED,
                            ResultState.REDIRECT_DENIED,
                            ResultState.MODEL_MISMATCH,
                        ):
                            next_request = replace(make_request(), provider=provider)
                            assert client.dispatch(next_request).state is expected

                auth = AdmissionAuthentication(bytes(range(32)))

                def consume(current):
                    if current == denied:
                        return None
                    return AuthorizedDispatch(current, profile, PROJECTION)

                admission = create_admission_app(
                    auth, consume,
                )
                import socket
                from unittest.mock import patch

                import uvicorn

                from services.llm_gateway import app as gateway_app

                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                    server = uvicorn.Server(
                        uvicorn.Config(admission, log_config=None, lifespan="off")
                    )
                    task = asyncio.create_task(server.serve(sockets=[listener]))
                    try:
                        async with asyncio.timeout(5):
                            while not server.started:
                                await asyncio.sleep(0.01)
                        with patch.object(gateway_app, "_SUPERVISOR_ORIGIN", f"http://127.0.0.1:{port}"):
                            await asyncio.to_thread(signed_dispatch)
                    finally:
                        server.should_exit = True
                        await task
                untrusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                monkeypatch.setattr(
                    provider_http, "_provider_tls_context", lambda: untrusted,
                )
                failure = await asyncio.to_thread(
                    ProviderHttp().send, profile, PROJECTION, credential="synthetic-wire-fixture",
                )
                assert failure.state is ResultState.OUTCOME_UNKNOWN

    asyncio.run(scenario())
    assert len(seen) == 4
    request_line, body, headers = seen[0]
    assert request_line == f"POST {route} HTTP/1.1"
    assert body[message_key] == [{"role": "user", "content": PROJECTION.decode()}]
    if provider is Provider.OPENAI:
        assert body["service_tier"] == "default"
        assert headers["authorization"].strip() == "bearer synthetic-wire-fixture"
    else:
        assert headers["anthropic-version"].strip() == "2026-10-21"
        assert headers["x-api-key"].strip() == "synthetic-wire-fixture"


def test_research_proxy_cannot_start_without_explicit_loopback_double():
    with pytest.raises(EgressDenied):
        ConnectProxy("research-gateway")
    with pytest.raises(EgressDenied):
        ConnectProxy("research-gateway", mock_route=MockRoute("api.openai.com", "10.0.0.1", 443))
    for blocked in ("127.0.0.1", "paper-api.alpaca.markets", "api.typesafe.ai"):
        with pytest.raises(EgressDenied):
            destination(f"CONNECT {blocked}:443 HTTP/1.1\r\n\r\n".encode(), "research-gateway")
