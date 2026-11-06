"""The implemented public and private Quant Lab route maps match the canonical OAS."""

from services.control_api.app import create_app
from services.control_api.quant_lab_dto import BASE, OPERATIONS, validator
from services.runtime.paper_api import create_private_paper_app
from tests.contracts.test_quant_lab_transport import operations


def test_all_quant_lab_routes_bind_the_same_operations_and_wire_components():
    canonical = operations()
    assert set(OPERATIONS) == canonical.keys()
    expected = set()
    for operation_id, operation in OPERATIONS.items():
        path, method, spec = canonical[operation_id]
        assert path == BASE + operation.path
        assert method == operation.method.lower()
        success = spec["responses"][str(operation.status)]["content"]["application/json"]["schema"]
        assert success == {"$ref": "#/components/schemas/QuantLab" + operation.response}
        assert "QuantLab" + operation.response in validator(operation.response).schema["$defs"]
        if operation.request is not None:
            request = spec["requestBody"]["content"]["application/json"]["schema"]
            assert request == {"$ref": "#/components/schemas/QuantLab" + operation.request}
        assert set(operation.query) == {
            parameter["name"]
            for parameter in spec.get("parameters", [])
            if parameter.get("in") == "query"
        }
        expected.add((path, method.upper(), operation_id))

    for app in (create_app(), create_private_paper_app(None)):
        actual = {
            (route.path, method, route.operation_id)
            for route in app.routes
            if route.operation_id in canonical
            for method in route.methods
        }
        assert actual == expected
