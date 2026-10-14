"""PTV-15 provider-local Jev profile; no network or credentials required."""

import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from packages.decisions.jev import JevDecisionEngine, load_config
from packages.domain.models import Choice
from tests.unit.test_decision import FixedClock, SeededIds, state

WIRE = Path(__file__).resolve().parents[2] / "contracts/providers/jev/2026-10-14"
ROOT = WIRE.parents[3]


def document(name: str) -> dict:
    return json.loads((WIRE / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def request_validator() -> Draft202012Validator:
    schema = document("request.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def response_validator() -> Draft202012Validator:
    schema = document("response.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_local_wire_examples(request_validator, response_validator):
    assert request_validator.is_valid(document("request.valid.json"))
    assert response_validator.is_valid(document("response.valid.json"))
    assert not response_validator.is_valid(document("response.invalid.json"))


def test_optional_shadow_pair_and_optional_noul_criteria(request_validator, response_validator):
    request = document("request.valid.json")
    response = document("response.valid.json")
    del request["questions"]["shadow_direction"]
    del response["answers"]["shadow_direction"]
    del request["questions"]["signal_conflict"]["criteria"]
    assert request_validator.is_valid(request)
    assert response_validator.is_valid(response)


@pytest.mark.parametrize(
    "path,value",
    [
        (("model",), "jev-latest"),
        (("questions", "regime", "type"), "score"),
        (("questions", "regime", "criteria", "TREND"), None),
        (("questions", "evidence_quality", "criteria"), ["Too short"]),
        (("questions", "signal_conflict", "criteria"), {"true": "Yes"}),
        (("questions", "shadow_direction", "criteria", "SHORT"), "Sell short"),
    ],
)
def test_invalid_request_profile(path, value, request_validator):
    request = document("request.valid.json")
    target = request
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not request_validator.is_valid(request)


@pytest.mark.parametrize(
    "path,value",
    [
        (("answers", "regime", "choice"), "SHORT"),
        (("answers", "regime", "probabilities", "TREND"), 1.2),
        (("answers", "regime", "probabilities", "SHORT"), 0),
        (("answers", "evidence_quality", "score"), "8.1"),
        (("answers", "evidence_quality", "legend", "10"), "Extra"),
        (("answers", "evidence_quality", "probabilities", "0"), -0.1),
        (("answers", "signal_conflict", "noul"), -0.1),
        (("answers", "shadow_direction", "orderQuantity"), "100"),
        (("usage", "input_tokens"), 1.2),
        (("usage", "output_tokens"), -1),
    ],
)
def test_invalid_response_profile(path, value, response_validator):
    response = document("response.valid.json")
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert not response_validator.is_valid(response)


def test_required_answer_and_top_level_fields(response_validator, request_validator):
    response = document("response.valid.json")
    del response["answers"]["signal_conflict"]
    assert not response_validator.is_valid(response)
    request = document("request.valid.json")
    request["order"] = {"side": "BUY"}
    assert not request_validator.is_valid(request)


def test_lexically_invalid_json_is_rejected_before_schema_validation():
    # NaN is Python's permissive extension, not a JSON number or a valid wire token.
    for raw in ('{"score": NaN}', '{"score": Infinity}'):
        with pytest.raises(ValueError):
            json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def test_examples_use_matching_project_question_ids():
    request = document("request.valid.json")
    response = document("response.valid.json")
    assert set(request["questions"]) == set(response["answers"])
    assert request["model"] == response["model"] == "jev-1.13.0"
    expected_legend = dict(enumerate(request["questions"]["evidence_quality"]["criteria"]))
    assert response["answers"]["evidence_quality"]["legend"] == {
        str(key): value for key, value in expected_legend.items()
    }
    probabilities = response["answers"]["evidence_quality"]["probabilities"]
    assert sum(probabilities.values()) == pytest.approx(1)
    assert response["answers"]["evidence_quality"]["score"] == pytest.approx(
        sum(int(level) * probability for level, probability in probabilities.items())
    )


@pytest.mark.parametrize(
    "example,expected",
    [("response.valid.json", "JEV_SHADOW"), ("response.invalid.json", "ABSTAIN_INVALID_RESPONSE")],
)
def test_response_examples_through_adapter_mock_transport(example, expected, request_validator):
    body = document(example)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request_validator.is_valid(json.loads(request.content))
        return httpx.Response(200, json=body)

    engine = JevDecisionEngine(
        clock=FixedClock(),
        ids=SeededIds(),
        api_key="synthetic-only",
        config=replace(load_config(), enabled=True),
        transport=httpx.MockTransport(handler),
    )
    decision = engine.decide(state(), "active.v1")
    assert decision.choice is Choice.ABSTAIN
    assert decision.reason_code == expected


@pytest.fixture(scope="module")
def decision_validators() -> tuple[Draft202012Validator, Draft202012Validator]:
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / "contracts/schemas/v1").glob("*.schema.json")
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema, default_specification=DRAFT202012))
        for schema in documents
    )
    internal = next(
        schema for schema in documents if schema["$id"].endswith("/decision-result.schema.json")
    )
    openapi = yaml.safe_load(
        (ROOT / "contracts/http/control-api.openapi.yaml").read_text(encoding="utf-8")
    )
    public = openapi["components"]["schemas"]["Decision"]
    openapi_id = "https://agentic-trading-lab.invalid/contracts/http/control-api.openapi.yaml"
    public_registry = Registry().with_resource(
        openapi_id, Resource.from_contents(openapi, default_specification=DRAFT202012)
    )
    Draft202012Validator.check_schema(internal)
    Draft202012Validator.check_schema(public)
    return Draft202012Validator(internal, registry=registry), Draft202012Validator(
        {"$ref": openapi_id + "#/components/schemas/Decision"}, registry=public_registry
    )


@pytest.mark.parametrize(
    "counts,valid",
    [
        ({}, True),
        ({"inputTokens": None, "outputTokens": None}, True),
        ({"inputTokens": 0, "outputTokens": 0}, True),
        ({"inputTokens": 100, "outputTokens": 10}, True),
        ({"inputTokens": 9007199254740991, "outputTokens": 1}, True),
        ({"inputTokens": 100}, False),
        ({"outputTokens": 10}, False),
        ({"inputTokens": None, "outputTokens": 10}, False),
        ({"inputTokens": 100, "outputTokens": None}, False),
        ({"inputTokens": True, "outputTokens": 10}, False),
        ({"inputTokens": -1, "outputTokens": 10}, False),
        ({"inputTokens": 9007199254740992, "outputTokens": 10}, False),
    ],
)
def test_durable_token_counts_match_internal_and_public_contract(
    counts, valid, decision_validators
):
    internal, public = decision_validators
    sample = json.loads(
        (ROOT / "contracts/examples/v1/decision-result.valid.json").read_text(encoding="utf-8")
    )
    sample.update(copy.deepcopy(counts))
    dto = {key: value for key, value in sample.items() if key != "schemaVersion"}
    assert internal.is_valid(sample) is valid
    assert public.is_valid(dto) is valid
