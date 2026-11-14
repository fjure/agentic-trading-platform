"""Local shape and sensitive-content checks for the offline research gateway."""

import json
import re

_CONTEXT_FIELDS = {
    "market", "trigger", "signal", "observationWindow", "expectedOutcome",
    "measurementHorizon", "benchmark",
}
_EXCERPT = "OPERATOR_RESEARCH_EXCERPT"
_CONFIRMED = "CONFIRMED_RESEARCH_FIELDS"
_UNSAFE_TEXT = re.compile(
    r"offline-canary-[\w-]{32}|\bsk-[\w-]{12,}|\bAKIA[A-Z0-9]{16}\b"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[\w.-]{12,}"
    r"|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b(?:\d[ -]?){9,}\d\b"
    r"|\b(?:api[_ -]?key|password|secret|token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_ACTIVE_CONTENT = re.compile(r"<[!/?a-z]|\b(?:https?|javascript|data):|\]\s*\(", re.IGNORECASE)


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate projection field")
    return result


def _research_text(value, limit: int) -> bool:
    return (
        type(value) is str and 0 < len(value) <= limit
        and bool(value.strip()) and not _UNSAFE_TEXT.search(value)
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def valid_projection(content: bytes, classes: tuple[str, ...] | None = None) -> bool:
    """Validate the contract without rewriting the operator-confirmed bytes."""
    if type(content) is not bytes or len(content) > 24_000:
        return False
    try:
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return False
    if type(value) is not dict or value.keys() != {"excerpt", "confirmedContext"}:
        return False
    context = value["confirmedContext"]
    if type(context) is not dict or not context.keys() <= _CONTEXT_FIELDS:
        return False
    expected_classes = {_EXCERPT, _CONFIRMED} if context else {_EXCERPT}
    return (
        (classes is None or set(classes) == expected_classes)
        and _research_text(value["excerpt"], 2048)
        and all(_research_text(field, 240) for field in context.values())
    )


def valid_discovery_projection(content: bytes) -> bool:
    if type(content) is not bytes or not 1 <= len(content) <= 16384:
        return False
    try:
        payload = json.loads(content, object_pairs_hook=_unique_object)
        return (
            type(payload) is dict and set(payload) == {"purpose", "evidence", "quotes"}
            and payload["purpose"] == "PROACTIVE_RESEARCH_DISCOVERY_V1"
            and type(payload["evidence"]) is dict
            and type(payload["quotes"]) is list and len(payload["quotes"]) <= 32
            and all(_research_text(quote, 2048) for quote in payload["quotes"])
            and safe_result_content(payload)
        )
    except (ValueError, RecursionError):
        return False


def safe_result_content(value) -> bool:
    if isinstance(value, str):
        return not _UNSAFE_TEXT.search(value) and not _ACTIVE_CONTENT.search(value)
    if isinstance(value, dict):
        return all(
            safe_result_content(key) and safe_result_content(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return all(safe_result_content(item) for item in value)
    return True
