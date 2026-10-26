"""The credential-free fixed relay validates all public output and bounds bodies."""

import json
from pathlib import Path

import httpx
import pytest

from packages.research.jobs import ResearchJobError
from services.control_api.research_client import ResearchClient
from services.control_api.research_routes import CURRENT_TRACE

ROOT = Path(__file__).resolve().parents[2]


def catalog():
    return json.loads(
        (ROOT / "contracts/examples/v1/research-source-catalog.valid.json").read_text()
    )


def wire(status, value):
    return httpx.Response(status, stream=httpx.ByteStream(json.dumps(value).encode()),
                          headers={"content-type":"application/json"})


def test_relay_fixed_host_get_identity_encoding_and_trace_without_keys():
    def provider(request):
        assert str(request.url) == "http://paper-api:8012/api/v1/research/sources"
        assert request.method == "GET" and request.headers["accept-encoding"] == "identity"
        assert request.headers["traceparent"].startswith("00-" + "a" * 32 + "-")
        assert not any("apca" in header or "authorization" in header for header in request.headers)
        return wire(200,catalog())

    token = CURRENT_TRACE.set("a" * 32)
    try:
        assert ResearchClient(transport=httpx.MockTransport(provider)).read_sources() == catalog()
    finally:
        CURRENT_TRACE.reset(token)


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(302, headers={"location": "https://unapproved.invalid"}),
        lambda: httpx.Response(200, json={**catalog(), "privateCursor": "synthetic-canary"}),
        lambda: httpx.Response(200, content=b'{"items":[],"items":[]}'),
        lambda: httpx.Response(200, json=catalog(), headers={"content-encoding": "gzip"}),
        lambda: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
        lambda: httpx.Response(503, json={"code": "PRIVATE_synthetic-canary"}),
        lambda: httpx.Response(401, json={"code": "UNAUTHORIZED", "payload": "synthetic-canary"}),
    ],
)
def test_relay_rejects_unsafe_untyped_or_unbounded_responses_without_context(response):
    client = ResearchClient(transport=httpx.MockTransport(lambda _: response()))
    with pytest.raises(ResearchJobError) as captured:
        client.read_sources()
    assert (captured.value.status, captured.value.code) == (503, "UNAVAILABLE")
    assert captured.value.__context__ is None and "canary" not in str(captured.value)


def test_typed_private_problem_relay_retains_only_approved_code_and_status():
    client = ResearchClient(
        transport=httpx.MockTransport(
                lambda _: wire(409, {"code": "CURSOR_INVALID", "traceId": "a" * 32})
        )
    )
    with pytest.raises(ResearchJobError) as captured:
        client.read_sources()
    assert (captured.value.status, captured.value.code) == (409, "CURSOR_INVALID")
