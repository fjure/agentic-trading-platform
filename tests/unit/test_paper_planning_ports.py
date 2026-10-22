"""Public proof decoding and sanitized Execution SQL publication boundaries."""

import json
from dataclasses import asdict, fields, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace

import pytest

from services.execution.gates import SubmitDenied
from services.execution.paper_quotes import publish_quote
from services.worker.paper_limit import PaperPlanningInputs
from services.worker.paper_planning import read_planning_inputs
from tests.contracts.test_paper_quote_snapshot import capture
from tests.unit.test_decision import state
from tests.unit.test_paper_limit_planner import inputs

WORKER = ("lab_paper_runtime", "atl_paper_worker", "atl_paper_worker_login")
EXECUTION = ("lab_paper_runtime", "atl_paper_execution", "atl_paper_execution_login")
CANARY = "synthetic-private-planning-key-canary"


def serialized():
    def encode(value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.isoformat().replace("+00:00", "Z")
        if isinstance(value, Enum):
            return value.value
        raise TypeError(type(value).__name__)

    return json.loads(json.dumps(asdict(inputs()), default=encode))


class Result:
    def __init__(self, value):
        self.value = value

    def one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class Connection:
    def __init__(self, raw=None, identity=WORKER, context=3):
        self.raw, self.identity, self.context = raw, identity, context
        self.calls = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "current_database()" in sql:
            return Result(self.identity)
        if "paper_planner_inputs" in sql:
            return Result(self.raw)
        if "SELECT version FROM public.risk_instrument_context" in sql:
            return Result(self.context)
        if "paper_quote_append" in sql:
            return Result({"quoteId": "quote-actual-port", "quoteHash": "c" * 64})
        raise AssertionError("unexpected SQL port")


def test_canonical_serialized_sql_proof_decodes_actual_domain_and_binds_requested_state():
    connection = Connection(serialized())
    decoded = read_planning_inputs(connection, state().id)
    assert type(decoded) is PaperPlanningInputs
    assert decoded == inputs()
    assert decoded.digest() == inputs().digest()
    assert len(connection.calls) == 2
    assert connection.calls[1][1] == {"state": state().id}


@pytest.mark.parametrize(
    "path,value",
    [
        (("account_generation",), True),
        (("policy_version",), 1.0),
        (("budget_version",), "1"),
        (("context_version",), 0),
        (("credential_generation",), 9007199254740992),
        (("session_open",), "true"),
        (("risk_budget",), 100.0),
        (("bid",), True),
        (("ask",), "NaN"),
        (("quote_provider_time",), "2026-10-14T15:00:00"),
        (("quote_observed_at",), "2026-10-14T15:00:00+01:00"),
        (("capability_observed_at",), 123),
        (("approval_valid_until",), "invalid"),
        (("fractionable",), True),
        (("fractional_limit_day",), "confirmed"),
        (("capability", "version"), True),
        (("capability", "limit_order"), None),
        (("capability", "quantity_step"), "0"),
        (("policy", "version"), True),
        (("policy", "quantity_decimals"), 10),
        (("policy", "order_type"), "MARKET"),
    ],
)
def test_malformed_public_proof_returns_none_without_legacy_fallback(path, value):
    raw = serialized()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    connection = Connection(raw)
    assert read_planning_inputs(connection, state().id) is None
    assert len(connection.calls) == 2


@pytest.mark.parametrize(
    "mode",
    [
        "extra",
        "credential",
        "missing-capability",
        "missing-field",
        "policy-extra",
        "capability-extra",
        "wrong-state",
        "no-proof",
    ],
)
def test_unknown_secret_missing_or_wrong_state_proof_cannot_be_used(mode):
    raw = serialized()
    if mode in {"extra", "credential"}:
        raw["unexpected" if mode == "extra" else "api_secret"] = CANARY
    elif mode == "missing-capability":
        del raw["capability"]["limit_order"]
    elif mode == "missing-field":
        del raw["quote_evidence_hash"]
    elif mode in {"policy-extra", "capability-extra"}:
        raw[mode.split("-")[0]]["api_key"] = CANARY
    elif mode == "wrong-state":
        raw["market_state_id"] = "other-state"
    else:
        raw = None
    connection = Connection(raw)
    assert read_planning_inputs(connection, state().id) is None
    assert len(connection.calls) == 2


@pytest.mark.parametrize(
    "identity",
    [
        ("lab_runtime", WORKER[1], WORKER[2]),
        (WORKER[0], "atl_worker", WORKER[2]),
        (WORKER[0], WORKER[1], "atl_control_api_login"),
        EXECUTION,
    ],
)
def test_wrong_database_role_or_login_cannot_read_planning_inputs(identity):
    connection = Connection(serialized(), identity)
    with pytest.raises(PermissionError, match="PAPER_PLANNER_IDENTITY_MISMATCH"):
        read_planning_inputs(connection, state().id)
    assert len(connection.calls) == 1


def quote_fixture():
    provider, _ = capture()
    snapshot = provider.snapshot("SPY", "2026-10-14", require_spread=True)
    source = replace(state(), spread=snapshot.spread)
    return source, snapshot


def test_quote_publication_allowlists_fields_and_forwards_exact_execution_fence():
    source, snapshot = quote_fixture()
    hostile = SimpleNamespace(
        **{field.name: getattr(snapshot, field.name) for field in fields(snapshot)},
        api_key=CANARY,
        raw_credentials={"secret": CANARY},
        provider_account_id=CANARY,
    )
    connection = Connection(identity=EXECUTION)
    owner = "11111111-1111-4111-8111-111111111111"
    result = publish_quote(
        SimpleNamespace(connection=connection), source, hostile, owner_id=owner, fencing_token=73
    )
    assert result == {"quoteId": "quote-actual-port", "quoteHash": "c" * 64}
    _, params = connection.calls[-1]
    assert params["state"] == source.id and params["context"] == 3
    assert params["owner"] == owner and params["fence"] == 73
    assert set(params) == {"state", "context", "owner", "fence", "quote"}
    assert json.loads(params["quote"]) == {
        "sourceId": "alpaca-market-data:iex",
        "bid": "100.001",
        "ask": "100.009",
        "providerTime": snapshot.quote_provider_time.isoformat().replace("+00:00", "Z"),
        "observedAt": snapshot.quote_observed_at.isoformat().replace("+00:00", "Z"),
        "sourceCapabilityVersion": snapshot.capability_version,
    }
    assert CANARY not in params["quote"]


@pytest.mark.parametrize(
    "mode",
    [
        "no-bid",
        "no-ask",
        "no-time",
        "crossed",
        "negative",
        "spread",
        "symbol",
        "source",
        "missing-context",
    ],
)
def test_incoherent_or_unavailable_quote_is_blocked_before_sql_append(mode):
    source, snapshot = quote_fixture()
    connection = Connection(identity=EXECUTION)
    if mode == "no-bid":
        snapshot = replace(snapshot, bid=None)
    elif mode == "no-ask":
        snapshot = replace(snapshot, ask=None)
    elif mode == "no-time":
        snapshot = replace(snapshot, quote_provider_time=None)
    elif mode == "crossed":
        snapshot = replace(snapshot, ask=snapshot.bid - 1)
    elif mode == "negative":
        snapshot = replace(snapshot, bid=Decimal("-1"))
    elif mode == "spread":
        source = replace(source, spread=Decimal("0.01"))
    elif mode == "symbol":
        snapshot = replace(snapshot, instrument=replace(snapshot.instrument, id="QQQ"))
    elif mode == "source":
        snapshot = replace(
            snapshot,
            source_capabilities=replace(snapshot.source_capabilities, source_id="other-feed"),
        )
    else:
        connection.context = None
    with pytest.raises(SubmitDenied, match="MARKET_SPREAD_UNAVAILABLE"):
        publish_quote(
            SimpleNamespace(connection=connection),
            source,
            snapshot,
            owner_id="owner",
            fencing_token=73,
        )
    assert not any("paper_quote_append" in sql for sql, _ in connection.calls)


@pytest.mark.parametrize(
    "identity",
    [
        ("lab_runtime", EXECUTION[1], EXECUTION[2]),
        (EXECUTION[0], "atl_execution", EXECUTION[2]),
        (EXECUTION[0], EXECUTION[1], "atl_paper_worker_login"),
    ],
)
def test_quote_publication_never_uses_other_database_or_impersonated_role(identity):
    source, snapshot = quote_fixture()
    connection = Connection(identity=identity)
    with pytest.raises((SubmitDenied, PermissionError)):
        publish_quote(
            SimpleNamespace(connection=connection),
            source,
            snapshot,
            owner_id="owner",
            fencing_token=73,
        )
    assert len(connection.calls) == 1
