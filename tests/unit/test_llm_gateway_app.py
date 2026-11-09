import json
from dataclasses import asdict
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from services.llm_gateway.app import DISPATCH_PATH, GatewayDispatchClient, create_gateway_app
from services.llm_gateway.content_policy import safe_result_content, valid_projection
from services.llm_gateway.provider_wire import ProviderResult, ResultState
from services.llm_gateway.supervisor_rpc import SIGNATURE, AdmissionAuthentication, _request_body
from tests.unit.test_llm_offline_gateway import make_request


def test_default_gateway_is_disabled():
    with TestClient(create_gateway_app()) as client:
        assert client.get("/health").json() == {"state": "DISABLED"}
        assert client.post(DISPATCH_PATH).status_code == 503


def test_private_dispatch_requires_gateway_domain_signature():
    gateway = Mock()
    result = ProviderResult(ResultState.INVALID_OUTPUT)
    gateway.dispatch.return_value = result
    auth = AdmissionAuthentication(bytes(range(32)))
    request = make_request()
    body = _request_body(request)
    with TestClient(create_gateway_app(gateway, auth)) as client:
        wrong = client.post(DISPATCH_PATH, content=body, headers={
            SIGNATURE: auth.request_signature(body),
        })
        assert wrong.status_code == 403
        gateway.dispatch.assert_not_called()
        response = client.post(DISPATCH_PATH, content=body, headers={
            SIGNATURE: auth.gateway_signature(body),
        })
    gateway.dispatch.assert_called_once_with(request)
    assert response.json() == asdict(result)
    assert response.headers[SIGNATURE] == auth.gateway_response_signature(body, response.content)
    assert response.headers["cache-control"] == "no-store"
    with TestClient(create_gateway_app(gateway, auth)) as client:
        assert GatewayDispatchClient(client, auth).dispatch(request) == result
        other_auth = AdmissionAuthentication(b"other-synthetic-key-for-private-rpc")
        assert GatewayDispatchClient(client, other_auth).dispatch(
            request
        ).state is ResultState.OUTCOME_UNKNOWN


@pytest.mark.parametrize("value", [
    "synthetic.user@example.invalid", "api_key=synthetic-secret", "030 123456789",
])
def test_sensitive_text_is_rejected_in_both_directions(value):
    projection = json.dumps({"excerpt": value, "confirmedContext": {}}).encode()
    assert not valid_projection(projection)
    assert not safe_result_content({"result": [value]})


def test_projection_requires_exact_shape_and_classes():
    content = b'{"excerpt":"synthetic","confirmedContext":{"market":"synthetic market"}}'
    assert valid_projection(content, ("OPERATOR_RESEARCH_EXCERPT", "CONFIRMED_RESEARCH_FIELDS"))
    assert not valid_projection(content, ("OPERATOR_RESEARCH_EXCERPT",))
    assert not valid_projection(content.replace(b"market", b"history"))
    assert not valid_projection(content.replace(b'"excerpt":', b'"excerpt":"first","excerpt":'))
    assert not safe_result_content({"result": "<a href='https://example.invalid'>click</a>"})
