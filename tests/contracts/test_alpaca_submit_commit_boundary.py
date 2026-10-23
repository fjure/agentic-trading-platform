"""The final pre-I/O commit callback is distinct from broker network waits."""

import pytest

from tests.contracts.test_alpaca_paper import PaperServer, make_adapter, prepared


def test_failed_final_commit_prevents_post_and_reentry_is_lookup_only():
    server = PaperServer()
    broker = make_adapter(server)
    intent = prepared(broker)
    calls = []

    def fail():
        calls.append("commit-failed")
        raise RuntimeError("synthetic-commit-failed")

    with pytest.raises(RuntimeError, match="synthetic-commit-failed"):
        broker.submit(intent, before_io=fail)
    assert calls == ["commit-failed"] and server.count("POST", "/v2/orders") == 0
    # An exception cannot prove whether the SQL commit reached durable storage.
    # The original client ID remains bound: subsequent calls perform lookup only.
    broker.submit(intent, before_io=lambda: pytest.fail("replayed uncertain commit"))
    assert calls == ["commit-failed"] and server.count("POST", "/v2/orders") == 0


def test_callback_occurs_after_preflight_and_before_exactly_one_post():
    server = PaperServer()
    broker = make_adapter(server)
    intent = prepared(broker)
    calls = []

    def commit():
        assert server.count("POST", "/v2/orders") == 0
        assert server.count("GET", "/v2/account") > 0
        calls.append("committed")

    result = broker.submit(intent, before_io=commit)
    assert broker.submit(intent, before_io=lambda: pytest.fail("replayed commit")) == result
    assert calls == ["committed"] and server.count("POST", "/v2/orders") == 1
