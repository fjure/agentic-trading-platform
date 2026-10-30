"""Exact additive Paper ACL manifests; no database or provider access."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from services.runtime.provision_paper import (
    PAPER_ROLES,
    _dormant_privilege_manifest,
    _verify_dormant_privileges,
)


def manifest(head):
    return _dormant_privilege_manifest([(head,)])


def test_existing_collection_manifest_remains_byte_identical():
    previous = manifest("0032_collection_control")
    assert len(previous) == 299
    assert hashlib.sha256(
        json.dumps(sorted(previous), separators=(",", ":")).encode()
    ).hexdigest() == ("4b1073b1abd4773af58c6ec229fc1948f8a1a5dd83236d606a361500456f8706")


def test_retrospective_storage_adds_only_its_eight_control_read_and_request_grants():
    previous = manifest("0032_collection_control")
    current = manifest("0033_retrospective_storage")
    assert previous <= current
    assert current - previous == {
        ("relation", "backtest." + name, "atl_paper_control_api", "SELECT", False)
        for name in (
            "retrospective_assessments",
            "license_receipts",
            "retrospective_datasets",
            "retrospective_revision_bindings",
            "retrospective_reports",
            "retrospective_audit",
        )
    } | {
        ("function", "backtest." + name, "atl_paper_control_api", "EXECUTE", False)
        for name in (
            "request_retrospective_assessment(uuid,text,text,jsonb,text,text,text)",
            "request_retrospective_run(uuid,text,text,jsonb,text,text,text)",
        )
    }


def test_retrospective_gate_adds_only_four_narrow_control_read_ports():
    previous = manifest("0033_retrospective_storage")
    current = manifest("0034_retrospective_gate")
    assert previous <= current
    assert current - previous == {
        (
            "relation",
            "backtest.retrospective_execution_gates",
            "atl_paper_control_api",
            "SELECT",
            False,
        ),
        ("relation", "backtest.source_license_status", "atl_paper_control_api", "SELECT", False),
        (
            "function",
            "backtest.current_retrospective_gate(uuid)",
            "atl_paper_control_api",
            "EXECUTE",
            False,
        ),
        (
            "function",
            "backtest.has_retrospective_correction(uuid)",
            "atl_paper_control_api",
            "EXECUTE",
            False,
        ),
    }
    baseline = manifest("0032_collection_control")
    assert {grant for grant in current if grant[2] != "atl_paper_control_api"} == {
        grant for grant in baseline if grant[2] != "atl_paper_control_api"
    }


class Catalog:
    def __init__(self, heads, grants=(), *, owned=False, ddl=False):
        self.heads, self.grants, self.owned, self.ddl = heads, grants, owned, ddl
        self.queries = []

    def execute(self, query, parameters=None):
        self.queries.append(query)
        if query.startswith("SELECT to_regclass"):
            return SimpleNamespace(fetchone=lambda: ("public.alembic_version",))
        if query == "SELECT version_num FROM public.alembic_version":
            return SimpleNamespace(fetchall=lambda: self.heads)
        if query.startswith("WITH acls"):
            assert set(parameters[0]) == set(PAPER_ROLES.values()) | {
                role + "_login" for role in PAPER_ROLES.values()
            }
            return SimpleNamespace(fetchall=lambda: self.grants)
        if query.startswith("WITH identities"):
            return SimpleNamespace(fetchone=lambda: (self.owned,))
        if query.startswith("SELECT has_database_privilege"):
            return SimpleNamespace(fetchone=lambda: (self.ddl,))
        pytest.fail("unexpected non-catalog query")


@pytest.mark.parametrize(
    "head",
    (
        "0032_collection_control",
        "0033_retrospective_storage",
        "0034_retrospective_gate",
    ),
)
def test_verifier_accepts_exact_reviewed_catalog_without_mutation(head):
    catalog = Catalog([(head,)], manifest(head))
    _verify_dormant_privileges(catalog)
    assert all(query.startswith(("SELECT", "WITH")) for query in catalog.queries)


@pytest.mark.parametrize(
    "heads",
    (
        [("0035_unreviewed",)],
        [("0034_retrospective_execution_gate",)],
        [("0033_retrospective_storage",), ("0034_retrospective_gate",)],
        [("0034_retrospective_gate",), ("0034_retrospective_gate",)],
    ),
)
def test_unknown_or_multiple_heads_fail_before_catalog_privilege_acceptance(heads):
    catalog = Catalog(heads)
    with pytest.raises(PermissionError, match="PAPER_PRIVILEGE_MANIFEST_UNSUPPORTED"):
        _verify_dormant_privileges(catalog)
    assert len(catalog.queries) == 2


@pytest.mark.parametrize(
    "extra",
    (
        ("relation", "backtest.retrospective_reports", "atl_paper_worker", "SELECT", False),
        ("relation", "backtest.retrospective_reports", "atl_paper_execution", "INSERT", False),
        ("relation", "backtest.retrospective_reports", "atl_paper_control_api", "UPDATE", False),
        (
            "relation",
            "backtest.retrospective_reports",
            "atl_paper_control_api_login",
            "SELECT",
            False,
        ),
        ("relation", "backtest.retrospective_reports", "atl_paper_control_api", "SELECT", True),
        (
            "function",
            "backtest.legacy_request_run(uuid,text,text,jsonb,text,text,text)",
            "atl_paper_control_api",
            "EXECUTE",
            False,
        ),
        (
            "relation",
            "backtest.retrospective_audit_event_id_seq",
            "atl_paper_control_api",
            "USAGE",
            False,
        ),
    ),
)
def test_extra_acl_or_grant_option_is_rejected_without_repair(extra):
    grants = manifest("0034_retrospective_gate") | {extra}
    catalog = Catalog([("0034_retrospective_gate",)], grants)
    with pytest.raises(PermissionError, match="PAPER_OBJECT_PRIVILEGES_UNSAFE"):
        _verify_dormant_privileges(catalog)
    assert catalog.grants == grants
    assert len(catalog.queries) == 3


def test_missing_grant_is_rejected():
    grants = manifest("0034_retrospective_gate")
    grants.remove(
        (
            "function",
            "backtest.current_retrospective_gate(uuid)",
            "atl_paper_control_api",
            "EXECUTE",
            False,
        )
    )
    with pytest.raises(PermissionError, match="PAPER_OBJECT_PRIVILEGES_UNSAFE"):
        _verify_dormant_privileges(Catalog([("0034_retrospective_gate",)], grants))


@pytest.mark.parametrize(
    ("owned", "ddl", "code"),
    (
        (True, False, "PAPER_OBJECT_PRIVILEGES_UNSAFE"),
        (False, True, "PAPER_DDL_PRIVILEGES_UNSAFE"),
    ),
)
def test_known_head_does_not_bypass_ownership_or_ddl_checks(owned, ddl, code):
    catalog = Catalog(
        [("0034_retrospective_gate",)],
        manifest("0034_retrospective_gate"),
        owned=owned,
        ddl=ddl,
    )
    with pytest.raises(PermissionError, match=code):
        _verify_dormant_privileges(catalog)
