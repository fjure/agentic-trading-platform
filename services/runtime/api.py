"""Private canonical API and fixed loopback frontend bridge."""

from __future__ import annotations

import os
import socket
import stat
import sys
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.parse import unquote

from fastapi import Request

from .common import write_status

ACTIVATION_SOCKET = Path("/run/control/api-activation.sock")
STATIC_ROOT = Path("/opt/lab/dashboard")
RESEARCH_SETUP_SOCKET = "/run/research-setup/setup.sock"
_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def static_path(root: Path, raw_path: str) -> Path:
    decoded = unquote(raw_path)
    if "\\" in decoded or "\x00" in decoded or any(p in {".", ".."} for p in decoded.split("/")):
        raise ValueError("INVALID_STATIC_PATH")
    base = root.resolve(strict=True)
    result = (base / decoded.lstrip("/")).resolve()
    if not result.is_relative_to(base):
        raise ValueError("INVALID_STATIC_PATH")
    if not result.is_file():
        if "." in Path(decoded).name:
            raise FileNotFoundError("STATIC_NOT_FOUND")
        result = base / "index.html"
    if not result.is_file():
        raise FileNotFoundError("STATIC_NOT_FOUND")
    return result


def create_bridge(config, root=STATIC_ROOT, *, auth=None):
    import httpx
    from fastapi import FastAPI
    from starlette.responses import FileResponse, Response, StreamingResponse

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    public_host = config.origin.partition("://")[2]

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        headers = request.scope.get("headers", ())
        hosts = [v for k, v in headers if k.lower() == b"host"]
        origins = [v for k, v in headers if k.lower() == b"origin"]
        if (
            hosts != [public_host.encode("ascii")]
            or len(origins) > 1
            or origins
            and origins != [config.origin.encode("ascii")]
            or any(
                k.lower() == b"forwarded" or k.lower().startswith(b"x-forwarded-")
                for k, _ in headers
            )
        ):
            return Response(status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    async def frontend(request: Request, path: str):
        if path == "api/v1/integrations/research-setup" or path.startswith(
            "api/v1/integrations/research-setup/"
        ):
            return await forward_research_setup(request, auth)
        if path.startswith("api/") or path in {"health", "ready"}:
            # Fixed origin and fixed literal prefix. No URL, redirects, proxy
            # discovery, X-Forwarded rewriting or arbitrary upstream selection.
            raw_path = request.scope["raw_path"]
            if not raw_path.startswith(b"/") or raw_path.startswith(b"//"):
                return Response(status_code=400)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1048576:
                    return Response(status_code=413)
            target = httpx.URL("http://127.0.0.1:8001").copy_with(
                raw_path=raw_path
                + (b"?" + request.scope["query_string"] if request.scope["query_string"] else b"")
            )
            client = httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(5.0, read=None)
            )
            try:
                upstream = await client.send(
                    client.build_request(
                        request.method,
                        target,
                        headers=[
                            (k, v)
                            for k, v in request.scope["headers"]
                            if k.decode("ascii").lower() not in _HOP
                        ],
                        content=bytes(body),
                    ),
                    stream=True,
                )
            except Exception:
                await client.aclose()
                return Response(status_code=503)

            async def chunks():
                try:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            response = StreamingResponse(chunks(), status_code=upstream.status_code)
            response.raw_headers = [
                (k, v) for k, v in upstream.headers.raw if k.decode("ascii").lower() not in _HOP
            ]
            return response
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405)
        try:
            asset = static_path(Path(root), path)
        except (ValueError, FileNotFoundError):
            return Response(status_code=404)
        return FileResponse(
            asset,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; object-src 'none'; "
                    "frame-ancestors 'none'"
                ),
            },
        )

    return app


async def forward_research_setup(request: Request, auth):
    """Stream only the fixed setup routes to a separate process over its socket."""
    import asyncio
    import re

    import httpx
    from starlette.responses import Response

    from services.control_api.agent_research_routes import agent_research_problem
    from services.control_api.auth import AuthDenied
    from services.setup.research_ingress import MAX_BODY, SETUP_ROUTE

    raw_path = request.scope["raw_path"]
    response_headers = {"Cache-Control": "no-store"}

    def problem(status, code):
        return agent_research_problem(status, code, None)

    match = SETUP_ROUTE.fullmatch(raw_path)
    if (
        match is None
        or request.scope["query_string"]
        or request.method not in ({"POST"} if match.group(1) else {"GET", "PUT"})
    ):
        return Response(status_code=404, headers=response_headers)
    sizes = request.headers.getlist("content-length")
    if sizes and (
        len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,5}", sizes[0]) or int(sizes[0]) > MAX_BODY
    ):
        return Response(status_code=413, headers=response_headers)
    if auth is None:
        return problem(503, "PROVIDER_UNAVAILABLE")
    cookies = request.headers.getlist("cookie")
    if len(cookies) != 1 or sum(
        part.partition("=")[0].strip() == auth.cookie_name for part in cookies[0].split(";")
    ) != 1:
        return problem(401, "SESSION_REQUIRED")
    try:
        auth.authenticate(request.cookies.get(auth.cookie_name, ""))
    except AuthDenied:
        return problem(401, "SESSION_REQUIRED")

    async def bounded_body():
        length = 0
        async for chunk in request.stream():
            length += len(chunk)
            if length > MAX_BODY:
                raise ValueError("setup body too large")
            yield chunk

    forwarded = {"host", "origin", "content-type", "x-research-setup-grant"}
    try:
        async with (
            asyncio.timeout(7),
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=RESEARCH_SETUP_SOCKET, retries=0),
                trust_env=False, follow_redirects=False, timeout=5,
            ) as client,
        ):
            upstream = await client.request(
                request.method, "http://research-setup" + raw_path.decode("ascii"),
                headers=[(k, v) for k, v in request.headers.raw if k.decode("ascii") in forwarded],
                content=bounded_body(),
            )
    except ValueError:
        return Response(status_code=413, headers=response_headers)
    except (TimeoutError, httpx.HTTPError):
        return problem(503, "PROVIDER_UNAVAILABLE")
    return Response(
        upstream.content, status_code=upstream.status_code, headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


def _private_dir(path):
    if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
        raise OSError("PRIVATE_ENDPOINT_UNAVAILABLE")
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077:
        raise PermissionError("PRIVATE_ENDPOINT_UNAVAILABLE")


def activation_server(auth, path=ACTIVATION_SOCKET):
    _private_dir(path.parent)
    if os.path.lexists(path):
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError("PRIVATE_ENDPOINT_UNAVAILABLE")
        # Private ephemeral socket can survive container process termination.
        path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    os.chmod(path, 0o600)
    listener.listen(1)
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(2)
            try:
                if connection.recv(32) == b"ACTIVATE\n":
                    connection.sendall(auth.issue_activation_code().encode("ascii") + b"\n")
            except Exception:
                pass  # No code, exception or request ever enters logs.


def activate():
    # Deliberately refuses pipelines/tool terminals; only the operator's private
    # interactive terminal may receive the ephemeral activation credential.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionError("INTERACTIVE_OPERATOR_REQUIRED")
    _private_dir(ACTIVATION_SOCKET.parent)
    info = ACTIVATION_SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PermissionError("PRIVATE_ENDPOINT_UNAVAILABLE")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(ACTIVATION_SOCKET))
        client.sendall(b"ACTIVATE\n")
        code = client.recv(128)
    if not code.endswith(b"\n") or len(code) > 100:
        raise ValueError("ACTIVATION_UNAVAILABLE")
    sys.stdout.write(code.decode("ascii"))
    sys.stdout.flush()


def run_api(config, ledger, telemetry=None):
    import uvicorn
    from sqlalchemy import text

    from packages.observability.runtime import RuntimeMetrics
    from services.control_api.app import create_app
    from services.control_api.auth import LocalAuth
    from services.control_api.backend import ControlBackend
    from services.control_api.backtest_client import BacktestClient
    from services.control_api.collection_client import CollectionClient
    from services.control_api.demo_client import DemoClient
    from services.control_api.events import EventFeed
    from services.control_api.paper_client import PaperClient
    from services.control_api.readiness_client import ReadinessClient
    from services.control_api.research_client import AgentResearchClient, ResearchClient
    from services.control_api.snapshot import SnapshotReader
    from services.control_api.trading_profile import TradingProfileBackend
    from services.execution.control_socket import LocalControlServer, dispatcher
    from services.setup.client import FixedSetupClient

    from .agent_quant_admission import QuantAdmissionClient, load_quant_admission_authentication
    from .agent_research_relay import AgentRelayAuthentication
    from .agent_service import load_relay_key

    auth = LocalAuth(config.origin, transport_origin="http://127.0.0.1:8001")
    sampler = RuntimeMetrics(ledger, telemetry, config.account_id) if telemetry else None

    def health():
        try:
            with ledger.transaction() as tx:
                ready = (
                    tx.connection.execute(text("SELECT current_user")).scalar_one()
                    == "atl_control_api"
                )
        except Exception:
            ready = False
        write_status("api", "READY" if ready else "UNAVAILABLE")
        return {
            "status": "HEALTHY" if ready else "UNAVAILABLE",
            "checks": {"DATABASE": "HEALTHY" if ready else "UNAVAILABLE"},
        }

    backtesting = BacktestClient()
    try:
        agent_research = AgentResearchClient(AgentRelayAuthentication(load_relay_key()))
    except ValueError:
        agent_research = None
    try:
        agent_quant_approval = QuantAdmissionClient(load_quant_admission_authentication())
    except ValueError:
        agent_quant_approval = None
    canonical = create_app(
        auth=auth,
        backend=ControlBackend(ledger, config.account_id),
        supervisor=SnapshotReader(ledger, config.account_id),
        events=EventFeed(ledger, config.account_id),
        health=health,
        delivery_observer=sampler.delivered if sampler else None,
        demo=DemoClient(),
        setup=FixedSetupClient(),
        trading_profile=TradingProfileBackend(ledger, config.account_id),
        paper=PaperClient(),
        research=ResearchClient(),
        backtesting=backtesting,
        readiness=ReadinessClient(),
        collection=CollectionClient(),
        quant_lab=backtesting.quant_lab,
        agent_research=agent_research,
        agent_quant_approval=agent_quant_approval,
    )

    def status_loop():
        while True:
            health()
            if sampler:
                sampler.sample()
            if telemetry:
                telemetry.export_local()
            canonical.state.research_metrics.export_local()
            sleep(5)

    Thread(target=status_loop, daemon=True).start()

    def control_server():
        path = Path("/run/control/control.sock")
        if path.exists():
            _private_dir(path.parent)
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                return
            path.unlink()
        with LocalControlServer(
            path, dispatcher(ledger=ledger, account_id=config.account_id)
        ) as server:
            while True:
                try:
                    server.serve_once()
                except TimeoutError:
                    pass

    Thread(target=control_server, daemon=True).start()
    Thread(target=activation_server, args=(auth,), daemon=True).start()
    private = uvicorn.Server(
        uvicorn.Config(
            canonical,
            host="127.0.0.1",
            port=8001,
            access_log=False,
            log_config=None,
            proxy_headers=False,
            server_header=False,
        )
    )
    Thread(target=private.run, daemon=True).start()
    uvicorn.run(
        create_bridge(config, auth=auth),
        host="0.0.0.0",
        port=8000,
        access_log=False,
        log_config=None,
        proxy_headers=False,
        server_header=False,
    )
