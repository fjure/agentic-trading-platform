"""Opaque TLS CONNECT gateway with fixed role destinations and no request logging."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sys
from dataclasses import dataclass

DESTINATIONS = {
    "execution": frozenset({"paper-api.alpaca.markets", "data.alpaca.markets"}),
    "worker": frozenset({"api.typesafe.ai"}),
}
MAX_HEADER = 8192
MAX_HELLO = 16384


class EgressDenied(ValueError):
    """Deliberately carries no caller-controlled data."""


def verify_client_hello(record: bytes, host: str) -> None:
    """Bind the encrypted tunnel to the CONNECT authority, including shared CDNs."""
    try:
        if record[0] != 22 or record[1] != 3 or len(record) > MAX_HELLO:
            raise ValueError
        if int.from_bytes(record[3:5]) != len(record) - 5:
            raise ValueError
        hello = memoryview(record)[5:]
        if hello[0] != 1 or int.from_bytes(hello[1:4]) != len(hello) - 4:
            raise ValueError
        offset = 4 + 2 + 32
        offset += 1 + hello[offset]
        offset += 2 + int.from_bytes(hello[offset:offset + 2])
        offset += 1 + hello[offset]
        extension_size = int.from_bytes(hello[offset:offset + 2])
        offset += 2
        if offset + extension_size != len(hello):
            raise ValueError
        names = []
        while offset < len(hello):
            kind = int.from_bytes(hello[offset:offset + 2])
            length = int.from_bytes(hello[offset + 2:offset + 4])
            offset += 4
            value = hello[offset:offset + length]
            # ECH can conceal a different inner name from the visible SNI.
            if len(value) != length or kind == 0xFE0D:
                raise ValueError
            if kind == 0:
                # Exactly one host_name; reject duplicates and noncanonical wire names.
                if (length < 5 or int.from_bytes(value[:2]) != length - 2
                        or value[2] != 0 or int.from_bytes(value[3:5]) != length - 5):
                    raise ValueError
                names.append(bytes(value[5:]).decode("ascii"))
            offset += length
        if names != [host]:
            raise ValueError
    except (ValueError, IndexError, UnicodeError) as exc:
        raise EgressDenied("egress denied") from exc


def destination(header: bytes, role: str) -> str:
    """Accept only canonical DNS authorities, port 443, and a minimal CONNECT."""
    try:
        lines = header.decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
        host, port = authority.split(":")
        if (
            len(header) > MAX_HEADER
            or not header.endswith(b"\r\n\r\n")
            or method != "CONNECT"
            or version != "HTTP/1.1"
            or port != "443"
            or host not in DESTINATIONS[role]
        ):
            raise EgressDenied("egress denied")
        seen = set()
        for line in lines[1:-2]:
            name, value = line.split(":", 1)
            name = name.lower()
            if name in seen or name not in {"host", "proxy-connection"}:
                raise EgressDenied("egress denied")
            seen.add(name)
            if name == "host" and value.strip() != authority:
                raise EgressDenied("egress denied")
            if name == "proxy-connection" and value.strip().lower() != "keep-alive":
                raise EgressDenied("egress denied")
        return host
    except (ValueError, KeyError, UnicodeError) as exc:
        raise EgressDenied("egress denied") from exc


@dataclass(frozen=True)
class MockRoute:
    """Explicit code-only test fixture; never read from environment or runtime config."""

    host: str
    address: str
    port: int


class ConnectProxy:
    def __init__(self, role: str, *, mock_route: MockRoute | None = None):
        if role not in DESTINATIONS:
            raise EgressDenied("egress denied")
        if mock_route is not None and mock_route.host not in DESTINATIONS[role]:
            raise EgressDenied("egress denied")
        self.role = role
        self.mock_route = mock_route
        self.slots = asyncio.Semaphore(32)

    async def resolve(self, host: str) -> tuple[str, int]:
        if self.mock_route is not None and host == self.mock_route.host:
            return self.mock_route.address, self.mock_route.port
        answers = await asyncio.get_running_loop().getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )
        addresses = [ipaddress.ip_address(a[4][0]) for a in answers]
        if not addresses or any(
            not address.is_global or address.is_multicast or address.is_reserved
            for address in addresses
        ):
            raise EgressDenied("egress denied")
        # Connect the validated numeric address, never resolve the name a second time.
        return answers[0][4][0], 443

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream = None
        connected = False
        acquired = False
        try:
            async with asyncio.timeout(15):
                await self.slots.acquire()
                acquired = True
                header = await reader.readuntil(b"\r\n\r\n")
                host = destination(header, self.role)
                address, port = await self.resolve(host)
                remote_reader, upstream = await asyncio.open_connection(address, port)
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                connected = True
                prefix = await reader.readexactly(5)
                size = int.from_bytes(prefix[3:5])
                if size + 5 > MAX_HELLO:
                    raise EgressDenied("egress denied")
                hello = prefix + await reader.readexactly(size)
                verify_client_hello(hello, host)
                upstream.write(hello)
                await upstream.drain()
            async with asyncio.timeout(120):
                await self.tunnel(reader, writer, remote_reader, upstream)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            if not connected:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                try:
                    await writer.drain()
                except OSError:
                    pass
        finally:
            if acquired:
                self.slots.release()
            writer.close()
            if upstream is not None:
                upstream.close()

    @staticmethod
    async def tunnel(reader, writer, remote_reader, upstream) -> None:
        async def copy(source, target):
            while data := await source.read(65536):
                target.write(data)
                await target.drain()

        tasks = [asyncio.create_task(copy(reader, upstream)),
                 asyncio.create_task(copy(remote_reader, writer))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def serve(role: str) -> None:
    proxy = ConnectProxy(role)
    server = await asyncio.start_server(proxy.handle, "0.0.0.0", 8080, limit=MAX_HEADER)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(serve(sys.argv[1]))
