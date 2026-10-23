"""Actual fixed-role human provenance, shared Risk and durable command recovery."""

from pathlib import Path

STAGE = "HUMAN_SQL"
MARKER = "PASS: human SQL provenance, shared reservations and fenced outcomes"


def test_paper_human_orders_postgres():
    from tests.integration.test_paper_provision import host

    assert (
        host(
            Path(__file__),
            MARKER,
            support_paths=(Path(__file__).with_name("test_paper_lifecycle.py"),),
        )
        == MARKER
    )


def test_paper_model_human_concurrent_reservations_postgres():
    from tests.integration.test_paper_provision import host

    for scenario in ("budget", "sell", "budget-denied", "sell-denied"):
        assert host(Path(__file__), MARKER, support_paths=(
            Path(__file__).with_name("test_paper_lifecycle.py"),
        ), guest_args=(scenario,)) == MARKER


def create_human(
    f, *, key="human-sql-order-0001", side="BUY", quantity="1", symbol="SPY", lifetime=10,
    state_id=None,
):
    import uuid
    from datetime import timedelta

    from sqlalchemy import text

    from services.worker.paper_planning import read_planning_inputs

    profile = f.api.read_profile(f.trace)
    body = {
        name: profile[name]
        for name in (
            "credentialGeneration",
            "accountGeneration",
            "policyVersion",
            "profileVersion",
            "limitPolicyVersion",
            "budgetVersion",
            "riskBudget",
            "currency",
        )
    }
    expiry = f.clock.now() + timedelta(seconds=lifetime)
    body |= dict(
        symbol=symbol,
        side=side,
        quantity=quantity,
        limitPrice="15",
        orderType="LIMIT",
        timeInForce="DAY",
        session="REGULAR",
        expiresAt=expiry.isoformat().replace("+00:00", "Z"),
        confirmation="PLACE_PAPER_LIMIT",
    )
    operation = f.api.enqueue("PLACE_LIMIT_ORDER", body, "human-operator", key, "b" * 32)
    owner = str(uuid.uuid4())
    claim = f.execution.claim(owner, lease_seconds=120)
    assert claim and claim["operationId"] == operation["operationId"]
    assert "acceptedAt" in claim
    assert not f.execution.finish(operation["operationId"], owner, claim["fence"])
    assert f.api.read_operation(operation["operationId"])["status"] == "RUNNING"
    with f.engines["execution"].begin() as c:
        proof = read_planning_inputs(c, state_id or symbol + "-state", execution=True)
        assert proof is not None
        raw = c.execute(
            text(
                "SELECT paper_human_proposal_create(CAST(:id AS uuid),CAST(:owner AS uuid),"
                ":fence,:state,:hash,:until)"
            ),
            dict(
                id=operation["operationId"],
                owner=owner,
                fence=claim["fence"],
                state=state_id or symbol + "-state",
                hash=proof.digest(),
                until=expiry,
            ),
        ).scalar_one()
    assert raw and "error" not in raw, raw
    return operation, claim, owner, raw


def guest():
    import time
    import uuid
    from dataclasses import fields
    from unittest.mock import patch

    import psycopg
    from test_paper_lifecycle import PaperFixture

    from packages.domain import models as m
    from packages.domain.paper_workflow import HumanOrderRequest, PaperTradeProposal
    from packages.persistence.ledger import _decode
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve

    f = PaperFixture()
    f.prepare()
    f.profile()
    f.refresh()
    assert "activationId" in f.start()[3]
    operation, claim, owner, raw = create_human(f, lifetime=2)
    proposal_id = raw["id"]
    assert set(raw) == {item.name for item in fields(PaperTradeProposal)}
    assert str(uuid.UUID(proposal_id)) == proposal_id
    assert raw["human_request_id"] == operation["operationId"]
    assert raw["decision_id"] is None and raw["proposal_source"] == "HUMAN_REQUEST"
    assert raw["trace_id"] == "b" * 32
    request_raw = f.sql(
        "execution", "SELECT paper_human_request_read(:id)", id=operation["operationId"]
    )
    request = _decode(HumanOrderRequest, request_raw)
    with f.ledger.transaction() as tx:
        proposal = tx.get(m.TradeProposal, proposal_id)
        assert type(proposal) is PaperTradeProposal
        proposal.validate_human_request(request)
        assert proposal.instrument_id == "SPY" and proposal.target is m.Target.LONG
        assert proposal.trace_id != tx.get(m.MarketState, "SPY-state").trace_id
    with (patch("services.worker.paper_limit._EASTERN", f.zone),
          patch("services.worker.paper_human_limit._EASTERN", f.zone)):
        risk = reserve(
            ledger=f.ledger, account_id="paper", proposal_id=proposal_id, limits=RiskLimits()
        )
    if not risk.approved:
        global STAGE
        STAGE = "HUMAN_SQL_" + risk.reason_codes[0]
    assert risk.approved, risk.reason_codes
    assert f.sql("execution", "SELECT count(*) FROM decisions") == 0
    assert f.sql("execution", "SELECT count(*) FROM order_intents") == 1
    assert not f.execution.finish(
        operation["operationId"], owner, claim["fence"], error_code="PROVIDER_REJECTED"
    )
    metadata = f.sql(
        "execution",
        "SELECT paper_human_operation_read(CAST(:id AS uuid))",
        id=operation["operationId"],
    )
    assert metadata == dict(
        proposalId=proposal_id,
        orderId=None,
        orderStatus=None,
        riskDecision="APPROVED",
        riskReasons=list(risk.reason_codes),
    )
    assert not f.sql(
        "execution",
        "SELECT paper_human_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence)",
        id=operation["operationId"],
        owner=str(uuid.uuid4()),
        fence=claim["fence"],
    )
    with psycopg.connect(dbname=f.database, **f.common) as c:
        before = c.execute("SELECT count(*) FROM paper_human_requests").fetchone()[0]
        for statement in (
            "UPDATE paper_human_requests SET actor_id='changed'",
            "UPDATE trade_proposals SET quantity=quantity+1",
            "DELETE FROM paper_limit_plans",
        ):
            try:
                with c.transaction():
                    c.execute(statement)
            except psycopg.Error:
                pass
            else:
                raise AssertionError("immutable human source was mutable")
        assert c.execute("SELECT count(*) FROM paper_human_requests").fetchone()[0] == before
    paused = f.api.enqueue("PAUSE", {"action": "PAUSE"}, "human-operator",
                           "human-pause-before-attempt", f.trace)
    assert paused["acceptedGeneration"] > operation["acceptedGeneration"]
    with psycopg.connect(dbname=f.database, **f.common) as c:
        c.execute("UPDATE paper_commands SET lease_until=clock_timestamp()-interval '1 second' "
                  "WHERE id=%s", (operation["operationId"],))
    previous_claim = claim
    owner = str(uuid.uuid4())
    claim = f.execution.claim(owner, lease_seconds=120)
    assert claim["operationId"] == operation["operationId"]
    assert claim["fence"] == previous_claim["fence"] + 1
    assert claim["acceptedAt"] == previous_claim["acceptedAt"]
    # Recovery returns the original immutable obligation, never new terms/TTL,
    # even when the caller offers a fresh hash, state or future expiry.
    from datetime import timedelta

    replay = f.sql("execution", "SELECT paper_human_proposal_create(CAST(:id AS uuid),"
                   "CAST(:owner AS uuid),:fence,'QQQ-state',:hash,:until)",
                   id=operation["operationId"], owner=owner, fence=claim["fence"],
                   hash="0" * 64, until=f.clock.now() + timedelta(seconds=20))
    assert replay == raw
    from services.execution.gates import SubmitDenied, evaluate_submit_gate

    try:
        with f.ledger.transaction() as tx:
            evaluate_submit_gate(tx=tx, account_id="paper",
                intent_id=f.sql("execution", "SELECT id FROM order_intents"),
                limits=RiskLimits(), owner_id=f.owner, fencing_token=f.fence,
                expected_broker_id="alpaca-paper")
    except SubmitDenied:
        pass
    else:
        raise AssertionError("paused recovered proposal gained submit authority")
    time.sleep(2.1)
    assert f.sql(
        "execution",
        "SELECT paper_human_finish(CAST(:id AS uuid),CAST(:owner AS uuid),:fence)",
        id=operation["operationId"],
        owner=owner,
        fence=claim["fence"],
    )
    assert f.api.read_operation(operation["operationId"])["errorCode"] == "APPROVAL_EXPIRED"
    assert f.sql("execution", "SELECT state FROM risk_reservations") == "RELEASED"
    assert f.sql("execution", "SELECT count(*) FROM broker_orders") == 0
    for role in ("api", "worker"):
        try:
            f.sql(role, "SELECT paper_human_request_read(:id)", id=operation["operationId"])
        except Exception as error:
            assert getattr(getattr(error, "orig", error), "sqlstate", None) == "42501"
        else:
            raise AssertionError("human private port exposed to another role")
    try:
        f.sql("worker", "INSERT INTO trade_proposals SELECT (jsonb_populate_record("
              "NULL::public.trade_proposals,to_jsonb(p)||"
              "jsonb_build_object('id','forged-human'))).* "
              "FROM trade_proposals p WHERE id=:id", id=proposal_id)
    except Exception as error:
        assert getattr(getattr(error, "orig", error), "sqlstate", None) == "42501"
    else:
        raise AssertionError("worker forged human provenance")
    print(MARKER)


def concurrent_guest(scenario):
    global STAGE
    force_human_denied = scenario.endswith("-denied")
    scenario = scenario.removesuffix("-denied")
    import json
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from threading import Barrier
    from types import SimpleNamespace
    from unittest.mock import patch

    import psycopg
    from test_paper_lifecycle import PaperFixture

    from packages.domain import models as m
    from packages.persistence.ledger import PostgresLedger
    from packages.risk.engine import RiskLimits
    from packages.risk.reservations import reserve
    from services.runtime.worker import WorkerRuntime

    f = PaperFixture()
    if scenario == "sell":
        f.initial_holding_value = f.current_holding_value = Decimal(900)
    f.prepare()
    f.profile()
    f.refresh()
    assert "activationId" in f.start()[3]
    state_id = "SPY-state"
    if scenario == "sell":
        state_id = "SPY-sell-state"
        # New audited bearish source over the genuinely imported position. No
        # position, reference, trigger or financial gate is fabricated/disabled.
        with psycopg.connect(dbname=f.database, **f.common) as c:
            c.execute("INSERT INTO audit_events(id,occurred_at,actor_id,action,reason_codes,"
                      "entity_id,entity_version,trace_id) VALUES(gen_random_uuid()::text,"
                      "clock_timestamp(),'fixture','fixture','[\"SYNTHETIC_FIXTURE\"]',%s,1,%s)",
                      (state_id, f.trace))
            c.execute("INSERT INTO market_states SELECT (jsonb_populate_record("
                      "NULL::public.market_states,to_jsonb(ms)||jsonb_build_object('id',%s::text,"
                      "'as_of',clock_timestamp(),'features',"
                      "'{\"previous_close\":\"16\",\"last_close\":\"15\","
                      "\"window_volume\":\"100\"}'::jsonb))).* "
                      "FROM market_states ms WHERE id='SPY-state'", (state_id,))
        stamp = m.utc_string(f.clock.now())
        quote = dict(sourceId="alpaca-market-data:iex", bid="14.99", ask="15",
                     providerTime=stamp, observedAt=stamp, sourceCapabilityVersion=1)
        assert "quoteId" in f.sql("execution",
            "SELECT paper_quote_append(:state,1,:owner,:fence,CAST(:quote AS jsonb))",
            state=state_id, owner=f.owner, fence=f.fence, quote=json.dumps(quote))
    runtime = WorkerRuntime(SimpleNamespace(deployment="paper", account_id="paper",
        instruments=("SPY",), decision_provider="mock"), PostgresLedger(engine=f.engines["worker"]))
    with (patch("services.worker.paper_limit._EASTERN", f.zone),
          patch("services.worker.paper_human_limit._EASTERN", f.zone)):
        assert runtime.tick() == "READY"
        if scenario == "sell":
            assert runtime.tick() == "READY"
        model = f.sql("execution", "SELECT id FROM trade_proposals WHERE market_state_id=:state",
                      state=state_id)
        if model is None:
            reason = f.sql("execution", "SELECT reason_codes->>0 FROM audit_events "
                           "WHERE action='paper.plan_outcome' ORDER BY occurred_at DESC LIMIT 1")
            STAGE = "HUMAN_SQL_MODEL_" + (reason or "MISSING")
        assert model is not None
        operation, claim, owner, human = create_human(f, quantity="40",
            side="SELL" if scenario == "sell" else "BUY", state_id=state_id)
        barrier = Barrier(2)

        def competing(proposal_id):
            barrier.wait(timeout=3)
            return reserve(ledger=f.ledger, account_id="paper", proposal_id=proposal_id,
                           limits=RiskLimits())

        if force_human_denied:
            outcomes = tuple(reserve(ledger=f.ledger, account_id="paper", proposal_id=value,
                                    limits=RiskLimits()) for value in (model, human["id"]))
            assert outcomes[0].approved and not outcomes[1].approved
        else:
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(competing, model)
                b = pool.submit(competing, human["id"])
                outcomes = a.result(timeout=5), b.result(timeout=5)
        assert sum(item.approved for item in outcomes) == 1, tuple(
            item.reason_codes for item in outcomes)
        assert f.sql("execution", "SELECT count(*) FROM risk_reservations "
                     "WHERE state='ACTIVE'") == 1
        assert f.sql("execution", "SELECT count(*) FROM order_intents") == 1
        if not outcomes[1].approved:
            assert f.sql("execution", "SELECT paper_human_finish(CAST(:id AS uuid),"
                         "CAST(:owner AS uuid),:fence)", id=operation["operationId"], owner=owner,
                         fence=claim["fence"])
            public = f.api.read_operation(operation["operationId"])
            assert public["errorCode"] == (
                "INSUFFICIENT_POSITION" if scenario == "sell" else "BUDGET_EXCEEDED")
        if scenario == "sell":
            assert f.sql("execution", "SELECT sum(p.quantity) FROM risk_reservations r "
                         "JOIN trade_proposals p ON p.id=r.proposal_id "
                         "WHERE r.state='ACTIVE'") <= 60
        else:
            assert f.sql("execution", "SELECT sum(reserved_notional)*1.01 "
                         "FROM risk_reservations WHERE state='ACTIVE'") <= 1000
    assert f.sql("execution", "SELECT count(*) FROM broker_orders") == 0
    print(MARKER)


if __name__ == "__main__":
    import sys
    import traceback

    try:
        if len(sys.argv) > 2:
            concurrent_guest(sys.argv[2])
        else:
            guest()
    except Exception as error:
        root = getattr(error, "orig", error)
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(
            f"APW02_FAIL:{STAGE}:{type(root).__name__}:{Path(frame.filename).name}:{frame.lineno}"
        )
        raise SystemExit(1) from None
