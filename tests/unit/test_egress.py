"""Negative egress authority checks and opaque local tunnel evidence, no provider I/O."""

import asyncio
import socket
import ssl

import pytest

from services.runtime.egress import (
    ConnectProxy,
    EgressDenied,
    MockRoute,
    destination,
    verify_client_hello,
)


def client_hello(host):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(incoming, outgoing, server_hostname=host)
    with pytest.raises(ssl.SSLWantReadError):
        client.do_handshake()
    return outgoing.read()


def test_client_hello_requires_matching_tls_sni():
    good = client_hello("paper-api.alpaca.markets")
    verify_client_hello(good, "paper-api.alpaca.markets")
    for bad in (b"GET / HTTP/1.1\r\n\r\n", good, good[:-1]):
        with pytest.raises(EgressDenied):
            verify_client_hello(bad, "api.typesafe.ai")


def test_encrypted_client_hello_is_rejected():
    record = bytearray(client_hello("paper-api.alpaca.markets"))
    offset = 5 + 4 + 2 + 32
    offset += 1 + record[offset]
    offset += 2 + int.from_bytes(record[offset:offset + 2])
    offset += 1 + record[offset]
    size = int.from_bytes(record[offset:offset + 2])
    record[offset:offset + 2] = (size + 4).to_bytes(2)
    record.extend(b"\xfe\x0d\x00\x00")
    record[3:5] = (len(record) - 5).to_bytes(2)
    record[6:9] = (len(record) - 9).to_bytes(3)
    with pytest.raises(EgressDenied):
        verify_client_hello(bytes(record), "paper-api.alpaca.markets")


@pytest.mark.parametrize("authority", [
    "api.alpaca.markets:443", "paper-api.alpaca.markets:80", "127.0.0.1:443",
    "[::1]:443", "paper-api.alpaca.markets.:443", "PAPER-api.alpaca.markets:443",
    "paper-api.alpaca.markets@evil.invalid:443", "evil.invalid:443",
    "paper-api.alpaca.markets:0443", "paper-api.alpaca.markets%00:443",
    "api.typesafe.ai:443",
])
def test_execution_rejects_other_authorities(authority):
    with pytest.raises(EgressDenied, match="^egress denied$"):
        destination(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode(), "execution")


@pytest.mark.parametrize("header", [
    b"GET https://paper-api.alpaca.markets HTTP/1.1\r\n\r\n",
    b"CONNECT paper-api.alpaca.markets:443 HTTP/1.0\r\n\r\n",
    b"CONNECT paper-api.alpaca.markets:443 HTTP/1.1\r\nHost: evil.invalid\r\n\r\n",
    b"CONNECT paper-api.alpaca.markets:443 HTTP/1.1\r\nAuthorization: synthetic\r\n\r\n",
    b"CONNECT paper-api.alpaca.markets:443 HTTP/1.1\r\nX: " + b"a" * 8192 + b"\r\n\r\n",
])
def test_rejects_other_protocols_and_headers(header):
    with pytest.raises(EgressDenied):
        destination(header, "execution")


def test_separate_role_allowlists():
    assert destination(b"CONNECT api.typesafe.ai:443 HTTP/1.1\r\n\r\n", "worker")
    with pytest.raises(EgressDenied):
        destination(b"CONNECT data.alpaca.markets:443 HTTP/1.1\r\n\r\n", "worker")


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_dns_private_destinations_rejected(monkeypatch, address):
    async def scenario():
        async def answers(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
        with pytest.raises(EgressDenied):
            await ConnectProxy("execution").resolve("paper-api.alpaca.markets")
    asyncio.run(scenario())


def test_opaque_tunnel_local_fixture(capsys):
    async def scenario():
        import secrets

        canary = secrets.token_bytes(32)
        hello = client_hello("paper-api.alpaca.markets")

        async def echo(reader, writer):
            assert await reader.readexactly(len(hello)) == hello
            data = await reader.readexactly(len(canary))
            writer.write(data)
            await writer.drain()
            writer.close()

        sink = await asyncio.start_server(echo, "127.0.0.1", 0)
        sink_port = sink.sockets[0].getsockname()[1]
        proxy = ConnectProxy("execution", mock_route=MockRoute(
            "paper-api.alpaca.markets", "127.0.0.1", sink_port
        ))
        gateway = await asyncio.start_server(proxy.handle, "127.0.0.1", 0, limit=8192)
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", gateway.sockets[0].getsockname()[1]
            )
            writer.write(b"CONNECT paper-api.alpaca.markets:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            writer.write(hello + canary)
            await writer.drain()
            assert await reader.readexactly(len(canary)) == canary
            writer.close()
            await writer.wait_closed()
        finally:
            gateway.close()
            sink.close()
            await gateway.wait_closed()
            await sink.wait_closed()
    asyncio.run(scenario())
    assert capsys.readouterr() == ("", "")
