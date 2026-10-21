"""Bounded fixed Paper relay; untrusted internal response data never becomes an error body."""

import json
from copy import deepcopy

import httpx
import pytest

import services.control_api.paper_client as module
from services.control_api.paper_client import PaperClient
from services.control_api.paper_commands import PaperCommandError
from tests.contracts.test_paper_workflow_contracts import example

TRACE = "a" * 32
OPERATION = "11111111-2222-4333-8444-555555555555"
CANARY = "synthetic-internal-response-canary-no-real-key"


def client(response=None, *, handler=None):
    calls = []
    def handle(request):
        calls.append(request)
        if handler is not None:
            return handler(request)
        return response or httpx.Response(200, json=example("paper-workflow"))
    return PaperClient(transport=httpx.MockTransport(handle)), calls


def canonical_error(call, status=503, code="TEMPORARILY_UNAVAILABLE"):
    with pytest.raises(PaperCommandError) as error:
        call()
    assert (error.value.status, error.value.code) == (status, code)
    assert str(error.value) == code and CANARY not in str(error.value)


def test_status_and_operation_relay_have_fixed_host_ports_routes_and_strict_projection():
    status = example("paper-workflow")
    instance, calls = client(httpx.Response(200, json=status))
    assert instance.read_status(TRACE) == status
    assert str(calls[0].url) == "http://paper-api:8012/status?trace=" + TRACE
    operation = example("paper-operation")
    instance, calls = client(httpx.Response(200, json=operation))
    assert instance.read_operation(OPERATION, TRACE) == operation
    assert str(calls[0].url) == "http://paper-api:8012/operations/" + OPERATION
    assert calls[0].method == "GET"


def test_command_and_rotation_send_only_caller_metadata_to_fixed_endpoints():
    def handle(request):
        if request.url.path == "/rotation/begin":
            return httpx.Response(200, json={"generation": 7})
        if request.url.path == "/rotation/complete":
            return httpx.Response(200, json={"completed": False})
        return httpx.Response(202, json=example("paper-operation"))
    instance, calls = client(handler=handle)
    assert instance.rotation_begin("local-operator", "metadata-key-0001", TRACE) == 7
    assert instance.rotation_complete(7) is False
    instance.enqueue("VERIFY_CONNECTION", {"credentialGeneration": 7}, "local-operator",
                     "metadata-key-0002", TRACE)
    assert [request.url.path for request in calls] == [
        "/rotation/begin", "/rotation/complete", "/commands",
    ]
    assert all(request.url.host == "paper-api" and request.url.port == 8012 for request in calls)
    bodies = [json.loads(request.content) for request in calls]
    assert bodies[:2] == [
        {"actorId": "local-operator", "key": "metadata-key-0001", "traceId": TRACE},
        {"generation": 7},
    ]
    assert bodies[2]["payload"] == {"credentialGeneration": 7}
    assert CANARY not in repr(bodies)


@pytest.mark.parametrize("status,body,expected", [
    (409, {"code": "VERSION_CONFLICT"}, (409, "VERSION_CONFLICT")),
    (404, {"code": "OPERATION_NOT_FOUND"}, (404, "OPERATION_NOT_FOUND")),
    (400, {"code": CANARY}, (503, "TEMPORARILY_UNAVAILABLE")),
    (409, {"code": "VERSION_CONFLICT", "apiKey": CANARY}, (503, "TEMPORARILY_UNAVAILABLE")),
    (302, {"code": "INVALID_REQUEST"}, (503, "TEMPORARILY_UNAVAILABLE")),
    (500, {"code": "INVALID_REQUEST"}, (503, "TEMPORARILY_UNAVAILABLE")),
])
def test_backend_error_codes_are_exact_allowlisted_and_redacted(status, body, expected):
    instance, calls = client(httpx.Response(status, json=body))
    canonical_error(lambda: instance.read_status(TRACE), *expected)
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=CANARY),
    httpx.Response(200, content=b'{"x":1,"x":2}'),
    httpx.Response(200, content=b'{"x":NaN}'),
    httpx.Response(200, content=b'[]'),
    httpx.Response(200, content=b'x' * 65537),
])
def test_malformed_duplicate_nonfinite_or_unbounded_response_is_canonical(response):
    instance, _ = client(response)
    canonical_error(lambda: instance.read_status(TRACE))


@pytest.mark.parametrize("kind", ["status", "operation"])
def test_success_response_cannot_smuggle_canary_fields_or_raw_validation_errors(kind):
    value = deepcopy(example("paper-workflow" if kind == "status" else "paper-operation"))
    value["apiKey"] = CANARY
    instance, _ = client(httpx.Response(200, json=value))
    canonical_error(lambda: instance.read_status(TRACE) if kind == "status"
                    else instance.read_operation(OPERATION, TRACE))


@pytest.mark.parametrize("generation", [True, 0, -1, "7", 9007199254740992])
def test_backend_generation_response_is_strict_and_uses_canonical_transport_error(generation):
    instance, _ = client(httpx.Response(200, json={"generation": generation}))
    canonical_error(lambda: instance.rotation_begin("local-operator", "metadata-key-0001", TRACE))


def test_deadline_and_transport_exceptions_are_redacted(monkeypatch):
    monkeypatch.setattr(module, "monotonic", lambda: 0)
    def failure(request):
        raise httpx.ReadTimeout(CANARY, request=request)
    instance, _ = client(handler=failure)
    canonical_error(lambda: instance.read_status(TRACE))
    clock = iter([0, 16])
    monkeypatch.setattr(module, "monotonic", lambda: next(clock))
    instance, _ = client()
    canonical_error(lambda: instance.read_status(TRACE))


def test_arbitrary_hosts_or_transports_are_not_constructor_options():
    with pytest.raises(TypeError):
        PaperClient(base_url="https://invalid.example/")
    with pytest.raises(ValueError, match="exact MockTransport"):
        PaperClient(transport=object())
    instance, calls = client()
    canonical_error(
        lambda: instance.read_status("https://invalid.example/"), 400, "INVALID_REQUEST",
    )
    assert calls == []
