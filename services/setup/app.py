"""Internal bounded write-only HTTP service. Authentication lives at Control API."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .store import MAX_BODY, ProviderStore, SetupInvalid, SetupUnavailable, parse_body


def _response(value, status=200):
    return JSONResponse(value, status_code=status, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


def create_app(store: ProviderStore | None = None) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False)
    storage = store if store is not None else ProviderStore()

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        return _response({"code": "INVALID_REQUEST"}, error.status_code)

    @app.exception_handler(SetupInvalid)
    async def invalid(_request, _error):
        return _response({"code": "INVALID_REQUEST"}, 400)

    @app.exception_handler(SetupUnavailable)
    async def unavailable(_request, _error):
        return _response({"code": "TEMPORARILY_UNAVAILABLE"}, 503)

    @app.exception_handler(Exception)
    async def unexpected(_request, _error):
        return _response({"code": "TEMPORARILY_UNAVAILABLE"}, 503)

    @app.get("/integrations")
    async def integrations():
        return _response(await run_in_threadpool(storage.status))

    async def save(provider, request):
        try:
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                raise SetupInvalid()
            declared = request.headers.get("content-length")
            if declared is not None and (not declared.isdecimal() or int(declared) > MAX_BODY):
                raise SetupInvalid()
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY:
                    raise SetupInvalid()
                body.extend(chunk)
            value = parse_body(provider, bytes(body))
            return _response(await run_in_threadpool(storage.save, provider, value))
        except (SetupInvalid, SetupUnavailable):
            raise
        except Exception:
            raise SetupInvalid() from None

    @app.put("/integrations/alpaca")
    async def alpaca(request: Request):
        return await save("alpaca", request)

    @app.put("/integrations/jev")
    async def jev(request: Request):
        return await save("jev", request)

    return app
