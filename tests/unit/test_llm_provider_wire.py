"""Offline provider format checks with synthetic response bodies."""

import pytest
from jsonschema import Draft202012Validator

from services.llm_gateway.provider_wire import Provider, ResultState, build_request, parse_response

SCHEMA = Draft202012Validator({
    "type": "object",
    "required": ["hypothesis"],
    "properties": {"hypothesis": {"type": "string"}},
    "additionalProperties": False,
})


def response(provider, text):
    if provider is Provider.OPENAI:
        return {"model": "fixture-model", "status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        }]}
    return {
        "model": "fixture-model", "type": "message", "role": "assistant",
        "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
    }


@pytest.mark.parametrize("provider", list(Provider))
def test_request_has_exactly_one_dynamic_user_message(provider):
    projection = '{"excerpt":"Prüfe diese Idee."}'
    body = build_request(
        provider, model="fixture-model", instructions="fixed", projection=projection,
        max_output_tokens=128, schema=SCHEMA.schema,
    )
    messages = body["input" if provider is Provider.OPENAI else "messages"]
    assert messages == [{"role": "user", "content": projection}]
    assert "tools" not in body


@pytest.mark.parametrize("provider", list(Provider))
def test_structured_response_keeps_missing_usage_unknown(provider):
    body = response(provider, '{"hypothesis":"synthetic"}')
    result = parse_response(provider, body, expected_model="fixture-model", schema=SCHEMA)
    assert result.state is ResultState.ACCEPTED
    assert result.usage is None and result.output == {"hypothesis": "synthetic"}
    wrong_model = parse_response(provider, body, expected_model="other-model", schema=SCHEMA)
    assert wrong_model.state is ResultState.MODEL_MISMATCH and wrong_model.output is None


@pytest.mark.parametrize("provider", list(Provider))
@pytest.mark.parametrize("payload", [
    '{"hypothesis":"a","hypothesis":"b"}',
    '{"hypothesis":"a","tool":"submit"}',
    '{"hypothesis":NaN}',
])
def test_untrusted_output_cannot_bypass_schema(provider, payload):
    result = parse_response(
        provider, response(provider, payload), expected_model="fixture-model", schema=SCHEMA,
    )
    assert result.state is ResultState.INVALID_OUTPUT and result.output is None
