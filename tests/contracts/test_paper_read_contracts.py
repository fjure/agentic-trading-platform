"""APW-09 bounded, truthful stored Paper read contracts; synthetic examples only."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from scripts.contract_formats import contract_formats

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text())
URI = "https://agentic-trading-lab.invalid/control-api"
REGISTRY = Registry().with_resource(
    URI, Resource.from_contents(OAS, default_specification=DRAFT202012)
)
READS = {
    "snapshot": ("getPaperSnapshot", "PaperSnapshot", "paper-snapshot"),
    "orders": ("listPaperOrders", "PaperOrderPage", "paper-order-page"),
    "fills": ("listPaperFills", "PaperFillPage", "paper-fill-page"),
    "operations": ("listPaperOperations", "PaperOperationPage", "paper-operation-page"),
    "traces/{traceId}": ("getPaperTrace", "PaperTrace", "paper-trace"),
}


def example(name):
    return json.loads((ROOT / "contracts/examples/v1" / (name + ".valid.json")).read_text())


def validator(component):
    return Draft202012Validator(
        {"$ref": URI + "#/components/schemas/" + component},
        registry=REGISTRY,
        format_checker=contract_formats(),
    )


@pytest.mark.parametrize("resource", READS)
def test_additive_reads_are_authenticated_bounded_and_own_namespace(resource):
    operation_id, component, filename = READS[resource]
    path = OAS["paths"]["/api/v1/paper/" + resource]
    assert set(path) == {"get"}
    operation = path["get"]
    assert operation["operationId"] == operation_id
    assert operation.get("security", OAS["security"]) == [{"LocalSession": []}]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/" + component
    }
    assert {"400", "401", "403", "409", "503"} <= operation["responses"].keys()
    assert "requestBody" not in operation
    assert "no GET contacts a provider" in operation["description"]
    parameters = {p["name"]: p for p in operation["parameters"]}
    if resource in {"orders", "fills", "operations"}:
        assert set(parameters) == {"limit", "cursor"}
        limit = parameters["limit"]["schema"]
        assert limit == {"type": "integer", "minimum": 1, "maximum": 50, "default": 25}
        for bad in (False, 0, 51, "25", 25.5):
            assert not Draft202012Validator(limit).is_valid(bad)
    assert example(filename)["namespace"] == "alpaca-paper"


@pytest.mark.parametrize("component,filename", [(v[1], v[2]) for v in READS.values()])
def test_public_and_versioned_examples_validate_identically(component, filename):
    schema = json.loads((ROOT / "contracts/schemas/v1" / (filename + ".schema.json")).read_text())
    Draft202012Validator.check_schema(schema)
    public, internal = (
        validator(component),
        Draft202012Validator(schema, format_checker=contract_formats()),
    )
    value = example(filename)
    public.validate(value)
    internal.validate(value)
    bad = value | {"namespace": "fake"}
    assert not public.is_valid(bad) and not internal.is_valid(bad)
    bad = value | {"providerAccountId": "11111111-1111-4111-8111-111111111111"}
    assert not public.is_valid(bad) and not internal.is_valid(bad)


def test_unknown_basis_is_null_while_actual_flat_book_is_empty():
    value = example("paper-snapshot.unconfigured")
    check = validator("PaperSnapshot")
    check.validate(value)
    assert value["portfolio"] is None and value["positions"] is None
    assert value["verificationOperationId"] is None
    assert value["status"]["credentialGeneration"] == value["profile"]["credentialGeneration"] == 0
    assert value["markets"][0]["quote"] is None
    for field in ("portfolio", "positions", "verificationOperationId"):
        missing = deepcopy(value)
        missing.pop(field)
        assert not check.is_valid(missing)
    assert not check.is_valid(value | {"positions": []})
    prepared = example("paper-snapshot")
    check.validate(prepared | {"positions": []})
    assert not check.is_valid(prepared | {"positions": None})
    assert not check.is_valid(prepared | {"verificationOperationId": "workflow-epoch-1"})


@pytest.mark.parametrize(
    "path,bad",
    [
        (("portfolio", "cash"), 100000),
        (("portfolio", "equity"), "1e5"),
        (("positions", 0, "quantity"), "-1"),
        (("positions", 0, "costBasis"), "7.5000000000000000001"),
        (("markets", 0, "quote", "bid"), "0"),
        (("markets", 0, "quote", "providerTime"), "2026-10-24T01:00:00+00:00"),
        (("markets", 0, "quote", "sourceId"), "arbitrary-provider"),
        (("markets", 0, "capability", "version"), True),
        (("markets", 0, "capability", "fractionalConfirmed"), "CONFIRMED"),
        (("markets", 0, "session", "open"), "true"),
        (("markets", 0, "session", "startsAt"), "not-a-timeZ"),
    ],
)
def test_snapshot_evidence_never_coerces_precision_types_sources_or_times(path, bad):
    value = example("paper-snapshot")
    current = value
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = bad
    assert not validator("PaperSnapshot").is_valid(value)


def test_unknown_cost_basis_stays_nullable_without_fabricating_a_derived_value():
    value = example("paper-snapshot")
    value["positions"][0]["costBasis"] = None
    validator("PaperSnapshot").validate(value)
    value["positions"][0].pop("costBasis")
    assert not validator("PaperSnapshot").is_valid(value)


def test_order_provenance_is_exclusive_and_historical_terms_remain_truthful():
    value = example("paper-order-page")["items"][0]
    check = validator("PaperOrder")
    check.validate(value)
    assert value["clientOrderId"] == "paper-human-client-1"
    missing_client = deepcopy(value)
    missing_client.pop("clientOrderId")
    assert not check.is_valid(missing_client)
    assert not check.is_valid(value | {"brokerOrderId": "provider-order-canary"})
    model = value | {
        "proposalSource": "MODEL_DECISION",
        "decisionId": "decision-1",
        "humanRequestId": None,
    }
    check.validate(model)
    for bad in (
        value | {"decisionId": "invented-decision"},
        value | {"humanRequestId": None},
        model | {"decisionId": None},
        model | {"humanRequestId": value["humanRequestId"]},
        value | {"limitPrice": None},
    ):
        assert not check.is_valid(bad)
    historical = model | {
        "orderType": "MARKET",
        "limitPrice": None,
        "timeInForce": None,
        "session": None,
    }
    check.validate(historical)
    assert not check.is_valid(historical | {"limitPrice": "15"})
    assert not check.is_valid(value | {"timeInForce": "GTC"})
    filled = value | {
        "status": "FILLED",
        "filledQuantity": "1",
        "cancel": value["cancel"] | {"state": "FINISHED"},
    }
    check.validate(filled)  # Resolved cancellation does not rewrite a genuine fill into CANCELED.


def test_fee_unknown_is_required_null_and_local_fill_id_is_not_provider_activity():
    value = example("paper-fill-page")["items"][0]
    assert value["fee"] is None and value["fillId"].startswith("paper-activity:")
    check = validator("PaperFill")
    for field, bad in (("fee", 0), ("fee", "-1"), ("quantity", "0"), ("price", "Infinity")):
        assert not check.is_valid(value | {field: bad})
    missing = deepcopy(value)
    missing.pop("fee")
    assert not check.is_valid(missing)
    assert not check.is_valid(value | {"activityId": "provider::raw-activity"})


@pytest.mark.parametrize(
    "filename,component,limit",
    [
        ("paper-order-page", "PaperOrderPage", 50),
        ("paper-fill-page", "PaperFillPage", 50),
        ("paper-operation-page", "PaperOperationPage", 50),
        ("paper-trace", "PaperTrace", 32),
    ],
)
def test_pages_and_trace_excerpts_have_hard_bounds(filename, component, limit):
    value = example(filename)
    value["items"] *= limit
    validator(component).validate(value)
    value["items"].append(deepcopy(value["items"][0]))
    assert not validator(component).is_valid(value)


@pytest.mark.parametrize(
    "cursor", ["", "a" * 1025, "../fake", "raw provider::id", "=base64", 12, None]
)
def test_query_cursor_is_bounded_opaque_and_does_not_accept_arbitrary_text(cursor):
    assert not validator("PaperReadCursor").is_valid(cursor)


def test_operation_history_reuses_original_receipts_and_strict_errors():
    item = example("paper-operation-page")["items"][0]
    assert OAS["components"]["schemas"]["PaperOperationPage"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/PaperOperation"
    }
    validator("PaperOperation").validate(item)
    failed = item | {"status": "BLOCKED"}
    assert not validator("PaperOperation").is_valid(failed)
    validator("PaperOperation").validate(failed | {"errorCode": "VERSION_CONFLICT"})


@pytest.mark.parametrize(
    "field,bad",
    [
        ("role", "private-operator-login"),
        ("action", "raw.provider.text"),
        ("reasonCodes", ["provider-secret-canary"]),
        ("reasonCodes", ["VERSION_CONFLICT"] * 2),
        ("entityVersion", 9007199254740992),
        ("entityId", "x" * 256),
        ("prompt", "synthetic prompt canary"),
        ("providerAccountId", "synthetic-account-canary"),
        ("actorId", "raw-private-actor"),
    ],
)
def test_trace_is_a_semantic_allowlist_never_raw_audit_or_prompt_text(field, bad):
    value = example("paper-trace")
    value["items"][0][field] = bad
    assert not validator("PaperTrace").is_valid(value)


def test_trace_completeness_is_explicit_and_not_implicitly_assumed():
    value = example("paper-trace")
    assert value["complete"] is False
    value.pop("complete")
    assert not validator("PaperTrace").is_valid(value)
