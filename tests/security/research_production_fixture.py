"""Container entry points for the fully internal ARP-09 Compose proof."""

import asyncio
import json
import os
import socket
import ssl
import sys
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Lock
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from services.llm_gateway import provider_http
from services.llm_gateway.__main__ import main as gateway_main
from services.llm_gateway.app import DISPATCH_PATH, GatewayDispatchClient
from services.llm_gateway.dispatch_journal import DispatchJournal
from services.llm_gateway.offline_gateway import AuthorizedDispatch, DispatchRequest
from services.llm_gateway.provider_http import Capability
from services.llm_gateway.provider_installation import DISPATCH_KEY_PATH, JOURNAL_PATH, PROFILE_PATH
from services.llm_gateway.provider_wire import Provider, ResultState
from services.llm_gateway.supervisor_rpc import AdmissionAuthentication, create_admission_app
from services.runtime.egress import MAX_HEADER, RESEARCH_EGRESS_ROLE, ConnectProxy, EgressDenied
from services.setup.research_credentials import ResearchCredentialReader, ResearchCredentialStore

ROOT = Path("/run/research-credentials")
CA = "/run/config/arp09-provider.pem"
PROJECTION = b'{"excerpt":"synthetic research","confirmedContext":{}}'
KEY = "arp09-synthetic-credential"
PROVIDER = Provider(os.environ.get("ARP_FIXTURE_PROVIDER", "OPENAI"))
HOST, ROUTE = {
    Provider.OPENAI: ("api.openai.com", "/v1/responses"),
    Provider.ANTHROPIC: ("api.anthropic.com", "/v1/messages"),
}[PROVIDER]
PROFILE = Capability(
    PROVIDER,
    "fixture-model",
    "fixture-profile",
    1,
    1,
    1,
    "fixture-v1",
    "fixed",
    json.dumps(
        {
            "type": "object",
            "properties": {"hypothesis": {"type": "string"}},
            "required": ["hypothesis"],
            "additionalProperties": False,
        }
    ),
    128,
    5,
)
ORIGIN = "http://research-llm-gateway:8080"
PROXY = "http://research-provider-egress:8080"


def auth():
    assert json.loads(PROFILE_PATH.read_text()) == {"fixture": "ARP-09"}
    key = DISPATCH_KEY_PATH.read_bytes()
    assert key == bytes(range(32))
    return AdmissionAuthentication(key)


def wait_for_health(origin, path, expected):
    with httpx.Client(base_url=origin, timeout=1, trust_env=False) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if client.get(path).status_code == expected:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.1)
    raise AssertionError("private test service did not become ready")


def assert_unreachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
    except OSError:
        return
    raise AssertionError(f"unexpected private connection to {host}:{port}")


def provision_journal():
    JOURNAL_PATH.parent.chmod(0o700)
    os.chown(JOURNAL_PATH.parent, 10002, 10002)
    os.setgid(10002)
    os.setuid(10002)
    DispatchJournal.provision(JOURNAL_PATH)


def setup(action):
    assert (os.getuid(), os.getgid()) == (10004, 10002)
    assert not DISPATCH_KEY_PATH.exists() and not JOURNAL_PATH.parent.exists()
    store = ResearchCredentialStore()
    if action == "seed":
        store.save(PROVIDER, KEY, expected_version=1)
    else:
        store.revoke(PROVIDER, expected_version=2, generation=1)


def serve_supervisor():
    spent = set()
    lock = Lock()

    def consume(request):
        if (
            request.projection_sha256 != sha256(PROJECTION).hexdigest()
            or request.provider != PROFILE.provider
        ):
            return None
        with lock:
            if request.attempt_id in spent:
                return None
            spent.add(request.attempt_id)
        return AuthorizedDispatch(request, PROFILE, PROJECTION)

    uvicorn.run(
        create_admission_app(auth(), consume),
        host="0.0.0.0",
        port=8013,
        access_log=False,
        log_level="warning",
    )


def tls_context():
    return ssl.create_default_context(cafile=CA)


def serve_gateway():
    # CA injection exists solely in this container fixture. Production transport,
    # authenticated admission, journal and credential volume reader remain in use.
    provider_http._provider_tls_context = tls_context
    credentials = ResearchCredentialReader(
        authorize=lambda binding, version, digest: (
            binding.profile_id == PROFILE.profile_id and version == 2 and len(digest) == 64
        ),
    )
    gateway_main(
        journal_path=JOURNAL_PATH,
        auth=auth(),
        credentials=credentials,
        capabilities=(PROFILE,),
    )


class EmulatorRoute(ConnectProxy):
    async def resolve(self, host):
        if host != HOST:
            raise EgressDenied("fixture has no alternate destination")
        address = socket.gethostbyname("arp09-emulator")
        return address, 8443


async def serve_proxy():
    # No public lookup or upstream is configured in this synthetic resolver.
    relay = EmulatorRoute(RESEARCH_EGRESS_ROLE)
    server = await asyncio.start_server(relay.handle, "0.0.0.0", 8080, limit=MAX_HEADER)
    async with server:
        await server.serve_forever()


def serve_emulator():
    app = FastAPI()
    calls = 0

    @app.get("/health")
    async def health():
        return {"calls": calls}

    @app.post(ROUTE)
    async def respond(request: Request):
        nonlocal calls
        if PROVIDER is Provider.OPENAI:
            authenticated = request.headers.get("authorization") == "Bearer " + KEY
            message_field = "input"
        else:
            authenticated = (
                request.headers.get("x-api-key") == KEY
                and request.headers.get("anthropic-version") == "2026-10-21"
            )
            message_field = "messages"
        if not authenticated:
            return JSONResponse(status_code=401, content={})
        body = await request.json()
        if body.get(message_field) != [{"role": "user", "content": PROJECTION.decode()}]:
            return JSONResponse(status_code=422, content={})
        calls += 1
        if calls == 2:
            return RedirectResponse("/health", status_code=307)
        if PROVIDER is Provider.ANTHROPIC:
            return {
                "model": PROFILE.model,
                "type": "message",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"hypothesis":"synthetic"}'}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "service_tier": "standard",
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            }
        return {
            "model": PROFILE.model,
            "status": "completed",
            "service_tier": "default",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": '{"hypothesis":"synthetic"}'}],
                }
            ],
        }

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8443,
        access_log=False,
        log_level="warning",
        ssl_certfile="/run/config/arp09-endpoint.pem",
        ssl_keyfile="/run/config/arp09-provider.key",
    )


def check_rejected_tls():
    # CONNECT uses the same permitted authority while the client supplies the wrong SNI.
    with socket.create_connection(("research-provider-egress", 8080), timeout=2) as connection:
        connection.sendall(f"CONNECT {HOST}:443 HTTP/1.1\r\nHost: {HOST}:443\r\n\r\n".encode())
        with connection.makefile("rb") as stream:
            assert stream.readline().startswith(b"HTTP/1.1 200 ")
            while (line := stream.readline()) != b"\r\n":
                assert line
        try:
            with tls_context().wrap_socket(connection, server_hostname="invalid.fixture"):
                pass
        except ssl.SSLEOFError:
            pass
        else:
            raise AssertionError("proxy accepted a mismatched SNI")
    with httpx.Client(proxy=PROXY, trust_env=False, timeout=3) as client:
        try:
            client.get(f"https://{HOST}/health")
        except httpx.ConnectError as error:
            assert "CERTIFICATE_VERIFY_FAILED" in str(error)
        else:
            raise AssertionError("non-fixture CA accepted emulator certificate")
        try:
            client.get("https://postgres/health")
        except httpx.ProxyError as error:
            assert "403" in str(error)
        else:
            raise AssertionError("egress accepted a non-provider authority")


def gateway_checks():
    assert (os.getuid(), os.getgid()) == (10002, 10002)
    assert os.statvfs(ROOT).f_flag & os.ST_RDONLY
    assert os.statvfs(Path("/")).f_flag & os.ST_RDONLY
    assert JOURNAL_PATH.stat().st_uid == 10002
    assert not Path("/run/research-setup").exists()
    assert not Path("/run/config/arp09-provider.key").exists()
    assert {path.name for path in Path("/run/secrets").iterdir()} == {DISPATCH_KEY_PATH.name}
    assert_unreachable("arp09-emulator", 8443)
    assert_unreachable("postgres", 5432)
    check_rejected_tls()


def check_role(role):
    assert not ROOT.exists() and not JOURNAL_PATH.parent.exists()
    if role == "supervisor":
        wait_for_health("http://postgres:5432", "/", 200)
        assert_unreachable("research-provider-egress", 8080)
        assert_unreachable("arp09-emulator", 8443)
    if role == "egress":
        assert not DISPATCH_KEY_PATH.exists()
        assert not Path(CA).exists()
        assert_unreachable("research-supervisor", 8013)
        assert_unreachable("postgres", 5432)


def request(attempt_id=None):
    return DispatchRequest(
        attempt_id or uuid4(),
        "a" * 64,
        sha256(PROJECTION).hexdigest(),
        PROFILE.provider,
        PROFILE.model,
        PROFILE.profile_id,
        1,
        1,
        1,
        PROFILE.envelope_version,
        datetime.now(UTC) + timedelta(minutes=2),
    )


def exercise(stage, attempt_id):
    wait_for_health(ORIGIN, "/health", 200)
    with httpx.Client(base_url=ORIGIN, timeout=10, trust_env=False) as client:
        dispatch = GatewayDispatchClient(client, auth())
        original = request(attempt_id)
        if stage == "initial":
            assert client.post(DISPATCH_PATH, json={}).status_code == 403
            accepted = dispatch.dispatch(original)
            assert accepted.state is ResultState.ACCEPTED, accepted.state
            assert accepted.output == {"hypothesis": "synthetic"}
            assert dispatch.dispatch(request()).state is ResultState.REDIRECT_DENIED
        else:
            assert dispatch.dispatch(original).state is ResultState.OUTCOME_UNKNOWN
            assert dispatch.dispatch(request()).state is ResultState.DISPATCH_DENIED


def verify_emulator():
    with httpx.Client(proxy=PROXY, verify=tls_context(), trust_env=False, timeout=3) as client:
        assert client.get(f"https://{HOST}/health").json() == {"calls": 2}


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    if action == "exercise":
        from uuid import UUID

        exercise(args[0], UUID(args[1]))
    else:
        {
            "journal": provision_journal,
            "setup": setup,
            "gateway": serve_gateway,
            "supervisor": serve_supervisor,
            "emulator": serve_emulator,
            "egress": lambda: asyncio.run(serve_proxy()),
            "gateway-checks": gateway_checks,
            "check-role": check_role,
            "verify-emulator": verify_emulator,
        }[action](*args)
