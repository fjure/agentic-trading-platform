"""Verify the start-only private Quant grant and canonical approval receipt."""

from datetime import UTC, datetime
from time import time

import httpx
import pytest
from fastapi.testclient import TestClient

from packages.backtesting.jobs import BacktestError
from services.control_api.agent_research_routes import AgentResearchError
from services.runtime.agent_quant_admission import (
    OPERATION,
    PATH,
    SIGNATURE,
    URL,
    QuantAdmissionAuthentication,
    QuantAdmissionClient,
    _AdmissionOnly,
    _check_grant,
    _decode,
    _encode,
    _start,
    create_quant_admission_app,
)

APPROVAL = "a2f4b8f0-1234-4000-8000-111111111111"
BATCH = "b2f4b8f0-1234-4000-8000-222222222222"
BINDING = "a" * 64
TRACE = "b" * 32
SCOPE = "c" * 64
KEY = "quant-start-command-001"
PAYLOAD = {"action": "APPROVE_AND_START", "expectedApprovalVersion": 1, "bindingHash": BINDING}
CONTEXT = {"actor": "local-operator", "key": KEY, "trace": TRACE, "scope": SCOPE}
DECIDED = datetime(2026, 11, 8, 12, 0, 1, tzinfo=UTC)
AUTH = QuantAdmissionAuthentication(bytes(range(32)))


class Quant:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def start_approved_experiment_batch(self, approval_id, binding, **context):
        self.calls.append((approval_id, binding, context))
        if self.failure is not None:
            raise self.failure
        return {"id": BATCH, "createdAt": "2026-11-08T12:00:02Z"}


def transport(service, receipt, *, response_key=AUTH, alter_response=lambda value: value):
    def relay(request):
        assert str(request.url) == URL
        assert request.method == "POST"
        assert set(request.headers) <= {
            "host",
            "content-type",
            "accept-encoding",
            "accept",
            "connection",
            "user-agent",
            "content-length",
            SIGNATURE.lower(),
        }
        body = request.content
        assert request.headers[SIGNATURE] == AUTH.request_signature(body)
        grant = _decode(body)
        _check_grant(grant)
        status, value = _start(service, receipt, grant)
        reply = _encode({"status": status, "value": alter_response(value)})
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                SIGNATURE: response_key.response_signature(body, reply),
            },
            stream=httpx.ByteStream(reply),
        )

    return httpx.MockTransport(relay)


def call(client, payload=PAYLOAD, context=CONTEXT):
    return client(OPERATION, {"id": APPROVAL}, {}, payload, **context)


def test_quant_grant_uses_native_approval_and_durable_receipt():
    service = Quant()
    receipts = []

    def read(actor, approval, batch):
        receipts.append((actor, approval, batch))
        return DECIDED

    client = QuantAdmissionClient(AUTH, transport=transport(service, read))
    status, value = call(client)
    assert status == 202
    assert value == {
        "approvalId": APPROVAL,
        "approvalVersion": 2,
        "state": "STARTED",
        "batchId": BATCH,
        "decidedAt": "2026-11-08T12:00:01Z",
    }
    assert call(client) == (status, value)
    assert service.calls == [(APPROVAL, BINDING, CONTEXT)] * 2
    assert receipts == [("local-operator", APPROVAL, BATCH)] * 2


@pytest.mark.parametrize(
    ("payload", "context", "status", "code"),
    [
        (PAYLOAD | {"action": "REJECT"}, CONTEXT, 403, "REQUEST_FORBIDDEN"),
        (PAYLOAD | {"expectedApprovalVersion": 2}, CONTEXT, 409, "APPROVAL_STALE"),
        (PAYLOAD, CONTEXT | {"actor": "research"}, 403, "REQUEST_FORBIDDEN"),
    ],
)
def test_quant_client_never_signs_an_unbound_operator_start(payload, context, status, code):
    calls = []
    client = QuantAdmissionClient(AUTH, transport=httpx.MockTransport(calls.append))
    with pytest.raises(AgentResearchError) as failure:
        call(client, payload, context)
    assert (failure.value.status, failure.value.code) == (status, code)
    assert calls == []


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (BacktestError(409, "IDEMPOTENCY_CONFLICT"), 409, "IDEMPOTENCY_CONFLICT"),
        (BacktestError(429, "QUANT_CAPACITY_EXHAUSTED"), 429, "QUANT_CAPACITY_EXHAUSTED"),
        (BacktestError(409, "BUDGET_EXHAUSTED"), 429, "AGENT_BUDGET_EXHAUSTED"),
    ],
)
def test_quant_failures_remain_typed_and_never_read_a_receipt(failure, status, code):
    service, reads = Quant(failure), []
    client = QuantAdmissionClient(
        AUTH, transport=transport(service, lambda *args: reads.append(args))
    )
    actual, problem = call(client)
    assert (actual, problem["status"], problem["code"], problem["traceId"]) == (
        status,
        status,
        code,
        TRACE,
    )
    assert reads == []


@pytest.mark.parametrize(
    ("response_key", "alter_response"),
    [
        (QuantAdmissionAuthentication(bytes(reversed(range(32)))), lambda value: value),
        (AUTH, lambda value: value | {"approvalId": "other-approval"}),
        (AUTH, lambda value: value | {"private": "internal diagnostics"}),
    ],
)
def test_quant_client_rejects_foreign_mismatched_or_expanded_replies(response_key, alter_response):
    client = QuantAdmissionClient(
        AUTH,
        transport=transport(
            Quant(), lambda *_: DECIDED, response_key=response_key, alter_response=alter_response
        ),
    )
    with pytest.raises(AgentResearchError) as failure:
        call(client)
    assert (failure.value.status, failure.value.code) == (503, "PROVIDER_UNAVAILABLE")


def test_quant_native_adapter_reuses_storage_admission_and_audit():
    class Storage:
        def __init__(self):
            self.calls = []

        def enqueue_approved(self, *args):
            self.calls.append(("start", args))
            raise BacktestError(429, "QUEUE_EXHAUSTED")

        def record_approval_denial(self, *args):
            self.calls.append(("audit", args))

    storage, version = Storage(), "a" * 40
    service = _AdmissionOnly(storage, version)
    with pytest.raises(BacktestError) as failure:
        service.start_approved_experiment_batch(APPROVAL, BINDING, **CONTEXT)
    assert (failure.value.status, failure.value.code) == (429, "QUANT_CAPACITY_EXHAUSTED")
    assert storage.calls == [
        ("start", (APPROVAL, BINDING, "local-operator", KEY, TRACE, version)),
        ("audit", (APPROVAL, "local-operator", TRACE, "QUANT_CAPACITY_EXHAUSTED")),
    ]


def test_quant_private_endpoint_verifies_grant_before_native_admission():
    service = Quant()
    app = create_quant_admission_app(AUTH, service=service, read_started_at=lambda *_: DECIDED)
    grant = {
        "id": APPROVAL,
        "bindingHash": BINDING,
        **CONTEXT,
        "issuedAt": int(time()),
        "nonce": "d" * 32,
    }
    body = _encode(grant)

    def headers(content):
        return {"Content-Type": "application/json", SIGNATURE: AUTH.request_signature(content)}

    with TestClient(app) as http:
        assert (
            http.post(PATH, content=body, headers={"Content-Type": "application/json"}).status_code
            == 403
        )
        tampered = _encode(grant | {"key": "changed-command-key"})
        assert http.post(PATH, content=tampered, headers=headers(body)).status_code == 403
        expired = _encode(grant | {"issuedAt": int(time()) - 60})
        assert http.post(PATH, content=expired, headers=headers(expired)).status_code == 400
        duplicate = body[:-1] + b',"actor":"local-operator"}'
        assert http.post(PATH, content=duplicate, headers=headers(duplicate)).status_code == 400
        assert service.calls == []
        response = http.post(PATH, content=body, headers=headers(body))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers[SIGNATURE] == AUTH.response_signature(body, response.content)
    assert response.json()["status"] == 202
    assert service.calls == [(APPROVAL, BINDING, CONTEXT)]
