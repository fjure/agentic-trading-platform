"""Independent local ingress for research credential mutations."""

import asyncio
import json
import os
import re
import secrets
import stat
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from services.control_api.agent_research_routes import (
    OPERATIONS,
    SETUP,
    AgentResearchError,
    agent_research_problem,
    validate_payload,
    validate_response,
)
from services.control_api.auth import AuthDenied, LocalAuth
from services.llm_gateway.provider_wire import Provider
from services.runtime.agent_provider_controls import _credential

from .llm_credentials import CredentialDenied
from .research_credentials import ResearchCredentialStore
from .research_ingress import MAX_BODY, PRIVATE_SETUP_PATH
from .store import _pairs

SOCKET = Path("/run/research-setup/setup.sock")
ACTIVATION_SOCKET = Path("/run/research-setup/private/activate.sock")
GRANT_HEADER = "x-research-setup-grant"
_GRANT = re.compile(r"[A-Za-z0-9_-]{43}")
_ROUTES = {
    (operation.method, operation.path): operation for operation in OPERATIONS if operation.setup
}


def create_app(storage, authority: LocalAuth, *, lifespan=None) -> FastAPI:
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False,
        lifespan=lifespan,
    )
    grant_lock = Lock()
    grant_scope = None

    def issue_grant(provider, action):
        nonlocal grant_scope
        with grant_lock:
            grant_scope = (provider, action)
            return authority.issue_activation_code()

    app.state.issue_grant = issue_grant

    async def status(request: Request, provider: str):
        trace = secrets.token_hex(16)
        if (
            request.scope["query_string"]
            or request.headers.getlist("host") != [authority.host]
            or request.headers.getlist("origin") not in ([], [authority.origin])
        ):
            return agent_research_problem(403, "REQUEST_FORBIDDEN", trace)
        try:
            provider_id = Provider(provider)
            value = _credential(await run_in_threadpool(storage.status, provider_id))
            operation = _ROUTES["PUT", SETUP + "/{provider}"]
            validate_response(operation, 200, value)
        except (AgentResearchError, CredentialDenied, ValueError):
            return agent_research_problem(503, "PROVIDER_UNAVAILABLE", trace)
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    async def mutate(request: Request, provider: str, action: str = ""):
        trace = secrets.token_hex(16)
        operation = _ROUTES.get(
            (request.method, SETUP + "/{provider}" + ("/" + action if action else ""))
        )

        def problem(status, code):
            return agent_research_problem(status, code, trace, operation)

        try:
            if operation is None or request.scope["query_string"]:
                return problem(404, "RESOURCE_NOT_FOUND")
            provider_id = Provider(provider)
            grants = request.headers.getlist(GRANT_HEADER)
            if (
                request.headers.getlist("host") != [authority.host]
                or request.headers.getlist("origin") != [authority.origin]
                or request.headers.getlist("content-type")
                not in (["application/json"], ["application/json; charset=utf-8"])
                or len(grants) != 1 or not _GRANT.fullmatch(grants[0])
            ):
                return problem(403, "REQUEST_FORBIDDEN")
            # A fresh server nonce prevents LocalAuth's session replay path from
            # accepting the same local setup grant on a second mutation.
            with grant_lock:
                if grant_scope != (provider, action or "save"):
                    return problem(403, "REQUEST_FORBIDDEN")
                authority.activate(grants[0], secrets.token_hex(24))
            if action == "activate":
                return problem(503, "PROVIDER_UNAVAILABLE")
            data = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(data) + len(chunk) > MAX_BODY:
                            return problem(422, "INVALID_REQUEST")
                        data.extend(chunk)
                payload = json.loads(data, object_pairs_hook=_pairs)
            finally:
                data.clear()
            validate_payload(operation, payload)
            version = payload["expectedCredentialVersion"]
            if action == "rotate":
                status = await run_in_threadpool(
                    storage.rotate, provider_id, payload.pop("credential"),
                    expected_version=version,
                    expected_generation=payload["expectedCredentialGeneration"],
                )
            elif action == "revoke":
                status = await run_in_threadpool(
                    storage.revoke, provider_id, expected_version=version,
                    generation=payload["credentialGeneration"],
                )
            else:
                status = await run_in_threadpool(
                    storage.save, provider_id, payload.pop("credential"), expected_version=version,
                )
        except AuthDenied:
            return problem(403, "REQUEST_FORBIDDEN")
        except CredentialDenied as error:
            if error.code == "VERSION_CONFLICT":
                return problem(409, "VERSION_CONFLICT")
            if error.code == "CREDENTIAL_REQUEST_DENIED":
                return problem(422, "INVALID_REQUEST")
            return problem(503, "PROVIDER_UNAVAILABLE")
        except (AgentResearchError, TimeoutError, ValueError, RecursionError):
            return problem(422, "INVALID_REQUEST")
        except Exception:
            return problem(503, "PROVIDER_UNAVAILABLE")
        value = _credential(status)
        try:
            validate_response(operation, 200, value)
        except AgentResearchError:
            return problem(503, "PROVIDER_UNAVAILABLE")
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    app.add_api_route(PRIVATE_SETUP_PATH + "/{provider}", mutate, methods=["PUT"])
    app.add_api_route(PRIVATE_SETUP_PATH + "/{provider}", status, methods=["GET"])
    app.add_api_route(PRIVATE_SETUP_PATH + "/{provider}/{action}", mutate, methods=["POST"])
    return app


def main():
    import socket

    import uvicorn

    from services.runtime.api import _private_dir

    if sys.argv[1:2] == ["activate"]:
        if (
            len(sys.argv) != 4 or sys.argv[2] not in {p.value for p in Provider}
            or sys.argv[3] not in {"save", "rotate", "revoke"}
        ):
            raise ValueError("activate requires provider and operation")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise PermissionError("INTERACTIVE_OPERATOR_REQUIRED")
        info = ACTIVATION_SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError("PRIVATE_ENDPOINT_UNAVAILABLE")
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(ACTIVATION_SOCKET))
            client.sendall(f"{sys.argv[2]} {sys.argv[3]}\n".encode("ascii"))
            code = client.recv(128).decode("ascii").strip()
        if not _GRANT.fullmatch(code):
            raise ValueError("ACTIVATION_UNAVAILABLE")
        print(code, flush=True)
        return

    authority = LocalAuth()
    _private_dir(ACTIVATION_SOCKET.parent)
    for path in (SOCKET, ACTIVATION_SOCKET):
        if os.path.lexists(path):
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid():
                raise PermissionError("PRIVATE_ENDPOINT_UNAVAILABLE")
            path.unlink()

    async def grant(reader, writer):
        try:
            async with asyncio.timeout(2):
                command = await reader.readline()
                match = re.fullmatch(rb"(OPENAI|ANTHROPIC) (save|rotate|revoke)\n", command)
                if match:
                    provider, action = (value.decode("ascii") for value in match.groups())
                    writer.write(app.state.issue_grant(provider, action).encode("ascii") + b"\n")
                    await writer.drain()
        except (TimeoutError, OSError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    @asynccontextmanager
    async def lifespan(_app):
        server = await asyncio.start_unix_server(grant, path=ACTIVATION_SOCKET, limit=32)
        os.chmod(ACTIVATION_SOCKET, 0o600)
        async with server:
            yield

    listener = socket.socket(socket.AF_UNIX)
    app = create_app(ResearchCredentialStore(), authority, lifespan=lifespan)
    with listener:
        listener.bind(str(SOCKET))
        os.chmod(SOCKET, 0o660)
        listener.listen(16)
        uvicorn.Server(uvicorn.Config(
            app,
            access_log=False, proxy_headers=False, log_config=None,
        )).run(sockets=[listener])


if __name__ == "__main__":
    main()
