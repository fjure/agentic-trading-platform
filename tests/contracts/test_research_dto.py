"""Runtime research response DTOs reject malformed public and nested values."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.control_api.research_dto import (
    RESPONSE_MODELS,
    ResearchBar,
    ResearchBarPage,
    ResearchContext,
    ResearchEvidence,
    ResearchEvidenceResult,
    ResearchJob,
    ResearchJobAction,
    ResearchJobPage,
    ResearchJobRequest,
    ResearchSourceCatalog,
)

ROOT = Path(__file__).resolve().parents[2]


def example(name):
    return json.loads((ROOT / f"contracts/examples/v1/{name}.valid.json").read_text("utf-8"))


@pytest.mark.parametrize(
    "key,model,name",
    [
        ("read_sources", ResearchSourceCatalog, "research-source-catalog"),
        ("read_bars", ResearchBarPage, "research-bar-page"),
        ("read_evidence", ResearchEvidenceResult, "research-evidence-result"),
        ("read_context", ResearchContext, "research-context"),
        ("read_job", ResearchJob, "research-job"),
        ("read_jobs", ResearchJobPage, "research-job-page"),
        ("create_job", ResearchJob, "research-job"),
    ],
)
def test_runtime_response_mapping_accepts_canonical_examples(key, model, name):
    assert RESPONSE_MODELS[key] is model
    assert model.model_validate(example(name)).model_dump() == example(name)


@pytest.mark.parametrize(
    "model,name",
    [
        (ResearchBar, "research-bar"),
        (ResearchEvidence, "research-evidence"),
        (ResearchJobRequest, "research-job-request"),
    ],
)
def test_all_nine_contract_examples_have_strict_runtime_dtos(model, name):
    model.model_validate(example(name))


def test_legacy_create_and_explicit_resume_share_post_body_without_breaking_create():
    create = example("research-job-request")
    resume = example("research-job-resume-request")
    assert ResearchJobAction.model_validate(create).model_dump() == create
    assert ResearchJobAction.model_validate(resume).model_dump() == resume
    with pytest.raises(ValidationError):
        ResearchJobAction.model_validate({"action": "DELETE", "jobId": resume["jobId"]})


def test_private_fields_are_rejected_at_multiple_nested_boundaries():
    job = example("research-job")
    job["request"]["privateCursor"] = "synthetic-private-canary"
    with pytest.raises(ValidationError):
        ResearchJob.model_validate(job)

    evidence = example("research-evidence")
    evidence["content"]["rawPayload"] = {"synthetic": "private"}
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(evidence)


@pytest.mark.parametrize(
    "kind,content",
    [
        ("NEWS", {"kind": "NEWS", "headline": "n", "language": "en", "raw": "canary"}),
        ("UNRECOGNIZED", {"kind": "UNRECOGNIZED", "provider": "x"}),
    ],
)
def test_evidence_union_rejects_unknown_or_extra_content(kind, content):
    evidence = example("research-evidence")
    evidence["content"] = content
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(evidence)


@pytest.mark.parametrize("actual,unit", [(None, "USD"), ("2.3", None)])
def test_macro_actual_and_unit_must_be_nullable_as_a_pair(actual, unit):
    evidence = example("research-evidence")
    evidence["content"] = {
        "kind": "MACRO_EVENT",
        "eventCode": "series",
        "region": "US",
        "actual": actual,
        "unit": unit,
    }
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(evidence)


def test_market_is_required_while_instrument_may_be_null():
    evidence = example("research-evidence")
    evidence["instrumentId"] = None
    evidence["content"] = {
        "kind": "MACRO_EVENT",
        "eventCode": "series",
        "region": "US",
        "actual": None,
        "unit": None,
    }
    assert ResearchEvidence.model_validate(evidence).instrumentId is None
    del evidence["market"]
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(evidence)


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", "2026-10-23T13:30:00+00:00"),
        ("start", "2026-02-30T13:30:00Z"),
        ("end", "2026-10-23T13:45:00.1234567Z"),
        ("open", 100),
        ("high", "1e2"),
        ("volume", "NaN"),
    ],
)
def test_bar_rejects_noncanonical_time_and_exact_decimal_values(field, value):
    bar = example("research-bar")
    bar[field] = value
    with pytest.raises(ValidationError):
        ResearchBar.model_validate(bar)


def test_bar_ohlc_currency_and_evidence_availability_invariants():
    bar = example("research-bar")
    bar.update(high="98", currency="EUR")
    with pytest.raises(ValidationError):
        ResearchBar.model_validate(bar)

    evidence = example("research-evidence")
    evidence.update(quality="INVALID", modelConfidence="0.99")
    # Confidence cannot turn bad source quality into valid evidence.
    assert ResearchEvidence.model_validate(evidence).quality == "INVALID"
    evidence["availableAt"] = "2026-10-25T18:00:00Z"
    evidence["ingestedAt"] = "2026-10-25T18:00:00Z"
    evidence["publishedAt"] = "2026-10-25T18:00:01Z"
    evidence["availabilityBasis"] = "VERIFIED_PUBLICATION"
    with pytest.raises(ValidationError):
        ResearchEvidence.model_validate(evidence)


def test_snapshot_version_time_and_metric_cross_fields_are_enforced():
    page = example("research-bar-page")
    page["items"][0]["commitVersion"] = 2
    with pytest.raises(ValidationError):
        ResearchBarPage.model_validate(page)
    page = example("research-bar-page")
    page["items"][0]["availableAt"] = "2026-10-25T18:00:01Z"
    with pytest.raises(ValidationError):
        ResearchBarPage.model_validate(page)

    context = example("research-context")
    context["snapshot"]["knowledgeAt"] = "2026-10-25T18:00:01Z"
    with pytest.raises(ValidationError):
        ResearchContext.model_validate(context)


def test_computable_metrics_require_value_reason_null_and_three_references():
    context = example("research-context")
    ids = [
        "33333333-3333-4333-8333-333333333333",
        "55555555-5555-4555-8555-555555555555",
        "66666666-6666-4666-8666-666666666666",
    ]
    context["evidenceIds"] = ids
    context["priceChange"].update(status="COMPUTABLE", value="0.25", evidenceIds=ids, reason=None)
    context["volatility"].update(status="COMPUTABLE", value="0.1", evidenceIds=ids, reason=None)
    assert ResearchContext.model_validate(context).priceChange.value == "0.25"
    context = example("research-context")
    context["priceChange"].update(status="COMPUTABLE", value="0.5", reason=None)
    with pytest.raises(ValidationError):
        ResearchContext.model_validate(context)


def test_strict_safe_integer_rejects_bool_and_unknown_error_code():
    job = example("research-job")
    job["pages"] = True
    with pytest.raises(ValidationError):
        ResearchJob.model_validate(job)
    job = example("research-job")
    job["errorCode"] = "synthetic raw exception"
    with pytest.raises(ValidationError):
        ResearchJob.model_validate(job)
