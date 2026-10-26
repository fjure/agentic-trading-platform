"""MDR-01: strict research examples, source separation and additive HTTP contract."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text("utf-8"))
URI = "https://agentic-trading-lab.invalid/research-contract"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)
MIRROR = json.loads((ROOT / "contracts/schemas/v1/research.schema.json").read_text("utf-8"))


def example(name):
    return json.loads((ROOT / f"contracts/examples/v1/{name}.valid.json").read_text("utf-8"))


def validator(name):
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + name},
        registry=REGISTRY,
        format_checker=contract_formats(),
    )


@pytest.mark.parametrize(
    "name,component",
    [
        ("research-bar", "ResearchBar"),
        ("research-bar-page", "ResearchBarPage"),
        ("research-evidence", "ResearchEvidence"),
        ("research-evidence-result", "ResearchEvidenceResult"),
        ("research-context", "ResearchContext"),
        ("research-source-catalog", "ResearchSourceCatalog"),
        ("research-job-request", "ResearchJobRequest"),
        ("research-job", "ResearchJob"),
        ("research-job-page", "ResearchJobPage"),
    ],
)
def test_public_and_standalone_examples(name, component):
    value = example(name)
    validator(component).validate(value)
    schema = {**MIRROR, "$ref": "#/$defs/" + component}
    Draft202012Validator(schema, format_checker=contract_formats()).validate(value)


def test_every_research_component_is_mirrored_without_drift():
    def rewrite(value):
        if isinstance(value, dict):
            return {
                k: (v.replace("#/components/schemas/", "#/$defs/") if k == "$ref" else rewrite(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value

    for name, schema in OAS["components"]["schemas"].items():
        if name.startswith("Research"):
            assert MIRROR["$defs"][name] == rewrite(schema)


def test_seven_operations_require_session_and_explicit_bounds():
    # Bestehende MDR-Verträge bleiben unverändert; die fünf additiven RDR-Verträge
    # mit eigenem ProblemDTO werden in test_readiness_examples.py geprüft.
    historical_paths = {
        "/api/v1/research/sources", "/api/v1/research/bars",
        "/api/v1/research/evidence/{id}", "/api/v1/research/context",
        "/api/v1/research/ingestion-jobs", "/api/v1/research/ingestion-jobs/{id}",
    }
    paths = {
        p: item for p, item in OAS["paths"].items()
        if p in historical_paths
    }
    assert paths.keys() == historical_paths
    assert sum(len(item) for item in paths.values()) == 7
    for path, item in paths.items():
        for method, operation in item.items():
            assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
            assert {"400", "401", "403", "404", "409", "503"} <= operation["responses"].keys()
            for status, response in operation["responses"].items():
                if int(status) >= 400:
                    assert response["content"]["application/problem+json"]["schema"] == {
                        "$ref": "#/components/schemas/ResearchProblem"
                    }
            if method == "post":
                assert {"$ref": "#/components/parameters/IdempotencyKey"} in operation["parameters"]
                assert "202" in operation["responses"] and "200" not in operation["responses"]
            if path.endswith(("/bars", "/context")):
                required = {p["name"] for p in operation["parameters"] if p.get("required")}
                assert {"instrumentId", "start", "end", "interval"} <= required
            for parameter in operation["parameters"]:
                if parameter.get("name") == "limit":
                    assert parameter["schema"]["maximum"] == 200


@pytest.mark.parametrize(
    "field,value",
    [
        ("apiKey", "synthetic-canary"),
        ("rawPayload", {}),
        ("pageToken", "private-canary"),
        ("url", "https://example.invalid"),
        ("mode", "LIVE"),
        ("feed", "sip"),
        ("adjustment", "split"),
        ("interval", "1Min"),
        ("instrumentId", "SPY"),
        ("start", "2026-10-23T00:00:00+02:00"),
        ("end", "2026-10-24T00:00:00.1234567Z"),
        ("start", "2026-02-30T00:00:00Z"),
    ],
)
def test_job_request_rejects_private_or_unsupported_inputs(field, value):
    request = example("research-job-request")
    request[field] = value
    assert not validator("ResearchJobRequest").is_valid(request)


@pytest.mark.parametrize(
    "value", [1, True, 1.25, "NaN", "Infinity", "1e3", "1" * 21, "0." + "1" * 19]
)
def test_bar_prices_are_exact_bounded_decimal_strings(value):
    bar = example("research-bar")
    bar["close"] = value
    assert not validator("ResearchBar").is_valid(bar)


@pytest.mark.parametrize(
    "value", ["0", "123.456", "-0.25", "99999999999999999999.123456789012345678"]
)
def test_decimal_contract_accepts_exact_fractional_values(value):
    validator("ResearchDecimal").validate(value)


@pytest.mark.parametrize(
    "kind,content",
    [
        ("NEWS", {"headline": "Synthetic release", "language": "en"}),
        (
            "FUNDAMENTAL",
            {
                "metric": "revenue",
                "value": "1.25",
                "unit": "USD",
                "periodEnd": "2026-01-01T00:00:00Z",
            },
        ),
        (
            "MACRO_EVENT",
            {"eventCode": "synthetic-series", "region": "US", "actual": "3.5", "unit": "percent"},
        ),
        (
            "DERIVED",
            {
                "metric": "VOLATILITY",
                "value": "0.01",
                "algorithmVersion": "decimal-population-v1",
                "evidenceIds": ["33333333-3333-4333-8333-333333333333"],
            },
        ),
    ],
)
def test_evidence_content_is_discriminated_not_free_provider_json(kind, content):
    evidence = example("research-evidence")
    evidence["content"] = {"kind": kind, **content}
    validator("ResearchEvidence").validate(evidence)
    bad = copy.deepcopy(evidence)
    bad["content"]["kind"] = "BAR"
    assert not validator("ResearchEvidence").is_valid(bad)
    bad = copy.deepcopy(evidence)
    bad["content"]["rawPayload"] = {"canary": "private"}
    assert not validator("ResearchEvidence").is_valid(bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("providerToken", "canary"),
        ("errorCode", "raw exception"),
        ("apiSecret", "canary"),
        ("status", "DONE"),
    ],
)
def test_job_projection_excludes_transport_and_arbitrary_errors(field, value):
    job = example("research-job")
    job[field] = value
    assert not validator("ResearchJob").is_valid(job)


def test_model_confidence_does_not_require_valid_data_quality():
    evidence = example("research-evidence")
    evidence.update(quality="INVALID", modelConfidence="0.99")
    validator("ResearchEvidence").validate(evidence)
    assert evidence["quality"] == "INVALID"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/path",
        "https://example.invalid/path?key=canary",
        "alpaca:bar?token=canary",
    ],
)
def test_source_reference_cannot_project_transport_queries(value):
    evidence = example("research-evidence")
    evidence["sourceReference"] = value
    assert not validator("ResearchEvidence").is_valid(evidence)


def test_first_observed_and_unavailable_sources_are_honest():
    evidence = example("research-evidence")
    assert evidence["publishedAt"] is None
    assert evidence["availableAt"] == evidence["ingestedAt"]
    source = example("research-source-catalog")["items"][0]
    assert source["state"] == "NOT_IMPLEMENTED"
    assert source["providerVerification"] == "UNVERIFIED"
    assert source["licenseStatus"] == "UNKNOWN"


@pytest.mark.parametrize(
    "status,value,reason",
    [
        ("COMPUTABLE", None, None),
        ("NOT_COMPUTABLE", "9", None),
        ("NOT_COMPUTABLE", None, None),
        ("COMPUTABLE", "9", "INVALID_QUALITY"),
    ],
)
def test_metric_status_cannot_fabricate_a_value(status, value, reason):
    metric = example("research-context")["priceChange"]
    metric.update(status=status, value=value, reason=reason)
    assert not validator("ResearchMetric").is_valid(metric)


def test_market_wide_macro_evidence_needs_no_fabricated_instrument():
    evidence = example("research-evidence")
    evidence.update(
        instrumentId=None,
        market="US",
        content=dict(kind="MACRO_EVENT", eventCode="release", region="US", actual=None, unit=None),
    )
    validator("ResearchEvidence").validate(evidence)
    evidence["content"]["unit"] = "percent"
    assert not validator("ResearchEvidence").is_valid(evidence)
