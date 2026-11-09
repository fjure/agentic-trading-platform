"""Pure v1 request and response formats for the two supported providers."""

import json
from dataclasses import dataclass
from enum import StrEnum

from jsonschema import Draft202012Validator
from referencing.exceptions import Unresolvable


class Provider(StrEnum):
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"


class ResultState(StrEnum):
    ACCEPTED = "ACCEPTED"
    REFUSED = "REFUSED"
    INCOMPLETE = "INCOMPLETE"
    MODEL_MISMATCH = "MODEL_MISMATCH"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    REDIRECT_DENIED = "REDIRECT_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    REJECTED_BEFORE_EXECUTION = "REJECTED_BEFORE_EXECUTION"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    DISPATCH_DENIED = "DISPATCH_DENIED"
    DISPATCH_CANCELLED = "DISPATCH_CANCELLED"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderResult:
    state: ResultState
    usage: Usage | None = None
    output: dict | None = None


def build_request(
    provider: Provider, *, model: str, instructions: str, projection: str,
    max_output_tokens: int, schema: dict,
) -> dict:
    """Build the fixed envelope around the exact approved projection text."""
    output_format = {"type": "json_schema", "schema": schema}
    if provider is Provider.OPENAI:
        return {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": projection}],
            "store": False,
            "max_output_tokens": max_output_tokens,
            "text": {"format": output_format | {"name": "research_result", "strict": True}},
        }
    if provider is Provider.ANTHROPIC:
        return {
            "model": model,
            "system": instructions,
            "messages": [{"role": "user", "content": projection}],
            "max_tokens": max_output_tokens,
            "output_config": {"format": output_format},
        }
    raise ValueError("PROVIDER_UNAVAILABLE")


def _usage(body: dict) -> Usage | None:
    usage = body.get("usage")
    if isinstance(usage, dict):
        values = (usage.get("input_tokens"), usage.get("output_tokens"))
        if all(type(value) is int and 0 <= value <= 2**31 - 1 for value in values):
            return Usage(*values)
    return None


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate response field")
    return fields


def _reject_nonfinite(_value):
    raise ValueError("invalid JSON number")


def _openai_content(body: dict) -> tuple[ResultState, object]:
    if body.get("status") != "completed":
        return ResultState.INCOMPLETE, None
    output = body.get("output")
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        return ResultState.INVALID_OUTPUT, None
    messages = [item for item in output if item.get("type") != "reasoning"]
    if len(messages) != 1:
        return ResultState.INVALID_OUTPUT, None
    message = messages[0]
    if (message.get("type"), message.get("role"), message.get("status")) != (
        "message", "assistant", "completed",
    ):
        return ResultState.INVALID_OUTPUT, None
    return ResultState.ACCEPTED, message.get("content")


def _anthropic_content(body: dict) -> tuple[ResultState, object]:
    if body.get("stop_reason") == "refusal":
        return ResultState.REFUSED, None
    if body.get("stop_reason") != "end_turn":
        return ResultState.INCOMPLETE, None
    if (body.get("type"), body.get("role")) != ("message", "assistant"):
        return ResultState.INVALID_OUTPUT, None
    return ResultState.ACCEPTED, body.get("content")


def parse_response(
    provider: Provider, body: object, *, expected_model: str, schema: Draft202012Validator,
) -> ProviderResult:
    """Project the validated structured result and bounded token counts."""
    if not isinstance(body, dict):
        return ProviderResult(ResultState.INVALID_OUTPUT)
    usage = _usage(body)
    if body.get("model") != expected_model:
        return ProviderResult(ResultState.MODEL_MISMATCH, usage)
    if provider is Provider.OPENAI:
        state, content = _openai_content(body)
        text_type = "output_text"
    elif provider is Provider.ANTHROPIC:
        state, content = _anthropic_content(body)
        text_type = "text"
    else:
        raise ValueError("PROVIDER_UNAVAILABLE")
    if state is not ResultState.ACCEPTED:
        return ProviderResult(state, usage)
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        return ProviderResult(ResultState.INVALID_OUTPUT, usage)
    block = content[0]
    if provider is Provider.OPENAI and block.get("type") == "refusal":
        return ProviderResult(ResultState.REFUSED, usage)
    raw = block.get("text")
    if block.get("type") != text_type or not isinstance(raw, str) or len(raw) > 65536:
        return ProviderResult(ResultState.INVALID_OUTPUT, usage)
    try:
        output = json.loads(raw, object_pairs_hook=_unique_fields, parse_constant=_reject_nonfinite)
        valid = isinstance(output, dict) and schema.is_valid(output)
    except (ValueError, RecursionError, Unresolvable):
        valid = False
    if not valid:
        return ProviderResult(ResultState.INVALID_OUTPUT, usage)
    return ProviderResult(ResultState.ACCEPTED, usage, output)
