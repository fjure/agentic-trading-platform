"""Fixed proxy selection without any network request or provider credentials."""

from dataclasses import replace

import httpx
import pytest

from packages.brokers.alpaca_paper import AlpacaPaperBrokerAdapter
from packages.decisions.jev import JevDecisionEngine, load_config
from tests.contracts.test_jev import _response
from tests.unit.test_decision import FixedClock, SeededIds, state


@pytest.mark.parametrize("adapter", [AlpacaPaperBrokerAdapter, JevDecisionEngine])
@pytest.mark.parametrize("value", ["http://arbitrary.invalid", 1, None])
def test_proxy_flag_rejects_arbitrary_configuration(adapter, value):
    with pytest.raises(ValueError, match="invalid egress"):
        adapter(clock=FixedClock(), ids=SeededIds(), egress_proxy=value)


@pytest.mark.parametrize("adapter", [AlpacaPaperBrokerAdapter, JevDecisionEngine])
def test_proxy_cannot_be_combined_with_injected_transport(adapter):
    with pytest.raises(ValueError, match="invalid egress"):
        adapter(
            clock=FixedClock(), ids=SeededIds(), egress_proxy=True,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )


def test_paper_proxy_is_execution_owned_and_environment_independent(monkeypatch):
    captured = []
    real_client = httpx.Client

    def client(**kwargs):
        captured.append(kwargs)
        return real_client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))

    monkeypatch.setattr(httpx, "Client", client)
    adapter = AlpacaPaperBrokerAdapter(clock=FixedClock(), ids=SeededIds(), egress_proxy=True)
    adapter._client.close()
    assert captured[0]["proxy"] == "http://execution-egress:8080"
    assert captured[0]["trust_env"] is False
    assert captured[0]["follow_redirects"] is False


def test_jev_uses_only_worker_proxy_and_verifies_tls(monkeypatch):
    captured = []
    real_client = httpx.Client

    def client(**kwargs):
        captured.append(kwargs)
        return real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_response()))
        )

    monkeypatch.setattr(httpx, "Client", client)
    adapter = JevDecisionEngine(
        clock=FixedClock(), ids=SeededIds(), egress_proxy=True,
        api_key="offline-test-only", config=replace(load_config(), enabled=True),
    )
    result = adapter.decide(state(), "runtime-decision-v1")
    assert result.model_id is not None
    assert captured[0]["proxy"] == "http://worker-egress:8080"
    assert captured[0]["verify"] is True and captured[0]["trust_env"] is False
    assert captured[0]["follow_redirects"] is False
