"""Narrow authenticated Control-to-Quant transport for explicit operator starts."""

import asyncio
import json
from hmac import compare_digest, digest
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from packages.backtesting.experiment_storage import ExperimentStorage
from packages.backtesting.jobs import BacktestError
from packages.domain.models import utc_string
from services.control_api.agent_research_routes import (
    IDEMPOTENCY_KEY,
    OPERATIONS,
    SCOPE,
    TRACE,
    AgentResearchError,
    problem_value,
    validate_arguments,
    validate_payload,
    validate_problem,
    validate_response,
)
from services.control_api.quant_lab_dto import HEX, UUID

PATH = "/private/v1/quant/approved-start"
URL = "http://quant-admission:8014" + PATH
SIGNATURE = "X-Quant-Admission-Signature"
RELAY_KEY_PATH = Path("/run/secrets/quant-admission-relay-key")
DATABASE_PATH = Path("/run/secrets/quant-admission-database")
MAX_BYTES = 2048
OPERATION = next(op for op in OPERATIONS if op.operation_id == "decideAgentQuantApproval")
_FIELDS = {"id", "bindingHash", "actor", "key", "trace", "scope", "issuedAt", "nonce"}


class QuantAdmissionAuthentication:
    """The signing key is shared exclusively by Control and the Quant admission authority."""

    def __init__(self, key: bytes):
        if type(key) is not bytes or len(key) != 32:
            raise ValueError("invalid Quant admission key")
        self._key = key

    def request_signature(self, request: bytes) -> str:
        return digest(self._key, b"apf-quant-admission-request-v1\0" + request, "sha256").hex()

    def response_signature(self, request: bytes, response: bytes) -> str:
        request_digest = digest(self._key, request, "sha256")
        return digest(
            self._key, b"apf-quant-admission-response-v1\0" + request_digest + response, "sha256"
        ).hex()


def _encode(value: dict) -> bytes:
    result = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
    if len(result) > MAX_BYTES:
        raise ValueError("invalid Quant admission body")
    return result


def _decode(raw: bytes) -> dict:
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("invalid Quant admission body")
    value = json.loads(raw.decode("ascii"))
    # Canonical bytes reject duplicate object keys and ambiguous encodings.
    if not isinstance(value, dict) or _encode(value) != raw:
        raise ValueError("invalid Quant admission body")
    return value


def _check_grant(value: dict) -> None:
    if set(value) != _FIELDS or value["actor"] != "local-operator":
        raise ValueError("invalid Quant admission grant")
    for name, pattern in (
        ("id", UUID),
        ("bindingHash", HEX),
        ("key", IDEMPOTENCY_KEY),
        ("trace", TRACE),
        ("scope", SCOPE),
        ("nonce", TRACE),
    ):
        if type(value[name]) is not str or not pattern.fullmatch(value[name]):
            raise ValueError("invalid Quant admission grant")
    if type(value["issuedAt"]) is not int or not -2 <= int(time()) - value["issuedAt"] <= 15:
        raise ValueError("expired Quant admission grant")


def started_receipt_reader(admission_engine):
    """Return only the durable time of a committed approval for the matching batch."""
    storage = ExperimentStorage(admission_engine)

    def read(actor, approval_id, batch_id):
        with storage.transaction() as connection:
            return connection.execute(
                text("""
                SELECT agent_research.quant_started_receipt(
                    :actor, CAST(:approval AS uuid), CAST(:batch AS uuid))
                """),
                {"actor": actor, "approval": approval_id, "batch": batch_id},
            ).scalar_one()

    return read


def _start(service, read_started_at, grant):
    identifier, actor = grant["id"], grant["actor"]
    try:
        batch = service.start_approved_experiment_batch(
            identifier,
            grant["bindingHash"],
            actor=actor,
            key=grant["key"],
            trace=grant["trace"],
            scope=grant["scope"],
        )
        decided_at = read_started_at(actor, identifier, batch["id"])
        if decided_at is None:
            raise BacktestError(503, "UNAVAILABLE")
        value = {
            "approvalId": identifier,
            "approvalVersion": 2,
            "state": "STARTED",
            "batchId": batch["id"],
            "decidedAt": utc_string(decided_at),
        }
        validate_response(OPERATION, 202, value)
        return 202, value
    except BacktestError as exc:
        status, code = exc.status, exc.code
        if code == "BUDGET_EXHAUSTED":
            status, code = 429, "AGENT_BUDGET_EXHAUSTED"
        value = problem_value(status, code, grant["trace"], OPERATION)
        return value["status"], value


def create_quant_admission_app(auth, *, service, read_started_at) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    slots, workers = anyio.CapacityLimiter(2), anyio.CapacityLimiter(2)

    @app.post(PATH)
    async def dispatch(request: Request):
        signatures = request.headers.getlist(SIGNATURE)
        if len(signatures) != 1 or not HEX.fullmatch(signatures[0]):
            return Response(status_code=403)
        if (
            request.url.query
            or request.headers.getlist("content-type") != ["application/json"]
            or request.headers.get("content-encoding", "identity") != "identity"
        ):
            return Response(status_code=400)
        try:
            slots.acquire_nowait()
        except anyio.WouldBlock:
            return Response(status_code=503)
        try:
            raw = bytearray()
            async with asyncio.timeout(4):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_BYTES:
                        raise ValueError("invalid Quant admission body")
                    raw.extend(chunk)
            body = bytes(raw)
            if not compare_digest(auth.request_signature(body), signatures[0]):
                return Response(status_code=403)
            grant = _decode(body)
            _check_grant(grant)
            status, value = await anyio.to_thread.run_sync(
                _start, service, read_started_at, grant, limiter=workers
            )
            reply = _encode({"status": status, "value": value})
            return Response(
                reply,
                media_type="application/json",
                headers={
                    SIGNATURE: auth.response_signature(body, reply),
                    "Cache-Control": "no-store",
                },
            )
        except (ValueError, RecursionError, TimeoutError):
            return Response(status_code=400)
        except Exception:
            return Response(status_code=503)
        finally:
            slots.release()

    return app


class QuantAdmissionClient:
    """Control's start-only handler; unprivileged Research receives no grant or key."""

    def __init__(self, auth: QuantAdmissionAuthentication, *, transport=None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise ValueError("exact MockTransport required")
        self._auth, self._transport = auth, transport

    def __call__(self, operation, params, query, payload, *, actor, key, trace, scope):
        if operation != OPERATION or actor != "local-operator":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        params, _ = validate_arguments(operation, params, query.items())
        validate_payload(operation, payload)
        if payload["action"] != "APPROVE_AND_START":
            raise AgentResearchError(403, "REQUEST_FORBIDDEN")
        if type(payload["expectedApprovalVersion"]) is not int:
            raise AgentResearchError(422, "INVALID_REQUEST")
        if payload["expectedApprovalVersion"] != 1:
            raise AgentResearchError(409, "APPROVAL_STALE")
        if not UUID.fullmatch(params["id"]):
            raise AgentResearchError(404, "RESOURCE_NOT_FOUND")
        grant = {
            "id": params["id"],
            "bindingHash": payload["bindingHash"],
            "actor": actor,
            "key": key,
            "trace": trace,
            "scope": scope,
            "issuedAt": int(time()),
            "nonce": uuid4().hex,
        }
        try:
            _check_grant(grant)
            body = _encode(grant)
            reply, signature = self._send(body)
            expected = self._auth.response_signature(body, reply)
            if not HEX.fullmatch(signature) or not compare_digest(expected, signature):
                raise ValueError("invalid Quant admission response signature")
            value = _decode(reply)
            if set(value) != {"status", "value"}:
                raise ValueError("invalid Quant admission response")
            status, result = value["status"], value["value"]
            if status == 202:
                validate_response(operation, status, result)
                if result["approvalId"] != params["id"] or result["approvalVersion"] != 2:
                    raise ValueError("invalid Quant admission receipt")
            else:
                validate_problem(operation, status, result)
                if result["traceId"] != trace:
                    raise ValueError("invalid Quant admission response trace")
            return status, result
        except Exception:
            raise AgentResearchError(503, "PROVIDER_UNAVAILABLE") from None

    def _send(self, body):
        deadline = monotonic() + 5
        with httpx.Client(
            transport=self._transport, timeout=3, trust_env=False, follow_redirects=False
        ) as client:
            with client.stream(
                "POST",
                URL,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                    SIGNATURE: self._auth.request_signature(body),
                },
            ) as response:
                signatures = response.headers.get_list(SIGNATURE)
                if (
                    response.status_code != 200
                    or response.headers.get_list("content-type") != ["application/json"]
                    or response.headers.get("content-encoding", "identity") != "identity"
                    or len(signatures) != 1
                ):
                    raise ValueError("invalid Quant admission response")
                raw = bytearray()
                for chunk in response.iter_raw():
                    if len(raw) + len(chunk) > MAX_BYTES or monotonic() >= deadline:
                        raise ValueError("invalid Quant admission response size")
                    raw.extend(chunk)
                return bytes(raw), signatures[0]


class _AdmissionOnly:
    """Call the same atomic native admission as QuantLabService with its restricted DB role."""

    def __init__(self, storage, code_version):
        self.storage, self.code_version = storage, code_version

    def start_approved_experiment_batch(
        self, approval_id, binding_hash, *, actor, key, trace, scope
    ):
        try:
            batch_id = self.storage.enqueue_approved(
                approval_id, binding_hash, actor, key, trace, self.code_version
            )
        except BacktestError as error:
            code = "QUANT_CAPACITY_EXHAUSTED" if error.status == 429 else error.code
            if code in {
                "APPROVAL_STALE",
                "QUANT_CAPACITY_EXHAUSTED",
                "BUDGET_EXHAUSTED",
                "IDEMPOTENCY_CONFLICT",
            }:
                try:
                    self.storage.record_approval_denial(approval_id, actor, trace, code)
                except BacktestError:
                    pass
            if error.status == 429:
                raise BacktestError(429, code) from None
            raise
        return {"id": str(batch_id)}


def load_quant_admission_authentication():
    from services.runtime.agent_service import load_relay_key

    try:
        return QuantAdmissionAuthentication(load_relay_key(RELAY_KEY_PATH))
    except ValueError:
        raise ValueError("QUANT_ADMISSION_UNAVAILABLE") from None


def _database_engine():
    from sqlalchemy import URL, create_engine, event

    from services.runtime.config import secret_fields

    role, login = "atl_agent_quant_admission", "atl_agent_quant_admission_login"
    credentials = secret_fields(DATABASE_PATH, {"username", "password"})
    if credentials["username"] != login:
        raise ValueError("QUANT_ADMISSION_DATABASE_UNAVAILABLE")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=login,
            password=credentials["password"],
            host="postgres",
            port=5432,
            database="lab_paper_runtime",
        ),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 3},
    )

    @event.listens_for(engine, "connect")
    def assign_role(connection, _record):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET ROLE atl_agent_quant_admission")
                cursor.execute("SET TIME ZONE 'UTC'")
                cursor.execute("SET statement_timeout='3s'")
                cursor.execute("SET lock_timeout='1s'")
                cursor.execute("SELECT current_user,current_database(),session_user")
                if cursor.fetchone() != (role, "lab_paper_runtime", login):
                    raise ValueError
            connection.commit()
        except Exception:
            connection.rollback()
            raise PermissionError("QUANT_ADMISSION_DATABASE_UNAVAILABLE") from None

    return engine


def main():
    import uvicorn

    from services.runtime.backtesting import source_version

    version = source_version()
    if version is None:
        raise ValueError("QUANT_ADMISSION_UNAVAILABLE")
    auth = load_quant_admission_authentication()
    engine = _database_engine()
    try:
        with engine.connect():
            pass
        service = _AdmissionOnly(ExperimentStorage(engine), version)
        app = create_quant_admission_app(
            auth, service=service, read_started_at=started_receipt_reader(engine)
        )
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8014,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            limit_concurrency=6,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
