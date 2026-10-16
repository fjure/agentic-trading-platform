"""Local session and request-boundary checks with synthetic ASGI requests only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from starlette.requests import Request
from starlette.responses import Response

from services.control_api.auth import AuthDenied, LocalAuth


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 15, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def request(
    *,
    host: str = "127.0.0.1:8000",
    origin: str | None = "http://127.0.0.1:8000",
    peer: str = "127.0.0.1",
    server: tuple[str, int] = ("127.0.0.1", 8000),
    scheme: str = "http",
    content_type: str | None = "application/json",
    extras: tuple[tuple[str, str], ...] = (),
) -> Request:
    headers = [(b"host", host.encode("ascii"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if content_type is not None:
        headers.append((b"content-type", content_type.encode("ascii")))
    headers.extend((name.encode("ascii"), value.encode("ascii")) for name, value in extras)
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/session",
            "scheme": scheme,
            "headers": headers,
            "client": (peer, 45000),
            "server": server,
        }
    )


def denied(status: int, code: str, call) -> None:
    with pytest.raises(AuthDenied) as error:
        call()
    assert (error.value.status, error.value.code) == (status, code)


def test_activation_retry_consumption_expiry_and_cookie() -> None:
    clock = Clock()
    auth = LocalAuth(clock=clock)
    code = auth.issue_activation_code()
    assert 32 <= len(code) <= 256
    key = "valid-key-0000001"
    token = auth.activate(code, key)
    assert auth.activate(code, key) == token
    assert auth.authenticate(token) == "local-operator"
    denied(409, "IDEMPOTENCY_CONFLICT", lambda: auth.activate("x" * 32, key))
    denied(400, "INVALID_REQUEST", lambda: auth.activate(code, "valid-key-0000002"))

    response = Response(status_code=204)
    auth.set_cookie(response, token)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("atl_session=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Max-Age=86400" in cookie
    assert "Secure" not in cookie
    clock.advance(hours=8)
    assert auth.authenticate(token) == "local-operator"
    assert auth.activate(code, key) == token
    retry = Response(status_code=204)
    auth.set_cookie(retry, token)
    assert "Max-Age=57600" in retry.headers["set-cookie"]
    clock.advance(hours=16)
    denied(401, "INVALID_REQUEST", lambda: auth.authenticate(token))
    denied(401, "INVALID_REQUEST", lambda: auth.set_cookie(Response(status_code=204), token))
    denied(400, "INVALID_REQUEST", lambda: auth.activate(code, key))


def test_activation_code_expires_and_replacement_invalidates_old_code() -> None:
    clock = Clock()
    auth = LocalAuth(clock=clock)
    replaced = auth.issue_activation_code()
    current = auth.issue_activation_code()
    denied(400, "INVALID_REQUEST", lambda: auth.activate(replaced, "valid-key-0000001"))
    clock.advance(minutes=5)
    denied(400, "INVALID_REQUEST", lambda: auth.activate(current, "valid-key-0000002"))
    fresh = auth.issue_activation_code()
    assert auth.authenticate(auth.activate(fresh, "valid-key-0000003")) == "local-operator"


def test_rate_limit_and_concurrent_code_consumption() -> None:
    clock = Clock()
    auth = LocalAuth(clock=clock)
    code = auth.issue_activation_code()
    for index in range(5):
        denied(
            400, "INVALID_REQUEST", lambda i=index: auth.activate("x" * 32, f"key-invalid-{i:05d}")
        )
    denied(503, "TEMPORARILY_UNAVAILABLE", lambda: auth.activate(code, "valid-key-0000001"))
    clock.advance(minutes=1)
    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(
            executor.map(
                lambda index: _try_activate(auth, code, f"concurrent-key-{index:05d}"),
                range(12),
            )
        )
    assert sum(isinstance(item, str) for item in outcomes) == 1
    assert all(isinstance(item, AuthDenied) for item in outcomes if not isinstance(item, str))


def _try_activate(auth: LocalAuth, code: str, key: str) -> str | AuthDenied:
    try:
        return auth.activate(code, key)
    except AuthDenied as exc:
        return exc


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8000",
        "http://0.0.0.0:8000",
        "http://192.168.1.20:8000",
        "http://example.com:8000",
        "http://127.0.0.1:8000/",
        "http://user@127.0.0.1:8000",
        "ftp://127.0.0.1:8000",
    ],
)
def test_rejects_noncanonical_or_nonloopback_configuration(origin: str) -> None:
    with pytest.raises(ValueError):
        LocalAuth(origin=origin)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "evil.example:8000"},
        {"host": "localhost:8000"},
        {"origin": "http://evil.example:8000"},
        {"origin": None},
        {"peer": "192.168.1.10"},
        {"peer": "testclient"},
        {"server": ("0.0.0.0", 8000)},
        {"server": ("127.0.0.1", 8001)},
        {"scheme": "https"},
        {"content_type": "text/plain"},
        {"content_type": None},
        {"extras": (("host", "127.0.0.1:8000"),)},
        {"extras": (("origin", "http://127.0.0.1:8000"),)},
        {"extras": (("cookie", "atl_session=one"), ("cookie", "atl_session=two"))},
        {"extras": (("cookie", "atl_session=one; atl_session=two"),)},
        {"extras": (("x-forwarded-host", "evil.example"),)},
        {"extras": (("forwarded", "host=evil.example"),)},
    ],
)
def test_request_guard_rejects_untrusted_inputs(kwargs: dict) -> None:
    auth = LocalAuth()
    denied(400, "INVALID_REQUEST", lambda: auth.guard(request(**kwargs), write=True))


def test_read_guard_allows_browser_get_without_origin_but_rejects_bad_origin() -> None:
    auth = LocalAuth()
    auth.guard(request(origin=None, content_type=None), write=False)
    denied(
        400,
        "INVALID_REQUEST",
        lambda: auth.guard(request(origin="http://evil.example:8000"), write=False),
    )
    auth.guard(request(content_type="application/json; charset=utf-8"), write=True)


def test_https_cookie_is_secure() -> None:
    auth = LocalAuth(origin="https://127.0.0.1:8443")
    response = Response(status_code=204)
    auth.set_cookie(response, auth.activate(auth.issue_activation_code(), "valid-key-0000001"))
    assert "Secure" in response.headers["set-cookie"]
