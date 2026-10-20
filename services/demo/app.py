"""Fixed internal protocol. External auth and origin belong to Control API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from services.demo.coordinator import DemoError, create_coordinator


def create_app(coordinator=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.coordinator = coordinator or create_coordinator()
        yield
        app.state.coordinator.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "AVAILABLE", "simulationOnly": True}

    @app.post("/internal/demo")
    async def dispatch(request: Request):
        # Protocol accepts only public parameters, never arbitrary call attributes.
        try:
            raw = await request.body()
            if len(raw) > 16384:
                raise DemoError("INVALID_REQUEST", 400)
            import json

            payload = json.loads(raw)
            if type(payload) is not dict:
                raise DemoError("INVALID_REQUEST", 400)
            op = payload.pop("operation", None)
            api = request.app.state.coordinator
            if op == "start" and set(payload) == {"payload", "key", "actor", "trace"}:
                status, body, headers = api.start(**payload)
                return JSONResponse(body, status_code=status, headers=headers)
            methods = {
                "list_runs": (set(), api.list_runs),
                "get_run": ({"run_id"}, api.get_run),
                "snapshot": ({"run_id", "trace"}, api.snapshot),
                "control": ({"run_id", "action", "actor", "trace"}, api.control),
            }
            if (
                op == "history"
                and set(payload) <= {"run_id", "resource", "cursor", "limit", "trace_id"}
                and {"run_id", "resource"} <= set(payload)
            ):
                return api.history(**payload)
            if op not in methods or set(payload) != methods[op][0]:
                raise DemoError("INVALID_REQUEST", 400)
            return methods[op][1](**payload)
        except DemoError as error:
            return JSONResponse({"code": error.code}, status_code=error.status)
        except (ValueError, TypeError):
            return JSONResponse({"code": "INVALID_REQUEST"}, status_code=400)
        except Exception:
            return JSONResponse({"code": "TEMPORARILY_UNAVAILABLE"}, status_code=503)

    return app
