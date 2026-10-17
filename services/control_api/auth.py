"""In-memory authentication for the single-operator loopback control API.

The launcher alone receives an activation code. Request handlers must call ``guard``
before activation or cookie authentication; the deployment must bind only loopback.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response

_CODE_LIFETIME = timedelta(minutes=5)
_SESSION_LIFETIME = timedelta(hours=24)
_REPLAY_LIFETIME = timedelta(hours=24)
_ATTEMPT_WINDOW = timedelta(minutes=1)
_MAX_ATTEMPTS = 5
_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")
_FORWARDED = frozenset({"forwarded", "x-real-ip", "x-original-host", "x-host"})


class AuthDenied(Exception):
    """A public status and code without echoing credentials or request values."""

    def __init__(self, status: int, code: str = "INVALID_REQUEST") -> None:
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class _Replay:
    code_hash: bytes
    token: str
    expires_at: datetime


class LocalAuth:
    """Single-process local session authority; process restart revokes all sessions."""

    cookie_name = "atl_session"

    def __init__(
        self,
        origin: str = "http://127.0.0.1:8000",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        transport_origin: str | None = None,
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError("origin must be an exact loopback HTTP origin")
        try:
            host = ipaddress.ip_address(parsed.hostname)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("origin must contain a loopback IP and valid port") from exc
        if not host.is_loopback or host.is_unspecified or port is None:
            raise ValueError("origin must contain a loopback IP and explicit port")
        # Reject odd but parseable spellings. One canonical string makes Host/Origin
        # comparison unambiguous, including IPv6 bracket notation.
        canonical_host = f"[{host.compressed}]" if host.version == 6 else str(host)
        canonical = f"{parsed.scheme}://{canonical_host}:{port}"
        if origin != canonical:
            raise ValueError("origin must use canonical loopback spelling")
        self.origin = canonical
        self.scheme = parsed.scheme
        self.host = f"{canonical_host}:{port}"
        self.port = port
        self.secure_cookie = parsed.scheme == "https"
        # A fixed local deployment bridge may use another private loopback port.
        # Public Host/Origin and peer validation remain independent of headers.
        transport = LocalAuth(transport_origin) if transport_origin is not None else None
        self.transport_port = transport.port if transport is not None else port
        self.transport_scheme = transport.scheme if transport is not None else parsed.scheme
        self._clock = clock
        self._lock = threading.RLock()
        self._code_hash: bytes | None = None
        self._code_expires: datetime | None = None
        self._sessions: dict[str, datetime] = {}
        self._replays: dict[str, _Replay] = {}
        self._attempts: deque[datetime] = deque()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return now.astimezone(UTC)

    def issue_activation_code(self) -> str:
        """Replace any outstanding code; the trusted launcher displays the return value."""
        code = secrets.token_urlsafe(32)
        with self._lock:
            self._code_hash = hashlib.sha256(code.encode("ascii")).digest()
            self._code_expires = self._now() + _CODE_LIFETIME
        return code

    def activate(self, code: str, idempotency_key: str) -> str:
        """Consume a valid code once, with a 24-hour same-payload retry record."""
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise AuthDenied(400)
        if not isinstance(code, str) or not 32 <= len(code) <= 256:
            raise AuthDenied(400)
        code_hash = hashlib.sha256(code.encode("utf-8")).digest()
        with self._lock:
            now = self._now()
            self._replays = {
                key: result for key, result in self._replays.items() if now < result.expires_at
            }
            self._sessions = {
                token: expires for token, expires in self._sessions.items() if now < expires
            }
            replay = self._replays.get(idempotency_key)
            if replay is not None:
                if not hmac.compare_digest(replay.code_hash, code_hash):
                    raise AuthDenied(409, "IDEMPOTENCY_CONFLICT")
                # Even after session expiry, an exact retry returns the old token.
                # It can never mint another authenticated session.
                return replay.token
            while self._attempts and now - self._attempts[0] >= _ATTEMPT_WINDOW:
                self._attempts.popleft()
            if len(self._attempts) >= _MAX_ATTEMPTS:
                raise AuthDenied(503, "TEMPORARILY_UNAVAILABLE")
            self._attempts.append(now)
            if (
                self._code_hash is None
                or self._code_expires is None
                or now >= self._code_expires
                or not hmac.compare_digest(self._code_hash, code_hash)
            ):
                raise AuthDenied(400)
            self._code_hash = None
            self._code_expires = None
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + _SESSION_LIFETIME
            self._replays[idempotency_key] = _Replay(code_hash, token, now + _REPLAY_LIFETIME)
            return token

    def authenticate(self, token: str) -> str:
        if not isinstance(token, str) or not token:
            raise AuthDenied(401)
        with self._lock:
            expires = self._sessions.get(token)
            if expires is None or self._now() >= expires:
                raise AuthDenied(401)
            return "local-operator"

    def set_cookie(self, response: Response, token: str) -> None:
        """Set the canonical browser cookie on an already authenticated response."""
        self.authenticate(token)
        response.set_cookie(
            key=self.cookie_name,
            value=token,
            max_age=max(0, int((self._sessions[token] - self._now()).total_seconds())),
            path="/",
            secure=self.secure_cookie,
            httponly=True,
            samesite="strict",
        )

    def guard(self, request: Request, *, write: bool) -> None:
        """Reject rebinding, proxy headers, remote peers and cross-origin requests."""
        raw_headers = request.scope.get("headers", ())
        by_name: dict[str, list[str]] = {}
        try:
            for raw_name, raw_value in raw_headers:
                name = raw_name.decode("ascii").lower()
                value = raw_value.decode("ascii")
                by_name.setdefault(name, []).append(value)
        except (UnicodeDecodeError, AttributeError, ValueError) as exc:
            raise AuthDenied(400) from exc
        if any(name in _FORWARDED or name.startswith("x-forwarded-") for name in by_name):
            raise AuthDenied(400)
        if any(len(by_name.get(name, ())) != 1 for name in ("host",)):
            raise AuthDenied(400)
        if by_name["host"][0] != self.host:
            raise AuthDenied(400)
        for name in ("origin", "content-type", "cookie", "idempotency-key"):
            if len(by_name.get(name, ())) > 1:
                raise AuthDenied(400)
        cookies = by_name.get("cookie", ())
        if cookies:
            session_cookies = sum(
                part.partition("=")[0].strip() == self.cookie_name for part in cookies[0].split(";")
            )
            if session_cookies > 1:
                raise AuthDenied(400)
        client = request.scope.get("client")
        if not isinstance(client, (tuple, list)) or len(client) < 1:
            raise AuthDenied(400)
        if not isinstance(client[0], str):
            raise AuthDenied(400)
        try:
            peer = ipaddress.ip_address(client[0])
        except (ValueError, TypeError) as exc:
            raise AuthDenied(400) from exc
        if not peer.is_loopback:
            raise AuthDenied(400)
        server = request.scope.get("server")
        if not isinstance(server, (tuple, list)) or len(server) < 2:
            raise AuthDenied(400)
        try:
            local_socket = ipaddress.ip_address(server[0])
        except (ValueError, TypeError) as exc:
            raise AuthDenied(400) from exc
        if (
            not local_socket.is_loopback
            or server[1] != self.transport_port
            or request.scope.get("scheme") != self.transport_scheme
        ):
            raise AuthDenied(400)
        origins = by_name.get("origin", ())
        if origins and origins[0] != self.origin:
            raise AuthDenied(400)
        if write:
            if not origins:
                raise AuthDenied(400)
            content_types = by_name.get("content-type", ())
            if len(content_types) != 1:
                raise AuthDenied(400)
            parts = [part.strip().lower() for part in content_types[0].split(";")]
            if parts[0] != "application/json" or any(part != "charset=utf-8" for part in parts[1:]):
                raise AuthDenied(400)
