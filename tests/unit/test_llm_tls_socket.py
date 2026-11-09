"""Real loopback HTTPS for both fixed provider routes, with no external DNS or egress."""

import asyncio
import json
import ssl
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from services.llm_gateway.provider_http import OfflineProviderHttp
from services.llm_gateway.provider_wire import Provider, ResultState
from services.runtime.egress import ConnectProxy, EgressDenied, MockRoute, destination
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


class _SocketDouble(OfflineProviderHttp):
    def __init__(self, port, trust):
        self._proxy = f"http://127.0.0.1:{port}"
        self._trust = trust

    def _client(self, timeout):
        return httpx.Client(
            proxy=self._proxy, verify=self._trust, trust_env=False,
            timeout=timeout, follow_redirects=False,
        )


@pytest.mark.parametrize(("provider", "host", "route", "message_key"), [
    (Provider.OPENAI, "api.openai.com", "/v1/responses", "input"),
    (Provider.ANTHROPIC, "api.anthropic.com", "/v1/messages", "messages"),
])
def test_fixed_provider_protocol_crosses_local_connect_and_verified_tls(
    tmp_path, provider, host, route, message_key,
):
    server_tls, trust = _contexts(tmp_path, host)
    seen = []

    async def provider_handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("ascii").strip().split("\r\n")
            headers = dict(line.lower().split(":", 1) for line in lines[1:])
            body = await reader.readexactly(int(headers["content-length"]))
            seen.append((lines[0], json.loads(body)))
            result = json.dumps(response(provider, '{"hypothesis":"synthetic"}')).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
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
                profile = replace(PROFILE, provider=provider)
                result = await asyncio.to_thread(_SocketDouble(relay_port, trust).send,
                                                 profile, PROJECTION)
                assert result.state is ResultState.ACCEPTED
                untrusted = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                failure = await asyncio.to_thread(_SocketDouble(relay_port, untrusted).send,
                                                  profile, PROJECTION)
                assert failure.state is ResultState.OUTCOME_UNKNOWN

    asyncio.run(scenario())
    [(request_line, body)] = seen
    assert request_line == f"POST {route} HTTP/1.1"
    assert body[message_key] == [{"role": "user", "content": PROJECTION.decode()}]


def test_research_proxy_cannot_start_without_explicit_loopback_double():
    with pytest.raises(EgressDenied):
        ConnectProxy("research-gateway")
    with pytest.raises(EgressDenied):
        ConnectProxy("research-gateway", mock_route=MockRoute("api.openai.com", "10.0.0.1", 443))
    for blocked in ("127.0.0.1", "paper-api.alpaca.markets", "api.typesafe.ai"):
        with pytest.raises(EgressDenied):
            destination(f"CONNECT {blocked}:443 HTTP/1.1\r\n\r\n".encode(), "research-gateway")
