#!/usr/bin/env python3
"""Static, offline contract/meta-schema/example checks; never calls a trading service."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "contracts/http/control-api.openapi.yaml"
SCHEMAS = ROOT / "contracts/schemas/v1"
EXAMPLES = ROOT / "contracts/examples/v1"
TOOLING = ROOT / ".tooling/contracts"


def fail(message: str) -> None:
    raise SystemExit(f"contract validation failed: {message}")


def resolve_local_ref(document: dict, ref: str) -> object:
    if not ref.startswith("#/components/"):
        fail(f"external or non-component OpenAPI reference forbidden: {ref}")
    current: object = document
    for key in ref[2:].split("/"):
        if not isinstance(current, dict) or key not in current:
            fail(f"unresolved OpenAPI reference: {ref}")
        current = current[key]
    return current


def iter_nodes(node: object):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_nodes(value)


def main() -> None:
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("openapi") != "3.1.2":
        fail("canonical OpenAPI version must be 3.1.2")

    for node in iter_nodes(document):
        if isinstance(node, dict) and "$ref" in node:
            resolve_local_ref(document, node["$ref"])
    if document["paths"]["/api/v1/events"]["get"].get("x-event-schema", {}).get("$ref") != "#/components/schemas/SupervisorEvent":
        fail("SSE data-frame schema reference is missing")
    for name, schema in document["components"]["schemas"].items():
        Draft202012Validator.check_schema(schema)

    response_examples = 0
    openapi_id = "https://agentic-trading-lab.invalid/contracts/http/control-api.openapi.yaml"
    openapi_registry = Registry().with_resource(openapi_id, Resource.from_contents(document, default_specification=DRAFT202012))
    media_objects = [media for response in document["components"]["responses"].values() for media in response.get("content", {}).values()]
    for path_item in document["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for response in operation["responses"].values():
                if "$ref" not in response:
                    media_objects.extend(response.get("content", {}).values())
    for media in media_objects:
        schema = media.get("schema")
        if schema is None:
            continue
        examples = ([media["example"]] if "example" in media else []) + [item["value"] for item in media.get("examples", {}).values()]
        for example in examples:
            check_schema = {"$ref": openapi_id + schema["$ref"]} if "$ref" in schema else schema
            errors = list(Draft202012Validator(check_schema, registry=openapi_registry, format_checker=contract_formats()).iter_errors(example))
            if errors:
                fail(f"OpenAPI response example: {errors[0].message}")
            response_examples += 1

    operations: list[str] = []
    for path, path_item in document["paths"].items():
        if not path.startswith("/api/v1/") and path not in {"/health", "/ready"}:
            fail(f"path outside /api/v1: {path}")
        if path in {"/health", "/ready"} and (
            set(path_item) != {"get"} or path_item["get"].get("security") != []
        ):
            fail("health exceptions must be unauthenticated GET operations only")
        if re.search(r"orders?/(submit|place|send)", path, re.I):
            fail(f"broker submit route is forbidden: {path}")
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operations.append(operation.get("operationId", ""))
            if method != "get":
                refs = [p.get("$ref", "") for p in operation.get("parameters", [])]
                if "#/components/parameters/IdempotencyKey" not in refs:
                    fail(f"missing Idempotency-Key on {method.upper()} {path}")
            if path == "/api/v1/risk-policy" and method == "put":
                refs = [p.get("$ref", "") for p in operation.get("parameters", [])]
                if "#/components/parameters/IfMatch" not in refs:
                    fail("risk policy update lacks If-Match")
    if len(operations) != len(set(operations)) or not all(operations):
        fail("operationIds are missing or duplicated")

    schema_docs = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.schema.json")}
    required_schema_files = {
        "common.schema.json", "control-event.schema.json", "decision-result.schema.json",
        "event-envelope.schema.json", "execution-report.schema.json", "market-state.schema.json",
        "risk-decision.schema.json", "risk-reservation.schema.json", "runtime-health.schema.json",
        "safety-event.schema.json", "trade-proposal.schema.json", "demo-run.schema.json",
        "integration-status.schema.json", "trading-profile.schema.json",
    }
    if not required_schema_files.issubset(schema_docs):
        fail(f"missing required versioned schemas: {sorted(required_schema_files - schema_docs.keys())}")
    registry = Registry()
    for filename, schema in schema_docs.items():
        Draft202012Validator.check_schema(schema)
        if not schema.get("$id", "").endswith("/v1/" + filename):
            fail(f"invalid stable schema id in {filename}")
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))

    mirrored = {
        "market-state.schema.json": "MarketState",
        "decision-result.schema.json": "Decision",
        "trade-proposal.schema.json": "TradeProposal",
        "risk-decision.schema.json": "RiskDecision",
    }
    for filename, component in mirrored.items():
        internal = schema_docs[filename]
        public = document["components"]["schemas"][component]
        if set(internal["required"]) - {"schemaVersion"} != set(public["required"]):
            fail(f"required-field drift between {filename} and OpenAPI {component}")
        if set(internal["properties"]) - {"schemaVersion"} != set(public["properties"]):
            fail(f"property drift between {filename} and OpenAPI {component}")
    internal_health = {
        key: value for key, value in schema_docs["runtime-health.schema.json"].items()
        if key not in {"$schema", "$id", "title"}
    }
    if internal_health != document["components"]["schemas"]["RuntimeHealth"]:
        fail("runtime health schema/OpenAPI mirror drift")
    if not {"/health", "/ready"}.issubset(document["paths"]):
        fail("required health/readiness operations missing")
    if schema_docs["common.schema.json"]["$defs"]["decimal"]["pattern"] != document["components"]["schemas"]["Decimal"]["pattern"]:
        fail("decimal lexical pattern drift")
    if schema_docs["common.schema.json"]["$defs"]["fraction"]["pattern"] != document["components"]["schemas"]["Fraction"]["pattern"]:
        fail("fraction lexical pattern drift")
    scalar_checks = [
        ("Decimal", "NaN"),
        ("Decimal", "1e3"),
        ("Decimal", "0." + "1" * 19),
        ("Decimal", "1" * 21),
        ("Fraction", "-0.1"),
        ("Fraction", "1.01"),
        ("UtcTimestamp", "2026-10-12T10:00:00+02:00"),
    ]
    for name, invalid in scalar_checks:
        if Draft202012Validator(document["components"]["schemas"][name], format_checker=contract_formats()).is_valid(invalid):
            fail(f"{name} unexpectedly accepts {invalid!r}")
    if Draft202012Validator(document["components"]["schemas"]["SetModeRequest"]).is_valid({"mode": "LIVE", "reason": "test"}):
        fail("LIVE mode unexpectedly accepted")

    checked = 0
    for name, schema in schema_docs.items():
        if name == "common.schema.json":
            continue
        example = EXAMPLES / name.replace(".schema.json", ".valid.json")
        if not example.is_file():
            fail(f"missing example: {example.name}")
        instance = json.loads(example.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, registry=registry, format_checker=contract_formats())
        errors = list(validator.iter_errors(instance))
        if errors:
            fail(f"{example.name}: {errors[0].message}")
        checked += 1

    for name in ("problem-refused", "problem-conflict", "problem-unavailable", "problem-broker-unknown"):
        problem = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
        if problem["code"] not in document["components"]["schemas"]["ReasonCode"]["enum"]:
            fail(f"unknown problem code in {name}")
        if problem["status"] < 400 or problem["status"] > 599:
            fail(f"invalid problem status in {name}")

    sample = {name: json.loads((EXAMPLES / f"{name}.valid.json").read_text(encoding="utf-8")) for name in ("market-state", "decision-result", "trade-proposal", "risk-decision", "risk-reservation", "execution-report")}
    decision_validator = Draft202012Validator(schema_docs["decision-result.schema.json"], registry=registry, format_checker=contract_formats())
    public_decision_validator = Draft202012Validator({"$ref": openapi_id + "#/components/schemas/Decision"}, registry=openapi_registry, format_checker=contract_formats())
    abstain = json.loads((EXAMPLES / "decision-result.abstain.json").read_text(encoding="utf-8"))
    for fixture in (sample["decision-result"], abstain):
        if not decision_validator.is_valid(fixture):
            fail("typed decision/ABSTAIN fixture is invalid")
        public_fixture = {key: value for key, value in fixture.items() if key != "schemaVersion"}
        if not public_decision_validator.is_valid(public_fixture):
            fail("typed decision/ABSTAIN public DTO drift")
    invalid_decisions = []
    bad = copy.deepcopy(sample["decision-result"])
    bad["typedAnswers"]["regime"]["noul"] = "0.2"
    invalid_decisions.append(bad)
    bad = copy.deepcopy(sample["decision-result"])
    bad["typedAnswers"]["regime"]["kind"] = "SCORE"
    invalid_decisions.append(bad)
    bad = copy.deepcopy(sample["decision-result"])
    del bad["typedAnswers"]["evidenceQuality"]
    invalid_decisions.append(bad)
    bad = copy.deepcopy(sample["decision-result"])
    bad["typedAnswers"]["signalConflict"]["confidence"] = "0.9"
    invalid_decisions.append(bad)
    bad = copy.deepcopy(abstain)
    bad["choice"] = "LONG"
    invalid_decisions.append(bad)
    bad = copy.deepcopy(abstain)
    bad["confidence"] = "0.9"
    invalid_decisions.append(bad)
    bad = copy.deepcopy(sample["decision-result"])
    bad["evidenceQuality"] = None
    invalid_decisions.append(bad)
    for invalid in invalid_decisions:
        public_invalid = {key: value for key, value in invalid.items() if key != "schemaVersion"}
        if decision_validator.is_valid(invalid) or public_decision_validator.is_valid(public_invalid):
            fail("mixed/missing question type or fabricated fallback accepted")
    envelope_validator = Draft202012Validator(schema_docs["event-envelope.schema.json"], registry=registry, format_checker=contract_formats())
    event = json.loads((EXAMPLES / "event-envelope.valid.json").read_text(encoding="utf-8"))
    for event_type in ("risk.decided.v1", "unknown.updated.v1"):
        invalid = copy.deepcopy(event)
        invalid["eventType"] = event_type
        if envelope_validator.is_valid(invalid):
            fail("event type/payload mismatch accepted")
    control_validator = Draft202012Validator(
        schema_docs["control-event.schema.json"], registry=registry, format_checker=contract_formats()
    )
    control = json.loads((EXAMPLES / "control-event.valid.json").read_text(encoding="utf-8"))
    invalid_control = json.loads(
        (EXAMPLES / "control-event.invalid.json").read_text(encoding="utf-8")
    )
    if control_validator.is_valid(invalid_control):
        fail("LIVE control event accepted")
    control_envelope = copy.deepcopy(event)
    control_envelope.update(eventType="control.mode.changed.v1", payload=control)
    if not envelope_validator.is_valid(control_envelope):
        fail("valid mode control envelope rejected")
    control_envelope["eventType"] = "control.policy.changed.v1"
    if envelope_validator.is_valid(control_envelope):
        fail("control event type/action mismatch accepted")
    control_envelope["payload"]["action"] = "POLICY_CHANGED"
    if not envelope_validator.is_valid(control_envelope):
        fail("valid policy control envelope rejected")
    if sample["decision-result"]["marketStateId"] != sample["market-state"]["id"]:
        fail("decision example does not reference market example")
    if sample["trade-proposal"]["decisionId"] != sample["decision-result"]["id"] or sample["trade-proposal"]["marketStateId"] != sample["market-state"]["id"]:
        fail("proposal example references a different decision or market state")
    if sample["risk-decision"]["proposalId"] != sample["trade-proposal"]["id"]:
        fail("risk example references a different proposal")
    if sample["risk-reservation"]["riskDecisionId"] != sample["risk-decision"]["id"] or sample["risk-reservation"]["proposalId"] != sample["trade-proposal"]["id"]:
        fail("reservation example references a different risk decision or proposal")

    print(f"PASS: OAS 3.1.2, {len(operations)} operations, local refs, {len(schema_docs)} schemas, {checked} schema examples, {response_examples} OpenAPI response examples, 4 problem examples")
    print(
        "PASS: typed ABSTAIN example, 7 decision negatives (internal/public), "
        "control LIVE/action negatives, 3 event type/payload negatives"
    )
    print("Schema/example checks passed. Baseline gate: scripts/check-contract-breaking.py (required separately in CI).")


if __name__ == "__main__":
    main()
