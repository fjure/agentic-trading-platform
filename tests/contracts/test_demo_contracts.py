"""Contract invariants for the demo and write-only setup API."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
OAS = yaml.safe_load((ROOT / "contracts/http/control-api.openapi.yaml").read_text())


def test_demo_and_setup_operation_ids_are_stable():
    expected = {
        "createDemoRun",
        "listDemoRuns",
        "getDemoRun",
        "getDemoSnapshot",
        "getDemoHistory",
        "controlDemoRun",
        "getIntegrationStatuses",
        "saveAlpacaIntegration",
        "saveJevIntegration",
        "getTradingProfile",
        "updateTradingProfile",
    }
    actual = {
        op["operationId"]
        for path in OAS["paths"].values()
        for op in path.values()
        if isinstance(op, dict) and "operationId" in op
    }
    assert expected <= actual


def test_setup_write_credentials_are_write_only_and_responses_are_redacted():
    schemas = OAS["components"]["schemas"]
    for name, fields in (
        ("AlpacaIntegrationWriteRequest", ("apiKey", "apiSecret")),
        ("JevIntegrationWriteRequest", ("apiKey",)),
    ):
        for field in fields:
            assert schemas[name]["properties"][field]["writeOnly"] is True
    assert "apiKey" not in schemas["components"] if "components" in schemas else True
    result = schemas["IntegrationSaveResult"]["properties"]
    assert set(result) == {"status", "restartRequired"}
    assert schemas["IntegrationStatus"]["properties"]["status"]["$ref"].endswith("IntegrationState")


def test_demo_is_marked_simulation_and_has_fixed_choices():
    schemas = OAS["components"]["schemas"]
    assert schemas["DemoRun"]["properties"]["simulationOnly"]["const"] is True
    assert schemas["DemoStrategy"]["enum"] == ["mock-neutral", "neutral-baseline"]
    assert schemas["DemoHistoryResource"]["enum"] == [
        "market-states",
        "decisions",
        "proposals",
        "risk-decisions",
        "orders",
        "audit",
        "runs",
        "costs",
    ]


def test_versioned_demo_setup_schemas_are_registered():
    expected = {
        "demo-run.schema.json",
        "integration-status.schema.json",
        "trading-profile.schema.json",
    }
    schemas_dir = ROOT / "contracts/schemas/v1"
    actual = {p.name for p in schemas_dir.glob("*.schema.json")}
    assert expected <= actual
    for name in expected:
        schema = __import__("json").loads((schemas_dir / name).read_text())
        assert schema["$id"].endswith("/v1/" + name)
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(schema)
        example = __import__("json").loads(
            (
                ROOT / "contracts/examples/v1" / name.replace(".schema.json", ".valid.json")
            ).read_text()
        )
        Draft202012Validator(schema).validate(example)
        if name == "demo-run.schema.json":
            invalid = dict(example, simulationOnly=False)
            assert not Draft202012Validator(schema).is_valid(invalid)
        if name == "integration-status.schema.json":
            invalid = __import__("copy").deepcopy(example)
            invalid["alpaca"]["apiKey"] = "synthetic-secret-canary"
            assert not Draft202012Validator(schema).is_valid(invalid)


def test_budget_decimal_bounds_are_exact():
    import re

    pattern = OAS["components"]["schemas"]["PositiveBudget"]["pattern"]
    assert re.fullmatch(pattern, "1000000")
    assert re.fullmatch(pattern, "1000000.000000")
    assert re.fullmatch(pattern, "0.01")
    assert not re.fullmatch(pattern, "1000000.1")
    assert not re.fullmatch(pattern, "9999999")
    assert not re.fullmatch(pattern, "0")


def test_demo_snapshot_cash_and_positions_are_optional_for_existing_readers():
    snapshot = OAS["components"]["schemas"]["SupervisorSnapshot"]
    assert "portfolioCash" not in snapshot["required"]
    assert "positions" not in snapshot["required"]
    assert snapshot["properties"]["portfolioCash"]["$ref"].endswith("Decimal")
    position = snapshot["properties"]["positions"]["items"]
    assert position["required"] == ["instrument", "quantity"]
    assert position["properties"]["quantity"]["$ref"].endswith("Decimal")


def test_empty_demo_history_is_a_valid_response():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    uri = "https://agentic-trading-lab.invalid/control-api"
    registry = Registry().with_resource(
        uri, Resource.from_contents(OAS, default_specification=DRAFT202012)
    )
    Draft202012Validator(
        {"$ref": uri + "#/components/schemas/DemoHistoryPage"},
        registry=registry,
    ).validate({"items": [], "nextCursor": None})
