"""Every final provider read precedes the durable authorization callback."""

import pytest

from services.execution.gates import SubmitDenied
from tests.contracts.test_alpaca_paper import PaperServer, make_adapter, prepared


def test_only_the_post_request_occurs_after_successful_final_commit():
    server = PaperServer()
    broker = make_adapter(server)
    intent = prepared(broker)
    boundary = []
    broker.submit(intent, before_io=lambda: boundary.append(len(server.requests)))
    assert len(boundary) == 1
    assert [(request.method, request.url.path) for request in server.requests[boundary[0]:]] == [
        ("POST", "/v2/orders"),
    ]


def test_kill_during_request_layer_final_account_read_prevents_commit_and_post():
    server = PaperServer()
    state = dict(final=False, killed=False, commits=0)

    def handler(request):
        response = server(request)
        if state["final"] and request.url.path == "/v2/account":
            state["killed"] = True
        return response

    broker = make_adapter(handler)
    intent = prepared(broker)
    original = broker._request

    def request(method, path, **kwargs):
        if method == "POST":
            state["final"] = True
        return original(method, path, **kwargs)

    broker._request = request

    def commit():
        if state["killed"]:
            raise SubmitDenied("PAPER_ACTIVATION_INVALID")
        state["commits"] += 1

    with pytest.raises(SubmitDenied, match="PAPER_ACTIVATION_INVALID"):
        broker.submit(intent, before_io=commit)
    assert state["killed"] and state["commits"] == 0
    assert server.count("POST", "/v2/orders") == 0
    broker.submit(intent, before_io=lambda: pytest.fail("unproven commit must remain lookup-only"))
    assert server.count("POST", "/v2/orders") == 0
